"""What the synthetic cyclist and the synthetic skier share: noise, gaps, uneven frame times and a film.

A model gives 3D landmark positions (33, 3) in metres at a time t (x forward, y to the athlete's
left, z up), as ``pose.synthetic.RunnerModel`` does. Here they are projected to the camera view and
disturbed the way a real pose estimator disturbs them, so the analysis can be tested against a known
truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import cv2
import numpy as np

from lopanalys.pose import synthetic
from lopanalys.pose.types import LM, Pose, View


@dataclass
class SynthBase:
    """Camera and disturbance settings shared by the synthetic athletes."""
    height_cm: float = 178.0
    view: View = View.SIDE_LEFT
    fps: float = 60.0
    duration_s: float = 20.0
    width: int = 640
    height: int = 360
    noise_px: float = 0.6
    outlier_rate: float = 0.0       # share of frames with one landmark thrown far off
    missing_rate: float = 0.0       # share of frames without a pose
    time_jitter: float = 0.0        # frame intervals vary by up to this share (a live camera)
    hands_hidden: bool = False      # the near elbow and wrist are not seen (hands lost on the bars)
    seed: int = 1


class Model(Protocol):
    def joints(self, t: float) -> np.ndarray: ...


_LEFT = [i for k, i in LM.items() if k.startswith("l_")] + [1, 2, 3, 7, 9, 17, 19, 21]


def frame_times(p: SynthBase) -> np.ndarray:
    n = int(round(p.duration_s * p.fps))
    if not p.time_jitter:
        return np.arange(n) / p.fps
    rng = np.random.default_rng(p.seed + 7)
    dts = (1.0 + rng.uniform(-p.time_jitter, p.time_jitter, n)) / p.fps
    return np.concatenate([[0.0], np.cumsum(dts[:-1])])


def visibility(p: SynthBase) -> np.ndarray:
    vis = np.full(33, 0.95)
    v = View(p.view)
    if v in (View.SIDE_LEFT, View.SIDE_RIGHT):
        far = np.zeros(33, bool)
        far[_LEFT] = True
        if v == View.SIDE_LEFT:
            far = ~far
        far[0] = False
        vis[far] = 0.6
        if p.hands_hidden:
            pre = "l_" if v == View.SIDE_LEFT else "r_"
            vis[[LM[pre + "elbow"], LM[pre + "wrist"]]] = 0.1
    return vis


def generate(model: Model, p: SynthBase) -> tuple[list[Pose | None], list[float]]:
    """(poses, the time of every frame). Poses carry noise, outliers and gaps as configured."""
    rng = np.random.default_rng(p.seed)
    proj = synthetic.Projector(p)       # type: ignore[arg-type]  (same camera fields as RunnerParams)
    vis = visibility(p)
    times = frame_times(p)
    poses: list[Pose | None] = []
    for i, tt in enumerate(times):
        xy = proj(model.joints(float(tt)))
        if p.noise_px:
            xy = xy + rng.normal(0, p.noise_px, xy.shape)
        if p.outlier_rate and rng.random() < p.outlier_rate:
            xy[int(rng.integers(11, 33))] += rng.normal(0, 0.08 * p.height, 2)
        if p.missing_rate and rng.random() < p.missing_rate:
            poses.append(None)
            continue
        poses.append(Pose(float(tt), i, xy, vis.copy(), p.width, p.height))
    return poses, [float(x) for x in times]


def px_per_cm(p: SynthBase) -> float:
    return synthetic.Projector(p).px_per_m / 100.0     # type: ignore[arg-type]


def render_frames(model: Model, p: SynthBase, background: Callable[[SynthBase], np.ndarray]) -> list[np.ndarray]:
    """Drawn BGR frames of the model. Keep ``duration_s`` and the image small: memory."""
    bg = background(p)
    return [synthetic.render(model.joints(float(tt)), p, bg) for tt in frame_times(p)]   # type: ignore[arg-type]


def write_video(path: str | Path, model: Model, p: SynthBase, background: Callable[[SynthBase], np.ndarray]) -> Path:
    """A small MP4 of the synthetic athlete, written frame by frame (nothing is held in memory)."""
    path = Path(path)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), p.fps, (p.width, p.height))
    if not w.isOpened():
        raise RuntimeError("cannot write the synthetic video")
    bg = background(p)
    try:
        for tt in frame_times(p):
            w.write(synthetic.render(model.joints(float(tt)), p, bg))    # type: ignore[arg-type]
    finally:
        w.release()
    return path


def replay(poses: list[Pose | None]) -> Any:
    """An estimator that returns the given poses frame by frame (for films of the synthetic athletes)."""
    from lopanalys.pose.estimator import ReplayEstimator
    return ReplayEstimator({i: p for i, p in enumerate(poses)})
