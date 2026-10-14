from pathlib import Path

BASE_URL = "https://rarestudy.testuk.org"
API_PROXY_BASE = "https://proxy.streamvideo.co.in/fetch/api.penpencil.co"

PROJECT_DIR = Path(__file__).resolve().parent
COOKIES_FILE = PROJECT_DIR / "cookies.json"
DOWNLOAD_DIR = PROJECT_DIR / "downloads"
TEMP_DIR = DOWNLOAD_DIR / ".tmp"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
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

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
