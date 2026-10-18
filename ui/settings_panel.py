"""Settings tab: output folder, quality, cookies and download tuning."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config import DOWNLOAD_DIR

QUALITIES = ["best", "1080", "720", "480", "360", "240"]


class SettingsPanel(QWidget):
    """Live-applied settings. Emits `changed` on any edit."""

    changed = pyqtSignal()
    reload_cookies = pyqtSignal(str)
    clear_recents = pyqtSignal()
    clear_cache = pyqtSignal()

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._loading = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 10, 0, 0)
        outer.setSpacing(10)

        header = QLabel("Settings")
        header.setObjectName("titleLabel")
        outer.addWidget(header)

        hint = QLabel("Changes are saved and applied immediately.")
        hint.setObjectName("mutedLabel")
        outer.addWidget(hint)

        form = QFormLayout()
        form.setContentsMargins(0, 6, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.output_dir = QLineEdit(self._dir_value())
        form.addRow("Download folder", self._with_browse(self.output_dir, self._browse_dir))

        self.quality = QComboBox()
        self.quality.addItems(QUALITIES)
        self.quality.setCurrentText(str(settings.get("quality", "best")))
        form.addRow("Default quality", self.quality)

        self.cookies = QLineEdit(str(settings.get("cookies_file", "")))
        form.addRow("cookies.json", self._with_browse(self.cookies, self._browse_file))

        self.cookie_status = QLabel("")
        self.cookie_status.setObjectName("mutedLabel")
        reload_btn = QPushButton("Reload cookies")
        reload_btn.clicked.connect(lambda: self.reload_cookies.emit(self.cookies.text().strip()))
        cookie_row = QHBoxLayout()
        cookie_row.setContentsMargins(0, 0, 0, 0)
        cookie_row.addWidget(self.cookie_status, 1)
        cookie_row.addWidget(reload_btn)
        form.addRow("Cookies", self._wrap(cookie_row))

        self.container = QComboBox()
        self.container.addItems(["DASH", "HLS"])
        self.container.setCurrentText(str(settings.get("container", "DASH")))
        form.addRow("Container", self.container)

        self.parallel = QSpinBox()
        self.parallel.setRange(1, 16)
        self.parallel.setValue(int(settings.get("max_parallel", 3)))
        form.addRow("Videos in parallel", self.parallel)

        self.workers = QSpinBox()
        self.workers.setRange(1, 64)
        self.workers.setValue(int(settings.get("workers", 16)))
        form.addRow("Segment workers", self.workers)

        self.retries = QSpinBox()
        self.retries.setRange(1, 10)
        self.retries.setValue(int(settings.get("retries", 3)))
        form.addRow("Retries per video", self.retries)

        self.verify = QCheckBox("Verify duration with ffprobe after download")
        self.verify.setChecked(bool(settings.get("verify", True)))
        form.addRow("", self.verify)

        self.auto_retry = QCheckBox("Automatically retry errors")
        self.auto_retry.setChecked(bool(settings.get("auto_retry", True)))
        form.addRow("", self.auto_retry)

        self.use_cache = QCheckBox("Instant reload: reuse the last chapter list for a link")
        self.use_cache.setChecked(bool(settings.get("use_cache", True)))
        form.addRow("", self.use_cache)

        self.theme = QComboBox()
        self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(str(settings.get("theme", "dark")))
        form.addRow("Theme", self.theme)

        outer.addLayout(form)

        tools_row = QHBoxLayout()
        self.clear_links_btn = QPushButton("Clear saved links")
        self.clear_links_btn.clicked.connect(self.clear_recents.emit)
        self.clear_cache_btn = QPushButton("Clear cached chapter lists")
        self.clear_cache_btn.clicked.connect(self.clear_cache.emit)
        tools_row.addWidget(self.clear_links_btn)
        tools_row.addWidget(self.clear_cache_btn)
        tools_row.addStretch(1)
        outer.addLayout(tools_row)
        outer.addStretch(1)

        for combo in (self.quality, self.container, self.theme):
            combo.currentTextChanged.connect(self._emit)
        for spin in (self.parallel, self.workers, self.retries):
            spin.valueChanged.connect(self._emit)
        for check in (self.verify, self.auto_retry, self.use_cache):
            check.toggled.connect(self._emit)
        self.output_dir.editingFinished.connect(self._emit)
        self.cookies.editingFinished.connect(self._emit)
        self._loading = False

    # ------------------------------------------------------------------ helpers

    def _dir_value(self) -> str:
        return str(self.settings.get("default_save_dir") or DOWNLOAD_DIR)

    def _wrap(self, layout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _with_browse(self, line_edit: QLineEdit, handler) -> QWidget:
        button = QPushButton("Browse")
        button.clicked.connect(lambda: handler(line_edit))
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, 1)
        layout.addWidget(button)
        return self._wrap(layout)

    def _browse_dir(self, line_edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose folder", line_edit.text())
        if chosen:
            line_edit.setText(chosen)
            self._emit()

    def _browse_file(self, line_edit: QLineEdit) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose cookies.json", line_edit.text(), "JSON files (*.json);;All files (*)"
        )
        if chosen:
            line_edit.setText(chosen)
            self.reload_cookies.emit(chosen)

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()

    # ------------------------------------------------------------------- public

    def values(self) -> dict:
        folder = self.output_dir.text().strip()
        return {
            "default_save_dir": folder or str(DOWNLOAD_DIR),
            "quality": self.quality.currentText(),
            "cookies_file": self.cookies.text().strip(),
            "container": self.container.currentText(),
            "max_parallel": self.parallel.value(),
            "workers": self.workers.value(),
            "retries": self.retries.value(),
            "verify": self.verify.isChecked(),
            "auto_retry": self.auto_retry.isChecked(),
            "use_cache": self.use_cache.isChecked(),
            "theme": self.theme.currentText(),
        }

    def set_cookie_status(self, text: str, ok: bool) -> None:
        self.cookie_status.setText(text)
        self.cookie_status.setObjectName("badgeOk" if ok else "badgeWarn")
        style = self.cookie_status.style()
        style.unpolish(self.cookie_status)
        style.polish(self.cookie_status)

    def sync_cookies_path(self, path: str) -> None:
        if self.cookies.text().strip() != path:
            self._loading = True
            self.cookies.setText(path)
            self._loading = False

    def sync_theme(self, theme: str) -> None:
        if self.theme.currentText() != theme:
            self._loading = True
            self.theme.setCurrentText(theme)
            self._loading = False
