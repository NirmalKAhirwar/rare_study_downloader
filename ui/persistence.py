"""Plain-JSON persistence for GUI settings and download history.

Everything lives in the user's home directory as human-readable JSON so files
can be inspected/edited while the app is running (source-only app: no frozen
binaries, no embedded data).
"""

import json
import threading
from pathlib import Path
from typing import Dict, List

from config import (
    COOKIES_FILE,
    DOWNLOAD_DIR,
    DOWNLOAD_WORKERS,
    VIDEO_DOWNLOAD_ATTEMPTS,
    VIDEO_DOWNLOAD_WORKERS,
)

SETTINGS_PATH = Path.home() / ".rarestudy_settings.json"
HISTORY_PATH = Path.home() / ".rarestudy_history.json"

DEFAULT_SETTINGS: Dict = {
    "theme": "dark",
    "default_save_dir": str(DOWNLOAD_DIR),
    "cookies_file": str(COOKIES_FILE),
    "quality": "best",
    "max_parallel": VIDEO_DOWNLOAD_WORKERS,
    "workers": DOWNLOAD_WORKERS,
    "retries": VIDEO_DOWNLOAD_ATTEMPTS,
    "verify": True,
    "container": "DASH",
    "auto_retry": True,
    "use_cache": True,
    "force_redownload": False,
    "show_welcome": True,
    "recent_urls": [],
    "column_widths": [],
}

_lock = threading.Lock()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_settings() -> Dict:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                settings.update(stored)
        except (ValueError, OSError):
            pass
    return settings


def save_settings(settings: Dict) -> None:
    with _lock:
        try:
            _atomic_write(SETTINGS_PATH, json.dumps(settings, indent=2))
        except OSError:
            pass


def load_history() -> List[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        stored = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(stored, list):
        return []
    return [item for item in stored if isinstance(item, dict)]


def save_history(items: List[dict]) -> None:
    with _lock:
        try:
            _atomic_write(HISTORY_PATH, json.dumps(items, indent=2))
        except OSError:
            pass
