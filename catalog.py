from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import API_PROXY_BASE, BASE_URL, MAX_RETRIES, RETRY_BACKOFF
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
    def __init__(self, cookies: Dict[str, str], referer: str = BASE_URL):
        self.session = requests.Session()
        self.session.cookies.update(cookies)
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.3"
                ),
                "Accept": "*/*",
                "Origin": BASE_URL,
                "Referer": referer.rstrip("/") + "/",
            }
        )
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _get_json(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{API_PROXY_BASE}/{path.lstrip('/')}"
        resp = self.session.get(url, params=params, timeout=60)
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
        base = (
            f"v2/programs/{ref.program_id}/subjects/{ref.subject_id}"
            f"/chapters/{ref.chapter_id}/topics/{chapter.id}"
        )
        sections = self._paginate(f"{base}/contents/sub-topic")
        lectures: List[Lecture] = []
        for section in sections:
            section_id = section.get("_id", "")
            section_name = section.get("name") or ""
            contents = self._paginate(f"{base}/sub-topic/{section_id}/contents")
            for item in contents:
                lecture = self._to_lecture(item, section_name)
                if lecture:
                    lectures.append(lecture)
        for idx, lecture in enumerate(lectures, 1):
            lecture.order = idx
        chapter.lectures = lectures
        return lectures

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
