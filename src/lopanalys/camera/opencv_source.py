"""Live sources through OpenCV: system cameras (USB webcams, capture cards), RTSP/IP cameras and
UDP MPEG-TS streams (the GoPro webcam stream uses this class too).

Live view must stay real time, so a reader thread pulls frames as fast as the device delivers them
and keeps only the newest one. A slow consumer (pose estimation) gets the latest frame and simply
misses the ones in between instead of falling behind. Every frame is stamped with
``time.perf_counter()`` when it was grabbed, and ``Frame.index`` counts grabbed frames, so a consumer
can see how many it skipped. Hiccups (USB unplugged, Wi-Fi drop, camera rebooted) lead to a
reconnect with back-off; ``frames()`` keeps waiting and resumes when frames come back.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterator, Sequence

import cv2
import numpy as np

from lopanalys.camera.base import Frame, SourceInfo, SourceKind, safe_url
from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

# Modes tried for system cameras, best first: high fps matters more than pixels for gait analysis,
# but 1080p is preferred when the fps is the same.
DEFAULT_MODES: tuple[tuple[int, int, float], ...] = (
    (1920, 1080, 120.0),
    (1280, 720, 120.0),
    (1920, 1080, 60.0),
    (1280, 720, 60.0),
    (1920, 1080, 30.0),
    (1280, 720, 30.0),
)

# FFmpeg options for low-latency network streams (RTSP, UDP). OpenCV reads them from this
# environment variable when a capture is opened; the format is key;value|key;value.
# threads;1: FFmpeg's frame threading holds back one frame per thread before it emits anything (12 threads
# = 0.4 s at 30 fps on the first real GoPro test, 2026-11-13). One thread decodes 1080p30 H.264 with ease.
LOW_LATENCY_FFMPEG = ("fflags;nobuffer|flags;low_delay|max_delay;0|reorder_queue_size;0|threads;1|"
                      "analyzeduration;1000000|probesize;500000")
RTSP_FFMPEG = "rtsp_transport;tcp|" + LOW_LATENCY_FFMPEG
_ENV_LOCK = threading.Lock()


class SourceState(str, Enum):
    OPENING = "opening"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass
class SourceStatus:
    """Published through ``on_status``; ``message`` is user-visible (translated)."""
    state: SourceState
    message: str = ""
    fps: float | None = None
    dropped: int = 0


class CameraError(RuntimeError):
    """A camera problem with a message the coach can act on (already translated)."""


def system_backend() -> int:
    """OpenCV backend for system cameras on this platform."""
    if sys.platform == "win32":
        return cv2.CAP_DSHOW   # indices match DirectShow device names (pygrabber), MJPG works
    if sys.platform == "darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_V4L2


def parse_system_id(source_id: str) -> tuple[int | str, int]:
    """'0' -> (0, platform backend); 'msmf:1' / 'dshow:1' / 'v4l2:/dev/video2' select a backend."""
    backends = {"dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY), "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY),
                "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY), "any": cv2.CAP_ANY}
    prefix, sep, rest = source_id.partition(":")
    if sep and prefix.lower() in backends:
        target: int | str = int(rest) if rest.isdigit() else rest
        return target, backends[prefix.lower()]
    return (int(source_id) if source_id.isdigit() else source_id), system_backend()


class FpsMeter:
    """Measures the real frame rate over a sliding window of timestamps."""

    def __init__(self, window: int = 60) -> None:
        self._t: deque[float] = deque(maxlen=window)

    def tick(self, t: float) -> None:
        self._t.append(t)

    @property
    def fps(self) -> float | None:
        if len(self._t) < 5 or self._t[-1] <= self._t[0]:
            return None
        return (len(self._t) - 1) / (self._t[-1] - self._t[0])

    def reset(self) -> None:
        self._t.clear()


@dataclass
class _Slot:
    frame: Frame | None = None
    seq: int = 0
    cond: threading.Condition = field(default_factory=threading.Condition)


class OpenCvSource:
    """A live OpenCV capture with a newest-frame-only reader thread and automatic reconnect.

    ``info.kind`` SYSTEM uses ``info.id`` as device index (``parse_system_id``); RTSP and anything
    else uses it as a URL/filename for the FFmpeg backend. ``modes`` lists (width, height, fps)
    to request from a system camera, best first; the first mode the camera accepts with the
    highest delivered fps wins. ``ffmpeg_options`` overrides the low-latency FFmpeg options.
    """

    def __init__(
        self,
        info: SourceInfo,
        *,
        modes: Sequence[tuple[int, int, float]] | None = None,
        ffmpeg_options: str | None = None,
        on_status: Callable[[SourceStatus], None] | None = None,
        open_timeout_s: float = 8.0,
        read_timeout_s: float = 3.0,
        stall_s: float = 3.0,
        max_reconnect_s: float = 5.0,
        before_reconnect: Callable[[], None] | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.info = info
        self.modes = tuple(modes) if modes else (
            ((info.width, info.height, info.fps or 60.0),) + DEFAULT_MODES if info.width and info.height else DEFAULT_MODES)
        self.ffmpeg_options = ffmpeg_options
        self.on_status = on_status
        self.open_timeout_s = open_timeout_s
        self.read_timeout_s = read_timeout_s
        self.stall_s = stall_s
        self.max_reconnect_s = max_reconnect_s
        self.before_reconnect = before_reconnect
        self.max_attempts = max_attempts   # None: keep trying until closed (the camera may come back)
        self._cap: cv2.VideoCapture | None = None
        self._slot = _Slot()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._meter = FpsMeter()
        self._grabbed = 0
        self._delivered = 0
        self.state = SourceState.CLOSED
        self.mode: tuple[int, int, float] | None = None   # what the device reported after open
        # Network streams by address (the phone as a camera) are measured: camera.network.StreamStats.
        self.stats: Any = None
        self._pts_base: float | None = None

    # ---- public -------------------------------------------------------------------------------
    @property
    def actual_fps(self) -> float | None:
        """Frame rate actually delivered by the device, measured from frame timestamps."""
        return self._meter.fps

    @property
    def dropped(self) -> int:
        """Frames grabbed but never handed to the consumer (it was busy)."""
        return max(0, self._grabbed - self._delivered)

    def open(self) -> None:
        """Opens the device (raises CameraError with a Swedish message) and starts the reader."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._set_state(SourceState.OPENING, t("camera.status.opening", label=self.info.label))
        try:
            self._cap = self._open_capture()
        except Exception:
            # not left as "opening": frames() on a source that never opened ends instead of waiting
            self._stop.set()
            self._set_state(SourceState.CLOSED, t("camera.status.closed", label=self.info.label))
            raise
        self._set_state(SourceState.RUNNING, t("camera.status.running", label=self.info.label))
        self._thread = threading.Thread(target=self._reader, name=f"cam-{safe_url(self.info.id)}", daemon=True)
        self._thread.start()

    def latest(self) -> Frame | None:
        """The newest frame without waiting (None before the first frame)."""
        return self._slot.frame

    def frames(self) -> Iterator[Frame]:
        """Yields the newest frame each time a new one has arrived; ends when the source closes."""
        last = 0
        while not self._stop.is_set():
            with self._slot.cond:
                self._slot.cond.wait_for(lambda: self._slot.seq != last or self._stop.is_set(), timeout=0.5)
                if self._stop.is_set() or self.state == SourceState.FAILED:
                    break
                if self._slot.seq == last or self._slot.frame is None:
                    continue
                last = self._slot.seq
                frame = self._slot.frame
            self._delivered += 1
            yield frame
        if self.state == SourceState.FAILED:
            raise CameraError(t("camera.error.lost", label=self.info.label))

    def close(self) -> None:
        """Stops the reader thread and releases the device. Safe to call twice."""
        self._stop.set()
        with self._slot.cond:
            self._slot.cond.notify_all()
        alive = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=self.read_timeout_s + 2)
            alive = self._thread.is_alive()
        self._thread = None
        if not alive:   # a reader stuck in a slow open releases the device itself when it returns
            self._release()
        if self.state != SourceState.FAILED:
            self._set_state(SourceState.CLOSED, t("camera.status.closed", label=self.info.label))

    def __enter__(self) -> "OpenCvSource":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- internals ----------------------------------------------------------------------------
    def _set_state(self, state: SourceState, message: str = "") -> None:
        self.state = state
        if self.on_status:
            try:
                self.on_status(SourceStatus(state, message, self.actual_fps, self.dropped))
            except Exception:
                log.exception("on_status failed")

    def _open_capture(self) -> cv2.VideoCapture:
        try:
            if self.info.kind == SourceKind.SYSTEM:
                cap = self._open_system()
            else:
                cap = self._open_stream()
        except cv2.error as e:
            # A driver that throws inside OpenCV ("Unknown C++ exception", GoPro's virtual camera on real
            # hardware 2026-11-14) becomes a message for the coach instead of a traceback.
            log.warning("opening %s failed in OpenCV: %s", safe_url(self.info.id), e)
            raise CameraError(self._open_error()) from e
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            raise CameraError(self._open_error())
        return cap

    def _open_error(self) -> str:
        if self.info.kind == SourceKind.SYSTEM:
            return t("camera.error.system_open", label=self.info.label)
        if self.info.kind == SourceKind.RTSP:
            return t("camera.error.rtsp_open", url=safe_url(self.info.id))
        return t("camera.error.stream_open", label=self.info.label)

    def _open_system(self) -> cv2.VideoCapture | None:
        target, backend = parse_system_id(self.info.id)
        cap = cv2.VideoCapture(target, backend)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(target, cv2.CAP_ANY)
            if not cap.isOpened():
                return cap
        try:
            best = choose_mode(cap, self.modes)
        except cv2.error:
            cap.release()        # a driver that throws must not keep the camera held
            raise
        self.mode = best
        return cap

    def _open_stream(self) -> cv2.VideoCapture:
        opts = self.ffmpeg_options
        if opts is None:
            opts = RTSP_FFMPEG if self.info.id.lower().startswith("rtsp") else LOW_LATENCY_FFMPEG
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.open_timeout_s * 1000),
                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.read_timeout_s * 1000)]
        with _ENV_LOCK:  # the env var is process-global; set it only around this open
            old = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts
            try:
                cap = cv2.VideoCapture(self.info.id, cv2.CAP_FFMPEG, params)
            finally:
                if old is None:
                    os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
                else:
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = old
        if cap.isOpened():
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self.mode = (w, h, float(cap.get(cv2.CAP_PROP_FPS) or 0.0))
            if self.info.kind == SourceKind.RTSP:
                from lopanalys.camera.network import StreamStats
                if self.stats is None:
                    self.stats, self._what = StreamStats(), safe_url(self.info.id)
                else:
                    self.stats.reconnects += 1
                self._pts_base = None
        return cap

    def _release(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                log.exception("release failed")

    def _publish(self, image: np.ndarray, now: float) -> None:
        self._grabbed += 1
        self._meter.tick(now)
        frame = Frame(image=image, t=now, index=self._grabbed - 1)
        with self._slot.cond:
            self._slot.frame = frame
            self._slot.seq += 1
            self._slot.cond.notify_all()

    def _reader(self) -> None:
        try:
            self._read_loop()
        finally:
            if self._stop.is_set():
                self._release()

    def _read_loop(self) -> None:
        last_ok = time.perf_counter()
        while not self._stop.is_set():
            cap = self._cap
            ok, image = (cap.read() if cap is not None else (False, None))
            now = time.perf_counter()
            if ok and image is not None and image.size:
                if image.ndim == 2:
                    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
                self._publish(image, now)
                last_ok = now
                if self.stats is not None:
                    self._measure(cap, now)
                continue
            if self._stop.is_set():
                break
            if now - last_ok < self.stall_s:
                time.sleep(0.01)
                continue
            if not self._reconnect():
                if not self._stop.is_set():
                    self._set_state(SourceState.FAILED, t("camera.error.lost", label=self.info.label))
                break
            last_ok = time.perf_counter()

    def _measure(self, cap: cv2.VideoCapture, now: float) -> None:
        """Network stream: count the picture and compare our clock with the stream's own time stamps.
        ``backlog_ms`` is how much later than at the best moment so far a picture came out of the
        decoder: it grows when pictures queue up on the way (the delay the program can see)."""
        st = self.stats
        st.tick(now)
        if st.first_frame_ms is None:
            st.first_frame_ms = 0.0
        try:
            pts = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
        except cv2.error:
            pts = 0.0
        if pts > 0:
            off = now - pts
            if self._pts_base is None or off < self._pts_base:
                self._pts_base = off
            st.backlog_ms = max(0.0, (off - self._pts_base) * 1000.0)
        st.log_if_due(self._what)

    def _reconnect(self) -> bool:
        """Reopens with back-off until it works, the source is closed or ``max_attempts`` is used up."""
        self._release()
        self._meter.reset()
        delay = 0.5
        attempt = 0
        while not self._stop.is_set():
            if self.max_attempts is not None and attempt >= self.max_attempts:
                return False
            attempt += 1
            self._set_state(SourceState.RECONNECTING, t("camera.status.reconnecting", label=self.info.label, n=attempt))
            try:
                if self.before_reconnect:
                    self.before_reconnect()
                cap = self._open_capture()
                if self._stop.is_set():
                    cap.release()
                    return False
                self._cap = cap
                self._set_state(SourceState.RUNNING, t("camera.status.running", label=self.info.label))
                return True
            except Exception as e:   # keep trying: the camera may come back
                log.info("reconnect %s failed: %s", safe_url(self.info.id), e)
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, self.max_reconnect_s)
        return False


def choose_mode(cap: cv2.VideoCapture, modes: Sequence[tuple[int, int, float]]) -> tuple[int, int, float]:
    """Requests each mode (MJPG first, since raw YUV over USB 2 caps 1080p at ~5 fps) and keeps the
    one with the highest reported fps, preferring larger frames on ties. Returns the mode the
    device reported. Cheap: only property get/set, no frames are read."""
    results: list[tuple[float, int, tuple[int, int, float], tuple[int, int, float]]] = []
    for w, h, fps in modes:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, fps)
        got = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
               float(cap.get(cv2.CAP_PROP_FPS) or 0.0))
        if got[0] <= 0 or got[1] <= 0:
            continue
        # Only trust a mode the camera actually took (its size matches what we asked for).
        if (got[0], got[1]) == (w, h):
            results.append((min(got[2], fps) if got[2] > 0 else 0.0, got[0] * got[1], (w, h, fps), got))
    if not results:
        return (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                float(cap.get(cv2.CAP_PROP_FPS) or 0.0))
    results.sort(key=lambda r: (r[0], r[1]), reverse=True)
    w, h, fps = results[0][2]
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            float(cap.get(cv2.CAP_PROP_FPS) or 0.0))


def probe_system_camera(source_id: str, modes: Sequence[tuple[int, int, float]] = DEFAULT_MODES) -> SourceInfo | None:
    """Opens a system camera briefly and returns its best mode, or None if it cannot be opened."""
    target, backend = parse_system_id(source_id)
    cap = cv2.VideoCapture(target, backend)
    try:
        if not cap.isOpened():
            return None
        try:
            w, h, fps = choose_mode(cap, modes)
        except cv2.error as e:   # a driver that throws inside OpenCV is a camera we can't use
            log.warning("probing camera %s failed in OpenCV: %s", source_id, e)
            return None
        return SourceInfo(SourceKind.SYSTEM, source_id, t("camera.label.system", n=source_id),
                          width=w or None, height=h or None, fps=fps or None)
    finally:
        cap.release()


def with_mode(info: SourceInfo, mode: tuple[int, int, float] | None) -> SourceInfo:
    """A copy of ``info`` with the size and fps from an opened source."""
    if not mode:
        return info
    w, h, fps = mode
    return replace(info, width=w or info.width, height=h or info.height, fps=fps or info.fps)
