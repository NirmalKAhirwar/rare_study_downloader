"""Shared job layer used by both the CLI (main.py) and the GUI (gui.py).

A "job" is one lecture (or one standalone video): resolve the stream, download
segments, decrypt/mux, verify with ffprobe and record it in the state manifest.
Callers get status/progress callbacks and can pause or cancel at any time.
"""

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests

from catalog import Catalog, Chapter, CourseRef, Lecture
from cookies_store import CookieStore
from config import DOWNLOAD_WORKERS, VIDEO_DOWNLOAD_ATTEMPTS
from downloader import (
    DownloadCancelled,
    DownloadError,
    RareStudyDownloader,
    ResolveError,
    ensure_tools,
    probe_duration,
)
from state import StateStore
from utils import duration_to_seconds, sanitize_filename

MIN_VALID_SIZE = 10 * 1024
VERIFY_TOLERANCE_RATIO = 0.02
VERIFY_TOLERANCE_MIN = 20.0

CACHE_DIR = Path.home() / ".rarestudy_cache"
CACHE_TTL_SECONDS = 12 * 60 * 60
CATALOG_FETCH_WORKERS = 8

STATUS_DOWNLOADED = "downloaded"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

SESSION_EXPIRED_MARKER = "Session expired"

NON_RETRYABLE_MARKERS = (
    "Session expired",
    "not found on PATH",
    "protected by DRM",
    "No video track",
)

StatusCallback = Callable[[str], None]
LogCallback = Callable[[str], None]
ProgressCallback = Callable[[str, int, int, int], None]


def verify_output(output: Path, expected: float, enabled: bool) -> Tuple[bool, Optional[float]]:
    """Probe output with ffprobe and compare against expected seconds.

    Returns (ok, measured_seconds).
    """

    if not enabled:
        return True, None
    measured = probe_duration(output)
    if measured is None:
        return False, None
    if not expected:
        return True, measured
    tolerance = max(VERIFY_TOLERANCE_MIN, expected * VERIFY_TOLERANCE_RATIO)
    return abs(measured - expected) <= tolerance, measured


def unique_name(name: str, used: set) -> str:
    base = sanitize_filename(name, "video")
    candidate = base
    counter = 2
    while candidate.lower() in used:
        candidate = f"{base} ({counter})"
        counter += 1
    used.add(candidate.lower())
    return candidate


def session_expired(exc: object) -> bool:
    return SESSION_EXPIRED_MARKER.lower() in str(exc).lower()


@dataclass
class JobOptions:
    quality: str = "best"
    container: str = "DASH"
    force: bool = False
    verify: bool = True
    retries: int = VIDEO_DOWNLOAD_ATTEMPTS
    workers: int = DOWNLOAD_WORKERS


@dataclass
class JobResult:
    status: str
    error: str = ""
    measured: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_DOWNLOADED, STATUS_SKIPPED)

    @property
    def cancelled(self) -> bool:
        return self.status == STATUS_CANCELLED

    @property
    def cookies_expired(self) -> bool:
        return session_expired(self.error)


@dataclass
class CollectionPlan:
    ref: CourseRef
    subject_name: str
    subject_dir: Path
    chapters: List[Chapter] = field(default_factory=list)

    @property
    def total_lectures(self) -> int:
        return sum(len(c.lectures) for c in self.chapters)


def cache_file(url: str) -> Path:
    digest = hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{digest}.json"


def plan_to_dict(plan: CollectionPlan) -> dict:
    return {
        "ref": asdict(plan.ref),
        "subject_name": plan.subject_name,
        "chapters": [
            {
                "id": chapter.id,
                "name": chapter.name,
                "order": chapter.order,
                "lectures": [asdict(lecture) for lecture in chapter.lectures],
            }
            for chapter in plan.chapters
        ],
    }


def plan_from_dict(data: dict, output_dir) -> Optional[CollectionPlan]:
    """Rebuild a plan from cached JSON, re-deriving paths from output_dir."""

    try:
        ref = CourseRef(**data["ref"])
        chapters = [
            Chapter(
                id=entry["id"],
                name=entry["name"],
                order=int(entry.get("order", 999)),
                lectures=[Lecture(**lecture) for lecture in entry.get("lectures", [])],
            )
            for entry in data["chapters"]
        ]
    except (KeyError, TypeError, ValueError):
        return None
    subject_name = data.get("subject_name") or ref.subject_name or ref.subject_id
    ref.subject_name = subject_name
    subject_dir = Path(output_dir) / sanitize_filename(subject_name, ref.subject_id)
    return CollectionPlan(
        ref=ref, subject_name=subject_name, subject_dir=subject_dir, chapters=chapters
    )


def read_cached_plan(url: str, output_dir, ttl: Optional[float] = CACHE_TTL_SECONDS):
    path = cache_file(url)
    try:
        if not path.exists():
            return None
        if ttl is not None and time.time() - path.stat().st_mtime > ttl:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    plan = plan_from_dict(data, output_dir)
    return plan if plan is not None and plan.chapters else None


def write_cached_plan(url: str, plan: CollectionPlan) -> None:
    path = cache_file(url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan_to_dict(plan)), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def load_collection(
    url: str,
    cookies: Dict[str, str],
    output_dir,
    *,
    progress: Optional[Callable[[str], None]] = None,
    workers: int = CATALOG_FETCH_WORKERS,
    use_cache: bool = False,
    cache_ttl: Optional[float] = CACHE_TTL_SECONDS,
) -> CollectionPlan:
    """Resolve a khazana-chapters URL into subject + chapters + lectures.

    Lectures are fetched in parallel. With use_cache a recent crawl is reused so
    re-loading a link is instant; callers can pass cache_ttl=None to force a crawl.
    """

    say = progress or (lambda _message: None)
    if use_cache:
        cached = read_cached_plan(
            url, output_dir, CACHE_TTL_SECONDS if cache_ttl is None else cache_ttl
        )
        if cached is not None:
            say(f"Loaded {len(cached.chapters)} chapter(s) from cache")
            return cached

    ref = CourseRef.from_url(url)
    catalog = Catalog(cookies)
    say("Fetching subject…")
    subject_name = (
        catalog.subject_name(ref.program_id, ref.subject_id) or ref.batch_name or ref.subject_id
    )
    ref.subject_name = subject_name
    say("Fetching chapters…")
    chapters = catalog.chapters(ref)
    catalog.load_lectures(ref, chapters, workers=workers, on_progress=say)
    subject_dir = Path(output_dir) / sanitize_filename(subject_name, ref.subject_id)
    plan = CollectionPlan(
        ref=ref, subject_name=subject_name, subject_dir=subject_dir, chapters=chapters
    )
    if use_cache:
        write_cached_plan(url, plan)
    return plan


def plan_chapter_outputs(
    plan: CollectionPlan, chapter: Chapter, lectures: List[Lecture]
) -> List[Tuple[Lecture, Path]]:
    """Compute output paths for the selected lectures of one chapter."""

    chapter_dir = plan.subject_dir / chapter.folder
    used: set = set()
    width = max(2, len(str(len(chapter.lectures) or 1)))
    planned: List[Tuple[Lecture, Path]] = []
    for lecture in lectures:
        base = unique_name(lecture.name, used)
        prefix = f"{lecture.order:0{width}d} - " if lecture.order else ""
        planned.append((lecture, chapter_dir / f"{prefix}{base}.mp4"))
    return planned


def plan_outputs(plan: CollectionPlan) -> Dict[Tuple[int, int], Path]:
    """Output path for every lecture, keyed by (chapter index, lecture index).

    Computed over each full chapter so a path never changes with the selection.
    """

    outputs: Dict[Tuple[int, int], Path] = {}
    for ci, chapter in enumerate(plan.chapters):
        for li, (_lecture, path) in enumerate(plan_chapter_outputs(plan, chapter, chapter.lectures)):
            outputs[(ci, li)] = path
    return outputs


def existing_output_sizes(
    outputs: Dict[Tuple[int, int], Path], min_size: int = MIN_VALID_SIZE
) -> Dict[Tuple[int, int], int]:
    """Subset of `outputs` already on disk that look like a complete file."""

    found: Dict[Tuple[int, int], int] = {}
    for key, path in outputs.items():
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size >= min_size:
            found[key] = size
    return found


def make_state(plan: CollectionPlan) -> StateStore:
    return StateStore(plan.subject_dir / ".state.json")


def _sleep_cancellable(seconds: float, cancel_event: Optional[object]) -> None:
    remaining = float(seconds)
    while remaining > 0:
        if cancel_event is not None and cancel_event.is_set():
            return
        step = min(0.2, remaining)
        time.sleep(step)
        remaining -= step


def _sync_cookies(store: Optional[CookieStore], downloader: RareStudyDownloader) -> None:
    if store is not None and store.refresh():
        downloader.set_cookies(store.cookies)


def _attempt_loop(
    options: JobOptions,
    downloader: RareStudyDownloader,
    page_url: str,
    work_key: str,
    output: Path,
    expected: float,
    *,
    label: str,
    store: Optional[CookieStore],
    cancel_event,
    on_status: StatusCallback,
    on_progress: Optional[ProgressCallback],
    on_log: LogCallback,
    show_progress: bool,
    on_success: Optional[Callable[[Optional[float]], None]] = None,
) -> JobResult:
    attempts = max(1, options.retries)
    last_error = ""
    for attempt in range(1, attempts + 1):
        if cancel_event is not None and cancel_event.is_set():
            on_status("Cancelled")
            return JobResult(STATUS_CANCELLED)
        try:
            _sync_cookies(store, downloader)
            on_status("Resolving")
            info = downloader.resolve(page_url, container=options.container)
            if cancel_event is not None and cancel_event.is_set():
                on_status("Cancelled")
                return JobResult(STATUS_CANCELLED)
            on_status("Downloading")
            downloader.download(
                info,
                output,
                quality=options.quality,
                work_key=work_key,
                show_progress=show_progress,
                logger=on_log,
                progress_cb=on_progress,
                cancel_event=cancel_event,
            )
            on_status("Verifying")
            ok, measured = verify_output(output, expected, options.verify)
            if not ok:
                raise DownloadError(
                    f"verification failed: got {measured}s, expected "
                    f"{expected or 'unknown'}"
                )
            if on_success is not None:
                on_success(measured)
            return JobResult(STATUS_DOWNLOADED, measured=measured)
        except DownloadCancelled:
            on_status("Cancelled")
            return JobResult(STATUS_CANCELLED)
        except (DownloadError, ResolveError, requests.RequestException, OSError) as exc:
            last_error = str(exc)
            retryable = not any(marker.lower() in last_error.lower() for marker in NON_RETRYABLE_MARKERS)
            if attempt < attempts and retryable:
                on_log(f"  retry  {label} (attempt {attempt}/{attempts}): {exc}")
                _sleep_cancellable(min(30, 2 * attempt), cancel_event)
                if cancel_event is not None and cancel_event.is_set():
                    on_status("Cancelled")
                    return JobResult(STATUS_CANCELLED)
            else:
                on_log(f"  FAILED {label}: {exc}")
                on_status("Cookies Expired" if session_expired(exc) else "Error")
                return JobResult(STATUS_FAILED, last_error)
    on_status("Error")
    return JobResult(STATUS_FAILED, last_error)


def run_lecture_job(
    options: JobOptions,
    downloader: RareStudyDownloader,
    ref: CourseRef,
    lecture: Lecture,
    output: Path,
    state: StateStore,
    base_dir: Path,
    *,
    on_status: Optional[StatusCallback] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_log: Optional[LogCallback] = None,
    cancel_event=None,
    store: Optional[CookieStore] = None,
    show_progress: bool = False,
) -> JobResult:
    emit_status: StatusCallback = on_status or (lambda _s: None)
    emit_log: LogCallback = on_log or (lambda _m: None)
    expected = duration_to_seconds(lecture.duration)

    try:
        ensure_tools()
    except DownloadError as exc:
        emit_log(f"  FAILED {lecture.name}: {exc}")
        emit_status("Error")
        return JobResult(STATUS_FAILED, str(exc))

    if not options.force:
        if state.is_complete(lecture.video_id, output, MIN_VALID_SIZE):
            emit_log(f"  skip   {lecture.name} (verified)")
            emit_status("Skipped")
            return JobResult(STATUS_SKIPPED)
        if output.exists() and output.stat().st_size >= MIN_VALID_SIZE:
            ok, measured = verify_output(output, expected, options.verify)
            if ok:
                state.record(lecture.video_id, output, base_dir, measured, verified=options.verify)
                emit_log(f"  skip   {lecture.name} (verified)")
                emit_status("Skipped")
                return JobResult(STATUS_SKIPPED, measured=measured)
            emit_log(f"  redo   {lecture.name} (existing file failed verification)")

    page_url = lecture.page_url(ref.program_id, ref.subject_id)

    def record(measured: Optional[float]) -> None:
        state.record(lecture.video_id, output, base_dir, measured, verified=options.verify)
        emit_log(f"  done   {lecture.name}")

    return _attempt_loop(
        options,
        downloader,
        page_url,
        lecture.video_id,
        output,
        expected,
        label=lecture.name,
        store=store,
        cancel_event=cancel_event,
        on_status=emit_status,
        on_progress=on_progress,
        on_log=emit_log,
        show_progress=show_progress,
        on_success=record,
    )


def run_single_job(
    options: JobOptions,
    downloader: RareStudyDownloader,
    page_url: str,
    output: Path,
    *,
    title: str = "",
    expected: float = 0.0,
    on_status: Optional[StatusCallback] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_log: Optional[LogCallback] = None,
    cancel_event=None,
    store: Optional[CookieStore] = None,
    show_progress: bool = False,
) -> JobResult:
    """Download one standalone khazana-video URL (no state manifest)."""

    emit_status: StatusCallback = on_status or (lambda _s: None)
    emit_log: LogCallback = on_log or (lambda _m: None)
    name = sanitize_filename(title or "", "video")

    try:
        ensure_tools()
    except DownloadError as exc:
        emit_log(f"  FAILED: {exc}")
        emit_status("Error")
        return JobResult(STATUS_FAILED, str(exc))

    if not options.force and output.exists() and output.stat().st_size >= MIN_VALID_SIZE:
        emit_log(f"  skip   {output.name} (already exists)")
        emit_status("Skipped")
        return JobResult(STATUS_SKIPPED)

    return _attempt_loop(
        options,
        downloader,
        page_url,
        name,
        output,
        expected,
        label=output.name,
        store=store,
        cancel_event=cancel_event,
        on_status=emit_status,
        on_progress=on_progress,
        on_log=emit_log,
        show_progress=show_progress,
        on_success=None,
    )
