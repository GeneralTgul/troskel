"""Small building blocks used by every view: cards, headers, buttons, form fields, status pills."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QAbstractSlider, QAbstractSpinBox, QBoxLayout, QComboBox, QFrame,
                               QHBoxLayout, QLabel, QLayout, QPushButton, QScrollArea, QScrollBar, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from lopanalys.ui import theme


def button(text: str, icon_name: str | None = None, kind: str = "", size: str = "",
           tooltip: str = "") -> QPushButton:
    """A themed push button. kind: primary | danger | warning | ghost | key | choice | rpe."""
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if size:
        b.setProperty("size", size)
    if icon_name:
        col = "#FFFFFF" if kind in ("primary", "danger") else ("ink" if kind == "warning" else "text")
        b.setIcon(theme.icon(icon_name, col))
        b.setIconSize(QSize(22, 22) if size == "big" else theme.ICON_SIZE)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def label(text: str = "", obj: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    return lb


def field_label(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setProperty("role", "field")
    return lb


class _Field(QWidget):
    """Field box whose minimum height follows its width.

    A wrapped hint below the input needs more lines when the column gets narrow. QGridLayout items with an
    alignment (the forms use AlignTop) don't ask for height-for-width, so the box was squeezed and the hint
    was drawn over the input (real hardware 2026-11-14). The box now reports the height its layout
    needs at the current width as its minimum, and asks for a new layout when the width changes.
    """

    def _needed(self, width: int) -> int:
        lay = self.layout()
        if lay is None or width <= 0:
            return 0
        return lay.heightForWidth(width) if lay.hasHeightForWidth() else lay.sizeHint().height()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt)
        # An aligned grid item gets its size hint, so the hint is at least the height resizeEvent found for the
        # current width. It is not computed here from self.width(): a hint that follows the width the layout is
        # handing out fed back into the layout, and on Windows app.setStyleSheet never returned (the Windows build
        # stood in test_pin_locks_at_start_and_after_idle for 1.5 h, 2026-11-15).
        base = super().sizeHint()
        return QSize(base.width(), max(base.height(), self.minimumHeight()))

    def resizeEvent(self, e: Any) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            need = self._needed(e.size().width())
            if need != self.minimumHeight():
                self.setMinimumHeight(need)
                self.updateGeometry()
        lay = self.layout()
        if lay is not None:     # the inner layout was left at the squeezed height otherwise
            lay.setGeometry(self.rect())


class WheelGuard(QObject):
    """Installed on the application: the mouse wheel over a list box, a number box or a slider that has
    not been clicked scrolls the page instead of changing the value (2026-11-16: scrolling down the
    settings over Språk changed the language)."""

    TYPES = (QComboBox, QAbstractSpinBox, QAbstractSlider)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802 - Qt's name
        if (e.type() == QEvent.Type.Wheel and isinstance(obj, self.TYPES) and not isinstance(obj, QScrollBar)
                and not obj.hasFocus()):
            e.ignore()        # stopped here but not accepted: Qt passes the wheel on to the parent, the scroll area
            return True
        return False


class PageStack(QStackedWidget):
    """The pages of the window: only the page shown sets the minimum size, so a wide page (the plan)
    does not make every other page, and the window, as wide as itself."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(lambda _: self.updateGeometry())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        w = self.currentWidget()
        return w.minimumSizeHint().expandedTo(w.minimumSize()) if w is not None else super().minimumSizeHint()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()


class PageScroller(QScrollArea):
    """The page stack in a frameless scroll area with a scroll bar in both directions, shown only when the
    page shown does not fit (a small laptop screen): nothing is cut off at the edge of the window.

    The page is sized here and not by ``widgetResizable``: Qt would ask the stack for its height for the
    width, and a stack answers for every page, so a short page grew as tall as the tallest one."""

    def __init__(self, stack: QStackedWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageScroller")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setWidget(stack)
        stack.installEventFilter(self)
        stack.currentChanged.connect(lambda _: self.fit())

    def fit(self) -> None:
        w = self.widget()
        if w is None:
            return
        need = w.minimumSizeHint()
        vp = self.maximumViewportSize()          # the room without scroll bars
        wide, tall = need.width() > vp.width(), need.height() > vp.height()
        bar = self.style().pixelMetric(self.style().PixelMetric.PM_ScrollBarExtent)
        vw = vp.width() - (bar if tall else 0)
        vh = vp.height() - (bar if wide else 0)
        size = QSize(max(vw, need.width()), max(vh, need.height()))
        if size != w.size():
            w.resize(size)

    def resizeEvent(self, e: Any) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(e)
        self.fit()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802 - Qt's name
        if obj is self.widget() and e.type() == QEvent.Type.LayoutRequest:
            QTimer.singleShot(0, self.fit)
        return super().eventFilter(obj, e)


class ColumnsBox(QWidget):
    """Two columns side by side, or one under the other when they do not fit in the width given to
    ``fit`` (a laptop screen): the page then scrolls down instead of sideways."""

    def __init__(self, spacing: int = 20, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(spacing)
        self.c1, self.c2 = QVBoxLayout(), QVBoxLayout()
        for c in (self.c1, self.c2):
            c.setSpacing(spacing)
            self.box.addLayout(c, 1)

    def side_by_side(self) -> bool:
        return self.box.direction() == QBoxLayout.Direction.LeftToRight

    def needed(self) -> int:
        """The width the two columns need side by side."""
        return self.c1.minimumSize().width() + self.c2.minimumSize().width() + self.box.spacing()

    def fit(self, width: int) -> None:
        d = (QBoxLayout.Direction.LeftToRight if width >= self.needed() else QBoxLayout.Direction.TopToBottom)
        if d != self.box.direction():
            self.box.setDirection(d)
            self.updateGeometry()


class _VScroll(QScrollArea):
    """A vertical scroll area that asks for the width of what it holds, so a narrow window scrolls the
    whole page sideways (with a bar) instead of cutting the form off at the right edge."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        hint = super().minimumSizeHint()
        w = self.widget()
        if w is None:
            return hint
        bar = self.style().pixelMetric(self.style().PixelMetric.PM_ScrollBarExtent)
        return QSize(max(hint.width(), w.minimumSizeHint().width() + bar), hint.height())


def scrolled(w: QWidget) -> QWidget:
    """``w`` in a frameless vertical scroll area, so a form keeps its minimum sizes on a small screen
    instead of being squeezed until the fields draw over each other."""
    sa = _VScroll()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(w)
    return sa


def field(text: str, w: QWidget, hint: str = "") -> QWidget:
    """A labelled form field (label above the input, optional hint below)."""
    box = _Field() if hint else QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    v.addWidget(field_label(text))
    v.addWidget(w)
    if hint:
        v.addWidget(label(hint, "Hint", wrap=True))
    return box


class Card(QFrame):
    """Rounded surface with padding. ``body`` is its QVBoxLayout."""

    def __init__(self, title: str = "", subtitle: str = "", flat: bool = False, pad: int = 20,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(pad, pad, pad, pad)
        self.body.setSpacing(12)
        if title:
            self.title = label(title, "SectionTitle")
            self.body.addWidget(self.title)
        if subtitle:
            self.body.addWidget(label(subtitle, "Hint", wrap=True))


class PageHeader(QWidget):
    """Title + subtitle on the left, action buttons on the right."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 8)
        h.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.title = label(title, "PageTitle")
        self.subtitle = label(subtitle, "PageSubtitle", wrap=True)
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        self.subtitle.setVisible(bool(subtitle))
        h.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(10)
        h.addLayout(self.actions)

    def add_action(self, w: QWidget) -> None:
        self.actions.addWidget(w)


def page(parent: QWidget | None = None, margins: int = 32) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget(parent)
    v = QVBoxLayout(w)
    v.setContentsMargins(margins, margins - 4, margins, margins)
    v.setSpacing(18)
    return w, v


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    return f


def clear_layout(lay: QLayout) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


class StatusPill(QWidget):
    """A dot + text, e.g. 'Ansluten' in green. state: ok | watch | fix | idle | busy."""

    COLORS = {"ok": "good", "watch": "watch", "fix": "fix", "idle": "muted", "busy": "accent2"}

    def __init__(self, text: str = "", state: str = "idle", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text, self._state = text, state
        self._blink = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(30)

    def set(self, text: str, state: str) -> None:
        self._text, self._state = text, state
        if state == "busy":
            self._timer.start(500)
        else:
            self._timer.stop()
            self._blink = False
        self.updateGeometry()
        self.update()

    def _tick(self) -> None:
        self._blink = not self._blink
        self.update()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._text) + 44, 30)

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.color(self.COLORS.get(self._state, "muted"))
        bg = QColor(c)
        bg.setAlpha(34)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        dot = QColor(c)
        if self._blink:
            dot.setAlpha(90)
        p.setBrush(dot)
        p.drawEllipse(QRectF(12, r.center().y() - 4.5, 9, 9))
        p.setPen(theme.color("text"))
        f = theme.font(13, QFont.Weight.DemiBold)
        p.setFont(f)
        p.drawText(r.adjusted(28, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


class Avatar(QWidget):
    """Round initials badge for an athlete."""

    def __init__(self, name: str, size: int = 40, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self.setFixedSize(size, size)

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hue = (sum(map(ord, self.name)) * 37) % 360
        p.setBrush(QColor.fromHsl(hue, 90, 70))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(self.rect())
        p.setPen(theme.color("text"))
        p.setFont(theme.font(int(self.height() * 0.38), QFont.Weight.Bold))
        ini = "".join(w[0] for w in self.name.split()[:2]).upper() or "?"
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, ini)


class Stepper(QWidget):
    """Wizard progress: numbered steps with the current one highlighted."""

    def __init__(self, steps: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.steps = steps
        self.current = 0
        self.setMinimumHeight(56)

    def set_current(self, i: int) -> None:
        self.current = i
        self.update()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.steps)
        w = self.width() / n
        cy = 16
        for i, name in enumerate(self.steps):
            cx = w * i + w / 2
            if i < n - 1:
                p.setPen(QPen(theme.color("accent" if i < self.current else "line"), 2))
                p.drawLine(int(cx + 18), cy, int(cx + w - 18), cy)
            done, cur = i < self.current, i == self.current
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent") if (done or cur) else theme.color("surface2"))
            p.drawEllipse(QRectF(cx - 13, cy - 13, 26, 26))
            p.setPen(theme.color("#FFFFFF" if (done or cur) else "muted"))
            p.setFont(theme.font(12, QFont.Weight.Bold))
            p.drawText(QRectF(cx - 13, cy - 13, 26, 26), Qt.AlignmentFlag.AlignCenter, "✓" if done else str(i + 1))
            p.setPen(theme.color("text" if cur else "muted"))
            p.setFont(theme.font(12, QFont.Weight.DemiBold if cur else QFont.Weight.Normal))
            p.drawText(QRectF(cx - w / 2, cy + 18, w, 20), Qt.AlignmentFlag.AlignCenter, name)


def fit_font(font: QFont, text: str, width: float, smallest: int = 11) -> QFont:
    """``font`` made smaller (down to ``smallest`` px) until ``text`` fits in ``width``."""
    from PySide6.QtGui import QFontMetricsF
    f = QFont(font)
    px = f.pixelSize() if f.pixelSize() > 0 else int(f.pointSizeF() * 96 / 72)
    while px > smallest and QFontMetricsF(f).horizontalAdvance(text) > width:
        px -= 1
        f.setPixelSize(px)
    return f


def fit_text(font: QFont, text: str, width: float, smallest: int = 11) -> tuple[QFont, str]:
    """The font shrunk to fit (``fit_font``), and the text shortened with "…" when it still doesn't fit at the
    smallest size (Segoe UI on the gym PC is too wide for "Bluetooth (FTMS)" even at 11 px)."""
    from PySide6.QtGui import QFontMetricsF
    f = fit_font(font, text, width, smallest)
    fm = QFontMetricsF(f)
    if fm.horizontalAdvance(text) <= width:
        return f, text
    # Qt's elidedText measures differently from horizontalAdvance for Segoe UI Variable on Windows and
    # returned the whole text at 176 px for a 150 px slot (CI 2026-11-14), so the cut is checked here.
    cut = fm.elidedText(text, Qt.TextElideMode.ElideRight, width)
    if cut == text or fm.horizontalAdvance(cut) > width:
        cut = text
        while cut and fm.horizontalAdvance(cut.rstrip() + "…") > width:
            cut = cut[:-1]
        cut = cut.rstrip() + "…"
    return f, cut


class ChoiceCard(QAbstractButton):
    """A big checkable card: icon in a tinted circle, a bold title and a description."""

    def __init__(self, title: str, desc: str, icon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title, self.desc, self.icon_name = title, desc, icon
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setAccessibleName(title)
        self.setAccessibleDescription(desc)

    def sizeHint(self) -> QSize:
        return QSize(280, 84)

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        hover = self.underMouse()
        bg = theme.color("accent", 34) if on else theme.color("surface2" if hover else "surface")
        p.setPen(QPen(theme.color("accent") if on else theme.color("line"), 2 if on else 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 14, 14)
        h = r.height()
        d = min(52.0, h * 0.56)
        c = QRectF(r.left() + 18, r.center().y() - d / 2, d, d)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("accent", 60 if on else 30))
        p.drawEllipse(c)
        isz = int(d * 0.52)
        p.drawPixmap(int(c.center().x() - isz / 2), int(c.center().y() - isz / 2),
                     theme.pixmap(self.icon_name, "accent" if on else "text", isz))
        x = c.right() + 16
        tf = theme.font(16, QFont.Weight.Bold)
        df = theme.font(13)
        p.setFont(tf)
        p.setPen(theme.color("text"))
        th = tf.pixelSize() * 1.4
        dr = QRectF(x, 0, r.right() - x - 14, h)
        p.setFont(df)
        desc_rect = p.fontMetrics().boundingRect(dr.toRect(), int(Qt.TextFlag.TextWordWrap), self.desc)
        block = th + 4 + desc_rect.height()
        y = r.center().y() - block / 2
        # The title shrinks to fit beside the check mark instead of being cut ("Bluetooth (real hardware 2026-11-14, FTM").
        tw = dr.width() - (30 if on else 0)
        tf, title = fit_text(tf, self.title, tw)
        p.setFont(tf)
        p.drawText(QRectF(x, y, tw, th), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.setFont(df)
        p.setPen(theme.color("muted"))
        p.drawText(QRectF(x, y + th + 4, dr.width(), desc_rect.height() + 2),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop) | int(Qt.TextFlag.TextWordWrap), self.desc)
        if on:
            p.drawPixmap(int(r.right() - 34), int(r.top() + 12), theme.pixmap("check", "accent", 20))
