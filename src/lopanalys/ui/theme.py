"""The visual identity in one place: colour tokens, fonts, the dark QSS theme and icons.

Every widget takes its colours from ``TOKENS`` (or the ``color()`` helper) and its icons from
``icon()``, so the look can be changed here without touching the views. The accent colour can
be overridden by the coach's branding (Inställningar), see ``set_accent()``.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent / "assets"

TOKENS: dict[str, str] = {
    "ink": "#141A21",
    "surface": "#1B232C",
    "surface2": "#232D38",
    "surface3": "#2C3845",
    "line": "#2F3B48",
    "text": "#E9EEF3",
    "muted": "#8B98A6",
    "accent": "#22A699",
    "accent2": "#3C82F6",
    "good": "#3FB950",
    "watch": "#F2A93B",
    "fix": "#E5484D",
}

FONT_FAMILIES = ["Segoe UI Variable", "Segoe UI", "Inter", "Arial", "Liberation Sans", "sans-serif"]

# Heart rate zone colours, zone 1..5 (easy -> max), as on a Garmin watch. Shared by TV tiles, charts and the report.
from lopanalys.core.zone_colors import GARMIN as ZONE_COLORS  # noqa: E402
from lopanalys.core.zone_colors import zone_color  # noqa: E402,F401


def color(name: str, alpha: int | None = None) -> QColor:
    """A token as QColor, optionally with alpha 0..255."""
    c = QColor(TOKENS.get(name, name))
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def set_accent(hex_colour: str | None) -> None:
    """Use the coach's brand colour as accent (falls back to the default teal)."""
    if hex_colour and QColor(hex_colour).isValid():
        TOKENS["accent"] = QColor(hex_colour).name()
    else:
        TOKENS["accent"] = "#22A699"
    _icon_pixmap.cache_clear()


def font(px: int, weight: QFont.Weight = QFont.Weight.Normal, tabular: bool = False) -> QFont:
    """App font at a pixel size. ``tabular`` turns on tabular numerals for live values."""
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    if tabular:
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
        except Exception:  # older Qt: proportional digits are acceptable
            pass
    return f


@lru_cache(maxsize=512)
def _icon_pixmap(name: str, colour: str, size: int) -> QPixmap:
    path = ASSETS / f"{name}.svg"
    svg = path.read_text(encoding="utf-8") if path.exists() else ""
    svg = svg.replace("currentColor", colour)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    if svg:
        r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(p, QRectF(0, 0, size, size))
        p.end()
    return pm


def pixmap(name: str, colour: str = "text", size: int = 24) -> QPixmap:
    """An icon from ui/assets drawn in a token colour (or any #hex)."""
    return _icon_pixmap(name, TOKENS.get(colour, colour), size)


def icon(name: str, colour: str = "text", size: int = 48) -> QIcon:
    """QIcon with a normal and a disabled (muted) state."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, colour, size), QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, "#55616E", size), QIcon.Mode.Disabled)
    ic.addPixmap(pixmap(name, colour, size), QIcon.Mode.Selected)
    return ic


ICON_SIZE = QSize(20, 20)


def polish_inputs(root: "QWidget") -> None:  # noqa: F821
    """Give spin boxes the same inner text margin as line edits (QSS padding is ignored there)."""
    from PySide6.QtWidgets import QAbstractSpinBox
    for sb in root.findChildren(QAbstractSpinBox):
        le = sb.lineEdit()
        if le is not None:
            le.setTextMargins(8, 0, 0, 0)


def qss() -> str:
    """The application style sheet, built from TOKENS."""
    k = dict(TOKENS)
    k["accent_hover"] = QColor(k["accent"]).lighter(115).name()
    k["accent_press"] = QColor(k["accent"]).darker(115).name()
    k["accent_soft"] = "rgba({},{},{},40)".format(*QColor(k["accent"]).getRgb()[:3])
    a, sf = QColor(k["accent"]), QColor(k["surface"])
    k["accent_sel"] = QColor(int(sf.red() * 0.72 + a.red() * 0.28), int(sf.green() * 0.72 + a.green() * 0.28),
                             int(sf.blue() * 0.72 + a.blue() * 0.28)).name()
    k["chevron"] = (ASSETS / "chevron-down-muted.svg").as_posix()
    k["check"] = (ASSETS / "check-white.svg").as_posix()
    k["fam"] = ", ".join(f'"{f}"' for f in FONT_FAMILIES)
    return """
* {{ font-family: {fam}; outline: none; }}
QWidget {{ background: transparent; color: {text}; font-size: 14px; }}
QMainWindow, QDialog, #Root {{ background: {ink}; }}
QToolTip {{ background: {surface2}; color: {text}; border: 1px solid {line}; padding: 6px 8px; border-radius: 6px; }}

/* navigation */
#Sidebar {{ background: {surface}; border-right: 1px solid {line}; }}
#Brand {{ font-size: 18px; font-weight: 700; color: {text}; }}
#BrandSub {{ color: {muted}; font-size: 12px; }}
#NavButton {{ text-align: left; padding: 11px 14px; border-radius: 10px; color: {muted};
             font-size: 15px; font-weight: 600; border: none; background: transparent; }}
#NavButton:hover {{ background: {surface2}; color: {text}; }}
#NavButton:checked {{ background: {accent_soft}; color: {text}; }}
#DemoBadge {{ background: {watch}; color: {ink}; border-radius: 8px; padding: 3px 8px; font-weight: 700; font-size: 11px; }}

/* page structure */
#PageTitle {{ font-size: 26px; font-weight: 700; }}
#PageSubtitle {{ color: {muted}; font-size: 14px; }}
#SectionTitle {{ font-size: 16px; font-weight: 700; }}
#Muted {{ color: {muted}; }}
#Hint {{ color: {muted}; font-size: 13px; }}
#Card {{ background: {surface}; border: 1px solid {line}; border-radius: 14px; }}
#CardFlat {{ background: {surface2}; border-radius: 12px; }}
#Divider {{ background: {line}; max-height: 1px; min-height: 1px; }}

/* buttons */
QPushButton {{ background: {surface2}; border: 1px solid {line}; border-radius: 10px;
              padding: 9px 16px; font-weight: 600; color: {text}; }}
QPushButton:hover {{ background: {surface3}; }}
QPushButton:pressed {{ background: {surface}; }}
QPushButton:disabled {{ color: #55616E; background: {surface}; border-color: {surface2}; }}
QPushButton[kind="primary"] {{ background: {accent}; border: 1px solid {accent}; color: #FFFFFF; }}
QPushButton[kind="primary"]:hover {{ background: {accent_hover}; }}
QPushButton[kind="primary"]:pressed {{ background: {accent_press}; }}
QPushButton[kind="primary"]:disabled {{ background: {surface2}; border-color: {surface2}; color: #55616E; }}
QPushButton[kind="danger"] {{ background: {fix}; border: 1px solid {fix}; color: #FFFFFF; }}
QPushButton[kind="danger"]:hover {{ background: #F0666A; }}
QPushButton[kind="warning"] {{ background: {watch}; border: 1px solid {watch}; color: {ink}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; color: {muted}; }}
QPushButton[kind="ghost"]:hover {{ background: {surface2}; color: {text}; }}
QPushButton[size="big"] {{ padding: 16px 22px; font-size: 17px; border-radius: 12px; }}
QPushButton[kind="key"] {{ font-size: 26px; font-weight: 600; padding: 0; border-radius: 12px; background: {surface2}; }}
QPushButton[kind="key"]:hover {{ background: {surface3}; }}
QPushButton[kind="choice"] {{ text-align: left; padding: 14px 16px; border-radius: 12px; background: {surface}; }}
QPushButton[kind="choice"]:checked {{ border: 2px solid {accent}; background: {accent_soft}; }}
QPushButton[kind="rpe"] {{ padding: 8px 0; font-size: 16px; }}
QPushButton[kind="rpe"]:checked {{ background: {accent}; border-color: {accent}; color: #FFFFFF; }}

/* inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {surface2}; border: 1px solid {line}; border-radius: 9px; padding: 8px 10px;
    selection-background-color: {accent}; color: {text}; min-height: 20px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {muted}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateEdit::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateEdit::down-button {{ width: 0; border: none; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 30px; background: transparent; }}
QDateEdit::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; margin-right: 10px; }}
QAbstractSpinBox {{ padding-left: 12px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {surface2}; border: 1px solid {line}; selection-background-color: {accent_soft};
                               padding: 4px; outline: none; }}
QCheckBox, QRadioButton {{ spacing: 10px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border: 2px solid {muted}; background: {surface2}; }}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 10px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QCheckBox::indicator:checked {{ image: url("{check}"); }}
QLabel[role="field"] {{ color: {muted}; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }}

/* lists and tables */
QListWidget, QTableWidget, QTreeWidget {{ background: {surface}; border: 1px solid {line}; border-radius: 12px;
    alternate-background-color: {surface}; gridline-color: {line};
    selection-background-color: {accent_sel}; selection-color: {text}; }}
QListWidget::item {{ padding: 10px 12px; border-radius: 8px; margin: 2px 4px; }}
QListWidget::item:hover {{ background: {surface2}; }}
QListWidget::item:selected {{ background: {accent_soft}; color: {text}; }}
QTableWidget::item, QTreeWidget::item {{ padding: 8px 10px; border: none; }}
QTableWidget::item:selected, QTreeWidget::item:selected {{ background: {accent_sel}; color: {text}; }}
QHeaderView::section {{ background: {surface}; color: {muted}; border: none; border-bottom: 1px solid {line};
                        padding: 10px; font-weight: 700; font-size: 12px; }}
QTableCornerButton::section {{ background: {surface}; border: none; }}

QScrollBar:vertical {{ background: {surface2}; width: 12px; margin: 0; border-radius: 6px; }}
QScrollBar::handle:vertical {{ background: {muted}; border-radius: 5px; min-height: 30px; margin: 2px; }}
QScrollBar:horizontal {{ background: {surface2}; height: 12px; margin: 0; border-radius: 6px; }}
QScrollBar::handle:horizontal {{ background: {muted}; border-radius: 5px; min-width: 30px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {text}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; height: 0; width: 0; }}
QScrollArea {{ border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 10px 16px; font-weight: 600; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
QProgressBar {{ background: {surface2}; border: none; border-radius: 6px; height: 12px; text-align: center; color: {text}; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}
QMenu {{ background: {surface2}; border: 1px solid {line}; padding: 6px; border-radius: 8px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent_soft}; }}
QMessageBox {{ background: {surface}; }}
QSlider::groove:horizontal {{ height: 6px; background: {surface3}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {accent}; width: 18px; margin: -6px 0; border-radius: 9px; }}
""".format(**k)
