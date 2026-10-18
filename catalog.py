import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import API_PROXY_BASE, BASE_URL, MAX_RETRIES, RETRY_BACKOFF, USER_AGENT
from utils import sanitize_filename


class CatalogError(RuntimeError):
    pass


@dataclass
class Lecture:
    child_id: str
    video_id: str
    name: str
    duration: str = ""
    thumbnail: str = ""
    video_url: str = ""
    section: str = ""
    completed: bool = False
    order: int = 0

    def page_url(self, program_id: str, subject_id: str) -> str:
        query = urlencode(
            {
                "parentId": program_id,
                "childId": self.child_id,
                "videoId": self.video_id,
                "videoUrl": self.video_url or "",
                "topicName": self.name,
                "thumbnail": self.thumbnail or "",
            }
        )
        return f"{BASE_URL}/khazana-video?{query}"


@dataclass
class Chapter:
    id: str
    name: str
    order: int = 999
    lectures: List[Lecture] = field(default_factory=list)

    @property
    def folder(self) -> str:
        return sanitize_filename(self.name, self.id)


@dataclass
class CourseRef:
    program_id: str
    subject_id: str
    chapter_id: str
    subject_name: str = "Subject"
    batch_name: str = ""

    @classmethod
    def from_url(cls, url: str) -> "CourseRef":
        query = parse_qs(urlparse(url).query)
        program_id = (query.get("programId") or [""])[0]
        subject_id = (query.get("subjectId") or [""])[0]
        chapter_id = (query.get("chapterId") or [""])[0]
        batch_name = (query.get("batchName") or [""])[0]
        if not program_id or not subject_id:
            raise CatalogError("URL must contain programId and subjectId.")
        return cls(
            program_id=program_id,
            subject_id=subject_id,
            chapter_id=chapter_id,
            batch_name=batch_name,
        )


class Catalog:
    """API client. One HTTP session per thread so pages can be fetched in parallel."""

    def __init__(self, cookies: Dict[str, str], referer: str = BASE_URL):
        self.referer = referer
        self.cookies = dict(cookies)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sessions: List[requests.Session] = []
        self.session = self._new_session()

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.cookies.update(self.cookies)
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Origin": BASE_URL,
                "Referer": self.referer.rstrip("/") + "/",
            }
        )
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        with self._lock:
            self._sessions.append(session)
        return session

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._new_session()
            self._local.session = session
        return session

    def set_cookies(self, cookies: Dict[str, str]) -> None:
        """Replace the cookies on every session (used when cookies.json changes)."""

        self.cookies = dict(cookies)
        with self._lock:
            for session in self._sessions:
                session.cookies.clear()
                session.cookies.update(self.cookies)

    def _get_json(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{API_PROXY_BASE}/{path.lstrip('/')}"
        resp = self._session().get(url, params=params, timeout=60)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as exc:
            raise CatalogError(f"Invalid JSON from {url}") from exc

    def _paginate(self, path: str, limit: int = 20) -> List[dict]:
        items: List[dict] = []
        page = 1
        while True:
            payload = self._get_json(path, {"page": page, "limit": limit})
            if not payload.get("success"):
                break
            batch = payload.get("data") or []
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < limit:
                break
            page += 1
        return items

    def subject_name(self, program_id: str, subject_id: str) -> str:
        payload = self._get_json(f"v2/programs/{program_id}/subjects")
        for subject in payload.get("data") or []:
            if subject.get("_id") == subject_id:
                return subject.get("name") or ""
        return ""

    def chapters(self, ref: CourseRef) -> List[Chapter]:
        path = (
            f"v2/programs/{ref.program_id}/subjects/{ref.subject_id}"
            f"/chapters/{ref.chapter_id}/topics/list"
        )
        topics = self._paginate(path)
        chapters = [
            Chapter(
                id=topic.get("_id", ""),
                name=topic.get("name") or f"Chapter {idx + 1}",
                order=int(topic.get("displayOrder") or 999),
            )
            for idx, topic in enumerate(topics)
        ]
        chapters.sort(key=lambda c: c.order)
        return chapters

    def lectures(self, ref: CourseRef, chapter: Chapter) -> List[Lecture]:
        if chapter.lectures:
            return chapter.lectures
        base = self._chapter_base(ref, chapter)
        sections = self._paginate(f"{base}/contents/sub-topic")
        entries: List[Tuple[str, List[dict]]] = []
        for section in sections:
            section_id = section.get("_id", "")
            section_name = section.get("name") or ""
            entries.append((section_name, self._paginate(f"{base}/sub-topic/{section_id}/contents")))
        self._fill(chapter, entries)
        return chapter.lectures

    def load_lectures(
        self,
        ref: CourseRef,
        chapters: List[Chapter],
        workers: int = 8,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> List[Chapter]:
        """Fill every chapter's lectures, fetching sections in parallel (much faster)."""

        pending = [chapter for chapter in chapters if not chapter.lectures]
        if not pending:
            return chapters
        workers = max(1, min(int(workers), 16))
        done = 0
        total = len(pending)
        if on_progress:
            on_progress(f"Fetching lectures for {total} chapter(s)…")

        def fetch_sections(chapter: Chapter) -> Tuple[Chapter, List[dict]]:
            base = self._chapter_base(ref, chapter)
            return chapter, self._paginate(f"{base}/contents/sub-topic")

        def fetch_contents(job: Tuple[Chapter, int, dict]) -> Tuple[Chapter, int, str, List[dict]]:
            chapter, index, section = job
            base = self._chapter_base(ref, chapter)
            section_id = section.get("_id", "")
            contents = self._paginate(f"{base}/sub-topic/{section_id}/contents")
            return chapter, index, section.get("name") or "", contents

        with ThreadPoolExecutor(max_workers=workers) as pool:
            section_lists = list(pool.map(fetch_sections, pending))
            jobs = [
                (chapter, index, section)
                for chapter, sections in section_lists
                for index, section in enumerate(sections)
            ]
            fetched = list(pool.map(fetch_contents, jobs)) if jobs else []

        grouped: Dict[int, List[Tuple[int, str, List[dict]]]] = {}
        for chapter, index, name, contents in fetched:
            grouped.setdefault(id(chapter), []).append((index, name, contents))

        for chapter, _sections in section_lists:
            entries = sorted(grouped.get(id(chapter), []))
            self._fill(chapter, [(name, contents) for _index, name, contents in entries])
            done += 1
            if on_progress:
                on_progress(f"Loaded {done}/{total} chapter(s)…")
        return chapters

    def _chapter_base(self, ref: CourseRef, chapter: Chapter) -> str:
        return (
            f"v2/programs/{ref.program_id}/subjects/{ref.subject_id}"
            f"/chapters/{ref.chapter_id}/topics/{chapter.id}"
        )

    def _fill(self, chapter: Chapter, entries: List[Tuple[str, List[dict]]]) -> None:
        lectures: List[Lecture] = []
        for section_name, contents in entries:
            for item in contents:
                lecture = self._to_lecture(item, section_name)
                if lecture:
                    lectures.append(lecture)
        for idx, lecture in enumerate(lectures, 1):
            lecture.order = idx
        chapter.lectures = lectures

    def _to_lecture(self, item: dict, section_name: str) -> Optional[Lecture]:
        if str(item.get("type", "")).upper() != "LECTURE":
            return None
        data = item.get("data") or {}
        details = data.get("videoDetails") or {}
        child_id = data.get("_id") or ""
        video_id = details.get("_id") or ""
        if not child_id or not video_id:
            return None
        return Lecture(
            child_id=child_id,
            video_id=video_id,
            name=data.get("title") or data.get("name") or "Lecture",
            duration=details.get("duration") or "",
            thumbnail=details.get("image") or "",
            video_url=data.get("videoUrl") or details.get("videoUrl") or details.get("embedCode") or "",
            section=section_name,
        )
