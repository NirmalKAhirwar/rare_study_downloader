"""Background URL/collection loading for the GUI."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from cookies_store import CookieStore
from downloader import title_from_page_url
from jobs import load_collection


@dataclass
class SingleVideo:
    url: str
    title: str


class LoadSignals(QObject):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)


class LoaderWorker(QThread):
    def __init__(
        self,
        url: str,
        store: CookieStore,
        output_dir,
        signals: LoadSignals,
        parent=None,
        use_cache: bool = True,
        force: bool = False,
    ):
        super().__init__(parent)
        self.url = url
        self.store = store
        self.output_dir = Path(output_dir)
        self.signals = signals
        self.use_cache = use_cache
        self.force = force

    def run(self) -> None:
        try:
            self.store.refresh()
            if not self.store.has_cookies:
                self.signals.failed.emit(self.store.last_error or "No cookies loaded.")
                return
            if urlparse(self.url).path.endswith("/khazana-chapters"):
                plan = load_collection(
                    self.url,
                    self.store.cookies,
                    self.output_dir,
                    progress=self.signals.progress.emit,
                    use_cache=self.use_cache,
                    cache_ttl=0 if self.force else None,
                )
                if not plan.chapters:
                    self.signals.failed.emit("No chapters found for this subject.")
                    return
                self.signals.loaded.emit(plan)
            else:
                self.signals.progress.emit("Reading page…")
                self.signals.loaded.emit(
                    SingleVideo(url=self.url, title=title_from_page_url(self.url))
                )
        except Exception as exc:  # noqa: BLE001 - report any load failure to the UI
            self.signals.failed.emit(str(exc))
