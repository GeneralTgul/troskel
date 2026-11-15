"""Moves bus messages from worker threads onto the Qt GUI thread.

Bus callbacks run on whatever thread published (Bluetooth loop, camera, runner). Qt widgets
may only be touched on the GUI thread, so ``BusBridge`` subscribes to every topic and re-emits
each payload as a Qt signal. The bridge object lives on the GUI thread, so Qt queues the
signal across threads automatically.

Also here: ``Worker``, a QRunnable-style helper for blocking calls (scans, downloads,
offline analysis) that reports back through signals.
"""
from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from lopanalys.core.bus import (TOPIC_DEVICE_STATUS, TOPIC_GAIT_STEP, TOPIC_GAIT_SUMMARY, TOPIC_GAIT_SUMMARY_2,
                                TOPIC_HEART_RATE, TOPIC_LOG, TOPIC_TECH_SUMMARY, TOPIC_TECH_SUMMARY_2,
                                TOPIC_TEST_CUE, TOPIC_TEST_STATE, TOPIC_TREADMILL, Bus)

log = logging.getLogger(__name__)


class BusBridge(QObject):
    """Qt signals for every bus topic. Create on the GUI thread; call ``close()`` when done."""

    treadmill = Signal(object)
    heart_rate = Signal(object)
    gait_step = Signal(object)
    gait_summary = Signal(object)
    gait_summary_2 = Signal(object)   # the second camera view
    tech_summary = Signal(object)     # pose.technique_live.TechniqueLive (bike fit, double poling)
    tech_summary_2 = Signal(object)   # the second camera view's
    test_state = Signal(object)
    device_status = Signal(object)
    log_line = Signal(str)
    test_cue = Signal(object)      # assessments.runner.Cue (topic "test_cue")

    def __init__(self, bus: Bus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bus = bus
        pairs = [(TOPIC_TREADMILL, self.treadmill), (TOPIC_HEART_RATE, self.heart_rate),
                 (TOPIC_GAIT_STEP, self.gait_step), (TOPIC_GAIT_SUMMARY, self.gait_summary),
                 (TOPIC_GAIT_SUMMARY_2, self.gait_summary_2), (TOPIC_TECH_SUMMARY, self.tech_summary),
                 (TOPIC_TECH_SUMMARY_2, self.tech_summary_2),
                 (TOPIC_TEST_STATE, self.test_state), (TOPIC_DEVICE_STATUS, self.device_status),
                 (TOPIC_LOG, self.log_line), (TOPIC_TEST_CUE, self.test_cue)]
        self._unsubs = [bus.subscribe(topic, sig.emit) for topic, sig in pairs]

    def close(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()


class Worker(QObject):
    """Runs ``fn(*args, progress=cb)`` (or ``fn(*args)``) on a daemon thread, off the GUI thread.

    Signals (delivered on the GUI thread): ``progress(float 0..1)``, ``done(result)``,
    ``failed(message, traceback)``, ``finished()``. A daemon thread never keeps the app from
    quitting, even when a long analysis cannot be interrupted.
    """

    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str, str)
    finished = Signal()

    def __init__(self, fn: Callable[..., Any], *args: Any, with_progress: bool = False,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._with_progress = with_progress
        self._thread = threading.Thread(target=self.run, daemon=True, name=f"ui-worker-{getattr(fn, '__name__', 'fn')}")

    def start(self) -> None:
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread.is_alive()

    def wait(self, timeout_ms: int) -> bool:
        self._thread.join(timeout_ms / 1000)
        return not self._thread.is_alive()

    def run(self) -> None:
        try:
            if self._with_progress:
                r = self._fn(*self._args, progress=lambda v: self.progress.emit(float(v)))
            else:
                r = self._fn(*self._args)
            self.done.emit(r)
        except Exception as e:  # reported to the user by the caller
            log.exception("worker failed")
            self.failed.emit(str(e) or e.__class__.__name__, traceback.format_exc())
        finally:
            self.finished.emit()


_running: set[Worker] = set()


def run_async(fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None,
              on_progress: Callable[[float], None] | None = None) -> Worker:
    """Start ``fn`` on a worker thread and call back on the GUI thread. Keeps the worker alive."""
    w = Worker(fn, *args, with_progress=on_progress is not None)
    if on_done:
        w.done.connect(on_done)
    if on_error:
        w.failed.connect(lambda msg, _tb: on_error(msg))
    if on_progress:
        w.progress.connect(on_progress)
    _running.add(w)
    w.finished.connect(lambda: _running.discard(w))
    w.start()
    return w


def wait_all(timeout_ms: int = 3000) -> None:
    """Give running workers a moment at shutdown (daemon threads never block the exit)."""
    for w in list(_running):
        w.wait(timeout_ms)
