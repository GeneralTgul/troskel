"""Cleaning and smoothing of landmark tracks.

Two kinds of smoothing, because they serve different needs:

* ``OneEuroFilter`` / ``PoseSmoother``: causal, adapts its cutoff to the speed of the movement
  (Casiez, Roussel & Vogel 2012, "1 Euro Filter", CHI). Used for the live skeleton on the TV, where
  a filter must not look into the future and jitter is more disturbing than a little lag.
* ``prepare()``: the analysis pipeline for a whole clip or the live window. It fixes left/right leg
  label swaps, removes spikes (Hampel filter), fills short gaps, resamples to an even time grid
  and low-pass filters forward and backward (zero phase, Butterworth). Zero phase matters: a
  causal filter would shift every gait event later by its lag.

The 8-10 Hz cutoff follows common practice for running kinematics (e.g. Winter, "Biomechanics and
Motor Control of Human Movement", 4th ed., ch. 3; residual analysis typically gives 6-12 Hz for
the lower limb in running).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import butter, filtfilt, medfilt

from lopanalys.pose.series import N_LANDMARKS, PoseSeries
from lopanalys.pose.types import LM, Pose

# Landmarks of each leg (left, right), swapped together when MediaPipe mixes up the legs.
_LEG_PARTS = ("knee", "ankle", "heel", "foot")
LEFT_LEG = [LM["l_" + p] for p in _LEG_PARTS]
RIGHT_LEG = [LM["r_" + p] for p in _LEG_PARTS]


# --- One Euro filter (live) -----------------------------------------------------------------------

class OneEuroFilter:
    """Vectorised One Euro filter. ``x`` may be any shape; NaN elements reset that element."""

    def __init__(self, min_cutoff: float = 1.5, beta: float = 0.02, d_cutoff: float = 1.0) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x: np.ndarray | None = None
        self._dx: np.ndarray | None = None
        self._t: float | None = None

    @staticmethod
    def _alpha(cutoff: np.ndarray | float, dt: float) -> np.ndarray | float:
        tau = 1.0 / (2 * math.pi * np.asarray(cutoff))
        return 1.0 / (1.0 + tau / dt)

    def reset(self) -> None:
        self._x = self._dx = self._t = None

    def __call__(self, t: float, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, float)
        if self._x is None or self._t is None or t <= self._t or self._x.shape != x.shape:
            self._x, self._dx, self._t = x.copy(), np.zeros_like(x), t
            return x.copy()
        dt = t - self._t
        self._t = t
        prev = np.where(np.isnan(self._x), x, self._x)
        dx = (x - prev) / dt
        dx = np.where(np.isnan(dx), 0.0, dx)
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * np.abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * prev
        x_hat = np.where(np.isnan(x), np.nan, x_hat)
        self._x = np.where(np.isnan(x), self._x, x_hat)
        self._dx = dx_hat
        return x_hat


class PoseSmoother:
    """One Euro smoothing of whole poses for the live overlay.

    ``beta`` is scaled by the image height so the same settings work at 720p and 4K.
    """

    def __init__(self, min_cutoff: float = 1.5, beta_per_height: float = 0.7) -> None:
        self._min_cutoff = min_cutoff
        self._beta_h = beta_per_height
        self._f: OneEuroFilter | None = None

    def reset(self) -> None:
        self._f = None

    def __call__(self, pose: Pose) -> Pose:
        if self._f is None:
            self._f = OneEuroFilter(self._min_cutoff, self._beta_h / max(pose.height, 1))
        xy = self._f(pose.t, pose.xy)
        return Pose(pose.t, pose.frame_index, xy, pose.visibility, pose.width, pose.height)


# --- the analysis pipeline -----------------------------------------------------------------------

def mask_low_visibility(s: PoseSeries, min_visibility: float = 0.2) -> PoseSeries:
    """Set landmarks below ``min_visibility`` to NaN. Kept low: the far leg in a side view often
    scores 0.3-0.6 but its position is still useful."""
    out = s.copy()
    out.xy[out.vis < min_visibility] = np.nan
    return out


_FEET = ((LM["l_heel"], LM["l_foot"]), (LM["r_heel"], LM["r_foot"]))


def foot_low_y(xy: np.ndarray, side: int) -> np.ndarray:
    """Image y of the lowest point (heel or toe) of the left (0) or right (1) foot, per frame."""
    h, f = _FEET[side]
    return np.fmax(xy[:, h, 1], xy[:, f, 1])


def belt_level(low: np.ndarray) -> float:
    """Belt line of one foot: the median of its lowest 20 % of positions (NaN when too few)."""
    v = low[~np.isnan(low)]
    if v.size < 5:
        return float("nan")
    return float(np.median(v[v >= np.percentile(v, 80)]))


def _swap_costs(t: np.ndarray, xy: np.ndarray, huber: float) -> np.ndarray:
    """C[i, a, b, c]: how badly frame i fits a constant-velocity prediction from frames i-2, i-1
    when the legs of frames i-2, i-1, i are taken swapped (1) or not (0)."""
    n = len(t)
    legs = np.stack([xy[:, LEFT_LEG], xy[:, RIGHT_LEG]], axis=1)      # (n, 2, 4, 2)
    cand = np.stack([legs, legs[:, ::-1]], axis=1)                     # (n, swap, leg, part, xy)
    C = np.zeros((n, 2, 2, 2))
    if n < 3:
        return C
    dt1, dt0 = t[2:] - t[1:-1], t[1:-1] - t[:-2]
    r = np.clip(np.where(dt0 > 0, dt1 / np.where(dt0 > 0, dt0, 1), 1.0), 0.0, 2.0)[:, None, None, None]
    for a in (0, 1):
        for b in (0, 1):
            p2, p1 = cand[:-2, a], cand[1:-1, b]
            pred = p1 + (p1 - p2) * r
            pred = np.where(np.isnan(pred), p1, pred)
            for c in (0, 1):
                d = np.linalg.norm(cand[2:, c] - pred, axis=-1).reshape(n - 2, -1)
                ok = ~np.isnan(d)
                d = np.where(d < huber, d, huber + 0.3 * (d - huber))      # an abrupt stop is not a swap
                cnt = ok.sum(axis=1)
                C[2:, a, b, c] = np.where(cnt >= 2, np.nansum(np.where(ok, d, 0), axis=1) * d.shape[1] / np.maximum(cnt, 1), 0.0)
    return C


def fix_leg_swaps(s: PoseSeries, label_cost: float = 0.01, level_cost: float = 4.0) -> PoseSeries:
    """Undo frames where the pose model swapped the left and right leg (near and far leg in a side view).

    MediaPipe mixes up the legs mostly when they cross, for one to a few frames (real hardware 2026-11-14,
    13 % of the frames). The swaps are found jointly for the whole clip (Viterbi over "kept" or
    "swapped" per frame), from three costs:

    * motion: each leg must follow a constant-velocity prediction from the two previous frames. When
      the legs cross they are at the same place but move in opposite directions (the stance foot
      backwards with the belt, the swing foot forwards), so the velocity tells them apart;
    * the model's own labels: a swap costs a little (``label_cost`` x image height per frame), so a long
      stretch is not flipped without reason;
    * the belt line: from the side the near and far foot stand on different lines in the image (the far
      one higher). A foot carrying the label of the higher line but standing clearly below the middle of
      the two lines is the other foot (``level_cost`` per pixel).
    """
    out = s.copy()
    xy, vis, n, H = out.xy, out.vis, len(s), float(s.height)
    if n < 3:
        out.meta["leg_swaps_fixed"] = 0
        return out
    C = _swap_costs(s.t, xy, 0.03 * H)
    unary = np.zeros((n, 2))
    unary[:, 1] = label_cost * H
    low = [foot_low_y(xy, 0), foot_low_y(xy, 1)]
    g = [belt_level(low[0]), belt_level(low[1])]
    if not np.isnan(g).any() and abs(g[0] - g[1]) > 0.008 * H:
        mid, margin = (g[0] + g[1]) / 2, 0.15 * abs(g[0] - g[1])
        hi = 0 if g[0] < g[1] else 1                 # the label whose belt line is higher in the image
        for state in (0, 1):
            raw_side = hi if state == 0 else 1 - hi  # the raw foot that carries the "hi" label
            unary[:, state] += level_cost * np.nan_to_num(np.maximum(0.0, low[raw_side] - (mid + margin)))
    # Viterbi over pairs (state i-1, state i)
    cost = (unary[0][:, None] + unary[1][None, :]).ravel()          # index 2*a + b
    back = np.zeros((n, 4), int)
    for i in range(2, n):
        tot = cost.reshape(2, 2)[:, :, None] + C[i] + unary[i][None, None, :]   # (a, b, c)
        best_a = np.argmin(tot, axis=0)                                          # (b, c)
        new = np.take_along_axis(tot, best_a[None], axis=0)[0]
        back[i] = (2 * best_a + np.arange(2)[:, None]).ravel()
        cost = new.ravel() - new.min()
    states = np.zeros(n, int)
    st = int(np.argmin(cost))
    for i in range(n - 1, 1, -1):
        states[i] = st & 1
        st = back[i][st]
    states[1], states[0] = st & 1, st >> 1
    sw = states == 1
    l_xy, r_xy = xy[sw][:, LEFT_LEG], xy[sw][:, RIGHT_LEG]
    l_v, r_v = vis[sw][:, LEFT_LEG], vis[sw][:, RIGHT_LEG]
    idx = np.flatnonzero(sw)
    xy[np.ix_(idx, LEFT_LEG)], xy[np.ix_(idx, RIGHT_LEG)] = r_xy, l_xy
    vis[np.ix_(idx, LEFT_LEG)], vis[np.ix_(idx, RIGHT_LEG)] = r_v, l_v
    out.meta["leg_swaps_fixed"] = int(sw.sum())
    out.meta["leg_swap_share"] = float(sw.mean())
    return out


def _fill_1d(t: np.ndarray, y: np.ndarray, max_gap_s: float) -> np.ndarray:
    ok = ~np.isnan(y)
    if ok.sum() < 2 or ok.all():
        return y
    filled = np.interp(t, t[ok], y[ok])
    # distance in time to the valid samples on each side; only bridge short gaps
    idx = np.arange(len(y))
    last = np.maximum.accumulate(np.where(ok, idx, -1))
    nxt = np.minimum.accumulate(np.where(ok, idx, len(y))[::-1])[::-1]
    inner = (last >= 0) & (nxt < len(y))
    gap = np.full(len(y), np.inf)
    gap[inner] = t[np.minimum(nxt[inner], len(y) - 1)] - t[np.maximum(last[inner], 0)]
    return np.where(ok | (inner & (gap <= max_gap_s)), filled, np.nan)


def fill_gaps(s: PoseSeries, max_gap_s: float = 0.12) -> PoseSeries:
    """Linear interpolation over gaps up to ``max_gap_s``; longer gaps stay NaN."""
    out = s.copy()
    flat = out.xy.reshape(len(out.t), -1)
    for k in range(flat.shape[1]):
        flat[:, k] = _fill_1d(out.t, flat[:, k], max_gap_s)
    out.xy = flat.reshape(len(out.t), N_LANDMARKS, 2)
    return out


def remove_spikes(s: PoseSeries, window_s: float = 0.1, n_sigma: float = 4.0) -> PoseSeries:
    """Hampel-style spike removal: samples far from the running median become NaN."""
    out = s.copy()
    k = max(3, int(round(window_s * s.fps)) | 1)
    flat = out.xy.reshape(len(out.t), -1)
    min_px = 0.015 * s.height
    if len(out.t) < k:
        return out
    for j in range(flat.shape[1]):
        y = flat[:, j]
        ok = ~np.isnan(y)
        if ok.sum() < k:
            continue
        tmp = np.interp(np.arange(len(y)), np.flatnonzero(ok), y[ok])
        med = medfilt(tmp, k)
        r = np.abs(tmp - med)
        sigma = 1.4826 * np.median(r[ok])
        bad = ok & (r > max(n_sigma * sigma, min_px))
        y[bad] = np.nan
    out.xy = flat.reshape(len(out.t), N_LANDMARKS, 2)
    return out


def resample_uniform(s: PoseSeries, fps: float | None = None) -> PoseSeries:
    """Resample to an even time grid (live sources drop frames). NaN stays NaN."""
    if len(s.t) < 2:
        return s.copy()
    fps = fps or s.fps
    dt = 1.0 / fps
    if np.allclose(np.diff(s.t), dt, rtol=0.02, atol=1e-4):
        return s.copy()
    t_new = np.arange(s.t[0], s.t[-1] + dt * 0.5, dt)
    j = np.clip(np.searchsorted(s.t, t_new), 1, len(s.t) - 1)
    a = ((t_new - s.t[j - 1]) / (s.t[j] - s.t[j - 1]))[:, None, None]
    xy = s.xy[j - 1] * (1 - a) + s.xy[j] * a
    vis = s.vis[j - 1] * (1 - a[:, :, 0]) + s.vis[j] * a[:, :, 0]
    fi = s.frame_index[j - 1] * (1 - a[:, 0, 0]) + s.frame_index[j] * a[:, 0, 0]
    return PoseSeries(t_new, xy, vis, fi, s.width, s.height, dict(s.meta))


def default_cutoff(fps: float) -> float:
    """Low-pass cutoff in Hz for joint angles and positions at a given frame rate."""
    return float(min(12.0, 0.3 * fps))


def event_cutoff(fps: float) -> float:
    """Cutoff for the foot tracks used to time contacts: higher, so the corner where the foot meets
    the belt is not rounded off (that would move contact later and toe-off earlier)."""
    return float(min(25.0, 0.4 * fps))


def lowpass(s: PoseSeries, cutoff_hz: float | None = None, order: int = 2) -> PoseSeries:
    """Zero-phase Butterworth low-pass on each continuous stretch of each landmark."""
    out = s.copy()
    fps = s.fps
    cutoff_hz = cutoff_hz or default_cutoff(fps)
    wn = min(cutoff_hz / (fps / 2), 0.95)
    b, a = butter(order, wn)
    pad = 3 * max(len(a), len(b))
    flat = out.xy.reshape(len(out.t), -1)
    for j in range(flat.shape[1]):
        y = flat[:, j]
        ok = ~np.isnan(y)
        if not ok.any():
            continue
        edges = np.flatnonzero(np.diff(np.r_[0, ok.astype(int), 0]))
        for i0, i1 in zip(edges[::2], edges[1::2]):
            if i1 - i0 > pad:
                y[i0:i1] = filtfilt(b, a, y[i0:i1])
    out.xy = flat.reshape(len(out.t), N_LANDMARKS, 2)
    return out


def clean(s: PoseSeries, *, min_visibility: float = 0.2) -> PoseSeries:
    """Everything but the low-pass: visibility mask, leg swaps, spikes, gaps, even time grid."""
    out = mask_low_visibility(s, min_visibility)
    out = fix_leg_swaps(out)
    out = remove_spikes(out)
    out = fill_gaps(out, max_gap_s=max(0.12, 3.5 / out.fps))
    return resample_uniform(out)


def prepare(s: PoseSeries, *, cutoff_hz: float | None = None, min_visibility: float = 0.2) -> PoseSeries:
    """The full cleaning pipeline used before measuring angles and positions."""
    return lowpass(clean(s, min_visibility=min_visibility), cutoff_hz)


def prepare_pair(s: PoseSeries, *, min_visibility: float = 0.2) -> tuple[PoseSeries, PoseSeries]:
    """(series for gait events, series for kinematics), cleaned once and low-passed twice."""
    base = clean(s, min_visibility=min_visibility)
    fps = base.fps
    return lowpass(base, event_cutoff(fps)), lowpass(base, default_cutoff(fps))
