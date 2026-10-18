"""Queue dispatcher: one manager thread hands runnable tasks to worker threads.

Workers mutate StudyTask fields directly and the GUI polls them, so no progress
signals cross threads.
"""

import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from cookies_store import CookieStore
from downloader import RareStudyDownloader
from jobs import (
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    JobOptions,
    run_lecture_job,
    run_single_job,
)
from state import StateStore

from ui.task import (
    KIND_LECTURE,
    STATUS_CANCELLED as UI_CANCELLED,
    STATUS_COMPLETED,
    STATUS_COOKIES_EXPIRED,
    STATUS_ERROR,
    STATUS_PAUSED,
    STATUS_QUEUED,
    STATUS_SKIPPED as UI_SKIPPED,
    StudyTask,
)


class DownloadManager:
    def __init__(
        self,
        tasks: List[StudyTask],
        get_options: Callable[[], Dict],
        store: CookieStore,
    ):
        self.tasks = tasks
        self.get_options = get_options
        self.store = store
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running: set = set()
        self._downloaders: Dict[int, RareStudyDownloader] = {}
        self._states: Dict[str, StateStore] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="DownloadManager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                options = self.get_options()
                max_parallel = max(1, int(options.get("max_parallel") or 1))
                with self._lock:
                    active = len(self._running)
                for task in list(self.tasks):
                    if active >= max_parallel:
                        break
                    if task.uid in self._running:
                        continue
                    if task.status != STATUS_QUEUED:
                        continue
                    task.begin()
                    with self._lock:
                        self._running.add(task.uid)
                    active += 1
                    threading.Thread(
                        target=self._worker, args=(task,), daemon=True
                    ).start()
            except Exception:  # never let the manager thread die
                pass
            time.sleep(1)

    def _downloader_for(self, workers: int) -> RareStudyDownloader:
        with self._lock:
            downloader = self._downloaders.get(workers)
            if downloader is None:
                downloader = RareStudyDownloader(cookies=self.store.cookies, workers=workers)
                self._downloaders[workers] = downloader
            return downloader

    def _state_for(self, base_dir: Path) -> StateStore:
        key = str(base_dir)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                state = StateStore(base_dir / ".state.json")
                self._states[key] = state
            return state

    def _set_status(self, task: StudyTask, status: str) -> None:
        if status == "Cancelled":
            status = STATUS_PAUSED if task.pause_flag else UI_CANCELLED
        elif status == "Skipped":
            status = UI_SKIPPED
        elif status == "Completed":
            status = STATUS_COMPLETED
        task.status = status

    def _finish(self, task: StudyTask, result) -> None:
        task.finished_at = time.time()
        if result.ok:
            task.progress = 100.0
            task.speed = 0.0
            task.status = STATUS_COMPLETED if result.status == STATUS_DOWNLOADED else UI_SKIPPED
        elif result.cancelled:
            task.status = STATUS_PAUSED if task.pause_flag else UI_CANCELLED
        elif result.status == STATUS_FAILED:
            task.error_message = result.error
            task.status = STATUS_COOKIES_EXPIRED if result.cookies_expired else STATUS_ERROR
        with self._lock:
            self._running.discard(task.uid)

    def _worker(self, task: StudyTask) -> None:
        try:
            options_dict = self.get_options()
            options = JobOptions(
                quality=task.quality or options_dict.get("quality") or "best",
                container=options_dict.get("container") or "DASH",
                force=bool(task.force or options_dict.get("force")),
                verify=bool(options_dict.get("verify", True)),
                retries=int(options_dict.get("retries") or 1),
                workers=int(options_dict.get("workers") or 1),
            )
            downloader = self._downloader_for(options.workers)
            on_status = lambda status: self._set_status(task, status)
            on_progress = task.update_progress

            if task.kind == KIND_LECTURE:
                if task.lecture is None or task.ref is None or task.base_dir is None:
                    raise RuntimeError("Task is missing lecture metadata; remove and re-add it.")
                result = run_lecture_job(
                    options,
                    downloader,
                    task.ref,
                    task.lecture,
                    task.output,
                    self._state_for(task.base_dir),
                    task.base_dir,
                    on_status=on_status,
                    on_progress=on_progress,
                    cancel_event=task.cancel_event,
                    store=self.store,
                )
            else:
                result = run_single_job(
                    options,
                    downloader,
                    task.page_url,
                    task.output,
                    title=task.name,
                    expected=task.expected,
                    on_status=on_status,
                    on_progress=on_progress,
                    cancel_event=task.cancel_event,
                    store=self.store,
                )
            self._finish(task, result)
        except Exception as exc:  # noqa: BLE001 - surface unexpected worker errors
            task.error_message = str(exc)
            task.status = STATUS_ERROR
            with self._lock:
                self._running.discard(task.uid)
