"""A sequence of poses as arrays, plus the small geometry helpers the analysis shares.

``PoseSeries`` holds every frame of a clip (or of the live window) as numpy arrays, with NaN where
a landmark is missing, so filters, gait events and metrics work on whole columns at once instead
of looping over ``Pose`` objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from lopanalys.pose.types import LM, Pose, Side, View

N_LANDMARKS = 33


@dataclass
class PoseSeries:
    """Poses on a time axis. ``xy`` is (N, 33, 2) image pixels, NaN where a landmark is missing."""
    t: np.ndarray                 # (N,) seconds, increasing
    xy: np.ndarray                # (N, 33, 2)
    vis: np.ndarray               # (N, 33)
    frame_index: np.ndarray       # (N,) source frame index (float after resampling)
    width: int
    height: int
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.t)

    @classmethod
    def from_poses(cls, poses: Sequence[Pose | None], times: Sequence[float] | None = None,
                   frame_indices: Sequence[int] | None = None) -> "PoseSeries":
        """Build from a list of poses. ``None`` entries (no person found) need ``times``."""
        real = [p for p in poses if p is not None]
        if not real:
            raise ValueError("no poses")
        n = len(poses)
        t = np.empty(n)
        fi = np.empty(n)
        xy = np.full((n, N_LANDMARKS, 2), np.nan)
        vis = np.zeros((n, N_LANDMARKS))
        for i, p in enumerate(poses):
            if p is None:
                if times is None:
                    raise ValueError("times are needed when a pose is missing")
                t[i] = times[i]
                fi[i] = frame_indices[i] if frame_indices is not None else i
                continue
            t[i] = p.t
            fi[i] = p.frame_index
            xy[i] = p.xy
            vis[i] = p.visibility
        return cls(t, xy, vis, fi, real[0].width, real[0].height)

    def copy(self) -> "PoseSeries":
        return PoseSeries(self.t.copy(), self.xy.copy(), self.vis.copy(), self.frame_index.copy(),
                          self.width, self.height, dict(self.meta))

    def slice(self, i0: int, i1: int) -> "PoseSeries":
        return PoseSeries(self.t[i0:i1].copy(), self.xy[i0:i1].copy(), self.vis[i0:i1].copy(),
                          self.frame_index[i0:i1].copy(), self.width, self.height, dict(self.meta))

    def lm(self, name: str) -> np.ndarray:
        """(N, 2) track of one landmark by name (see ``types.LM``)."""
        return self.xy[:, LM[name]]

    def side_lm(self, side: Side, part: str) -> np.ndarray:
        """(N, 2) track of ``part`` ("hip", "knee", ...) on one side."""
        return self.lm(("l_" if side == Side.LEFT else "r_") + part)

    def mid(self, part: str) -> np.ndarray:
        """Midpoint of the left and right landmark (e.g. "hip" -> pelvis centre)."""
        a, b = self.lm("l_" + part), self.lm("r_" + part)
        out = np.where(np.isnan(a), b, a)
        both = ~np.isnan(a) & ~np.isnan(b)
        out[both] = (a[both] + b[both]) / 2
        return out

    @property
    def fps(self) -> float:
        if len(self.t) < 2:
            return 30.0
        return float(1.0 / np.median(np.diff(self.t)))

    def pose_at(self, i: int) -> Pose:
        """The (possibly filtered) pose at row ``i``."""
        return Pose(float(self.t[i]), int(round(self.frame_index[i])), self.xy[i].copy(),
                    self.vis[i].copy(), self.width, self.height)

    def pose_at_time(self, t: float) -> Pose:
        """Linearly interpolated pose at time ``t`` (sub-frame)."""
        x = interp_rows(self.t, self.xy.reshape(len(self.t), -1), t).reshape(N_LANDMARKS, 2)
        v = interp_rows(self.t, self.vis, t)
        return Pose(float(t), int(round(float(np.interp(t, self.t, self.frame_index)))), x, v,
                    self.width, self.height)


def interp_rows(t: np.ndarray, values: np.ndarray, at: float) -> np.ndarray:
    """Interpolate every column of ``values`` (N, K) at time ``at``."""
    if at <= t[0]:
        return values[0].copy()
    if at >= t[-1]:
        return values[-1].copy()
    j = int(np.searchsorted(t, at))
    a = (at - t[j - 1]) / (t[j] - t[j - 1])
    return values[j - 1] * (1 - a) + values[j] * a


def interp_track(t: np.ndarray, track: np.ndarray, at: float) -> np.ndarray:
    """Interpolate a (N, 2) track at time ``at``."""
    return interp_rows(t, track, at)


# --- angles -------------------------------------------------------------------------------------

def interior_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Angle ABC in degrees (vectorised over leading dims)."""
    v1 = np.asarray(a, float) - b
    v2 = np.asarray(c, float) - b
    n = np.linalg.norm(v1, axis=-1) * np.linalg.norm(v2, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.sum(v1 * v2, axis=-1) / n
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def angle_from_vertical(top: np.ndarray, bottom: np.ndarray, facing: int) -> np.ndarray:
    """Angle of the segment top->bottom against the downward vertical, in degrees.

    Positive when ``bottom`` lies ahead of ``top`` in the running direction (``facing`` = +1 when the
    athlete faces image right). Image y grows downwards.
    """
    d = np.asarray(bottom, float) - top
    return np.degrees(np.arctan2(facing * d[..., 0], d[..., 1]))


def lean_from_vertical(bottom: np.ndarray, top: np.ndarray, facing: int) -> np.ndarray:
    """Angle of bottom->top against the upward vertical, + when ``top`` is ahead (forward lean)."""
    d = np.asarray(top, float) - bottom
    return np.degrees(np.arctan2(facing * d[..., 0], -d[..., 1]))


def nanmedian(values: Iterable[float | None]) -> float | None:
    """Median of the non-None, finite values, or None."""
    arr = np.array([v for v in values if v is not None and np.isfinite(v)], float)
    return float(np.median(arr)) if arr.size else None


def is_side_view(view: View | str) -> bool:
    return View(view) in (View.SIDE_LEFT, View.SIDE_RIGHT)


def near_side(view: View | str) -> Side | None:
    """The leg closest to the camera in a side view."""
    v = View(view)
    if v == View.SIDE_LEFT:
        return Side.LEFT
    if v == View.SIDE_RIGHT:
        return Side.RIGHT
    return None
