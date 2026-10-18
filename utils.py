import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import COOKIES_FILE, MAX_RETRIES, RETRY_BACKOFF

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def load_cookies(path: Path = COOKIES_FILE) -> Dict[str, str]:
    """Load cookies from a browser-export JSON (list) or a plain name->value dict."""

    with open(path, "r", encoding="utf-8-sig") as f:
        raw = json.load(f)

    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}

    cookies: Dict[str, str] = {}
    for item in raw:
        if isinstance(item, dict) and "name" in item and "value" in item:
            cookies[item["name"]] = item["value"]

    return cookies


def create_session(cookies: Dict[str, str]) -> requests.Session:
    """Create a session with cookies, retries and default headers."""

    session = requests.Session()
    session.cookies.update(cookies)

    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def sanitize_filename(name: str, fallback: str = "video") -> str:
    """Turn an arbitrary string into a file name safe on macOS and Windows.

    Strips control characters and Windows-invalid characters, collapses
    whitespace, avoids trailing dots/spaces and reserved device names.
    """

    name = re.sub(r"[\x00-\x1f]", "", str(name or "")).strip()
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name)
    name = name[:150].rstrip(" .")
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = f"_{name}"
    return name or fallback


def duration_to_seconds(value) -> float:
    """Convert 'HH:MM:SS', 'MM:SS', or a plain number of seconds to float."""

    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            return 0.0
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return 0.0
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def human_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}PB"


def free_space_bytes(path: Path) -> Optional[int]:
    """Free bytes on the volume holding `path` (walks up to an existing folder)."""

    probe = Path(path)
    while True:
        try:
            return shutil.disk_usage(probe).free
        except OSError:
            if probe.parent == probe:
                return None
            probe = probe.parent


def tool_hint(tool: str = "ffmpeg") -> str:
    """Platform-specific install instructions for a missing external tool."""

    if sys.platform == "win32":
        return (
            f"{tool} was not found on PATH. Install it (e.g. 'winget install Gyan.FFmpeg') "
            "and reopen your terminal, or set the FFMPEG/FFPROBE environment variables."
        )
    return (
        f"{tool} was not found on PATH. Install it with 'brew install ffmpeg', "
        "or set the FFMPEG/FFPROBE environment variables."
    )
