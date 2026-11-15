"""The pole stroke on the SkiErg: the counterpart of the ground contacts in ``pose.gait``.

From the side the near wrist's height swings once per stroke. The start is the highest point and the
finish the lowest, but the curve is flat there, so the times are taken where the hand's downward speed
is followed down to a threshold and extended to zero: the drive begins when the hand starts to move
down and ends when it stops.
The Concept2 monitor (PM5) reports drive and recovery time per stroke and is the reference for these
times on real hardware (docs/PROV.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import find_peaks

from lopanalys.pose.series import PoseSeries
from lopanalys.pose.types import Side

MIN_STROKE_S = 0.6            # two starts closer than this are one stroke (100 strokes/min)
MIN_TRAVEL = 0.15             # of the body height: smaller swings of the hand are not strokes
SPEED_THRESHOLD = 0.1         # of the stroke's peak downward speed: where the drive starts and ends


@dataclass
class Stroke:
    """One stroke, from the start of its drive to the start of the next. Rows are fractional."""
    i_top: float          # the hand at its highest
    i_start: float        # the drive begins
    i_bottom: float       # the hand at its lowest
    i_end: float          # the drive ends
    i_next: float         # the next drive begins
    t_start: float
    t_end: float
    t_next: float

    @property
    def drive_s(self) -> float:
        return self.t_end - self.t_start

    @property
    def recovery_s(self) -> float:
        return self.t_next - self.t_end

    @property
    def period_s(self) -> float:
        return self.t_next - self.t_start


@dataclass
class StrokeCycles:
    strokes: list[Stroke] = field(default_factory=list)
    travel_px: float = 0.0            # median height the hand travels per stroke
    missing_share: float = 0.0        # share of the frames where the wrist was not seen

    @property
    def rate_spm(self) -> float | None:
        p = [s.period_s for s in self.strokes]
        return float(60.0 / np.median(p)) if p else None

    @property
    def rhythm_cv(self) -> float:
        """Coefficient of variation of the stroke time (0 = perfectly even)."""
        p = np.array([s.period_s for s in self.strokes])
        return float(np.std(p) / np.mean(p)) if len(p) >= 3 else 0.0


def _edge(vel: np.ndarray, t: np.ndarray, ipk: int, limit: int, step: int) -> float | None:
    """The time where the hand starts (step -1) or stops (step +1) moving down, around the peak ``ipk``.

    The speed is followed from the peak out to ``SPEED_THRESHOLD`` and to three times that, and the
    line through those two points is extended to zero speed. Taking the threshold crossing itself
    would cut a tenth off every drive, because the hand speeds up and slows down gradually
    (the same idea as ``gait._extrapolate``).
    """
    def crossing(share: float) -> float | None:
        thr = share * vel[ipk]
        i = ipk
        while i != limit and vel[i + step] < thr:
            i += step
        if i == limit:
            return None
        return float(t[i] + (t[i + step] - t[i]) * (thr - vel[i]) / (vel[i + step] - vel[i]))

    t1, t3 = crossing(SPEED_THRESHOLD), crossing(3 * SPEED_THRESHOLD)
    if t1 is None:
        return None
    if t3 is None:
        return t1
    t0 = t1 - 0.5 * (t3 - t1)
    span = abs(t1 - t3)
    return float(min(max(t0, t1 - 2 * span), t1 + 2 * span))


def detect(s: PoseSeries, side: Side, stature_px: float | None = None) -> StrokeCycles:
    """Strokes from the near wrist's height (side view)."""
    y = s.side_lm(side, "wrist")[:, 1]
    ok = ~np.isnan(y)
    out = StrokeCycles(missing_share=float((~ok).mean()))
    if ok.sum() < 16:
        return out
    rows = np.arange(len(y), dtype=float)
    h = -np.interp(rows, np.flatnonzero(ok), y[ok])          # height, up is positive
    amp = float(np.percentile(h, 95) - np.percentile(h, 5))
    need = max(MIN_TRAVEL * stature_px if stature_px else 0.0, 0.4 * amp, 3.0)
    tops, _ = find_peaks(h, distance=max(2, int(MIN_STROKE_S * s.fps)), prominence=need)
    if len(tops) < 2:
        return out
    vel = np.gradient(h, s.t)                                 # negative while the hand moves down
    marks = []
    for k, a in enumerate(tops):
        b = tops[k + 1] if k + 1 < len(tops) else len(h) - 1
        if b - a < 3:
            continue
        lo = a + int(np.argmin(h[a:b + 1]))
        if lo - a < 2 or h[a] - h[lo] < need:
            continue
        ipk = a + int(np.argmin(vel[a:lo + 1]))
        start = _edge(vel, s.t, ipk, max(a - 3, 0), -1)
        end = _edge(vel, s.t, ipk, min(lo + 3, len(h) - 1), +1)
        if start is None or end is None:
            continue
        start, end = float(np.interp(start, s.t, rows)), float(np.interp(end, s.t, rows))
        marks.append((float(a), start, float(lo), end, h[a] - h[lo]))
    tt = lambda r: float(np.interp(r, rows, s.t))            # noqa: E731
    for (a, st, lo, en, _), nxt in zip(marks[:-1], marks[1:]):
        out.strokes.append(Stroke(a, st, lo, en, nxt[1], tt(st), tt(en), tt(nxt[1])))
    out.travel_px = float(np.median([m[4] for m in marks])) if marks else 0.0
    return out
