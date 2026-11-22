"""Video files as a frame source: SD-card clips from a GoPro (MP4, also 120/240 fps), phone MOV files
and the app's own recordings.

Timestamps are the true presentation times from the container (``CAP_PROP_POS_MSEC``), rebased so
the first frame is t=0. If the file has a sidecar written by ``camera.recorder.Recorder``
(``<file>.json`` with per-frame times), those are used instead: a live recording's real capture
times survive re-analysis even when the camera's frame rate wandered.

``pace="fast"`` delivers frames as fast as they decode (offline analysis); ``pace="realtime"``
sleeps so playback runs at real speed (demo mode, playback of a recording in the live view), and
``loop=True`` starts over at the end with timestamps that keep increasing.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Iterator, Literal

import cv2

from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.camera.opencv_source import CameraError
from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

Pace = Literal["fast", "realtime"]
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".ts")


def sidecar_path(path: Path) -> Path:
    """The timestamp sidecar that belongs to a video file."""
    return path.with_name(path.name + ".json")


def file_info(path: str | Path) -> SourceInfo:
    """SourceInfo for a video file (reads the header only). Raises CameraError if it cannot be read."""
    p = Path(path)
    if not p.is_file():
        raise CameraError(t("camera.error.file_missing", path=str(p)))
    cap = cv2.VideoCapture(str(p), cv2.CAP_FFMPEG)
    try:
        if not cap.isOpened():
            raise CameraError(t("camera.error.file_unreadable", name=p.name))
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    finally:
        cap.release()
    return SourceInfo(SourceKind.FILE, str(p), t("camera.label.file", name=p.name),
                      width=w or None, height=h or None, fps=fps or None, live=False)


class FileSource:
    """A video file. ``frames()`` yields every frame in order (no dropping, unlike live sources)."""

    def __init__(self, info_or_path: SourceInfo | str | Path, *, pace: Pace = "fast", loop: bool = False,
                 speed: float = 1.0, use_sidecar: bool = True) -> None:
        self.info = info_or_path if isinstance(info_or_path, SourceInfo) else file_info(info_or_path)
        self.path = Path(self.info.id)
        self.pace: Pace = pace
        self.loop = loop
        self.speed = speed if speed > 0 else 1.0
        self.use_sidecar = use_sidecar
        self._cap: cv2.VideoCapture | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._next_index = 0
        self._pts0: float | None = None
        self._sidecar: list[float] | None = None
        self.fps: float = 0.0
        self.frame_count: int = 0
        self.width = 0
        self.height = 0

    # ---- properties ---------------------------------------------------------------------------
    @property
    def duration(self) -> float:
        """Length in seconds (from the sidecar if present, else frame count / fps)."""
        if self._sidecar and len(self._sidecar) > 1:
            return self._sidecar[-1] - self._sidecar[0] + (1.0 / self.fps if self.fps else 0.0)
        return self.frame_count / self.fps if self.fps else 0.0

    @property
    def position(self) -> int:
        """Index of the next frame ``read()`` returns."""
        return self._next_index

    # ---- lifecycle ----------------------------------------------------------------------------
    def open(self) -> None:
        """Opens the file. Raises CameraError (Swedish) if it is missing or cannot be decoded."""
        if not self.path.is_file():
            raise CameraError(t("camera.error.file_missing", path=str(self.path)))
        with self._lock:
            self._stop.clear()
            self._cap = cv2.VideoCapture(str(self.path), cv2.CAP_FFMPEG)
            if not self._cap.isOpened():
                self._cap.release()
                self._cap = None
                raise CameraError(t("camera.error.file_unreadable", name=self.path.name))
            self.fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
            self.frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._next_index = 0
            self._pts0 = None
            self._sidecar = self._load_sidecar() if self.use_sidecar else None

    def close(self) -> None:
        """Releases the file; a running ``frames()`` loop ends."""
        self._stop.set()
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None

    def __enter__(self) -> "FileSource":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- reading ------------------------------------------------------------------------------
    def read(self) -> Frame | None:
        """Decodes the next frame, or None at the end of the file."""
        with self._lock:
            if self._cap is None:
                return None
            ok, image = self._cap.read()
            if not ok or image is None:
                return None
            idx = self._next_index
            pts = self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            self._next_index += 1
            return Frame(image=image, t=self._timestamp(idx, pts), index=idx)

    def frames(self) -> Iterator[Frame]:
        """Every frame in order, paced to real time if ``pace == "realtime"``. With ``loop`` it
        restarts at the end and adds the clip length to the timestamps so they keep rising."""
        if self._cap is None:
            self.open()
        offset = 0.0
        index_offset = 0
        wall0: float | None = None
        t_first: float | None = None
        while not self._stop.is_set():
            frame = self.read()
            if frame is None:
                if not self.loop or self._stop.is_set() or self._next_index == 0:
                    break
                offset += self.duration
                index_offset += self._next_index
                self.seek_frame(0)
                continue
            if offset or index_offset:
                frame = Frame(frame.image, frame.t + offset, frame.index + index_offset)
            if self.pace == "realtime":
                now = time.perf_counter()
                if wall0 is None or t_first is None:
                    wall0, t_first = now, frame.t
                due = wall0 + (frame.t - t_first) / self.speed
                if due > now and self._stop.wait(due - now):
                    break
                if now - due > 0.5:   # we fell far behind (slow machine): resync instead of racing
                    wall0, t_first = now, frame.t
            yield frame

    def seek_frame(self, index: int) -> None:
        """Moves to frame ``index`` (clamped), so the next ``read()`` returns it."""
        with self._lock:
            if self._cap is None:
                self.open()
            assert self._cap is not None
            index = max(0, min(index, max(self.frame_count - 1, 0)))
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            self._next_index = index

    def seek(self, seconds: float) -> None:
        """Moves to the frame at ``seconds`` from the start of the clip."""
        if self._sidecar:
            import bisect
            target = self._sidecar[0] + seconds
            self.seek_frame(bisect.bisect_left(self._sidecar, target - 1e-6))
        else:
            self.seek_frame(int(round(seconds * self.fps)))

    # ---- internals ----------------------------------------------------------------------------
    def _timestamp(self, idx: int, pts: float) -> float:
        if self._sidecar and idx < len(self._sidecar):
            return self._sidecar[idx] - self._sidecar[0]
        if self._sidecar:   # a frame or two past the last stamp: go on from it, never back in time
            return self._sidecar[-1] - self._sidecar[0] + (idx - len(self._sidecar) + 1) / self.fps
        # Container PTS. Some backends report 0 for every frame; fall back to index/fps then.
        if self._pts0 is None:
            self._pts0 = pts if idx == 0 else pts - idx / self.fps
        ts = pts - self._pts0
        if idx > 0 and ts <= 0:
            return idx / self.fps
        return ts

    def _load_sidecar(self) -> list[float] | None:
        p = sidecar_path(self.path)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            ts = [float(x) for x in data.get("t", [])]
        except (ValueError, TypeError, OSError):
            log.warning("bad sidecar %s, using container timestamps", p.name)
            return None
        if not ts or (self.frame_count and abs(len(ts) - self.frame_count) > 2):
            log.warning("sidecar %s has %d stamps for %d frames, ignored", p.name, len(ts), self.frame_count)
            return None
        if any(b < a for a, b in zip(ts, ts[1:])):
            return None
        return ts
