"""Thème Kolnoa : sombre, sobre, un seul accent (ambre). Appliqué une fois à l'application ; aucun style n'est disséminé dans les widgets
(sauf par `objectName`, défini ici). Les couleurs de la timeline y sont lues pour rester cohérentes."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# Identité commune aux apps Kolnoa (palette de Kolnoa Photo Studio : noir anthracite, gris graphite, accents dorés)
BG0 = "#151517"        # fond de la timeline / du moniteur (un cran sous BG1)
BG1 = "#1b1b1e"        # fond de la fenêtre
BG2 = "#232327"        # panneaux
BG3 = "#2b2b30"        # champs, boutons
LINE = "#38383f"
TEXT = "#d8d8dc"
MUTED = "#8b8b93"
ACCENT = "#d39a4e"     # or Kolnoa
ACCENT_HI = "#e3b06d"
GOLD_DARK = "#2d2a20"  # fond d'un élément sélectionné
VIDEO = "#4f6f9a"
AUDIO = "#4a8466"
PLAYHEAD = "#e3b06d"
DANGER = "#d05050"

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif; font-size: 13px; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG1}; }}
QWidget#panel {{ background: {BG2}; border: 1px solid {LINE}; border-radius: 8px; }}
QWidget#topbar {{ background: {BG2}; border-bottom: 1px solid {LINE}; }}
QLabel#brand {{ font-size: 15px; font-weight: 700; letter-spacing: 0.5px; }}
QLabel#logoSub {{ color: #808783; font-family: "Segoe UI Light", "Segoe UI"; font-size: 13px; letter-spacing: 5px; }}
QComboBox {{ background: {BG3}; border: 1px solid {LINE}; border-radius: 7px; padding: 5px 10px; color: {TEXT}; }}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {BG2}; border: 1px solid {LINE}; selection-background-color: {GOLD_DARK}; selection-color: {ACCENT_HI}; outline: 0; }}
QTabBar#seqTabs::tab {{ background: {BG2}; color: {MUTED}; border: 1px solid {LINE}; border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px; padding: 5px 14px; margin-right: 2px; }}
QTabBar#seqTabs::tab:selected {{ background: {GOLD_DARK}; color: {ACCENT_HI}; border-color: {ACCENT}; }}
QTabBar#seqTabs::tab:hover {{ color: {TEXT}; }}
QPushButton#mode {{ background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0; padding: 14px 16px; color: {TEXT}; }}
QPushButton#mode:hover {{ color: {ACCENT_HI}; }}
QPushButton#mode:checked {{ color: {ACCENT_HI}; border-bottom: 2px solid {ACCENT}; }}
QLabel#badge {{ color: {MUTED}; border: 1px solid {LINE}; border-radius: 8px; padding: 1px 8px; font-size: 11px; }}
QLabel#badgeOk {{ color: {ACCENT_HI}; border: 1px solid {ACCENT}; border-radius: 8px; padding: 1px 8px; font-size: 11px; }}
QPlainTextEdit {{ background: {BG0}; border: 1px solid {LINE}; border-radius: 8px; padding: 8px 10px; selection-background-color: {ACCENT}; selection-color: #15120a; }}
QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 3px; background: {LINE}; border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 1px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT_HI}; }}
QLabel#project {{ color: {MUTED}; }}
QLabel#panelTitle {{ color: {MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1.2px; text-transform: uppercase; }}
QLabel#timecode {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 16px; font-weight: 600; color: {ACCENT_HI}; }}
QLabel#zone {{ color: {MUTED}; font-size: 12px; }}
QLabel#monitor {{ background: {BG0}; color: {MUTED}; border: 1px solid {LINE}; border-radius: 8px; font-size: 14px; }}
QStatusBar {{ background: {BG2}; border-top: 1px solid {LINE}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; }}
QMenuBar {{ background: {BG2}; border-bottom: 1px solid {LINE}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {BG3}; border-radius: 4px; }}
QMenu {{ background: {BG2}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {BG3}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 6px; }}
QPushButton {{ background: {BG3}; border: 1px solid {LINE}; border-radius: 6px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:pressed {{ background: {BG2}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #15120a; font-weight: 700; }}
QPushButton#primary:hover {{ background: {ACCENT_HI}; border-color: {ACCENT_HI}; }}
QPushButton#chip {{ background: transparent; border: 1px solid {LINE}; border-radius: 12px; padding: 3px 11px; color: {MUTED}; font-size: 12px; }}
QPushButton#chip:hover {{ color: {TEXT}; border-color: {MUTED}; }}
QPushButton#icon {{ background: transparent; border: none; padding: 6px 9px; font-size: 16px; border-radius: 6px; }}
QPushButton#icon:hover {{ background: {BG3}; }}
QPushButton#play {{ background: {ACCENT}; border: none; color: #15120a; border-radius: 17px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; font-size: 14px; font-weight: 800; padding: 0; }}
QPushButton#play:hover {{ background: {ACCENT_HI}; }}
QLineEdit#prompt {{ background: {BG0}; border: 1px solid {LINE}; border-radius: 10px; padding: 9px 14px; font-size: 14px; selection-background-color: {ACCENT}; }}
QLineEdit#prompt:focus {{ border-color: {ACCENT}; }}
QLineEdit {{ background: {BG0}; border: 1px solid {LINE}; border-radius: 6px; padding: 5px 8px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 6px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {BG3}; }}
QListWidget::item:selected {{ background: {GOLD_DARK}; border-left: 3px solid {ACCENT}; color: {ACCENT_HI}; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BG3}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BG3}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{ background: {BG3}; color: {TEXT}; border: 1px solid {LINE}; padding: 4px 8px; }}
QProgressBar {{ background: {BG0}; border: 1px solid {LINE}; border-radius: 6px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {MUTED}; background: {BG0}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
"""


def apply_theme(app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None or app.property("kolnoa_theme"):
        return
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in ((QPalette.Window, BG1), (QPalette.Base, BG0), (QPalette.AlternateBase, BG2), (QPalette.Button, BG3),
                      (QPalette.Text, TEXT), (QPalette.WindowText, TEXT), (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, BG3),
                      (QPalette.ToolTipText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#15120a"),
                      (QPalette.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    f = QFont("Segoe UI")
    f.setPointSize(10)
    app.setFont(f)
    app.setStyleSheet(QSS)
    app.setProperty("kolnoa_theme", True)
