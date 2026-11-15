"""Custom-painted live tiles. Font sizes follow the tile size, so the same widgets work on the
coach's laptop and full screen on a TV read from 3 m."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from lopanalys.ui import theme

STATUS_COLOR = {"ok": "good", "watch": "watch", "fix": "fix", None: "muted", "": "muted"}


def _rounded_bg(p: QPainter, r: QRectF, radius: float, colour: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    p.drawRoundedRect(r, radius, radius)


class MetricTile(QWidget):
    """Title with icon, a big number with unit, and a small line below.

    ``set_value("152", sub="Zon 3", colour="#F2A93B")``: the colour tints the value and draws
    a bar along the left edge (used for HR zones and status).
    """

    def __init__(self, title: str, unit: str = "", icon: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title, self.unit, self.icon = title, unit, icon
        self.value, self.sub = "–", ""
        self.colour: str | None = None
        self.tint_value = True
        self.progress: float | None = None   # optional 0..1 bar under the value (e.g. zone position)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(140, 100)

    def set_value(self, value: str, sub: str = "", colour: str | None = None,
                  progress: float | None = None, tint_value: bool = True) -> None:
        new = (value, sub, colour, progress, tint_value)
        if new != (self.value, self.sub, self.colour, self.progress, self.tint_value):
            self.value, self.sub, self.colour, self.progress, self.tint_value = new
            self.update()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        h = r.height()
        s = min(h, r.width() * 0.62)             # size unit for fonts
        _rounded_bg(p, r, max(10.0, s * 0.08), theme.color("surface"))
        pad = max(12.0, s * 0.11)
        if self.colour:
            p.setBrush(theme.color(self.colour))
            bar = QRectF(r.left(), r.top() + pad * 0.8, max(5.0, s * 0.03), h - pad * 1.6)
            p.drawRoundedRect(bar, bar.width() / 2, bar.width() / 2)
        # title
        tf = theme.font(max(12, int(s * 0.105)), QFont.Weight.DemiBold)
        x = r.left() + pad + (s * 0.03 if self.colour else 0)
        y = r.top() + pad
        if self.icon:
            isz = int(tf.pixelSize() * 1.15)
            p.drawPixmap(int(x), int(y), theme.pixmap(self.icon, "muted", isz))
            x_t = x + isz + tf.pixelSize() * 0.45
        else:
            x_t = x
        p.setFont(tf)
        p.setPen(theme.color("muted"))
        p.drawText(QRectF(x_t, y - 2, r.right() - x_t - pad, tf.pixelSize() * 1.5),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title.upper())
        # value
        sub_h = tf.pixelSize() * 1.6 if self.sub else 0
        vf = theme.font(int(s * 0.44), QFont.Weight.Bold, tabular=True)
        uf = theme.font(max(12, int(s * 0.13)), QFont.Weight.DemiBold)
        avail = r.width() - (x - r.left()) - pad
        fm = QFontMetricsF(vf)
        um = QFontMetricsF(uf)
        need = fm.horizontalAdvance(self.value) + (um.horizontalAdvance(self.unit) + s * 0.05 if self.unit else 0)
        if need > avail and need > 0:
            vf.setPixelSize(max(10, int(vf.pixelSize() * avail / need)))
            fm = QFontMetricsF(vf)
        # centre the value block (value, progress, sub) in the space under the title
        title_bottom = y + tf.pixelSize() * 1.5
        prog_h = s * 0.07 if self.progress is not None else 0
        block = fm.ascent() * 0.92 + prog_h + sub_h
        free_top = title_bottom + max(0.0, (r.bottom() - pad - title_bottom - block) / 2)
        base_y = free_top + fm.ascent() * 0.92
        p.setFont(vf)
        p.setPen(theme.color(self.colour) if self.colour and self.tint_value else theme.color("text"))
        p.drawText(QPointF(x, base_y - fm.descent() * 0.4), self.value)
        if self.unit:
            p.setFont(uf)
            p.setPen(theme.color("muted"))
            p.drawText(QPointF(x + fm.horizontalAdvance(self.value) + s * 0.05, base_y - fm.descent() * 0.4), self.unit)
        if self.progress is not None:
            track = QRectF(x, base_y + s * 0.035, avail, max(4.0, s * 0.03))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("surface3"))
            p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
            p.setBrush(theme.color(self.colour or "accent"))
            p.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * max(0.0, min(1.0, self.progress)),
                                     track.height()), track.height() / 2, track.height() / 2)
        if self.sub:
            p.setFont(tf)
            p.setPen(theme.color("muted"))
            p.drawText(QRectF(x, base_y + prog_h, avail, sub_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, self.sub)


class CountdownRing(QWidget):
    """Stage name, a big countdown and a progress ring to the next change."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fraction = 0.0
        self.big = "–:–"
        self.top = ""
        self.bottom = ""
        self.colour = "accent"
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_state(self, top: str, big: str, bottom: str, fraction: float, colour: str = "accent") -> None:
        self.top, self.big, self.bottom, self.fraction, self.colour = top, big, bottom, fraction, colour
        self.update()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 4
        r = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        w = max(8.0, side * 0.06)
        rr = r.adjusted(w / 2, w / 2, -w / 2, -w / 2)
        p.setPen(QPen(theme.color("surface3"), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(rr)
        p.setPen(QPen(theme.color(self.colour), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        span = int(-360 * 16 * max(0.0, min(1.0, self.fraction)))
        if span:
            p.drawArc(rr, 90 * 16, span)
        p.setPen(theme.color("muted"))
        p.setFont(theme.font(max(11, int(side * 0.075)), QFont.Weight.DemiBold))
        p.drawText(QRectF(r.left(), r.top() + side * 0.2, side, side * 0.12), Qt.AlignmentFlag.AlignCenter, self.top)
        p.setPen(theme.color("text"))
        p.setFont(theme.font(int(side * 0.26), QFont.Weight.Bold, tabular=True))
        p.drawText(QRectF(r.left(), r.top() + side * 0.3, side, side * 0.38), Qt.AlignmentFlag.AlignCenter, self.big)
        p.setPen(theme.color("muted"))
        p.setFont(theme.font(max(11, int(side * 0.065)), QFont.Weight.Normal))
        p.drawText(QRectF(r.left() + side * 0.15, r.top() + side * 0.66, side * 0.7, side * 0.14),
                   Qt.AlignmentFlag.AlignCenter, self.bottom)


class GaitTile(QWidget):
    """A gait metric with traffic-light status: name, value + unit, target range."""

    def __init__(self, title: str, unit: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title, self.unit = title, unit
        self.value = "–"
        self.status: str | None = None
        self.status_text = ""
        self.reference = ""
        self.setMinimumSize(120, 90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_value(self, value: str, status: str | None, status_text: str = "", reference: str = "") -> None:
        self.value, self.status, self.status_text, self.reference = value, status, status_text, reference
        self.update()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        s = min(r.height(), r.width() * 0.7)
        _rounded_bg(p, r, max(10.0, s * 0.08), theme.color("surface"))
        col = theme.color(STATUS_COLOR.get(self.status, "muted"))
        pad = max(10.0, s * 0.11)
        # status light
        d = s * 0.14
        p.setPen(Qt.PenStyle.NoPen)
        glow = QColor(col)
        glow.setAlpha(60)
        p.setBrush(glow)
        c = QPointF(r.right() - pad - d / 2, r.top() + pad + d / 2)
        p.drawEllipse(c, d * 0.85, d * 0.85)
        p.setBrush(col)
        p.drawEllipse(c, d / 2, d / 2)
        tf = theme.font(max(11, int(s * 0.11)), QFont.Weight.DemiBold)
        p.setFont(tf)
        p.setPen(theme.color("muted"))
        p.drawText(QRectF(r.left() + pad, r.top() + pad - 2, r.width() - 2 * pad - d * 1.5, tf.pixelSize() * 1.5),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title.upper())
        vf = theme.font(int(s * 0.34), QFont.Weight.Bold, tabular=True)
        fm = QFontMetricsF(vf)
        p.setFont(vf)
        p.setPen(theme.color("text"))
        base = r.top() + r.height() * 0.66
        p.drawText(QPointF(r.left() + pad, base), self.value)
        uf = theme.font(max(11, int(s * 0.12)), QFont.Weight.DemiBold)
        p.setFont(uf)
        p.setPen(theme.color("muted"))
        if self.unit == "°":
            p.setFont(theme.font(int(s * 0.22), QFont.Weight.Bold))
            p.drawText(QPointF(r.left() + pad + fm.horizontalAdvance(self.value) + s * 0.01, base - fm.ascent() * 0.45), "°")
        else:
            p.drawText(QPointF(r.left() + pad + fm.horizontalAdvance(self.value) + s * 0.04, base), self.unit)
        sf = theme.font(max(10, int(s * 0.095)), QFont.Weight.DemiBold)
        p.setFont(sf)
        p.setPen(col)
        line = self.status_text + (f"  ·  {self.reference}" if self.reference else "")
        p.drawText(QRectF(r.left() + pad, r.bottom() - pad - sf.pixelSize() * 1.4, r.width() - 2 * pad,
                          sf.pixelSize() * 1.4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(sf).elidedText(line, Qt.TextElideMode.ElideRight, r.width() - 2 * pad))


class PromptBanner(QWidget):
    """Big prompt across the video ('Ta laktatprov', 'Sätt bandet på 12,0 km/h'); can flash."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text = ""
        self.sub = ""
        self.icon = ""
        self.colour = "watch"
        self._on = True
        self._flash = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def show_prompt(self, text: str, sub: str = "", colour: str = "watch", icon: str = "",
                    flash: bool = False) -> None:
        changed = (text, sub, colour, flash) != (self.text, self.sub, self.colour, self._flash)
        self.text, self.sub, self.colour, self.icon = text, sub, colour, icon
        if changed:
            self._flash = flash
            self._on = True
            if flash:
                self._timer.start(600)
            else:
                self._timer.stop()
        self.show()
        self.raise_()
        self.update()

    def clear(self) -> None:
        self._timer.stop()
        self.text = ""
        self.hide()

    def _tick(self) -> None:
        self._on = not self._on
        self.update()

    def paintEvent(self, _e: Any) -> None:
        if not self.text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        c = theme.color(self.colour)
        bg = QColor(c) if self._on else QColor(c).darker(170)
        bg.setAlpha(240)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() * 0.2, r.height() * 0.2)
        p.fillPath(path, bg)
        fg = theme.color("ink") if self.colour in ("watch", "good") else QColor("#FFFFFF")
        h = r.height()
        x = r.left() + h * 0.35
        if self.icon:
            sz = int(h * 0.5)
            p.drawPixmap(int(x), int(r.center().y() - sz / 2), theme.pixmap(self.icon, fg.name(), sz))
            x += sz + h * 0.25
        f = theme.font(int(h * (0.36 if self.sub else 0.45)), QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(fg)
        if self.sub:
            p.drawText(QRectF(x, r.top() + h * 0.1, r.right() - x - h * 0.3, h * 0.52),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text)
            p.setFont(theme.font(int(h * 0.18), QFont.Weight.DemiBold))
            p.drawText(QRectF(x, r.top() + h * 0.58, r.right() - x - h * 0.3, h * 0.3),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.sub)
        else:
            p.drawText(QRectF(x, r.top(), r.right() - x, h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self.text)
