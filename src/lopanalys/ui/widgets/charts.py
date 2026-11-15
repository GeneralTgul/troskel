"""Light-weight charts painted with QPainter: live HR over time, lactate points and the
lactate curve with thresholds. No chart library, so the look matches the rest of the app."""
from __future__ import annotations

import math
from typing import Any, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from lopanalys.ui import theme
from lopanalys.ui.fmt import num


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Round tick values covering lo..hi."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=mag * 10)
    start = math.ceil(lo / step) * step
    out, v = [], start
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


class _Plot(QWidget):
    """Shared frame: card background, title, plot rect, axis mapping."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = title
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 120)
        self.background = True
        self.xr = (0.0, 1.0)
        self.yr = (0.0, 1.0)

    def unit(self) -> float:
        return max(1.0, min(self.height(), self.width() * 0.6))

    def frame(self, p: QPainter, left_axis: bool = True, bottom_axis: bool = True) -> QRectF:
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        s = self.unit()
        if self.background:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("surface"))
            p.drawRoundedRect(r, max(10.0, s * 0.06), max(10.0, s * 0.06))
        pad = max(10.0, s * 0.07)
        top = r.top() + pad
        if self.title:
            f = theme.font(max(12, int(s * 0.085)), QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(theme.color("muted"))
            p.drawText(QRectF(r.left() + pad, top - 2, r.width() - 2 * pad, f.pixelSize() * 1.5),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title.upper())
            top += f.pixelSize() * 1.8
        lf = self.axis_font()
        left = r.left() + pad + (lf.pixelSize() * 2.6 if left_axis else 0)
        right_pad = lf.pixelSize() * 2.6 if getattr(self, "right_axis", False) else 0
        bottom = r.bottom() - pad - (lf.pixelSize() * 1.6 if bottom_axis else 0)
        return QRectF(left, top, r.right() - pad - right_pad - left, bottom - top)

    def axis_font(self) -> QFont:
        return theme.font(max(10, int(self.unit() * 0.06)))

    def map(self, pr: QRectF, x: float, y: float) -> QPointF:
        (x0, x1), (y0, y1) = self.xr, self.yr
        fx = (x - x0) / (x1 - x0) if x1 != x0 else 0.5
        fy = (y - y0) / (y1 - y0) if y1 != y0 else 0.5
        return QPointF(pr.left() + fx * pr.width(), pr.bottom() - fy * pr.height())

    def grid(self, p: QPainter, pr: QRectF, yt: Sequence[float], xt: Sequence[float] = (),
             ydec: int = 0, xdec: int = 0, xlabel_fn: Any = None) -> None:
        p.setFont(self.axis_font())
        for v in yt:
            pt = self.map(pr, self.xr[0], v)
            p.setPen(QPen(theme.color("line"), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(pr.left(), pt.y()), QPointF(pr.right(), pt.y()))
            p.setPen(theme.color("muted"))
            p.drawText(QRectF(pr.left() - 60, pt.y() - 10, 54, 20),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, num(v, ydec))
        for v in xt:
            pt = self.map(pr, v, self.yr[0])
            p.setPen(theme.color("muted"))
            txt = xlabel_fn(v) if xlabel_fn else num(v, xdec)
            p.drawText(QRectF(pt.x() - 40, pr.bottom() + 4, 80, 22), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, txt)


class TimeChart(_Plot):
    """A live time series (e.g. HR) over the last ``window_s`` seconds, with optional zone bands."""

    def __init__(self, title: str = "", window_s: float = 600, colour: str = "fix",
                 parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.window_s = window_s
        self.colour = colour
        self.points: list[tuple[float, float]] = []
        self.bands: list[tuple[float, float, str]] = []
        self.y_min_span = 40.0
        # The top of the scale (the max HR on the TV): the axis ends there and says it with a line,
        # higher values still lift the top. None: the top follows the values.
        self.y_top: float | None = None
        self.y_top_label = "max"

    def add(self, t: float, y: float) -> None:
        if self.points and t < self.points[-1][0]:  # clock restarted (test started): new series
            self.points.clear()
        self.points.append((t, y))
        cut = t - self.window_s * 1.5
        if self.points and self.points[0][0] < cut:
            self.points = [pt for pt in self.points if pt[0] >= cut]
        self.update()

    def clear(self) -> None:
        self.points.clear()
        self.update()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pr = self.frame(p, bottom_axis=False)
        pts = self.points
        t1 = pts[-1][0] if pts else self.window_s
        t0 = max(0.0, t1 - self.window_s)
        vis = [pt for pt in pts if pt[0] >= t0]
        ys = [y for _, y in vis] or [60.0, 180.0]
        lo, hi = min(ys), max(ys)
        if hi - lo < self.y_min_span:
            mid = (hi + lo) / 2
            lo, hi = mid - self.y_min_span / 2, mid + self.y_min_span / 2
        lo, hi = lo - 4, hi + 4
        top = self.y_top if self.y_top and self.y_top > lo + 10 else None
        if top is not None:
            hi = max(top, max(ys) + 2)
        self.xr, self.yr = (t0, max(t1, t0 + 1)), (lo, hi)
        for b0, b1, col in self.bands:
            a, b = max(b0, lo), min(b1, hi)
            if b > a:
                c = theme.color(col, 22)
                p.fillRect(QRectF(pr.left(), self.map(pr, t0, b).y(), pr.width(),
                                  self.map(pr, t0, a).y() - self.map(pr, t0, b).y()), c)
        ticks = nice_ticks(lo, hi, 3)
        if top is not None:      # the max HR is the top tick; a round tick right under it would crowd it
            ticks = [v for v in ticks if top - v > (hi - lo) * 0.12]
        self.grid(p, pr, ticks)
        if top is not None:
            self.top_label(p, pr, top)
        if len(vis) < 2:
            return
        path = QPainterPath(self.map(pr, *vis[0]))
        for pt in vis[1:]:
            path.lineTo(self.map(pr, *pt))
        poly = QPolygonF([self.map(pr, vis[0][0], lo)] + [self.map(pr, *pt) for pt in vis]
                         + [self.map(pr, vis[-1][0], lo)])
        g = QLinearGradient(0, pr.top(), 0, pr.bottom())
        g.setColorAt(0, theme.color(self.colour, 70))
        g.setColorAt(1, theme.color(self.colour, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPolygon(poly)
        p.setPen(QPen(theme.color(self.colour), max(2.0, self.unit() * 0.012), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)  # an open path must not be filled
        p.drawPath(path)
        last = self.map(pr, *vis[-1])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(self.colour))
        p.drawEllipse(last, 5, 5)


    def top_label(self, p: QPainter, pr: QRectF, top: float) -> QRectF:
        """The line and the label of ``y_top`` ("199 max") at the top of the scale; returns where
        the label was drawn."""
        y = self.map(pr, self.xr[0], top).y()
        p.setPen(QPen(theme.color("fix", 160), 1.5, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(pr.left(), y), QPointF(pr.right(), y))
        f = self.axis_font()
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(theme.color("fix"))
        rect = QRectF(pr.left() - 60, y - 10, 54, 20)
        p.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, num(top, 0))
        lab = QRectF(pr.right() - pr.width() * 0.6, y + 2, pr.width() * 0.6 - 4, f.pixelSize() * 1.35)
        p.drawText(lab, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self.y_top_label)
        return rect


class LactateChart(_Plot):
    """Lactate (mmol/L) against speed: points, optional fitted curve, LT1/LT2 markers, HR points."""

    def __init__(self, title: str = "", live: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.live = live
        self.points: list[tuple[float, float]] = []
        self.hr_points: list[tuple[float, float]] = []
        self.curve: list[tuple[float, float]] = []
        self.lt1: tuple[float, float] | None = None
        self.lt2: tuple[float, float] | None = None
        self.labels = ("LT1", "LT2")
        self.x_unit = "km/h"
        self.x_decimals = 1          # decimals of a load in the threshold labels (0 for watt)
        self.others: list[tuple[str, str, list[tuple[float, float]]]] = []  # (legend, colour, points)
        self.right_axis = False
        self.legend = ""

    def set_data(self, points: list[tuple[float, float]], curve: list[tuple[float, float]] | None = None,
                 lt1: tuple[float, float] | None = None, lt2: tuple[float, float] | None = None,
                 hr_points: list[tuple[float, float]] | None = None) -> None:
        self.points = sorted(points)
        self.curve = curve or []
        self.lt1, self.lt2 = lt1, lt2
        self.hr_points = sorted(hr_points or [])
        self.right_axis = bool(self.hr_points) and not self.live
        self.update()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pr = self.frame(p)
        allp = self.points + self.curve + [pt for _, _, pts in self.others for pt in pts]
        xs = [x for x, _ in allp + (self.hr_points if not self.live else [])] or [8.0, 16.0]
        ys = [y for _, y in allp] or [0.0, 6.0]
        x0, x1 = min(xs), max(xs)
        if x1 - x0 < 2:
            x0, x1 = x0 - 1, x1 + 1
        span = x1 - x0
        self.xr = (x0 - span * 0.06, x1 + span * 0.06)
        self.yr = (0.0, max(6.0, max(ys) * 1.15))
        self.grid(p, pr, nice_ticks(0, self.yr[1], 4), nice_ticks(x0, x1, 5 if self.width() > 400 else 3),
                  ydec=0, xdec=0)
        s = self.unit()
        # HR on a hidden secondary scale (only the shape, labelled at the points)
        if self.hr_points and not self.live:
            hy = [h for _, h in self.hr_points]
            hlo, hhi = min(hy) - 10, max(hy) + 10
            p.setFont(self.axis_font())
            p.setPen(theme.color("fix", 200))
            from lopanalys.core.i18n import t as _t
            p.drawText(QRectF(pr.right() - 40, pr.top() - 22, 96, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       _t("ui.chart.hr_axis"))
            for hv in nice_ticks(hlo, hhi, 4):
                y = pr.bottom() - (hv - hlo) / (hhi - hlo) * pr.height()
                if pr.top() <= y <= pr.bottom():
                    p.drawText(QRectF(pr.right() + 6, y - 10, 50, 20),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, num(hv, 0))
            path = QPainterPath()
            for i, (x, h) in enumerate(self.hr_points):
                pt = QPointF(self.map(pr, x, 0).x(), pr.bottom() - (h - hlo) / (hhi - hlo) * pr.height())
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setPen(QPen(theme.color("fix", 150), max(1.5, s * 0.006), Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        for side, (lt, name, col) in enumerate(((self.lt1, self.labels[0], "accent2"), (self.lt2, self.labels[1], "watch"))):
            if lt is None:
                continue
            pt = self.map(pr, lt[0], lt[1])
            p.setPen(QPen(theme.color(col), max(1.5, s * 0.006), Qt.PenStyle.DashLine))
            p.drawLine(QPointF(pt.x(), pr.top() + 18), QPointF(pt.x(), pr.bottom()))
            f = theme.font(max(11, int(s * 0.06)), QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(theme.color(col))
            # LT1 label to the left of its line, LT2 to the right, so close thresholds never collide
            box = QRectF(pt.x() - 146, pr.top() - 4, 140, 20) if side == 0 else QRectF(pt.x() + 6, pr.top() - 4, 140, 20)
            align = Qt.AlignmentFlag.AlignRight if side == 0 else Qt.AlignmentFlag.AlignLeft
            p.drawText(box, align | Qt.AlignmentFlag.AlignVCenter, f"{name} {num(lt[0], self.x_decimals)}")
        if len(self.curve) > 1:
            path = QPainterPath(self.map(pr, *self.curve[0]))
            for c in self.curve[1:]:
                path.lineTo(self.map(pr, *c))
            p.setPen(QPen(theme.color("accent"), max(2.0, s * 0.012), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        elif len(self.points) > 1:
            path = QPainterPath(self.map(pr, *self.points[0]))
            for c in self.points[1:]:
                path.lineTo(self.map(pr, *c))
            p.setPen(QPen(theme.color("accent"), max(2.0, s * 0.012)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        rad = max(4.0, s * 0.028)
        legend = [(self.legend, "accent")] if self.others and self.legend else []
        for name, col, pts in self.others:
            if len(pts) > 1:
                path = QPainterPath(self.map(pr, *pts[0]))
                for c in pts[1:]:
                    path.lineTo(self.map(pr, *c))
                p.setPen(QPen(theme.color(col), max(2.0, s * 0.01), Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(col))
            for c in pts:
                p.drawEllipse(self.map(pr, *c), rad * 0.8, rad * 0.8)
            legend.append((name, col))
        if legend:
            lf = theme.font(max(11, int(s * 0.055)), QFont.Weight.DemiBold)
            p.setFont(lf)
            y = pr.top() + 4
            for name, col in legend:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.color(col))
                p.drawRoundedRect(QRectF(pr.left() + 12, y + lf.pixelSize() * 0.35, 14, 6), 3, 3)
                p.setPen(theme.color("text"))
                p.drawText(QPointF(pr.left() + 32, y + lf.pixelSize()), name)
                y += lf.pixelSize() * 1.6
        for x, y in self.points:
            pt = self.map(pr, x, y)
            p.setPen(QPen(theme.color("ink"), 2))
            p.setBrush(theme.color("text"))
            p.drawEllipse(pt, rad, rad)
            if self.live:
                p.setPen(theme.color("text"))
                p.setFont(theme.font(max(11, int(s * 0.07)), QFont.Weight.Bold))
                p.drawText(QRectF(pt.x() - 40, pt.y() - rad - 26, 80, 22), Qt.AlignmentFlag.AlignCenter, num(y))
        if not self.points:
            p.setPen(theme.color("muted"))
            p.setFont(theme.font(max(12, int(s * 0.07))))
            from lopanalys.core.i18n import t
            p.drawText(pr, Qt.AlignmentFlag.AlignCenter, t("ui.chart.no_lactate"))


class HrSpeedChart(_Plot):
    """Stable heart rate per step against speed, with the fitted line (the zones test without lactate).

    The line is solid over the measured steps and dashed where it is extended; a step whose heart
    rate had not settled is drawn hollow. Horizontal bands show the heart-rate zones."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.points: list[tuple[float, float, bool]] = []      # (speed, hr, settled)
        self.line: list[tuple[float, float, bool]] = []        # (speed, hr, extrapolated end)
        self.bands: list[tuple[float, float, str, str]] = []   # (hr low, hr high, colour, label)
        self.max_hr: float | None = None
        self.empty_text = ""

    def set_data(self, points: Sequence[tuple[float, float, bool]], line: Sequence[tuple[float, float, bool]] = (),
                 bands: Sequence[tuple[float, float, str, str]] = (), max_hr: float | None = None) -> None:
        self.points, self.line, self.bands, self.max_hr = sorted(points), list(line), list(bands), max_hr
        self.update()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pr = self.frame(p)
        s = self.unit()
        if not self.points:
            p.setPen(theme.color("muted"))
            p.setFont(theme.font(max(12, int(s * 0.07))))
            p.drawText(pr, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.empty_text)
            return
        xs = [x for x, *_ in self.points] + [x for x, *_ in self.line]
        ys = [y for _, y, _ in self.points] + [y for _, y, _ in self.line]
        x0, x1 = min(xs), max(xs)
        y0 = min(ys) - 8
        y1 = max(ys + ([self.max_hr] if self.max_hr else [])) + 8
        span = max(x1 - x0, 1.0)
        self.xr, self.yr = (x0 - span * 0.05, x1 + span * 0.05), (y0, y1)
        p.setClipRect(pr.adjusted(0, -1, 0, 1))
        for lo, hi, col, _name in self.bands:
            a, b = max(lo, y0), min(hi, y1)
            if b > a:
                top, bottom = self.map(pr, x0, b).y(), self.map(pr, x0, a).y()
                p.fillRect(QRectF(pr.left(), top, pr.width(), bottom - top), QColor(theme.color(col).red(),
                           theme.color(col).green(), theme.color(col).blue(), 34))
        p.setClipping(False)
        self.grid(p, pr, nice_ticks(y0, y1, 5), nice_ticks(x0, x1, 6 if self.width() > 400 else 3))
        p.setFont(self.axis_font())
        for lo, hi, col, name in self.bands:
            mid = (max(lo, y0) + min(hi, y1)) / 2
            if min(hi, y1) - max(lo, y0) > (y1 - y0) * 0.06:
                p.setPen(theme.color(col))
                p.drawText(QRectF(pr.right() - 64, self.map(pr, x0, mid).y() - 10, 60, 20),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, name)
        if self.max_hr:
            y = self.map(pr, x0, self.max_hr).y()
            p.setPen(QPen(theme.color("fix"), max(1.5, s * 0.005), Qt.PenStyle.DashLine))
            p.drawLine(QPointF(pr.left(), y), QPointF(pr.right(), y))
            from lopanalys.core.i18n import t
            p.drawText(QRectF(pr.left() + 8, y - 22, 260, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       t("ui.chart.max_hr", n=num(self.max_hr, 0)))
        width = max(2.0, s * 0.011)
        for (xa, ya, ea), (xb, yb, eb) in zip(self.line, self.line[1:]):
            dashed = ea or eb
            p.setPen(QPen(theme.color("accent"), width * (0.75 if dashed else 1.0),
                          Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(self.map(pr, xa, ya), self.map(pr, xb, yb))
        rad = max(5.0, s * 0.026)
        p.setFont(theme.font(max(11, int(s * 0.055)), QFont.Weight.Bold))
        for x, y, settled in self.points:
            pt = self.map(pr, x, y)
            p.setPen(QPen(theme.color("text" if settled else "watch"), 2))
            p.setBrush(theme.color("text") if settled else theme.color("surface"))
            p.drawEllipse(pt, rad, rad)
            p.setPen(theme.color("text" if settled else "watch"))
            p.drawText(QRectF(pt.x() - 40, pt.y() - rad - 24, 80, 20), Qt.AlignmentFlag.AlignCenter, num(y, 0))
