"""Live-reloading cookie store.

cookies.json is refreshed externally (often daily), so the store watches the
file's (mtime_ns, size) signature and re-reads it whenever it changes - no
application restart required.
"""

import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from config import COOKIES_FILE
from utils import load_cookies


class CookieStore:
    def __init__(self, path: Path = COOKIES_FILE):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cookies: Dict[str, str] = {}
        self._signature: Optional[Tuple[int, int]] = None
        self._version = 0
        self.last_error: Optional[str] = None
        self.refresh(force=True)

    @property
    def cookies(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._cookies)

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def has_cookies(self) -> bool:
        with self._lock:
            return bool(self._cookies)

    @property
    def status(self) -> str:
        with self._lock:
            if self.last_error:
                return self.last_error
            if self._cookies:
                return f"Loaded {len(self._cookies)} cookies"
            return "No cookies loaded"

    def _signature_of(self) -> Optional[Tuple[int, int]]:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def refresh(self, force: bool = False) -> bool:
        """Re-read cookies.json when it changed. Returns True if cookies changed.

        Keeps the last good cookies when the file is missing or unreadable, so
        in-flight downloads are never disrupted by a bad refresh.
        """

        signature = self._signature_of()
        if signature is None:
            with self._lock:
                self.last_error = f"Cookies file not found: {self.path}"
            return False

        with self._lock:
            if not force and signature == self._signature:
                return False

        try:
            cookies = load_cookies(self.path)
        except Exception as exc:  # noqa: BLE001 - surface any read/parse error
            with self._lock:
                self.last_error = f"Failed to read cookies: {exc}"
            return False

        if not cookies:
            with self._lock:
                self.last_error = f"Cookies file has no cookies: {self.path}"
            return False

        with self._lock:
            changed = cookies != self._cookies or self._version == 0
            self._cookies = cookies
            self._signature = signature
            self.last_error = None
            if changed:
                self._version += 1
            return changed
