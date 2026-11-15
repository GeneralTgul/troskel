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
            sf = QFont(tf)
            sw = QFontMetricsF(sf).horizontalAdvance(self.sub)
            if sw > avail:          # a long line under the value shrinks rather than being cut off
                sf.setPixelSize(max(8, int(sf.pixelSize() * avail / sw)))
            p.setFont(sf)
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


def wrap_text(text: str, fm: QFontMetricsF, width: float) -> list[str]:
    """Greedy word wrap of ``text`` into lines no wider than ``width`` (a word wider than the width
    gets a line of its own and is left too wide: the caller then picks a smaller font)."""
    lines: list[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}" if cur else word
        if cur and fm.horizontalAdvance(cand) > width:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def fit_text(text: str, weight: QFont.Weight, width: float, height: float, start_px: int,
             max_lines: int = 2, min_px: int = 9, line_h: float = 1.18) -> tuple[QFont, list[str]]:
    """The largest font (from ``start_px`` down) at which ``text`` fits in ``width`` x ``height``:
    one line if it can at a readable size, else wrapped into up to ``max_lines``. Returns the font and
    the lines; every line is then at most ``width`` wide (the prompt is never clipped)."""
    px = max(min_px, int(start_px))
    while True:
        f = theme.font(px, weight)
        fm = QFontMetricsF(f)
        for n in range(1, max_lines + 1):
            lines = [text] if n == 1 else wrap_text(text, fm, width)
            if (len(lines) <= n and len(lines) * px * line_h <= height + 0.5
                    and all(fm.horizontalAdvance(ln) <= width for ln in lines)):
                # one line only at 85 % of the size that would fill the height, else two lines
                if n == 1 or len(lines) > 1:
                    return f, lines
        if px <= min_px:
            return f, wrap_text(text, fm, width) or [text]
        px = max(min_px, int(px * 0.93))


class PromptBanner(QWidget):
    """Big prompt across the video ('Ta laktatprov', 'Sätt bandet på 5:00 min/km'); can flash.

    ``kicker`` is a small line above the prompt ("NÄSTA STEG OM 12 S"), ``sub`` a smaller line
    below it. Every line is fitted to the banner: the font shrinks, and the prompt wraps onto a
    second line, before anything would be cut off (``layout()``, which the tests read too)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text = ""
        self.sub = ""
        self.kicker = ""
        self.icon = ""
        self.colour = "watch"
        self._on = True
        self._flash = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def show_prompt(self, text: str, sub: str = "", colour: str = "watch", icon: str = "",
                    flash: bool = False, kicker: str = "") -> None:
        changed = (text, sub, colour, flash) != (self.text, self.sub, self.colour, self._flash)
        self.text, self.sub, self.colour, self.icon, self.kicker = text, sub, colour, icon, kicker
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
        self.kicker = ""
        self.hide()

    def _tick(self) -> None:
        self._on = not self._on
        self.update()

    def layout(self) -> dict[str, Any]:
        """Where everything goes: {"box", "icon", "lines": [(font, text, QRectF)]}. Each line's
        rectangle lies inside the box and is at least as wide as its text."""
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        h = r.height()
        pad = max(8.0, min(h * 0.2, r.width() * 0.035))
        x = r.left() + pad
        icon = None
        if self.icon:
            sz = max(16.0, min(h * 0.42, r.width() * 0.09))
            icon = QRectF(x, r.center().y() - sz / 2, sz, sz)
            x += sz + pad * 0.8
        w = max(10.0, r.right() - pad - x)
        inner = h - 2 * pad * 0.7
        # the share of the height for each part; the prompt gets what is left
        k_h = inner * 0.24 if self.kicker else 0.0
        s_h = inner * 0.3 if self.sub else 0.0
        t_h = inner - k_h - s_h
        out: list[tuple[QFont, str, QRectF]] = []
        parts = []
        if self.kicker:
            parts.append((self.kicker.upper(), QFont.Weight.Bold, k_h, 1))
        parts.append((self.text, QFont.Weight.Bold, t_h, 2))
        if self.sub:
            parts.append((self.sub, QFont.Weight.DemiBold, s_h, 2))
        fitted = []
        for text, weight, part_h, n in parts:
            f, lines = fit_text(text, weight, w, part_h, int(part_h / 1.18 if n == 1 or part_h < 40 else part_h / 1.3),
                                max_lines=n)
            fitted.append((f, lines))
        # centre the block vertically
        line_h = [f.pixelSize() * 1.18 for f, _ in fitted]
        total = sum(lh * len(lines) for lh, (_, lines) in zip(line_h, fitted))
        y = r.top() + max(pad * 0.4, (h - total) / 2)
        for (f, lines), lh in zip(fitted, line_h):
            for ln in lines:
                out.append((f, ln, QRectF(x, y, w, lh)))
                y += lh
        return {"box": r, "icon": icon, "lines": out}

    def paintEvent(self, _e: Any) -> None:
        if not self.text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lay = self.layout()
        r = lay["box"]
        c = theme.color(self.colour)
        bg = QColor(c) if self._on else QColor(c).darker(170)
        bg.setAlpha(238)
        path = QPainterPath()
        rad = min(r.height() * 0.18, 22.0)
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, bg)
        fg = theme.color("ink") if self.colour in ("watch", "good") else QColor("#FFFFFF")
        if lay["icon"] is not None:
            ir = lay["icon"]
            ring = QColor(fg)
            ring.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ring)
            p.drawEllipse(ir.adjusted(-ir.width() * 0.18, -ir.width() * 0.18, ir.width() * 0.18, ir.width() * 0.18))
            sz = int(ir.width() * 0.72)
            p.drawPixmap(int(ir.center().x() - sz / 2), int(ir.center().y() - sz / 2), theme.pixmap(self.icon, fg.name(), sz))
        kick = self.kicker.upper() if self.kicker else None
        for f, text, rect in lay["lines"]:
            p.setFont(f)
            col = QColor(fg)
            if kick is not None and text == kick:
                col.setAlpha(200)
            p.setPen(col)
            p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
