"""Job model for the GUI queue.

A StudyTask is one mutable object shared between the worker thread (writes
fields) and the GUI thread (polls them every 500 ms). Only the worker thread
mutates the task; only the GUI thread touches Qt objects.
"""

import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from catalog import CourseRef, Lecture

KIND_LECTURE = "lecture"
KIND_SINGLE = "single"

STATUS_QUEUED = "Queued"
STATUS_RESOLVING = "Resolving"
STATUS_DOWNLOADING = "Downloading"
STATUS_VERIFYING = "Verifying"
STATUS_PAUSING = "Pausing..."
STATUS_PAUSED = "Paused"
STATUS_CANCELLING = "Cancelling..."
STATUS_CANCELLED = "Cancelled"
STATUS_ERROR = "Error"
STATUS_COOKIES_EXPIRED = "Cookies Expired"
STATUS_COMPLETED = "Completed"
STATUS_SKIPPED = "Skipped"

# Statuses that mean "the worker thread owns this task right now".
ACTIVE_STATUSES = {
    STATUS_RESOLVING,
    STATUS_DOWNLOADING,
    STATUS_VERIFYING,
}
# Statuses the manager is allowed to dispatch.
RUNNABLE_STATUSES = {STATUS_QUEUED}

FINISHED_STATUSES = {
    STATUS_COMPLETED,
    STATUS_SKIPPED,
    STATUS_ERROR,
    STATUS_COOKIES_EXPIRED,
    STATUS_CANCELLED,
}

_SPEED_WINDOW = 4.0


@dataclass
class TrackStats:
    """Progress of one track (video or audio) of a single video."""

    done: int = 0
    total: int = 0
    bytes: int = 0

    @property
    def percent(self) -> float:
        if not self.total:
            return 0.0
        return min(100.0, self.done * 100.0 / self.total)

    @property
    def started(self) -> bool:
        return self.total > 0

    def estimated_bytes(self) -> float:
        if self.done <= 0:
            return 0.0
        return self.bytes / self.done * self.total

    def reset(self) -> None:
        self.done = 0
        self.total = 0
        self.bytes = 0

    def snapshot(self) -> Dict:
        return {"done": self.done, "total": self.total, "bytes": self.bytes}

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "TrackStats":
        data = data or {}
        return cls(
            done=int(data.get("done") or 0),
            total=int(data.get("total") or 0),
            bytes=int(data.get("bytes") or 0),
        )



class StudyTask:
    def __init__(
        self,
        *,
        kind: str,
        name: str,
        output: Path,
        quality: str = "best",
        lecture: Optional[Lecture] = None,
        ref: Optional[CourseRef] = None,
        page_url: str = "",
        chapter: str = "",
        subject: str = "",
        base_dir: Optional[Path] = None,
        duration: str = "",
        expected: float = 0.0,
        uid: Optional[str] = None,
        status: str = STATUS_QUEUED,
        force: bool = False,
    ):
        self.kind = kind
        self.name = name
        self.output = Path(output)
        self.quality = quality
        self.lecture = lecture
        self.ref = ref
        self.page_url = page_url
        self.chapter = chapter
        self.subject = subject
        self.base_dir = Path(base_dir) if base_dir else None
        self.duration = duration
        self.expected = expected
        self.uid = uid or uuid.uuid4().hex
        self.status = status
        self.force = force

        self.phase = ""
        self.progress = 0.0
        self.segments_done = 0
        self.segments_total = 0
        self.downloaded_bytes = 0
        self.speed = 0.0
        self.video = TrackStats()
        self.audio = TrackStats()
        self.error_message = ""
        self.pause_flag = False
        self.cancel_flag = False
        self.cancel_event: Optional[threading.Event] = None
        self.started_at = 0.0
        self.finished_at = 0.0
        self.retry_count = 0
        self._samples: deque = deque(maxlen=32)
        self.tree_item = None

    @property
    def group_key(self) -> str:
        if self.subject and self.chapter:
            return f"{self.subject} / {self.chapter}"
        return self.subject or "Single videos"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATUSES

    def begin(self) -> None:
        """Prepare a task for a fresh dispatch (keeps cached segments on disk)."""

        self.cancel_event = threading.Event()
        self.pause_flag = False
        self.cancel_flag = False
        self.error_message = ""
        self.phase = ""
        self.progress = 0.0
        self.segments_done = 0
        self.segments_total = 0
        self.downloaded_bytes = 0
        self.speed = 0.0
        self.video.reset()
        self.audio.reset()
        self._samples.clear()
        self.started_at = time.time()
        self.finished_at = 0.0
        self.status = STATUS_QUEUED

    def update_progress(self, phase: str, done: int, total: int, bytes_done: int) -> None:
        now = time.time()
        self.phase = phase
        track = {"video": self.video, "audio": self.audio}.get(str(phase).lower())
        if track is not None:
            track.done = done
            track.total = total
            track.bytes = bytes_done
        self.segments_done = self.video.done + self.audio.done
        self.segments_total = self.video.total + self.audio.total
        self.downloaded_bytes = self.video.bytes + self.audio.bytes
        self.progress = self._overall_percent()
        self._samples.append((now, self.downloaded_bytes))
        while len(self._samples) > 2 and now - self._samples[0][0] > _SPEED_WINDOW:
            self._samples.popleft()
        if len(self._samples) >= 2:
            t0, b0 = self._samples[0]
            dt = now - t0
            if dt > 0.3:
                self.speed = max(0.0, (self.downloaded_bytes - b0) / dt)

    def _overall_percent(self) -> float:
        started = [t for t in (self.video, self.audio) if t.started]
        if not started:
            return 0.0
        return sum(t.percent for t in started) / len(started)

    @property
    def estimated_total_bytes(self) -> float:
        total = sum(t.estimated_bytes() for t in (self.video, self.audio))
        if total > 0:
            return total
        return float(self.downloaded_bytes)

    @property
    def remaining_bytes(self) -> float:
        return max(0.0, self.estimated_total_bytes - self.downloaded_bytes)

    def eta_seconds(self) -> Optional[float]:
        if self.speed <= 0 or self.segments_done <= 0:
            return None
        remaining = self.remaining_bytes
        if remaining <= 0:
            return 0.0
        return remaining / self.speed

    def snapshot(self) -> Dict:
        return {
            "uid": self.uid,
            "kind": self.kind,
            "name": self.name,
            "output": str(self.output),
            "quality": self.quality,
            "chapter": self.chapter,
            "subject": self.subject,
            "base_dir": str(self.base_dir) if self.base_dir else "",
            "duration": self.duration,
            "expected": self.expected,
            "page_url": self.page_url,
            "status": self.status,
            "force": self.force,
            "error_message": self.error_message,
            "video": self.video.snapshot(),
            "audio": self.audio.snapshot(),
            "lecture": asdict(self.lecture) if self.lecture else None,
            "ref": asdict(self.ref) if self.ref else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "StudyTask":
        lecture = Lecture(**data["lecture"]) if data.get("lecture") else None
        ref = CourseRef(**data["ref"]) if data.get("ref") else None
        status = data.get("status") or STATUS_QUEUED
        if status in ACTIVE_STATUSES or status in RUNNABLE_STATUSES or status == STATUS_PAUSING:
            status = STATUS_PAUSED
        task = cls(
            kind=data.get("kind") or KIND_LECTURE,
            name=data.get("name") or "video",
            output=Path(data.get("output") or ""),
            quality=data.get("quality") or "best",
            lecture=lecture,
            ref=ref,
            page_url=data.get("page_url") or "",
            chapter=data.get("chapter") or "",
            subject=data.get("subject") or "",
            base_dir=Path(data["base_dir"]) if data.get("base_dir") else None,
            duration=data.get("duration") or "",
            expected=float(data.get("expected") or 0.0),
            uid=data.get("uid"),
            status=status,
            force=bool(data.get("force")),
        )
        task.error_message = data.get("error_message") or ""
        task.video = TrackStats.from_dict(data.get("video"))
        task.audio = TrackStats.from_dict(data.get("audio"))
        task.downloaded_bytes = task.video.bytes + task.audio.bytes
        task.segments_done = task.video.done + task.audio.done
        task.segments_total = task.video.total + task.audio.total
        task.progress = task._overall_percent()
        return task

    @staticmethod
    def snapshots(tasks: List["StudyTask"]) -> List[Dict]:
        return [task.snapshot() for task in tasks]
