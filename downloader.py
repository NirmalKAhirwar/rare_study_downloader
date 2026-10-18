import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    API_HEADERS,
    BASE_URL,
    DOWNLOAD_WORKERS,
    FFMPEG,
    FFPROBE,
    MAX_RETRIES,
    PAGE_HEADERS,
    RETRY_BACKOFF,
    STREAM_HEADERS,
    TEMP_DIR,
)
from utils import human_bytes, sanitize_filename, tool_hint

MPD_NS = "{urn:mpeg:dash:schema:mpd:2011}"
CENC_NS = "{urn:mpeg:cenc:2013}"


class ResolveError(RuntimeError):
    pass


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    """Raised when a download is paused or cancelled by the caller."""


def ensure_tools() -> None:
    """Raise DownloadError with install hints if ffmpeg/ffprobe are unavailable."""

    for tool in (FFMPEG, FFPROBE):
        if shutil.which(tool) is None:
            raise DownloadError(tool_hint(tool))


def title_from_page_url(page_url: str) -> str:
    """Best-effort display name from a khazana-video page URL."""

    query = parse_qs(urlparse(page_url).query)
    for key in ("topicName", "videoName", "title"):
        if query.get(key):
            return sanitize_filename(query[key][0])
    return sanitize_filename(Path(urlparse(page_url).path).name or "video")


@dataclass
class Representation:
    id: str
    is_video: bool
    bandwidth: int = 0
    width: int = 0
    height: int = 0
    codecs: str = ""
    mime_type: str = ""
    lang: str = ""
    kid: str = ""
    init_url: str = ""
    segment_urls: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.is_video:
            return f"{self.height}p" if self.height else f"video-{self.id}"
        return "audio"


@dataclass
class StreamInfo:
    mpd_url: str
    keys: Dict[str, str] = field(default_factory=dict)
    drm: Optional[dict] = None
    title: str = "video"
    representations: List[Representation] = field(default_factory=list)


@dataclass
class MediaFiles:
    video: Optional[Path] = None
    audio: Optional[Path] = None


def _iso_to_seconds(value: str) -> float:
    m = re.match(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?"
        r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?",
        value or "",
    )
    if not m:
        return 0.0
    years, months, days, hours, minutes, seconds = m.groups()
    total = 0.0
    total += float(days or 0) * 86400
    total += float(hours or 0) * 3600
    total += float(minutes or 0) * 60
    total += float(seconds or 0)
    return total


def _norm_kid(value: str) -> str:
    return re.sub(r"[^0-9a-f]", "", (value or "").lower())


def parse_clear_keys(raw) -> Dict[str, str]:
    keys: Dict[str, str] = {}
    if not raw:
        return keys
    if isinstance(raw, dict):
        for k, v in raw.items():
            keys[_norm_kid(str(k))] = _norm_kid(str(v))
        return keys
    for entry in raw:
        text = str(entry).strip()
        if ":" in text:
            kid, key = text.split(":", 1)
            keys[_norm_kid(kid)] = _norm_kid(key)
    return keys


def _run_flags() -> Dict:
    """Stop ffmpeg/ffprobe from flashing a console window when run on Windows."""

    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def probe_duration(path: Path) -> Optional[float]:
    """Return media duration in seconds using ffprobe, or None if unreadable."""

    try:
        result = subprocess.run(
            [
                FFPROBE, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            **_run_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def append_signature(url: str, sig: Dict[str, str]) -> str:
    if not sig:
        return url
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    for key, value in sig.items():
        if key not in query:
            query[key] = [value]
    flat = {k: v[0] for k, v in query.items()}
    return urlunparse(parts._replace(query=urlencode(flat)))


class RareStudyDownloader:
    def __init__(self, cookies: Dict[str, str], base_url: str = BASE_URL, workers: int = DOWNLOAD_WORKERS):
        self.base_url = base_url.rstrip("/")
        self.cookies = dict(cookies)
        self.workers = max(1, workers)
        self._local = threading.local()
        self._cookies_lock = threading.Lock()
        self._cookies_version = 0

    def set_cookies(self, cookies: Dict[str, str]) -> None:
        """Swap the cookie jar; existing thread-local sessions pick it up on next use."""

        with self._cookies_lock:
            self.cookies = dict(cookies)
            self._cookies_version += 1

    def _get_session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(PAGE_HEADERS)
            retry = Retry(
                total=MAX_RETRIES,
                backoff_factor=RETRY_BACKOFF,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET"}),
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=self.workers, pool_maxsize=self.workers)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._local.session = session
            self._local.cookies_version = None
        if getattr(self._local, "cookies_version", None) != self._cookies_version:
            session.cookies.clear()
            session.cookies.update(self.cookies)
            self._local.cookies_version = self._cookies_version
        return session

    def _get(self, url: str, headers: dict, **kwargs) -> requests.Response:
        resp = self._get_session().get(url, headers=headers, timeout=60, **kwargs)
        resp.raise_for_status()
        return resp

    def extract_media_token(self, page_url: str) -> str:
        resp = self._get(page_url, headers={**PAGE_HEADERS, "Referer": self.base_url + "/"})
        match = re.search(r'const MEDIA_TOKEN\s*=\s*"([^"]+)"', resp.text)
        if not match:
            if "login" in resp.url.lower():
                raise ResolveError("Session expired or not logged in. Refresh cookies.json.")
            raise ResolveError("MEDIA_TOKEN not found on page (video may be unavailable).")
        return match.group(1)

    def resolve(self, page_url: str, container: str = "DASH") -> StreamInfo:
        token = self.extract_media_token(page_url)
        api_url = f"{self.base_url}/v1/videos/video-url-details"
        params = {"mediaToken": token, "videoContainerType": container}
        resp = self._get(api_url, headers={**API_HEADERS, "Referer": page_url}, params=params)

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ResolveError(f"Invalid response from video API: {resp.text[:200]}") from exc

        if not payload.get("success") or not payload.get("data", {}).get("url"):
            raise ResolveError(payload.get("message") or "Video not available.")

        data = payload["data"]
        mpd_url = data["url"]
        keys = parse_clear_keys(payload.get("keys") or data.get("keys"))
        drm = payload.get("drmDetails") or data.get("drmDetails")

        info = StreamInfo(mpd_url=mpd_url, keys=keys, drm=drm, title=self._title(page_url))
        info.representations = self.parse_mpd(mpd_url)
        return info

    def _title(self, page_url: str) -> str:
        return title_from_page_url(page_url)

    def parse_mpd(self, mpd_url: str) -> List[Representation]:
        resp = self._get(mpd_url, headers=STREAM_HEADERS)
        root = ET.fromstring(resp.content)
        sig = self._signature_params(mpd_url)
        duration = _iso_to_seconds(root.get("mediaPresentationDuration", ""))

        reps: List[Representation] = []
        for adaptation in root.iter(f"{MPD_NS}AdaptationSet"):
            content_type = adaptation.get("contentType", "")
            is_video = content_type == "video"
            lang = ""
            for child in adaptation:
                if child.tag == f"{MPD_NS}Representation":
                    reps.append(
                        self._build_representation(child, adaptation, mpd_url, sig, duration, is_video, lang)
                    )

        if not reps:
            raise ResolveError("No playable representations found in the manifest.")
        return reps

    def _signature_params(self, mpd_url: str) -> Dict[str, str]:
        query = parse_qs(urlparse(mpd_url).query, keep_blank_values=True)
        return {k: v[0] for k, v in query.items() if k.lower() != "start"}

    def _build_representation(self, node, adaptation, mpd_url, sig, duration, is_video, lang) -> Representation:
        width = int(node.get("width") or adaptation.get("maxWidth") or 0)
        height = int(node.get("height") or adaptation.get("maxHeight") or 0)
        kid = ""
        for cp in adaptation.findall(f"{MPD_NS}ContentProtection"):
            if cp.get(f"{CENC_NS}default_KID"):
                kid = _norm_kid(cp.get(f"{CENC_NS}default_KID"))
                break
        rep = Representation(
            id=node.get("id", "0"),
            is_video=is_video,
            bandwidth=int(node.get("bandwidth") or 0),
            width=width,
            height=height,
            codecs=node.get("codecs", ""),
            mime_type=node.get("mimeType", ""),
            lang=lang,
            kid=kid,
        )

        template = node.find(f"{MPD_NS}SegmentTemplate")
        if template is None:
            template = adaptation.find(f"{MPD_NS}SegmentTemplate")
        if template is None:
            return rep

        timescale = int(template.get("timescale") or 1)
        start_number = int(template.get("startNumber") or 1)
        init_tmpl = template.get("initialization")
        media_tmpl = template.get("media")
        if not media_tmpl:
            return rep

        if init_tmpl:
            rep.init_url = append_signature(urljoin(mpd_url, init_tmpl), sig)

        numbers = self._segment_numbers(template, timescale, duration)
        rep.segment_urls = [
            append_signature(urljoin(mpd_url, media_tmpl.replace("$Number$", str(n))), sig)
            for n in numbers
        ]
        if not rep.segment_urls:
            rep.segment_urls = [append_signature(urljoin(mpd_url, media_tmpl), sig)]
        return rep

    def _segment_numbers(self, template, timescale: int, duration: float) -> List[int]:
        start_number = int(template.get("startNumber") or 1)
        timeline = template.find(f"{MPD_NS}SegmentTimeline")
        if timeline is not None:
            count = 0
            for seg in timeline.findall(f"{MPD_NS}S"):
                count += 1 + int(seg.get("r") or 0)
            return list(range(start_number, start_number + count))

        seg_duration = template.get("duration")
        if seg_duration and duration:
            count = int((duration * timescale) / int(seg_duration)) + 1
            return list(range(start_number, start_number + count))

        return []

    def list_streams(self, info: StreamInfo) -> None:
        print(f"Title: {info.title}")
        print(f"Manifest: {info.mpd_url}")
        print(f"Encryption: {'ClearKey' if info.keys else ('Widevine' if info.drm else 'None')}")
        print("Streams:")
        for rep in sorted(info.representations, key=lambda r: (not r.is_video, -r.height, -r.bandwidth)):
            kind = "video" if rep.is_video else "audio"
            detail = f"{rep.width}x{rep.height}" if rep.is_video else rep.mime_type
            print(f"  [{kind}] {rep.label:<8} {detail:<12} {rep.bandwidth/1000:8.0f} kbps  ({len(rep.segment_urls)} segs)")

    def _select_video(self, reps: List[Representation], quality: str) -> Representation:
        videos = [r for r in reps if r.is_video]
        if not videos:
            raise DownloadError("No video track found in manifest.")
        videos.sort(key=lambda r: r.height or r.bandwidth, reverse=True)
        if quality == "best":
            return videos[0]
        if quality.isdigit():
            target = int(quality)
            for rep in videos:
                if rep.height == target:
                    return rep
            lower = [r for r in videos if r.height and r.height <= target]
            if lower:
                return lower[0]
        return videos[0]

    def _select_audio(self, reps: List[Representation]) -> Optional[Representation]:
        audios = [r for r in reps if not r.is_video]
        if not audios:
            return None
        audios.sort(key=lambda r: r.bandwidth, reverse=True)
        return audios[0]

    def download(
        self,
        info: StreamInfo,
        output: Path,
        quality: str = "best",
        work_key: str = "",
        show_progress: bool = True,
        logger=print,
        progress_cb=None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        video = self._select_video(info.representations, quality)
        audio = self._select_audio(info.representations)

        if not info.keys and info.drm:
            raise DownloadError(f"Stream is protected by DRM ({info.drm.get('drmType', 'unknown')}); cannot decrypt.")

        key = self._key_for(info, video)
        workdir = TEMP_DIR / sanitize_filename(f"{work_key or info.title}_{quality}", "video")
        workdir.mkdir(parents=True, exist_ok=True)

        if show_progress:
            logger(f"Downloading '{info.title}' [{video.label}]")
        else:
            logger(f"  start  {info.title}")

        self._check_cancel(cancel_event)
        enc_video = self._download_representation(video, workdir, "video", show_progress, progress_cb, cancel_event)
        self._check_cancel(cancel_event)
        dec_video = self._decrypt(enc_video, workdir / "video_dec.mp4", key)

        dec_audio = None
        if audio:
            self._check_cancel(cancel_event)
            enc_audio = self._download_representation(audio, workdir, "audio", show_progress, progress_cb, cancel_event)
            self._check_cancel(cancel_event)
            dec_audio = self._decrypt(enc_audio, workdir / "audio_dec.mp4", key)

        self._check_cancel(cancel_event)
        output.parent.mkdir(parents=True, exist_ok=True)
        if dec_audio:
            self._merge(dec_video, dec_audio, output)
        else:
            shutil.move(str(dec_video), str(output))

        shutil.rmtree(workdir, ignore_errors=True)
        return output

    @staticmethod
    def _check_cancel(cancel_event: Optional[threading.Event]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Download cancelled.")

    def _key_for(self, info: StreamInfo, rep: Representation) -> Optional[str]:
        if not info.keys:
            return None
        if rep.kid and rep.kid in info.keys:
            return info.keys[rep.kid]
        if len(info.keys) == 1:
            return next(iter(info.keys.values()))
        raise DownloadError(
            "Multiple decryption keys present and none match KID "
            f"{rep.kid or 'unknown'}: {', '.join(info.keys)}"
        )

    def _download_representation(
        self,
        rep: Representation,
        workdir: Path,
        name: str,
        show_progress: bool = True,
        progress_cb=None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        seg_dir = workdir / name
        seg_dir.mkdir(parents=True, exist_ok=True)
        urls = ([rep.init_url] if rep.init_url else []) + rep.segment_urls
        total = len(urls)
        lock = threading.Lock()
        counter = {"done": 0, "bytes": 0}

        def fetch(index: int, url: str) -> Path:
            target = seg_dir / f"{index:05d}.m4s"
            if target.exists() and target.stat().st_size > 0:
                with lock:
                    counter["done"] += 1
                    counter["bytes"] += target.stat().st_size
                    if progress_cb:
                        progress_cb(name, counter["done"], total, counter["bytes"])
                return target
            self._fetch_segment(url, target, cancel_event)
            with lock:
                counter["done"] += 1
                counter["bytes"] += target.stat().st_size
                if progress_cb:
                    progress_cb(name, counter["done"], total, counter["bytes"])
                elif show_progress:
                    done, size = counter["done"], counter["bytes"]
                    print(f"\r  {name}: {done}/{total} segments ({human_bytes(size)})", end="", flush=True)
            return target

        cancelled = False
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(fetch, i, url): i for i, url in enumerate(urls)}
            for future in as_completed(futures):
                try:
                    future.result()
                except DownloadCancelled:
                    cancelled = True
                    if cancel_event is not None:
                        cancel_event.set()
        self._check_cancel(cancel_event if cancelled else None)
        if show_progress and not progress_cb:
            print()

        enc_path = workdir / f"{name}_enc.mp4"
        with open(enc_path, "wb") as out:
            for index in range(total):
                with open(seg_dir / f"{index:05d}.m4s", "rb") as part:
                    shutil.copyfileobj(part, out)
        return enc_path

    def _fetch_segment(self, url: str, target: Path, cancel_event: Optional[threading.Event] = None) -> None:
        last_error: Optional[Exception] = None
        session = self._get_session()
        for attempt in range(3):
            self._check_cancel(cancel_event)
            try:
                resp = session.get(url, headers=STREAM_HEADERS, timeout=60)
                resp.raise_for_status()
                tmp = target.with_suffix(".part")
                tmp.write_bytes(resp.content)
                tmp.replace(target)
                return
            except DownloadCancelled:
                raise
            except Exception as exc:
                last_error = exc
                time.sleep(1.0 + attempt)
        raise DownloadError(f"Failed to download segment: {url} ({last_error})")

    def _decrypt(self, enc_path: Path, out_path: Path, key: Optional[str]) -> Path:
        if not key:
            shutil.copyfile(str(enc_path), str(out_path))
            return out_path
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-decryption_key", key,
            "-i", str(enc_path),
            "-c", "copy",
            str(out_path),
        ]
        self._run(cmd, "Decrypting stream")
        return out_path

    def _merge(self, video: Path, audio: Path, output: Path) -> None:
        cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(video),
            "-i", str(audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c", "copy",
            "-movflags", "+faststart",
            str(output),
        ]
        self._run(cmd, "Merging video + audio")

    def _run(self, cmd: List[str], action: str) -> None:
        try:
            process = subprocess.run(
                cmd, capture_output=True, encoding="utf-8", errors="replace", **_run_flags()
            )
        except FileNotFoundError as exc:
            raise DownloadError(f"{action} failed: {tool_hint(cmd[0])}") from exc
        except OSError as exc:
            raise DownloadError(f"{action} failed: {exc}") from exc
        if process.returncode != 0:
            raise DownloadError(f"{action} failed: {process.stderr.strip()[-500:]}")
