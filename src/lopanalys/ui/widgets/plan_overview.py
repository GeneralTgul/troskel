"""The training plan's overview on screen: the blocks as a band over one stacked bar per week.

The same picture as the first page of the printout (report.plan_builder.overview_svg), painted with
the program's own colours so that it reads on the dark surface and can be photographed without a
web view. Minutes per zone in the watch's colours; test, race and easy weeks are marked below.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from lopanalys.core.i18n import t
from lopanalys.plan.models import ZONES
from lopanalys.plan.text import hours
from lopanalys.ui import theme

BLOCK_ALPHA = {"base": 60, "build": 110, "peak": 170, "taper": 40}


class PlanChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan: Any = None
        self.setMinimumSize(360, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_plan(self, plan: Any) -> None:
        self.plan = plan
        self.update()

    def _mark(self, wk: Any) -> tuple[str, bool]:
        """The word under a week and whether it is strong: a race beats a test beats an easy week."""
        races = [w.goal for w in wk.workouts if w.goal]
        goal = self.plan.goal
        if races:
            final = goal is not None and any(g.date == goal.date and g.priority == "A" for g in races)
            return t("plan.pdf.mark.goal" if final else "plan.pdf.mark.race"), True
        if wk.kind == "test":
            return t("plan.pdf.mark.test"), True
        return (t("plan.pdf.mark.easy"), False) if wk.kind == "easy" else ("", False)

    def paintEvent(self, _e: Any) -> None:  # noqa: N802 (Qt)
        plan = self.plan
        if plan is None or not plan.weeks:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        weeks, n = plan.weeks, len(plan.weeks)
        dense = n > 16
        left, right = 44.0, 6.0
        band_h, top = 30.0, 58.0
        bottom = self.height() - 44.0
        col = (self.width() - left - right) / n
        bar_w = min(col * 0.6, 46.0)
        peak = max([wk.minutes for wk in weeks] + [60])
        step = 60 if peak <= 360 else 120
        y_max = -(-peak // step) * step
        if y_max - peak < 0.12 * y_max:
            y_max += step / 2

        def y(minutes: float) -> float:
            return bottom - (bottom - top) * minutes / y_max

        small, bold = theme.font(12), theme.font(12, QFont.Weight.DemiBold)
        # blocks
        for b in plan.blocks:
            r = QRectF(left + (b.first_week - 1) * col + 2, 0, b.weeks * col - 4, band_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color("accent", BLOCK_ALPHA.get(b.kind, 60)))
            p.drawRoundedRect(r, 7, 7)
            p.setFont(bold)
            p.setPen(theme.color("text"))
            fm = p.fontMetrics()
            for text in (t("plan.pdf.block_weeks", name=b.name, weeks=b.weeks), b.name, t(f"plan.block_short.{b.kind}")):
                if fm.horizontalAdvance(text) + 6 <= r.width():
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
                    break
        # grid and hours
        p.setFont(small)
        m = 0
        while m <= y_max:
            p.setPen(theme.color("muted" if m == 0 else "line"))
            p.drawLine(int(left), int(y(m)), int(self.width() - right), int(y(m)))
            if m:
                p.setPen(theme.color("muted"))
                p.drawText(QRectF(0, y(m) - 9, left - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           t("plan.pdf.hours_axis", h=m // 60))
            m += step
        # one bar per week
        for i, wk in enumerate(weeks):
            cx = left + (i + 0.5) * col
            zm = wk.zone_minutes()
            segs = [(theme.zone_color(k), zm.get(z, 0.0)) for k, z in enumerate(ZONES)]
            segs.append((theme.TOKENS["muted"], max(0.0, wk.minutes - sum(zm.values()))))   # the test: no zone
            total = sum(mm for _, mm in segs)
            if total:
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(cx - bar_w / 2, y(total), bar_w, bottom - y(total) + 6), 5, 5)
                p.save()
                p.setClipPath(clip)
                p.setClipRect(QRectF(0, 0, self.width(), bottom), Qt.ClipOperation.IntersectClip)
                acc = 0.0
                p.setPen(Qt.PenStyle.NoPen)
                for colour, mm in segs:
                    if mm >= 1:
                        p.setBrush(QColor(colour))
                        p.drawRect(QRectF(cx - bar_w / 2, y(acc + mm), bar_w, y(acc) - y(acc + mm) - (1.5 if acc else 0)))
                        acc += mm
                p.restore()
                if not dense:
                    p.setFont(small)
                    p.setPen(theme.color("text"))
                    p.drawText(QRectF(cx - col / 2, y(total) - 19, col, 16), Qt.AlignmentFlag.AlignCenter, hours(total))
            if not dense or wk.index == 1 or wk.index % 4 == 0:
                p.setFont(bold)
                p.setPen(theme.color("text"))
                p.drawText(QRectF(cx - col / 2, bottom + 5, col, 16), Qt.AlignmentFlag.AlignCenter, str(wk.index))
            mark, strong = self._mark(wk)
            if mark and (strong or not dense):
                p.setFont(bold if strong else small)
                p.setPen(theme.color("text" if strong else "muted"))
                p.drawText(QRectF(cx - col, bottom + 23, 2 * col, 16), Qt.AlignmentFlag.AlignCenter,
                           mark if p.fontMetrics().horizontalAdvance(mark) <= 2 * col - 4 else mark[:1])
        p.setFont(small)
        p.setPen(theme.color("muted"))
        p.drawText(QRectF(0, bottom + 5, left - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   t("plan.pdf.week_axis"))
        p.end()
