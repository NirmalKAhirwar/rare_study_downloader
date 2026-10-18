"""Welcome dialog. Settings live in the Settings tab (ui/settings_panel.py)."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)


class WelcomeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome")
        self.setMinimumWidth(460)
        self.dont_show = QCheckBox("Don't show this again")
        layout = QVBoxLayout(self)
        title = QLabel("rare-study-downloader")
        title.setObjectName("titleLabel")
        layout.addWidget(title)
        body = QLabel(
            "1. On the Browse & Select tab, paste a khazana-chapters URL (or pick a previous\n"
            "   link from the drop-down) and press Load.\n"
            "2. Tick the lectures you want and press Add selected to Queue.\n"
            "3. On the Downloads tab press Start. Pause keeps partial segments so it can resume.\n\n"
            "The Settings tab holds the download folder, quality, cookies and download tuning.\n"
            "cookies.json is re-read automatically whenever the file changes, so you can\n"
            "replace it while the app is running and just press Start again."
        )
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addWidget(self.dont_show)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
