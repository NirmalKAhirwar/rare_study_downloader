"""GUI entry point: python gui.py

Runs from source on macOS and Windows. No frozen binaries, no embedded data -
cookies.json, settings and history stay as plain files you can edit any time.
"""

import sys

from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.persistence import load_settings
from ui.theme import apply_theme


def main() -> int:
    settings = load_settings()
    app = QApplication(sys.argv)
    app.setApplicationName("rare-study-downloader")
    apply_theme(app, settings.get("theme", "dark"))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
