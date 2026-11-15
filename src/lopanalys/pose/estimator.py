"""Pose estimation: an abstract interface, the MediaPipe Pose Landmarker and a replay fake.

``MediaPipeEstimator`` wraps the MediaPipe Tasks API (``mediapipe>=0.10``; there is no
``mp.solutions`` in current releases). Running modes:
  * "video" (default): synchronous, uses the landmarker's own tracking between frames. Used for
    files and also for live analysis, where ``LiveAnalyzer`` itself skips frames to keep up.
  * "live": ``LIVE_STREAM`` mode with a result callback; ``process`` returns the newest finished
    result, which may belong to an earlier frame (check ``Pose.frame_index``).

ROI: on a treadmill the runner stays in one place. ``RoiTracker`` keeps a stable crop around the
runner (with margin and hysteresis), which helps the person detector when the runner is small in a
wide GoPro frame and keeps other people in the room (the coach) from being picked.
"""
from __future__ import annotations

import abc
import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from lopanalys.core.i18n import t
from lopanalys.pose.types import Pose

log = logging.getLogger(__name__)


class PoseEstimator(abc.ABC):
    """Turns one BGR image into a ``Pose`` in full-image pixel coordinates (or None)."""

    @abc.abstractmethod
    def process(self, image: np.ndarray, t: float, frame_index: int) -> Pose | None: ...

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources. Safe to call twice."""

    def reset(self) -> None:  # noqa: B027
        """Forget tracking state (new clip)."""

    def __enter__(self) -> "PoseEstimator":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class ReplayEstimator(PoseEstimator):
    """Returns prepared poses: a dict by frame index, or a function (frame_index, t, image) -> Pose.

    For tests and demo mode. ``delay_s`` simulates a slow model.
    """

    def __init__(self, poses: dict[int, Pose | None] | Callable[[int, float, np.ndarray], Pose | None],
                 delay_s: float = 0.0) -> None:
        self._poses = poses
        self._delay = delay_s
        self.calls = 0

    def process(self, image: np.ndarray, t: float, frame_index: int) -> Pose | None:
        self.calls += 1
        if self._delay:
            threading.Event().wait(self._delay)
        if callable(self._poses):
            p = self._poses(frame_index, t, image)
        else:
            p = self._poses.get(frame_index)
        if p is None:
            return None
        return Pose(t, frame_index, p.xy.copy(), p.visibility.copy(), image.shape[1], image.shape[0])


class RoiTracker:
    """A stable crop rectangle around the runner, in full-image pixels (x0, y0, x1, y1)."""

    def __init__(self, margin: float = 0.35, edge: float = 0.08, lost_after: int = 15) -> None:
        self.margin = margin
        self.edge = edge
        self.lost_after = lost_after
        self.rect: tuple[int, int, int, int] | None = None
        self._misses = 0

    def crop_for(self, w: int, h: int) -> tuple[int, int, int, int]:
        return self.rect if self.rect is not None else (0, 0, w, h)

    def update(self, pose: Pose | None) -> None:
        w, h = pose.width if pose else 0, pose.height if pose else 0
        if pose is None:
            self._misses += 1
            if self._misses >= self.lost_after:
                self.rect = None
            return
        self._misses = 0
        ok = pose.visibility > 0.3
        if ok.sum() < 8:
            return
        pts = pose.xy[ok]
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        bw, bh = x1 - x0, y1 - y0
        size = max(bw, bh) * (1 + 2 * self.margin)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        want = self._clip(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2, w, h)
        if (want[2] - want[0]) * (want[3] - want[1]) > 0.5 * w * h:
            self.rect = None                # the runner fills the frame anyway: no crop
            return
        if self.rect is None:
            self.rect = want
            return
        rx0, ry0, rx1, ry1 = self.rect
        ew, eh = self.edge * (rx1 - rx0), self.edge * (ry1 - ry0)
        near_edge = x0 < rx0 + ew or y0 < ry0 + eh or x1 > rx1 - ew or y1 > ry1 - eh
        too_big = (rx1 - rx0) > 1.8 * (want[2] - want[0])
        if near_edge or too_big:
            self.rect = want

    @staticmethod
    def _clip(x0: float, y0: float, x1: float, y1: float, w: int, h: int) -> tuple[int, int, int, int]:
        bw, bh = x1 - x0, y1 - y0
        if x0 < 0:
            x0, x1 = 0, min(w, bw)
        if x1 > w:
            x0, x1 = max(0, w - bw), w
        if y0 < 0:
            y0, y1 = 0, min(h, bh)
        if y1 > h:
            y0, y1 = max(0, h - bh), h
        return int(x0), int(y0), int(x1), int(y1)


class MediaPipeEstimator(PoseEstimator):
    """MediaPipe Pose Landmarker (33 landmarks incl. heel and toe)."""

    def __init__(self, variant: str = "full", model_path: str | Path | None = None, mode: str = "video",
                 roi: bool = True, min_detection: float = 0.5, min_tracking: float = 0.5,
                 progress_cb: Callable[[int, int], None] | None = None) -> None:
        from lopanalys.pose.models import ensure_model
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
        except Exception as e:   # pragma: no cover - packaging problem
            raise RuntimeError(t("pose.error.mediapipe", reason=str(e))) from e
        self._mp = mp
        path = Path(model_path) if model_path else ensure_model(variant, progress_cb)
        self.mode = mode
        self._lock = threading.Lock()
        self._latest: Pose | None = None
        self._pending: dict[int, tuple[int, float, int, int, tuple[int, int, int, int]]] = {}
        running = vision.RunningMode.LIVE_STREAM if mode == "live" else vision.RunningMode.VIDEO
        kw = {}
        if mode == "live":
            kw["result_callback"] = self._on_result
        opts = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(path)), running_mode=running, num_poses=1,
            min_pose_detection_confidence=min_detection, min_pose_presence_confidence=min_detection,
            min_tracking_confidence=min_tracking, **kw)
        try:
            self._lm = vision.PoseLandmarker.create_from_options(opts)
        except Exception as e:
            raise RuntimeError(t("pose.error.mediapipe", reason=str(e))) from e
        self._roi = RoiTracker() if roi else None
        self._last_ts = -1
        self._closed = False

    def _to_pose(self, result: object, frame_index: int, tsec: float, w: int, h: int,
                 rect: tuple[int, int, int, int]) -> Pose | None:
        lms = getattr(result, "pose_landmarks", None)
        if not lms:
            return None
        x0, y0, x1, y1 = rect
        cw, ch = x1 - x0, y1 - y0
        pts = lms[0]
        xy = np.array([[p.x * cw + x0, p.y * ch + y0] for p in pts], float)
        vis = np.array([(p.visibility if p.visibility is not None else (p.presence or 0.0)) for p in pts], float)
        return Pose(tsec, frame_index, xy, np.clip(vis, 0, 1), w, h)

    def _on_result(self, result: object, image: object, ts_ms: int) -> None:
        info = self._pending.pop(ts_ms, None)
        if info is None:
            return
        fi, tsec, w, h, rect = info
        pose = self._to_pose(result, fi, tsec, w, h, rect)
        with self._lock:
            self._latest = pose
        if self._roi is not None:
            self._roi.update(pose if pose else None)

    def process(self, image: np.ndarray, t: float, frame_index: int) -> Pose | None:
        import cv2
        if self._closed:
            return None
        h, w = image.shape[:2]
        rect = self._roi.crop_for(w, h) if self._roi else (0, 0, w, h)
        x0, y0, x1, y1 = rect
        rgb = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
        mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        ts = max(int(t * 1000), self._last_ts + 1)
        self._last_ts = ts
        if self.mode == "live":
            self._pending[ts] = (frame_index, t, w, h, rect)
            if len(self._pending) > 30:   # results the model dropped
                for k in sorted(self._pending)[:-30]:
                    self._pending.pop(k, None)
            self._lm.detect_async(mp_img, ts)
            with self._lock:
                return self._latest
        result = self._lm.detect_for_video(mp_img, ts)
        pose = self._to_pose(result, frame_index, t, w, h, rect)
        if self._roi is not None:
            self._roi.update(pose)
        return pose

    def reset(self) -> None:
        if self._roi is not None:
            self._roi.rect = None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._lm.close()
            except Exception:   # pragma: no cover
                log.exception("closing the pose landmarker failed")


def make_estimator(variant: str = "full", mode: str = "video", **kw: object) -> PoseEstimator:
    """The default estimator (MediaPipe). Raises RuntimeError/ModelError with a coach-readable text."""
    return MediaPipeEstimator(variant=variant, mode=mode, **kw)  # type: ignore[arg-type]
