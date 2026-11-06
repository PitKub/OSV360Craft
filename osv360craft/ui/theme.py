"""Dark and light themes (Qt style sheets built from one token table)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

TOKENS = {
    "dark": {
        "bg": "#151619", "panel": "#1d1f23", "panel2": "#25282d", "border": "#33363c", "text": "#e6e7e9",
        "muted": "#9aa0a6", "accent": "#3d8bfd", "accent_text": "#ffffff", "ok": "#3fb950", "warn": "#d29922",
        "bad": "#f85149", "input": "#111214", "sel": "#2b4f86",
    },
    "light": {
        "bg": "#f4f5f7", "panel": "#ffffff", "panel2": "#eceef1", "border": "#d0d4da", "text": "#1d2127",
        "muted": "#5f6670", "accent": "#1a6ae6", "accent_text": "#ffffff", "ok": "#1a7f37", "warn": "#9a6700",
        "bad": "#cf222e", "input": "#ffffff", "sel": "#cfe0fb",
    },
}


def stylesheet(t: dict) -> str:
    return f"""
    QWidget {{ background: {t['bg']}; color: {t['text']}; font-size: 10pt; }}
    QFrame#Sidebar {{ background: {t['panel']}; border-right: 1px solid {t['border']}; }}
    QLabel#AppTitle {{ font-size: 15pt; font-weight: 600; padding: 14px 12px 2px 12px; background: transparent; }}
    QLabel#AppVersion {{ color: {t['muted']}; padding: 0 12px 12px 12px; background: transparent; }}
    QPushButton#Nav {{ text-align: left; padding: 9px 14px; border: none; border-radius: 6px; margin: 1px 8px;
                       background: transparent; }}
    QPushButton#Nav:hover {{ background: {t['panel2']}; }}
    QPushButton#Nav:checked {{ background: {t['sel']}; font-weight: 600; }}
    QLabel#PageTitle {{ font-size: 16pt; font-weight: 600; padding: 4px 0 8px 0; }}
    QLabel#Muted, QLabel[muted="true"] {{ color: {t['muted']}; }}
    QLabel#Ok {{ color: {t['ok']}; }} QLabel#Warn {{ color: {t['warn']}; }} QLabel#Bad {{ color: {t['bad']}; }}
    QGroupBox {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 8px; margin-top: 26px;
                 padding: 20px 16px 14px 16px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 4px; top: 0px;
                        padding: 0 4px 6px 4px; background: transparent; }}
    QGroupBox QWidget {{ background: transparent; font-weight: normal; }}
    QPushButton {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 6px 12px; }}
    QPushButton:hover {{ border-color: {t['accent']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QPushButton#Primary {{ background: {t['accent']}; color: {t['accent_text']}; border: none; font-weight: 600; }}
    QPushButton#Danger {{ color: {t['bad']}; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {t['input']}; border: 1px solid {t['border']}; border-radius: 5px; padding: 5px 8px; }}
    QComboBox QAbstractItemView {{ background: {t['panel']}; selection-background-color: {t['sel']}; }}
    QTableWidget, QTreeWidget, QListWidget {{ background: {t['panel']}; alternate-background-color: {t['panel2']};
        border: 1px solid {t['border']}; border-radius: 6px; gridline-color: {t['border']};
        selection-background-color: {t['sel']}; selection-color: {t['text']}; }}
    QHeaderView::section {{ background: {t['panel2']}; border: none; border-right: 1px solid {t['border']}; padding: 6px 8px; }}
    QTableWidget::item {{ padding: 4px 6px; }}
    QListWidget::item {{ padding: 4px 6px; }}
    QProgressBar {{ background: {t['input']}; border: 1px solid {t['border']}; border-radius: 5px; text-align: center; height: 16px; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 4px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t['border']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {t['accent']}; width: 14px; margin: -6px 0; border-radius: 7px; }}
    QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 6px; }}
    QTabBar::tab {{ background: {t['panel2']}; padding: 6px 12px; border-top-left-radius: 5px; border-top-right-radius: 5px; }}
    QTabBar::tab:selected {{ background: {t['sel']}; }}
    QScrollArea {{ border: none; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {t['muted']};
        background: {t['input']}; }}
    QCheckBox::indicator {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {t['border']}; }}
    QToolTip {{ background: {t['panel']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 6px 8px; }}
    QStatusBar {{ background: {t['panel']}; border-top: 1px solid {t['border']}; }}
    """


def apply_theme(app: QApplication, name: str) -> dict:
    t = TOKENS.get(name, TOKENS["dark"])
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(t["bg"]))
    pal.setColor(QPalette.WindowText, QColor(t["text"]))
    pal.setColor(QPalette.Base, QColor(t["input"]))
    pal.setColor(QPalette.AlternateBase, QColor(t["panel2"]))
    pal.setColor(QPalette.Text, QColor(t["text"]))
    pal.setColor(QPalette.Button, QColor(t["panel2"]))
    pal.setColor(QPalette.ButtonText, QColor(t["text"]))
    pal.setColor(QPalette.Highlight, QColor(t["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(t["accent_text"]))
    pal.setColor(QPalette.ToolTipBase, QColor(t["panel"]))
    pal.setColor(QPalette.ToolTipText, QColor(t["text"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t))
    return t
