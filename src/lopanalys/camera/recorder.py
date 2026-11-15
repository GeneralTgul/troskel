"""Writes a live stream to an MP4 file plus a sidecar JSON with the real time of every frame.

Live cameras do not keep a perfectly steady frame rate (and the live view drops frames on purpose),
so the container's nominal fps is only approximate. The sidecar ``<file>.json`` keeps each frame's
capture time; ``camera.file_source.FileSource`` reads it back, so a recording can be re-analysed
later with the true timing.

The codec is H.264 when this OpenCV build can encode it (FFmpeg with OpenH264, or Media Foundation
on Windows), otherwise MPEG-4 Part 2 (``mp4v``), which every build can write. Encoding runs in a
background thread with a bounded queue, so a slow disk never stalls the live view; if the queue is
full the frame is dropped and counted (and left out of the sidecar, so timestamps stay true).
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from lopanalys.camera.base import Frame
from lopanalys.camera.file_source import sidecar_path
from lopanalys.camera.opencv_source import CameraError
from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

SIDECAR_VERSION = 1
_CODEC_CACHE: dict[tuple[str, int, int], tuple[str, int]] = {}


@dataclass(frozen=True)
class _Candidate:
    fourcc: str
    backend: int


def _candidates() -> list[_Candidate]:
    c = [_Candidate("avc1", cv2.CAP_FFMPEG)]
    if sys.platform == "win32" and hasattr(cv2, "CAP_MSMF"):
        c.append(_Candidate("H264", cv2.CAP_MSMF))
    c.append(_Candidate("mp4v", cv2.CAP_FFMPEG))
    return c


def _open_writer(path: Path, fps: float, size: tuple[int, int], codec: str | None) -> tuple[cv2.VideoWriter, str]:
    """Opens the best writer this build supports. The probe result is cached per size."""
    cands = _candidates()
    if codec:
        cands = [c for c in cands if c.fourcc == codec] or [_Candidate(codec, cv2.CAP_FFMPEG)]
    key = (codec or "", size[0], size[1])
    if key in _CODEC_CACHE:
        fourcc, backend = _CODEC_CACHE[key]
        cands = [_Candidate(fourcc, backend)] + [c for c in cands if c.fourcc != fourcc]
    for c in cands:
        w = cv2.VideoWriter(str(path), c.backend, cv2.VideoWriter_fourcc(*c.fourcc), float(fps), size)
        if w.isOpened():
            _CODEC_CACHE[key] = (c.fourcc, c.backend)
            return w, c.fourcc
        w.release()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    raise CameraError(t("camera.error.recorder_open", path=str(path)))


class Recorder:
    """``Recorder(path, fps, size)``; ``write(frame)`` from the capture loop, ``close()`` when done.

    ``frame`` may be a ``camera.base.Frame`` (its timestamp is kept) or a BGR image (stamped with
    ``time.perf_counter()``). Frames of another size are resized. ``meta`` is stored in the sidecar
    (source label, athlete id, anything the caller wants to find again).
    """

    def __init__(self, path: str | Path, fps: float, size: tuple[int, int], *, codec: str | None = None,
                 queue_size: int = 120, meta: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        if self.path.suffix.lower() not in (".mp4", ".mov", ".m4v"):
            self.path = self.path.with_suffix(".mp4")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fps = float(fps) if fps and fps > 0 else 30.0
        self.size = (int(size[0]), int(size[1]))
        self.meta = dict(meta or {})
        self._writer, self.codec = _open_writer(self.path, self.fps, self.size, codec)
        self._q: queue.Queue[tuple[np.ndarray, float] | None] = queue.Queue(maxsize=queue_size)
        self._times: list[float] = []
        self.dropped = 0
        self.written = 0
        self._closed = False
        self._error: BaseException | None = None
        self._started = time.time()
        self._thread = threading.Thread(target=self._run, name=f"rec-{self.path.name}", daemon=True)
        self._thread.start()

    def write(self, frame: Frame | np.ndarray) -> bool:
        """Queues one frame. Returns False if it was dropped (queue full or recorder closed)."""
        if self._closed:
            return False
        if self._error is not None:
            raise CameraError(t("camera.error.recorder_write", path=str(self.path)))
        if isinstance(frame, Frame):
            image, ts = frame.image, frame.t
        else:
            image, ts = frame, time.perf_counter()
        try:
            self._q.put_nowait((image, ts))
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def close(self) -> Path:
        """Writes the remaining frames, closes the file and writes the sidecar. Returns the path."""
        if self._closed:
            return self.path
        self._closed = True
        self._q.put(None)
        self._thread.join()
        self._writer.release()
        self._write_sidecar()
        if self._error is not None:
            raise CameraError(t("camera.error.recorder_write", path=str(self.path)))
        return self.path

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def duration(self) -> float:
        """Seconds between the first and the last written frame."""
        return self._times[-1] - self._times[0] if len(self._times) > 1 else 0.0

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            image, ts = item
            if self._error is not None:
                continue
            try:
                if image.ndim == 2:
                    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
                if (image.shape[1], image.shape[0]) != self.size:
                    image = cv2.resize(image, self.size, interpolation=cv2.INTER_AREA)
                if self._times and ts <= self._times[-1]:
                    ts = self._times[-1] + 1e-6   # keep the sidecar strictly increasing
                self._writer.write(np.ascontiguousarray(image, dtype=np.uint8))
                self._times.append(ts)
                self.written += 1
            except Exception as e:   # disk full, codec error: remember, report on next write/close
                log.exception("recording %s failed", self.path.name)
                self._error = e

    def _write_sidecar(self) -> None:
        t0 = self._times[0] if self._times else 0.0
        measured = (len(self._times) - 1) / self.duration if self.duration > 0 else None
        data = {
            "version": SIDECAR_VERSION,
            "video": self.path.name,
            "codec": self.codec,
            "nominal_fps": self.fps,
            "measured_fps": measured,
            "size": list(self.size),
            "frames": len(self._times),
            "dropped": self.dropped,
            "started_unix": self._started,
            "clock": "monotonic",
            "t0": t0,
            "t": [round(x - t0, 6) for x in self._times],
            "meta": self.meta,
        }
        sidecar_path(self.path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
