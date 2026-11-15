"""One opener per camera: a FrameSource wrapper that holds a per-camera lease from open() to close().

A GoPro (and most webcams) can only be driven by one source at a time. Without this, a camera
preview that is still starting up when the coach searches again or moves on to the live view
finishes later, gives up and runs its ``close()`` -> ``stop_webcam(exit_mode=True)``, which kills
the webcam mode the next source just started (real hardware 2026-11-14).

``ExclusiveSource.open()`` waits (on the caller's thread, never the GUI thread) until the previous
holder of the same camera has closed completely. ``close()`` may be called from any thread at any
time: while ``open()`` is still running it only marks the source closed, and the opening thread
closes the inner source and releases the lease when ``open()`` returns; an inner source with a
``cancel_open()`` method is told to give up early. A second caller of ``open()`` while another thread
is opening (the preview handing its source over to the check page) waits for that open and gets its
result. No Qt imports here.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterator

from lopanalys.camera.base import Frame
from lopanalys.camera.opencv_source import CameraError
from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

_leases_guard = threading.Lock()
_leases: dict[str, threading.Lock] = {}

WAIT_S = 30.0   # longest wait for the previous holder; a GoPro start + stream timeout is ~15 s


def _lease(key: str) -> threading.Lock:
    with _leases_guard:
        return _leases.setdefault(key, threading.Lock())


def camera_busy(key: str) -> bool:
    """True while some source holds the camera ``key`` (for tests and diagnostics)."""
    return _lease(key).locked()


class ExclusiveSource:
    """Wraps a FrameSource so that at most one source at a time has the same camera open."""

    _IDLE, _OPENING, _OPEN, _CLOSING, _CLOSED = range(5)

    def __init__(self, inner: Any, key: str, wait_s: float = WAIT_S) -> None:
        self.inner = inner
        self.key = key
        self.wait_s = wait_s
        self._lock = _lease(key)
        self._state = self._IDLE
        self._held = False
        self._mu = threading.Lock()
        self._closed_evt = threading.Event()
        self._opened_evt = threading.Event()   # set when open() has finished, either way
        self._open_error: BaseException | None = None

    def __getattr__(self, name: str) -> Any:
        # info, latest(), record_clip(), actual_fps, dropped ... come from the wrapped source.
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def open(self) -> None:
        with self._mu:
            if self._state == self._OPEN:
                return
            if self._state == self._OPENING:
                waiting = True
            elif self._state in (self._CLOSING, self._CLOSED):
                if self._open_error is not None:
                    raise self._open_error
                raise CameraError(t("camera.error.closed"))
            else:
                waiting = False
                self._state = self._OPENING
        if waiting:
            # Another thread is opening this source (a handed-over preview): wait for its result.
            self._opened_evt.wait()
            with self._mu:
                if self._state == self._OPEN:
                    return
                err = self._open_error
            if err is not None:
                raise err
            raise CameraError(t("camera.error.closed"))
        try:
            self._open_locked()
        except BaseException as e:
            self._open_error = e
            raise
        finally:
            self._opened_evt.set()

    def _open_locked(self) -> None:
        t0 = time.monotonic()
        deadline = t0 + self.wait_s
        waited = False
        while not self._lock.acquire(timeout=0.1):
            waited = True
            if self._closed_evt.is_set():
                self._finish_close()
                raise CameraError(t("camera.error.closed"))
            if time.monotonic() > deadline:
                with self._mu:
                    self._state = self._CLOSED
                raise CameraError(t("camera.error.busy", label=getattr(getattr(self.inner, "info", None), "label", "")))
        self._held = True
        if waited:
            log.info("camera %s: waited %.2f s for the previous source to close, opening", self.key,
                     time.monotonic() - t0)
        try:
            self.inner.open()
        except Exception:
            self._finish_close()
            raise
        with self._mu:
            if not self._closed_evt.is_set():
                self._state = self._OPEN
                return
        # close() came while we were opening: we own the cleanup.
        self._finish_close()
        raise CameraError(t("camera.error.closed"))

    def frames(self) -> Iterator[Frame]:
        if self._state != self._OPEN:
            self.open()
        yield from self.inner.frames()

    def close(self) -> None:
        """Safe from any thread and to call twice; returns at once while open() is still running."""
        with self._mu:
            self._closed_evt.set()
            if self._state == self._OPENING:
                cancel = getattr(self.inner, "cancel_open", None)
                if callable(cancel):
                    try:
                        cancel()
                    except Exception:
                        log.debug("cancel_open failed", exc_info=True)
                return
            if self._state in (self._CLOSING, self._CLOSED):
                return
            was_open = self._state == self._OPEN
            self._state = self._CLOSING
        if was_open:
            self._finish_close()
        else:
            with self._mu:
                self._state = self._CLOSED

    def _finish_close(self) -> None:
        try:
            if self._held:
                self.inner.close()
        except Exception:
            log.exception("closing camera %s failed", self.key)
        finally:
            with self._mu:
                self._state = self._CLOSED
                held, self._held = self._held, False
            if held:
                self._lock.release()

    @property
    def closed(self) -> bool:
        return self._state == self._CLOSED

    def wait_closed(self, timeout: float | None = None) -> bool:
        """Blocks until the camera is released (tests; never call on the GUI thread)."""
        end = None if timeout is None else time.monotonic() + timeout
        while self._state != self._CLOSED:
            if end is not None and time.monotonic() > end:
                return False
            time.sleep(0.01)
        return True
