"""The pedal revolution: the cycling counterpart of the ground contacts in ``pose.gait``.

From the side the near ankle moves in an almost circular path around the bottom bracket. A circle is
fitted to that path, and the crank angle is the direction from the centre to the ankle: 0 deg at the
top (top dead centre, TDC), 90 deg forward (3 o'clock), 180 deg at the bottom (BDC). Unwrapped over
time it gives every revolution and the cadence. Pedalling always turns the foot forward over the top,
so the direction of rotation also tells which way the cyclist faces.

From the front there is no circle: there a revolution runs between two highest points of the knee.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import find_peaks

from lopanalys.pose.series import PoseSeries
from lopanalys.pose.types import Side


@dataclass
class Revolution:
    """One turn of the crank, TDC to TDC. Rows are fractional indices into the series."""
    i_top: float
    i_forward: float      # 90 deg, 3 o'clock
    i_bottom: float       # 180 deg
    i_back: float         # 270 deg
    i_end: float          # the next TDC
    t_top: float
    t_end: float
    side: Side | None = None

    @property
    def period_s(self) -> float:
        return self.t_end - self.t_top

    def rows(self, n: int) -> np.ndarray:
        """The whole rows inside the revolution."""
        return np.arange(max(0, int(np.ceil(self.i_top))), min(n, int(np.floor(self.i_end)) + 1))


@dataclass
class PedalCycles:
    revolutions: list[Revolution] = field(default_factory=list)
    centre: tuple[float, float] | None = None     # the fitted circle, about the bottom bracket
    radius_px: float | None = None
    residual: float = 1.0          # RMS distance from the circle, as a share of the radius
    facing: int = 1                # from the direction of rotation
    forward_share: float = 0.0     # share of the frames where the crank angle moves forward
    missing_share: float = 0.0     # share of the frames where the ankle was not seen
    theta: np.ndarray | None = None    # unwrapped crank angle per row, degrees
    method: str = "circle"         # "circle" (side view) | "knee" (front view)

    @property
    def cadence_rpm(self) -> float | None:
        p = [r.period_s for r in self.revolutions if r.side in (None, self.revolutions[0].side)]
        return float(60.0 / np.median(p)) if p else None

    @property
    def cadence_spread(self) -> float:
        """Coefficient of variation of the revolution time (0 = perfectly even pedalling)."""
        p = np.array([r.period_s for r in self.revolutions if r.side in (None, self.revolutions[0].side)])
        return float(np.std(p) / np.mean(p)) if len(p) >= 3 else 0.0


def fit_circle(xy: np.ndarray) -> tuple[float, float, float, float]:
    """Least-squares circle (Kasa) through the points, refitted once without outliers.

    Returns (cx, cy, radius, rms residual / radius)."""
    pts = xy[~np.isnan(xy).any(axis=1)]
    if len(pts) < 8:
        raise ValueError("too few points for a circle")

    def fit(p: np.ndarray) -> tuple[float, float, float, np.ndarray]:
        a = np.c_[2 * p[:, 0], 2 * p[:, 1], np.ones(len(p))]
        b = (p ** 2).sum(axis=1)
        (cx, cy, c), *_ = np.linalg.lstsq(a, b, rcond=None)
        r = float(np.sqrt(max(c + cx * cx + cy * cy, 1e-9)))
        return float(cx), float(cy), r, np.hypot(p[:, 0] - cx, p[:, 1] - cy) - r

    cx, cy, r, res = fit(pts)
    mad = float(np.median(np.abs(res - np.median(res)))) or 1e-9
    keep = np.abs(res) < 4.5 * 1.4826 * mad + 1e-6
    if 8 <= keep.sum() < len(pts):
        cx, cy, r, res = fit(pts[keep])
    return cx, cy, r, float(np.sqrt(np.mean(res ** 2)) / r)


def _interp_nan(y: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(y)
    if ok.all() or not ok.any():
        return y
    return np.interp(np.arange(len(y)), np.flatnonzero(ok), y[ok])


def detect_side(s: PoseSeries, side: Side) -> PedalCycles:
    """Revolutions from the near ankle's circle (side view)."""
    ankle = s.side_lm(side, "ankle")
    missing = float(np.isnan(ankle[:, 0]).mean())
    out = PedalCycles(missing_share=missing)
    if (~np.isnan(ankle[:, 0])).sum() < 16:
        return out
    try:
        cx, cy, r, res = fit_circle(ankle)
    except ValueError:
        return out
    x, y = _interp_nan(ankle[:, 0]), _interp_nan(ankle[:, 1])
    raw = np.unwrap(np.arctan2(x - cx, -(y - cy)))       # 0 at the top, grows clockwise on screen
    d = np.diff(raw)
    rot = 1 if np.median(d) >= 0 else -1
    theta = np.degrees(rot * raw)
    out.centre, out.radius_px, out.residual, out.facing, out.theta = (cx, cy), r, res, rot, theta
    out.forward_share = float((rot * d > 0).mean()) if len(d) else 0.0
    mono = np.maximum.accumulate(theta)
    rows = np.arange(len(theta), dtype=float)

    def row_at(angle: float) -> float | None:
        if angle < mono[0] or angle > mono[-1]:
            return None
        return float(np.interp(angle, mono, rows))

    k0, k1 = int(np.ceil(mono[0] / 360.0)), int(np.floor(mono[-1] / 360.0))
    for k in range(k0, k1):
        marks = [row_at(360.0 * k + a) for a in (0.0, 90.0, 180.0, 270.0, 360.0)]
        if any(m is None for m in marks):
            continue
        i0, i90, i180, i270, i1 = marks       # type: ignore[misc]
        t0, t1 = float(np.interp(i0, rows, s.t)), float(np.interp(i1, rows, s.t))
        if t1 - t0 < 0.25:        # faster than 240 rpm: a glitch, not a revolution
            continue
        out.revolutions.append(Revolution(i0, i90, i180, i270, i1, t0, t1))
    return out


def detect_front(s: PoseSeries) -> PedalCycles:
    """Revolutions per leg from the knee's height (front view): highest point = that leg's TDC."""
    out = PedalCycles(method="knee", facing=0)
    fps = s.fps
    rows = np.arange(len(s), dtype=float)
    miss = []
    for side in (Side.LEFT, Side.RIGHT):
        y = s.side_lm(side, "knee")[:, 1]
        miss.append(float(np.isnan(y).mean()))
        if (~np.isnan(y)).sum() < 16:
            continue
        y = _interp_nan(y)
        amp = float(np.percentile(y, 95) - np.percentile(y, 5))
        if amp < 2.0:
            continue
        tops, _ = find_peaks(-y, distance=max(2, int(0.3 * fps)), prominence=0.4 * amp)
        for a, b in zip(tops[:-1], tops[1:]):
            lo = a + int(np.argmax(y[a:b + 1]))           # lowest knee = that leg's BDC
            t0, t1 = float(np.interp(a, rows, s.t)), float(np.interp(b, rows, s.t))
            if not 0.25 <= t1 - t0 <= 3.0:
                continue
            out.revolutions.append(Revolution(float(a), (a + lo) / 2, float(lo), (lo + b) / 2, float(b), t0, t1, side))
    out.missing_share = float(np.mean(miss)) if miss else 1.0
    out.forward_share = 1.0 if out.revolutions else 0.0
    out.residual = 0.0
    return out
