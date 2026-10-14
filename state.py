import json
import threading
import time
from pathlib import Path
from typing import Dict, Optional

STATE_VERSION = 1


class StateStore:
    """Persistent record of completed downloads for a subject.

    Keyed by video id so that renamed/moved files are detected and
    re-verified instead of silently skipped.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("videos"), dict):
                    return data
            except (ValueError, OSError):
                pass
        return {"version": STATE_VERSION, "videos": {}}

    def is_complete(self, key: str, output: Path, min_size: int = 0) -> bool:
        with self._lock:
            entry = self._data["videos"].get(key)
        if not entry or not output.exists():
            return False
        size = output.stat().st_size
        if size < max(min_size, 1):
            return False
        return size == int(entry.get("size") or -1)

    def record(
        self,
        key: str,
        output: Path,
        base_dir: Path,
        duration: Optional[float] = None,
        verified: bool = False,
    ) -> None:
        try:
            rel = str(output.relative_to(base_dir))
        except ValueError:
            rel = output.name
        entry = {
            "file": rel,
            "size": output.stat().st_size,
            "duration": round(duration, 3) if duration is not None else None,
            "verified": bool(verified),
            "updated": int(time.time()),
        }
        with self._lock:
            self._data["videos"][key] = entry
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
