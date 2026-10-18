import os
import shutil
import sys
from pathlib import Path

BASE_URL = "https://rarestudy.testuk.org"
API_PROXY_BASE = "https://proxy.streamvideo.co.in/fetch/api.penpencil.co"

PROJECT_DIR = Path(__file__).resolve().parent
COOKIES_FILE = PROJECT_DIR / "cookies.json"
DOWNLOAD_DIR = PROJECT_DIR / "downloads"
TEMP_DIR = DOWNLOAD_DIR / ".tmp"
TOOLS_DIR = PROJECT_DIR / ".tools" / "ffmpeg"


def _platform_token() -> str:
    if sys.platform == "win32":
        return "Windows NT 10.0; Win64; x64"
    if sys.platform == "darwin":
        return "Macintosh; Intel Mac OS X 10_15_7"
    return "X11; Linux x86_64"


USER_AGENT = (
    f"Mozilla/5.0 ({_platform_token()}) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.3"
)

PAGE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

API_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE_URL,
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

STREAM_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}

MAX_RETRIES = 5
RETRY_BACKOFF = 0.5
DOWNLOAD_WORKERS = 16
VIDEO_DOWNLOAD_WORKERS = 3
VIDEO_DOWNLOAD_ATTEMPTS = 3

def _resolve_tool(name: str) -> str:
    """Locate ffmpeg/ffprobe: bundled download, env override, then PATH.

    `setup_env.py` can download the binaries into `.tools/ffmpeg/`; they are used
    automatically when present. Set FFMPEG / FFPROBE to force a specific binary.
    """

    override = os.environ.get(name.upper())
    if override:
        return override
    bundled = TOOLS_DIR / f"{name}.exe" if os.name == "nt" else TOOLS_DIR / name
    if bundled.exists():
        return str(bundled)
    return shutil.which(name) or name


FFMPEG = _resolve_tool("ffmpeg")
FFPROBE = _resolve_tool("ffprobe")


def tool_missing(tool: str) -> bool:
    separators = [sep for sep in (os.sep, os.altsep) if sep]
    if any(sep in tool for sep in separators):
        return not Path(tool).exists()
    return shutil.which(tool) is None
