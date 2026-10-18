"""Main window: URL loader, chapter/lecture picker, download queue, settings."""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QAction, QBrush, QColor, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import DOWNLOAD_DIR
from cookies_store import CookieStore
from jobs import (
    CACHE_DIR,
    MIN_VALID_SIZE,
    CollectionPlan,
    existing_output_sizes,
    plan_outputs,
)
from ui.dialogs import WelcomeDialog
from ui.loader import LoadSignals, LoaderWorker, SingleVideo
from ui.manager import DownloadManager
from ui.persistence import load_history, load_settings, save_history, save_settings
from ui.settings_panel import SettingsPanel
from ui.task import (
    KIND_LECTURE,
    KIND_SINGLE,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_COOKIES_EXPIRED,
    STATUS_ERROR,
    STATUS_PAUSED,
    STATUS_QUEUED,
    STATUS_SKIPPED,
    StudyTask,
)
from ui.theme import apply_theme
from utils import duration_to_seconds, free_space_bytes, human_bytes, sanitize_filename

COL_NAME, COL_STATUS, COL_VIDEO, COL_AUDIO, COL_SPEED, COL_ETA, COL_SIZE = range(7)
PLAN_ROLE = int(Qt.ItemDataRole.UserRole) + 1

BAR_VIDEO_COLOR = "#4f8cff"
BAR_AUDIO_COLOR = "#b07cff"
DISK_WARN_RATIO = 1.0

PRESENT_COLOR_DARK = "#5fd39a"
PRESENT_COLOR_LIGHT = "#1f7a4d"
PLAN_PRESENT_SUFFIX = "   [already downloaded]"
PLAN_ROW_TOOLTIP = "Already in your download folder - skipped unless Force re-download is on."

STATUS_COLORS = {
    "Queued": "#8b93a7",
    "Resolving": "#4f8cff",
    "Downloading": "#4f8cff",
    "Verifying": "#2fb8a6",
    "Pausing...": "#f5b755",
    "Paused": "#f5b755",
    "Cancelling...": "#c1683c",
    "Cancelled": "#8b93a7",
    "Cookies Expired": "#e08a3c",
    "Error": "#e05d5d",
    "Completed": "#4ec98a",
    "Skipped": "#67a5c9",
}


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def open_in_file_manager(path: Path) -> None:
    """Reveal a path using the platform's file manager (macOS/Windows)."""

    target = str(path)
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


class TaskDelegate(QStyledItemDelegate):
    """Paints the status pill and the per-track (video/audio) progress bars."""

    def paint(self, painter, option, index):  # noqa: N802 (Qt API)
        column = index.column()
        if column == COL_STATUS:
            status = str(index.data(Qt.ItemDataRole.UserRole) or "")
            if status:
                self._paint_pill(painter, option, status)
                return
        elif column in (COL_VIDEO, COL_AUDIO):
            value = index.data(Qt.ItemDataRole.UserRole)
            color = BAR_VIDEO_COLOR if column == COL_VIDEO else BAR_AUDIO_COLOR
            if value is not None:
                self._paint_bar(painter, option, float(value), color)
                return
        super().paint(painter, option, index)

    @staticmethod
    def _is_dark(option) -> bool:
        return option.palette.window().color().lightness() < 128

    def _paint_pill(self, painter: QPainter, option, status: str) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
        color = QColor(STATUS_COLORS.get(status, "#8b93a7"))
        metrics = option.fontMetrics
        width = min(max(10, rect.width() - 8), metrics.horizontalAdvance(status) + 18)
        pill = QRectF(rect.x() + 4, rect.y() + (rect.height() - 18) / 2, width, 18)
        background = QColor(color)
        background.setAlpha(50 if self._is_dark(option) else 35)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(pill, 9, 9)
        painter.setPen(color)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, status)
        painter.restore()

    def _paint_bar(self, painter: QPainter, option, value: float, color: str) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
        dark = self._is_dark(option)
        track = QColor("#232833" if dark else "#e6eaf2")
        bar = QRectF(rect.x() + 4, rect.y() + (rect.height() - 8) / 2, max(0, rect.width() - 52), 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(bar, 4, 4)
        if value > 0:
            fill = QRectF(bar.x(), bar.y(), bar.width() * min(value, 100.0) / 100.0, bar.height())
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(fill, 4, 4)
        painter.setPen(option.palette.windowText().color())
        text_rect = QRectF(bar.right() + 6, rect.y(), 46, rect.height())
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), f"{value:.0f}%")
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.store = CookieStore(Path(self.settings.get("cookies_file")))
        self.tasks: List[StudyTask] = []
        self.plan: Optional[CollectionPlan] = None
        self.single: Optional[SingleVideo] = None
        self.loader: Optional[LoaderWorker] = None
        self._plan_updating = False
        self._chapter_items: Dict[int, QTreeWidgetItem] = {}
        self._lecture_items: Dict[Tuple[int, int], QTreeWidgetItem] = {}
        self._group_items: Dict[str, QTreeWidgetItem] = {}
        self._plan_outputs: Dict[Tuple[int, int], Path] = {}
        self._plan_present: Dict[Tuple[int, int], int] = {}
        self._current_quality = self.settings.get("quality", "best")
        self._disk_free: Optional[int] = None
        self._disk_need = 0.0
        self._disk_checked = 0.0

        self.signals = LoadSignals()
        self.signals.loaded.connect(self._on_loaded)
        self.signals.failed.connect(self._on_load_failed)
        self.signals.progress.connect(self._on_load_progress)
        self.manager = DownloadManager(self.tasks, self._options, self.store)

        self.setWindowTitle("rare-study-downloader")
        self.setMinimumSize(900, 640)
        self.resize(1080, 760)
        self._build_ui()
        self._build_menu()
        self.tabs.setCurrentIndex(int(self.settings.get("last_tab", 0)))

        self.load_history()

        self.manager.start()
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_ui)
        self.ui_timer.start(500)

        self.cookie_timer = QTimer(self)
        self.cookie_timer.timeout.connect(self._refresh_cookie_label)
        self.cookie_timer.start(5000)
        self._refresh_cookie_label()

        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.timeout.connect(self._save_history_now)

        if self.settings.get("show_welcome", True):
            QTimer.singleShot(200, self._show_welcome)

    # ---------------------------------------------------------------- UI setup

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.browse_tab = self._build_browse_page()
        self.downloads_tab = self._build_downloads_page()
        self.settings_tab = self._build_settings_page()
        self.tabs.addTab(self.browse_tab, "Browse & Select")
        self.tabs.addTab(self.downloads_tab, "Downloads")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

        self.status = self.statusBar()
        self.disk_badge = QLabel("")
        self.disk_badge.setObjectName("mutedLabel")
        self.status.addPermanentWidget(self.disk_badge)
        self.cookie_badge = QLabel("")
        self.cookie_badge.setObjectName("mutedLabel")
        self.status.addPermanentWidget(self.cookie_badge)

    def _build_browse_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_input_card())
        layout.addWidget(self._build_plan_card(), 1)
        return page

    def _build_downloads_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_queue_card(), 1)
        layout.addLayout(self._build_action_bar())
        return page

    def _build_settings_page(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 14)
        self.settings_panel = SettingsPanel(self.settings)
        self.settings_panel.changed.connect(self._on_settings_changed)
        self.settings_panel.reload_cookies.connect(self.on_reload_cookies)
        self.settings_panel.clear_recents.connect(self._clear_recent_urls)
        self.settings_panel.clear_cache.connect(self._clear_link_cache)
        layout.addWidget(self.settings_panel)
        return card

    def _on_tab_changed(self, index: int) -> None:
        if index == self.tabs.indexOf(self.downloads_tab):
            self.update_ui()
        elif index == self.tabs.indexOf(self.settings_tab):
            self._refresh_cookie_label()

    def _build_input_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        title = QLabel("rare-study-downloader")
        title.setObjectName("titleLabel")
        grid.addWidget(title, 0, 0, 1, 3)

        self.url_input = QComboBox()
        self.url_input.setEditable(True)
        self.url_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.url_input.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.url_input.lineEdit().setPlaceholderText(
            "Paste a khazana-chapters or khazana-video URL (recent links are kept here)"
        )
        self.url_input.lineEdit().returnPressed.connect(self.on_load)
        self.url_input.activated.connect(lambda _i: self.on_load())
        self._reload_recent_items()

        self.load_btn = QPushButton("Load")
        self.load_btn.setObjectName("primaryBtn")
        self.load_btn.clicked.connect(self.on_load)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Re-crawl the site and update the saved chapter list")
        self.refresh_btn.clicked.connect(lambda: self.on_load(force=True))
        grid.addWidget(self.url_input, 1, 0)
        grid.addWidget(self.load_btn, 1, 1)
        grid.addWidget(self.refresh_btn, 1, 2)

        grid.setColumnStretch(0, 1)
        return card

    def _build_plan_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)

        header = QHBoxLayout()
        self.plan_title = QLabel("Chapters and lectures")
        self.plan_title.setObjectName("titleLabel")
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_all_plan_checks(True))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_all_plan_checks(False))
        self.force_check = QCheckBox("Force re-download")
        self.force_check.setToolTip(
            "Add and re-download lectures even when the file is already in your download folder."
        )
        self.force_check.setChecked(bool(self.settings.get("force_redownload", False)))
        self.force_check.toggled.connect(self._on_force_toggled)
        self.add_btn = QPushButton("Add selected to Queue")
        self.add_btn.setObjectName("primaryBtn")
        self.add_btn.clicked.connect(self.on_add_to_queue)
        header.addWidget(self.plan_title)
        header.addStretch(1)
        header.addWidget(select_all)
        header.addWidget(select_none)
        header.addWidget(self.force_check)
        header.addWidget(self.add_btn)
        layout.addLayout(header)

        self.plan_hint = QLabel("")
        self.plan_hint.setObjectName("mutedLabel")
        layout.addWidget(self.plan_hint)

        self.plan_tree = QTreeWidget()
        self.plan_tree.setHeaderLabels(["Chapter / Lecture", "Duration"])
        self.plan_tree.setUniformRowHeights(True)
        self.plan_tree.itemChanged.connect(self._on_plan_item_changed)
        self.plan_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.plan_tree)
        return card

    def _on_force_toggled(self, checked: bool) -> None:
        self.settings["force_redownload"] = bool(checked)
        save_settings(self.settings)
        self._refresh_plan_hint()

    def _build_queue_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)

        header = QHBoxLayout()
        title = QLabel("Queue")
        title.setObjectName("titleLabel")
        self.stats_label = QLabel("")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.stats_label)
        layout.addLayout(header)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(
            ["Name", "Status", "Video", "Audio", "Speed", "ETA", "Size"]
        )
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setItemDelegate(TaskDelegate(self.tree))
        widths = self.settings.get("column_widths") or []
        defaults = [360, 130, 130, 130, 90, 70, 100]
        for index, default in enumerate(defaults):
            self.tree.setColumnWidth(index, int(widths[index]) if index < len(widths) else default)
        self.tree.header().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree)
        return card

    def _build_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        specs = [
            ("Select all", None, self.on_select_all),
            ("Start / Resume", "successBtn", self.on_start),
            ("Pause", "warningBtn", self.on_pause),
            ("Cancel", "dangerBtn", self.on_cancel),
            ("Retry", None, self.on_retry),
            ("Remove", None, self.on_remove),
            ("Clear finished", None, self.on_clear_finished),
            ("Open folder", None, self.on_open_folder),
        ]
        for text, name, handler in specs:
            button = QPushButton(text)
            if name:
                button.setObjectName(name)
            button.clicked.connect(handler)
            bar.addWidget(button)
        bar.addStretch(1)
        return bar

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.on_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        view_menu = self.menuBar().addMenu("View")
        self.dark_action = QAction("Dark theme", self, checkable=True)
        self.dark_action.setChecked(self.settings.get("theme", "dark") == "dark")
        self.dark_action.triggered.connect(lambda: self._set_theme("dark"))
        self.light_action = QAction("Light theme", self, checkable=True)
        self.light_action.setChecked(self.settings.get("theme") == "light")
        self.light_action.triggered.connect(lambda: self._set_theme("light"))
        view_menu.addAction(self.dark_action)
        view_menu.addAction(self.light_action)

        help_menu = self.menuBar().addMenu("Help")
        welcome_action = QAction("Welcome", self)
        welcome_action.triggered.connect(self._show_welcome)
        help_menu.addAction(welcome_action)

    # ------------------------------------------------------------ plan loading

    def _save_dir(self) -> Path:
        raw = str(self.settings.get("default_save_dir") or "").strip()
        return Path(raw) if raw else DOWNLOAD_DIR

    def _reload_recent_items(self) -> None:
        current = self.url_input.currentText()
        self.url_input.blockSignals(True)
        self.url_input.clear()
        self.url_input.addItems(list(self.settings.get("recent_urls", [])))
        self.url_input.setCurrentText(current)
        self.url_input.blockSignals(False)

    def _remember_url(self, url: str) -> None:
        url = url.strip()
        if not url:
            return
        recents = [item for item in self.settings.get("recent_urls", []) if item != url]
        recents.insert(0, url)
        self.settings["recent_urls"] = recents[:20]
        save_settings(self.settings)
        self._reload_recent_items()

    def _clear_recent_urls(self) -> None:
        self.settings["recent_urls"] = []
        save_settings(self.settings)
        self._reload_recent_items()
        self.status.showMessage("Saved links cleared", 4000)

    def _clear_link_cache(self) -> None:
        removed = 0
        try:
            for path in CACHE_DIR.glob("*.json"):
                path.unlink()
                removed += 1
        except OSError as exc:
            QMessageBox.warning(self, "Could not clear cache", str(exc))
            return
        self.status.showMessage(f"Removed {removed} cached chapter list(s)", 4000)

    def on_load(self, force: bool = False) -> None:
        url = self.url_input.currentText().strip()
        if not url:
            return
        if self.loader is not None and self.loader.isRunning():
            return
        self.load_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.status.showMessage("Loading…")
        use_cache = bool(self.settings.get("use_cache", True))
        self.loader = LoaderWorker(
            url,
            self.store,
            self._save_dir(),
            self.signals,
            self,
            use_cache=use_cache,
            force=force,
        )
        self.loader.finished.connect(self._on_loader_finished)
        self.loader.start()

    def _on_loader_finished(self) -> None:
        self.load_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)

    def _on_load_progress(self, message: str) -> None:
        self.status.showMessage(message)

    def _on_loaded(self, result: object) -> None:
        self.status.showMessage("Loaded", 4000)
        self.plan_tree.clear()
        self._chapter_items.clear()
        self._lecture_items.clear()
        self._plan_outputs = {}
        self._plan_present = {}
        self.plan = None
        self.single = None
        self._remember_url(self.url_input.currentText().strip())

        if isinstance(result, CollectionPlan):
            self.plan = result
            self._plan_outputs = plan_outputs(result)
            self._plan_present = existing_output_sizes(self._plan_outputs)
            self.plan_title.setText(
                f"{result.subject_name} - {len(result.chapters)} chapters"
            )
            for ci, chapter in enumerate(result.chapters):
                present = sum(
                    1 for li in range(len(chapter.lectures)) if (ci, li) in self._plan_present
                )
                summary = f"{len(chapter.lectures)} videos"
                if present:
                    summary += f" · {present} on disk"
                item = QTreeWidgetItem([chapter.name, summary])
                item.setData(0, PLAN_ROLE, ("chapter", ci))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked)
                if present == len(chapter.lectures) and present:
                    item.setForeground(0, self._present_brush())
                    item.setForeground(1, self._present_brush())
                self.plan_tree.addTopLevelItem(item)
                self._chapter_items[ci] = item
                for li, lecture in enumerate(chapter.lectures):
                    label = f"{lecture.order:02d} - {lecture.name}" if lecture.order else lecture.name
                    child = QTreeWidgetItem([label, lecture.duration or ""])
                    child.setData(0, PLAN_ROLE, ("lecture", ci, li))
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                    if (ci, li) in self._plan_present:
                        path = self._plan_outputs.get((ci, li))
                        child.setText(0, label + PLAN_PRESENT_SUFFIX)
                        child.setForeground(0, self._present_brush())
                        child.setForeground(1, self._present_brush())
                        size = self._plan_present[(ci, li)]
                        child.setToolTip(0, f"{PLAN_ROW_TOOLTIP}\n{path}\n{human_bytes(size)}")
                        child.setToolTip(1, str(path) if path else "")
                    item.addChild(child)
                    self._lecture_items[(ci, li)] = child
            self.plan_tree.collapseAll()
        elif isinstance(result, SingleVideo):
            self.single = result
            self.plan_title.setText("Single video")
            output = self._save_dir() / f"{sanitize_filename(result.title, 'video')}.mp4"
            exists = output.exists() and output.stat().st_size >= MIN_VALID_SIZE
            child = QTreeWidgetItem([result.title, ""])
            child.setData(0, PLAN_ROLE, ("single",))
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Checked)
            if exists:
                child.setText(0, result.title + PLAN_PRESENT_SUFFIX)
                child.setForeground(0, self._present_brush())
                child.setForeground(1, self._present_brush())
                child.setToolTip(
                    0,
                    f"{PLAN_ROW_TOOLTIP}\n{output}\n{human_bytes(output.stat().st_size)}",
                )
            self.plan_tree.addTopLevelItem(child)
        self._refresh_plan_hint()

    def _present_brush(self):
        theme = str(self.settings.get("theme", "dark"))
        return QColor(PRESENT_COLOR_DARK if theme == "dark" else PRESENT_COLOR_LIGHT)

    def _refresh_plan_hint(self) -> None:
        if self.plan is None:
            self.plan_hint.setText("")
            return
        total = len(self._plan_outputs)
        present = len(self._plan_present)
        if not total:
            self.plan_hint.setText("")
            return
        if present:
            skipping = (
                "force is on, so they will be re-downloaded"
                if self.force_check.isChecked()
                else "they will be skipped when added"
            )
            self.plan_hint.setText(
                f"{present} of {total} lecture(s) already in your download folder - {skipping}."
            )
        else:
            self.plan_hint.setText("")

    def _on_load_failed(self, message: str) -> None:
        self.status.showMessage("Load failed", 4000)
        QMessageBox.warning(self, "Could not load URL", message)

    def _set_all_plan_checks(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._plan_updating = True
        for item in self._lecture_items.values():
            item.setCheckState(0, state)
        for item in self._chapter_items.values():
            item.setCheckState(0, state)
        self._plan_updating = False

    def _on_plan_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._plan_updating:
            return
        self._plan_updating = True
        state = item.checkState(0)
        if item.childCount():
            for index in range(item.childCount()):
                item.child(index).setCheckState(0, state)
        else:
            parent = item.parent()
            if parent is not None and parent.childCount():
                states = [parent.child(i).checkState(0) for i in range(parent.childCount())]
                if all(s == Qt.CheckState.Checked for s in states):
                    parent.setCheckState(0, Qt.CheckState.Checked)
                elif all(s == Qt.CheckState.Unchecked for s in states):
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self._plan_updating = False

    # -------------------------------------------------------------- queue edits

    def on_add_to_queue(self) -> None:
        added: List[StudyTask] = []
        skipped = 0
        force = self.force_check.isChecked()
        if self.plan is not None:
            for ci, chapter in enumerate(self.plan.chapters):
                picks = [
                    (li, lecture)
                    for li, lecture in enumerate(chapter.lectures)
                    if self._lecture_items.get((ci, li)) is not None
                    and self._lecture_items[(ci, li)].checkState(0) == Qt.CheckState.Checked
                ]
                if not picks:
                    continue
                for li, lecture in picks:
                    output = self._plan_outputs.get((ci, li))
                    if output is None:
                        continue
                    if not force and (ci, li) in self._plan_present:
                        skipped += 1
                        continue
                    added.append(
                        StudyTask(
                            kind=KIND_LECTURE,
                            name=lecture.name,
                            output=output,
                            quality=self._current_quality,
                            lecture=lecture,
                            ref=self.plan.ref,
                            chapter=chapter.name,
                            subject=self.plan.subject_name,
                            base_dir=self.plan.subject_dir,
                            duration=lecture.duration,
                            expected=duration_to_seconds(lecture.duration),
                            page_url=lecture.page_url(self.plan.ref.program_id, self.plan.ref.subject_id),
                            force=force,
                        )
                    )
        elif self.single is not None:
            name = sanitize_filename(self.single.title, "video")
            output = self._save_dir() / f"{name}.mp4"
            already = output.exists() and output.stat().st_size >= MIN_VALID_SIZE
            if already and not force:
                skipped += 1
            else:
                added.append(
                    StudyTask(
                        kind=KIND_SINGLE,
                        name=self.single.title,
                        output=output,
                        quality=self._current_quality,
                        page_url=self.single.url,
                        force=force,
                    )
                )

        existing = {str(task.output) for task in self.tasks}
        queued = 0
        for task in added:
            if str(task.output) in existing:
                continue
            self.tasks.append(task)
            self._add_task_row(task)
            existing.add(str(task.output))
            queued += 1
        parts = []
        if queued:
            parts.append(f"Added {queued} video(s) to the queue")
        if skipped:
            parts.append(f"{skipped} already downloaded, skipped")
        if parts:
            self.status.showMessage(" · ".join(parts), 4000)
        else:
            self.status.showMessage("Nothing new to add", 4000)
        if queued:
            self.tabs.setCurrentIndex(self.tabs.indexOf(self.downloads_tab))
            self._schedule_history_save()

    def _add_task_row(self, task: StudyTask) -> None:
        group = self._group_items.get(task.group_key)
        if group is None:
            group = QTreeWidgetItem(self.tree, [task.group_key, "", "", "", "", "", ""])
            group.setExpanded(True)
            group.setFirstColumnSpanned(False)
            self._group_items[task.group_key] = group
        item = QTreeWidgetItem(group, [task.name, task.status, "", "", "", "", ""])
        item.setToolTip(COL_NAME, str(task.output))
        task.tree_item = item

    def _selected_tasks(self) -> List[StudyTask]:
        selected = [task for task in self.tasks if task.tree_item and task.tree_item.isSelected()]
        return selected

    def _targets(self) -> List[StudyTask]:
        selected = self._selected_tasks()
        return selected or list(self.tasks)

    def on_select_all(self) -> None:
        for task in self.tasks:
            if task.tree_item:
                task.tree_item.setSelected(True)

    def on_start(self) -> None:
        resume_states = {
            STATUS_PAUSED,
            STATUS_ERROR,
            STATUS_CANCELLED,
            STATUS_COOKIES_EXPIRED,
            "Pausing...",
            "Cancelling...",
        }
        count = 0
        for task in self._targets():
            if task.status in resume_states or task.status == STATUS_QUEUED:
                task.pause_flag = False
                task.cancel_flag = False
                task.status = STATUS_QUEUED
                count += 1
        if count:
            self.status.showMessage(f"Queued {count} video(s)", 4000)
        self._schedule_history_save()

    def on_pause(self) -> None:
        for task in self._targets():
            if task.is_active:
                task.pause_flag = True
                task.status = "Pausing..."
                if task.cancel_event:
                    task.cancel_event.set()
            elif task.status == STATUS_QUEUED:
                task.pause_flag = True
                if task.cancel_event:
                    task.cancel_event.set()
                task.status = STATUS_PAUSED

    def on_cancel(self) -> None:
        for task in self._targets():
            if task.is_active:
                task.cancel_flag = True
                task.status = "Cancelling..."
                if task.cancel_event:
                    task.cancel_event.set()
            elif task.status == STATUS_QUEUED:
                task.cancel_flag = True
                if task.cancel_event:
                    task.cancel_event.set()
                task.status = STATUS_CANCELLED

    def on_retry(self) -> None:
        retry_states = {
            STATUS_ERROR,
            STATUS_CANCELLED,
            STATUS_COOKIES_EXPIRED,
            STATUS_PAUSED,
            STATUS_QUEUED,
            "Pausing...",
            "Cancelling...",
        }
        for task in self._targets():
            if task.status in retry_states:
                task.retry_count += 1
                task.pause_flag = False
                task.cancel_flag = False
                task.status = STATUS_QUEUED
        self._schedule_history_save()

    def on_remove(self) -> None:
        targets = self._selected_tasks()
        if not targets:
            targets = [t for t in self.tasks if t.is_finished]
        if not targets:
            return
        for task in targets:
            if task.tree_item is not None:
                parent = task.tree_item.parent()
                if parent is not None:
                    parent.removeChild(task.tree_item)
                    if parent.childCount() == 0:
                        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(parent))
                        self._group_items.pop(task.group_key, None)
            if task in self.tasks:
                self.tasks.remove(task)
        self._schedule_history_save()

    def on_clear_finished(self) -> None:
        for task in list(self.tasks):
            if task.status in (STATUS_COMPLETED, STATUS_SKIPPED):
                if task.tree_item is not None:
                    parent = task.tree_item.parent()
                    if parent is not None:
                        parent.removeChild(task.tree_item)
                        if parent.childCount() == 0:
                            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(parent))
                            self._group_items.pop(task.group_key, None)
                self.tasks.remove(task)
        self._schedule_history_save()

    def on_open_folder(self) -> None:
        selected = self._selected_tasks()
        if selected:
            target = selected[0].output.parent
        else:
            target = self._save_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
            open_in_file_manager(target)
        except OSError as exc:
            QMessageBox.warning(self, "Could not open folder", str(exc))

    # ----------------------------------------------------------------- settings

    def on_settings(self) -> None:
        self.tabs.setCurrentWidget(self.settings_tab)

    def _on_settings_changed(self) -> None:
        new = self.settings_panel.values()
        self.settings.update(new)
        save_settings(self.settings)
        self._current_quality = self.settings.get("quality", "best")

        path = str(new.get("cookies_file") or "")
        if path and str(self.store.path) != path:
            self.store = CookieStore(Path(path))
            self.manager.store = self.store
            self.store.refresh(force=True)

        if new.get("theme"):
            self._set_theme(str(new["theme"]))
        self._refresh_cookie_label()

    def _set_theme(self, theme: str) -> None:
        self.settings["theme"] = theme
        self.dark_action.setChecked(theme == "dark")
        self.light_action.setChecked(theme == "light")
        self.settings_panel.sync_theme(theme)
        apply_theme(QApplication.instance(), theme)
        self._recolor_plan()
        save_settings(self.settings)

    def _recolor_plan(self) -> None:
        brush = self._present_brush()
        clear = QBrush()
        for (ci, li), item in self._lecture_items.items():
            present = (ci, li) in self._plan_present
            item.setForeground(0, brush if present else clear)
            item.setForeground(1, brush if present else clear)
        if self.plan is None:
            return
        for ci, item in self._chapter_items.items():
            if ci >= len(self.plan.chapters):
                continue
            count = len(self.plan.chapters[ci].lectures)
            present = sum(1 for li in range(count) if (ci, li) in self._plan_present)
            full = bool(count) and present == count
            item.setForeground(0, brush if full else clear)
            item.setForeground(1, brush if full else clear)

    def on_reload_cookies(self, path: str = "") -> None:
        target = Path((path or self.settings.get("cookies_file") or "").strip())
        if str(target) != str(self.store.path):
            self.store = CookieStore(target)
            self.manager.store = self.store
        else:
            self.store.refresh(force=True)
        self.settings["cookies_file"] = str(target)
        save_settings(self.settings)
        self.settings_panel.sync_cookies_path(str(target))
        self._refresh_cookie_label()
        self.status.showMessage(self.store.status, 5000)

    def _refresh_cookie_label(self) -> None:
        self.store.refresh()
        text = self.store.status
        ok = self.store.last_error is None
        self.cookie_badge.setText(f"Cookies: {text}")
        self._restyle_badge(self.cookie_badge, "badgeWarn" if not ok else "badgeOk")
        self.settings_panel.set_cookie_status(text, ok)

    def _options(self) -> Dict:
        return {
            "quality": self._current_quality,
            "container": self.settings.get("container", "DASH"),
            "force": False,
            "verify": bool(self.settings.get("verify", True)),
            "retries": int(self.settings.get("retries", 3)),
            "workers": int(self.settings.get("workers", 16)),
            "max_parallel": int(self.settings.get("max_parallel", 3)),
        }

    # ------------------------------------------------------------------ update

    def update_ui(self) -> None:
        active = queued = done = 0
        total_speed = 0.0
        total_need = 0.0
        for task in self.tasks:
            item = task.tree_item
            if item is None:
                continue
            item.setText(COL_NAME, task.name)
            item.setText(COL_STATUS, task.status)
            item.setData(COL_STATUS, Qt.ItemDataRole.UserRole, task.status)
            self._update_track_cell(item, COL_VIDEO, task.video)
            self._update_track_cell(item, COL_AUDIO, task.audio)
            item.setText(COL_SPEED, f"{human_bytes(task.speed)}/s" if task.is_active and task.speed else "")
            item.setText(COL_ETA, format_eta(task.eta_seconds()) if task.is_active else "")
            size = task.estimated_total_bytes or task.downloaded_bytes
            item.setText(COL_SIZE, human_bytes(size) if size else "")
            item.setToolTip(COL_NAME, self._task_tooltip(task))

            if not task.is_finished:
                total_need += task.remaining_bytes
            if task.is_active:
                active += 1
                total_speed += task.speed
            elif task.status == STATUS_QUEUED:
                queued += 1
            if task.status in (STATUS_COMPLETED, STATUS_SKIPPED):
                done += 1

        self._update_groups()
        self._update_disk_badge(total_need)
        self.stats_label.setText(
            f"Active {active}   Queued {queued}   Done {done}   ·   {human_bytes(total_speed)}/s"
        )
        label = f"Downloads ({len(self.tasks)})" if self.tasks else "Downloads"
        if self.tabs.tabText(self.tabs.indexOf(self.downloads_tab)) != label:
            self.tabs.setTabText(self.tabs.indexOf(self.downloads_tab), label)

    @staticmethod
    def _update_track_cell(item: QTreeWidgetItem, column: int, track) -> None:
        if track.started:
            item.setText(column, f"{track.done}/{track.total}")
            item.setData(column, Qt.ItemDataRole.UserRole, float(track.percent))
            item.setToolTip(column, f"{track.done}/{track.total} segments · {human_bytes(track.bytes)}")
        else:
            item.setText(column, "")
            item.setData(column, Qt.ItemDataRole.UserRole, None)
            item.setToolTip(column, "")

    @staticmethod
    def _task_tooltip(task: StudyTask) -> str:
        lines = [str(task.output)]
        if task.force:
            lines.append("force re-download: on")
        if task.video.started:
            lines.append(
                f"video  {task.video.done}/{task.video.total} segments · {human_bytes(task.video.bytes)}"
            )
        if task.audio.started:
            lines.append(
                f"audio  {task.audio.done}/{task.audio.total} segments · {human_bytes(task.audio.bytes)}"
            )
        if task.error_message:
            lines.append("")
            lines.append(task.error_message)
        return "\n".join(lines)

    def _update_disk_badge(self, need: float) -> None:
        now = time.time()
        if now - self._disk_checked > 2.0 or self._disk_free is None:
            self._disk_checked = now
            self._disk_free = free_space_bytes(self._save_dir())
        free = self._disk_free
        if free is None:
            self.disk_badge.setText("Disk: n/a")
            self._restyle_badge(self.disk_badge, "mutedLabel")
            return
        short = need > 0 and free < need * DISK_WARN_RATIO
        text = f"Disk free {human_bytes(free)}"
        if need > 0:
            text += f" · need ~{human_bytes(need)}"
        self.disk_badge.setText(text)
        self.disk_badge.setToolTip(
            "Not enough free space for the remaining downloads."
            if short
            else f"Free space on the download volume ({self._save_dir()})"
        )
        self._restyle_badge(self.disk_badge, "badgeWarn" if short else "mutedLabel")

    @staticmethod
    def _restyle_badge(label: QLabel, name: str) -> None:
        label.setObjectName(name)
        style = label.style()
        style.unpolish(label)
        style.polish(label)

    def _update_groups(self) -> None:
        summary: Dict[str, List[StudyTask]] = {}
        for task in self.tasks:
            summary.setdefault(task.group_key, []).append(task)
        for key, group_item in self._group_items.items():
            tasks = summary.get(key, [])
            if not tasks:
                continue
            finished = sum(1 for t in tasks if t.status in (STATUS_COMPLETED, STATUS_SKIPPED))
            active = sum(1 for t in tasks if t.is_active)
            errors = sum(1 for t in tasks if t.status in (STATUS_ERROR, STATUS_COOKIES_EXPIRED))
            progress = sum(t.progress for t in tasks) / len(tasks)
            if active:
                label = f"Active  {finished}/{len(tasks)}"
            elif errors:
                label = f"{errors} error(s)  {finished}/{len(tasks)}"
            else:
                label = f"{finished}/{len(tasks)} done"
            group_item.setText(COL_STATUS, label)
            group_item.setData(COL_STATUS, Qt.ItemDataRole.UserRole, "")
            group_item.setText(COL_VIDEO, f"{progress:.0f}% overall")
            group_item.setData(COL_VIDEO, Qt.ItemDataRole.UserRole, None)
            group_item.setText(COL_AUDIO, "")
            group_item.setData(COL_AUDIO, Qt.ItemDataRole.UserRole, None)
            group_item.setText(COL_SPEED, "")
            group_item.setText(COL_ETA, "")
            group_item.setText(COL_SIZE, f"{len(tasks)} videos")

    # ----------------------------------------------------------------- history

    def load_history(self) -> None:
        for data in load_history():
            try:
                task = StudyTask.from_dict(data)
            except Exception:  # noqa: BLE001 - skip unreadable entries
                continue
            self.tasks.append(task)
            self._add_task_row(task)
        if self.tasks:
            self.status.showMessage(f"Restored {len(self.tasks)} queued video(s) as paused", 6000)

    def _schedule_history_save(self) -> None:
        self.history_timer.start(800)

    def _save_history_now(self) -> None:
        save_history(StudyTask.snapshots(self.tasks))

    def _show_welcome(self) -> None:
        dialog = WelcomeDialog(self)
        dialog.exec()
        self.settings["show_welcome"] = not dialog.dont_show.isChecked()
        save_settings(self.settings)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        widths = [self.tree.columnWidth(i) for i in range(self.tree.columnCount())]
        self.settings["column_widths"] = widths
        self.settings.update(self.settings_panel.values())
        self.settings["last_tab"] = self.tabs.currentIndex()
        save_settings(self.settings)
        self._save_history_now()
        self.manager.stop()
        super().closeEvent(event)
