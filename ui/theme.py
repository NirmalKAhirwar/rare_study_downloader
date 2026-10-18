"""Dark/light theme for the rare-study GUI (original QSS)."""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

ACCENT = "#4f8cff"
DARK_BG = "#0f1115"
DARK_CARD = "#171a21"
DARK_BORDER = "#262b36"
DARK_TEXT = "#e6e9ef"
DARK_MUTED = "#8b93a7"


def _dark_qss() -> str:
    return f"""
    QMainWindow, QDialog {{ background: {DARK_BG}; }}
    QWidget {{ color: {DARK_TEXT}; font-size: 13px; }}
    QFrame#card {{
        background: {DARK_CARD};
        border: 1px solid {DARK_BORDER};
        border-radius: 10px;
    }}
    QLabel#titleLabel {{ font-size: 15px; font-weight: 600; }}
    QLabel#statValue {{ font-size: 20px; font-weight: 700; }}
    QLabel#statLabel, QLabel#mutedLabel {{ color: {DARK_MUTED}; font-size: 11px; }}
    QLabel#badgeOk {{ color: #6ee7a8; }}
    QLabel#badgeWarn {{ color: #f5b755; }}
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
        background: #10131a;
        border: 1px solid {DARK_BORDER};
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
    QComboBox::drop-down {{ border: 0; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {DARK_CARD};
        border: 1px solid {DARK_BORDER};
        selection-background-color: {ACCENT};
    }}
    QPushButton {{
        background: #232833;
        border: 1px solid {DARK_BORDER};
        border-radius: 6px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{ background: #2c3340; }}
    QPushButton:pressed {{ background: #1c212b; }}
    QPushButton:disabled {{ color: #5a6072; background: #1a1e26; }}
    QPushButton#primaryBtn {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
    QPushButton#primaryBtn:hover {{ background: #6199ff; }}
    QPushButton#successBtn {{ background: #1f8a4c; border-color: #1f8a4c; color: white; }}
    QPushButton#successBtn:hover {{ background: #24a05a; }}
    QPushButton#warningBtn {{ background: #a9761b; border-color: #a9761b; color: white; }}
    QPushButton#warningBtn:hover {{ background: #c08520; }}
    QPushButton#dangerBtn {{ background: #a8322f; border-color: #a8322f; color: white; }}
    QPushButton#dangerBtn:hover {{ background: #c13c38; }}
    QTreeWidget {{
        background: #10131a;
        border: 1px solid {DARK_BORDER};
        border-radius: 8px;
        outline: 0;
    }}
    QTreeWidget::item {{ padding: 4px 2px; border-bottom: 1px solid #171b23; }}
    QTreeWidget::item:selected {{ background: #1d2a44; }}
    QHeaderView::section {{
        background: {DARK_CARD};
        color: {DARK_MUTED};
        border: 0;
        border-bottom: 1px solid {DARK_BORDER};
        padding: 6px 4px;
    }}
    QCheckBox {{ spacing: 6px; }}
    QTabWidget::pane {{ border: 0; }}
    QTabBar::tab {{
        background: transparent;
        color: {DARK_MUTED};
        padding: 8px 16px;
        margin-right: 4px;
        border: 1px solid transparent;
        border-color: transparent;
        border-bottom: 2px solid transparent;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }}
    QTabBar::tab:hover {{ color: {DARK_TEXT}; }}
    QTabBar::tab:selected {{
        color: {DARK_TEXT};
        background: {DARK_CARD};
        border-color: {DARK_BORDER};
        border-bottom: 2px solid {ACCENT};
    }}
    QMenuBar {{ background: {DARK_BG}; }}
    QMenuBar::item:selected {{ background: {DARK_CARD}; }}
    QMenu {{ background: {DARK_CARD}; border: 1px solid {DARK_BORDER}; }}
    QMenu::item:selected {{ background: {ACCENT}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: #333a48; border-radius: 5px; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QStatusBar {{ color: {DARK_MUTED}; }}
    """


def _light_qss() -> str:
    return """
    QMainWindow, QDialog { background: #f4f6fa; }
    QWidget { color: #1b1f27; font-size: 13px; }
    QFrame#card { background: #ffffff; border: 1px solid #dde2ec; border-radius: 10px; }
    QLabel#titleLabel { font-size: 15px; font-weight: 600; }
    QLabel#statValue { font-size: 20px; font-weight: 700; }
    QLabel#statLabel, QLabel#mutedLabel { color: #6b7280; font-size: 11px; }
    QLabel#badgeOk { color: #15803d; }
    QLabel#badgeWarn { color: #b45309; }
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
        background: #ffffff;
        border: 1px solid #dde2ec;
        border-radius: 6px;
        padding: 6px 8px;
    }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #4f8cff; }
    QPushButton {
        background: #eef1f7;
        border: 1px solid #d8dde8;
        border-radius: 6px;
        padding: 6px 12px;
    }
    QPushButton:hover { background: #e3e8f2; }
    QPushButton:disabled { color: #a0a6b3; }
    QPushButton#primaryBtn { background: #4f8cff; border-color: #4f8cff; color: white; font-weight: 600; }
    QPushButton#successBtn { background: #1f8a4c; border-color: #1f8a4c; color: white; }
    QPushButton#warningBtn { background: #c1851d; border-color: #c1851d; color: white; }
    QPushButton#dangerBtn { background: #bf3a35; border-color: #bf3a35; color: white; }
    QTreeWidget {
        background: #ffffff;
        border: 1px solid #dde2ec;
        border-radius: 8px;
        outline: 0;
    }
    QTreeWidget::item { padding: 4px 2px; border-bottom: 1px solid #eef1f7; }
    QTreeWidget::item:selected { background: #dbe7ff; color: #1b1f27; }
    QHeaderView::section {
        background: #ffffff;
        color: #6b7280;
        border: 0;
        border-bottom: 1px solid #dde2ec;
        padding: 6px 4px;
    }
    QCheckBox { spacing: 6px; }
    QTabWidget::pane { border: 0; }
    QTabBar::tab {
        background: transparent;
        color: #6b7280;
        padding: 8px 16px;
        margin-right: 4px;
        border: 1px solid transparent;
        border-bottom: 2px solid transparent;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }
    QTabBar::tab:hover { color: #1b1f27; }
    QTabBar::tab:selected {
        color: #1b1f27;
        background: #ffffff;
        border-color: #dde2ec;
        border-bottom: 2px solid #4f8cff;
    }
    QMenuBar { background: #f4f6fa; }
    QMenu { background: #ffffff; border: 1px solid #dde2ec; }
    QMenu::item:selected { background: #dbe7ff; }
    QStatusBar { color: #6b7280; }
    """


def apply_theme(app: QApplication, theme: str) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    if theme == "light":
        palette.setColor(QPalette.ColorRole.Window, QColor("#f4f6fa"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#1b1f27"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#1b1f27"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)
        app.setStyleSheet(_light_qss())
    else:
        palette.setColor(QPalette.ColorRole.Window, QColor(DARK_BG))
        palette.setColor(QPalette.ColorRole.Base, QColor("#10131a"))
        palette.setColor(QPalette.ColorRole.Text, QColor(DARK_TEXT))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(DARK_TEXT))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)
        app.setStyleSheet(_dark_qss())
