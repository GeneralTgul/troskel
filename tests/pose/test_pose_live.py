"""LiveAnalyzer: publishes steps and summaries, skips frames when slow, stops cleanly."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.core.bus import TOPIC_GAIT_STEP, TOPIC_GAIT_SUMMARY, TOPIC_TREADMILL, Bus
from lopanalys.pose.estimator import ReplayEstimator
from lopanalys.pose.live import LiveAnalyzer
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import View


class FakeSource:
    """Delivers small blank frames at ``fps`` in real time (timestamps from the synthetic clock)."""

    def __init__(self, n: int, fps: float) -> None:
        self.info = SourceInfo(SourceKind.SYSTEM, "0", "fake", 320, 180, fps)
        self.n, self.fps = n, fps
        self.closed = threading.Event()
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def frames(self):
        img = np.zeros((180, 320, 3), np.uint8)
        t0 = time.perf_counter()
        for i in range(self.n):
            if self.closed.is_set():
                return
            wait = t0 + i / self.fps - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            yield Frame(img, i / self.fps, i)

    def close(self) -> None:
        self.closed.set()


def poses_by_index(p):
    poses, _ = generate(p)
    return {i: q for i, q in enumerate(poses)}


def test_live_publishes_and_stops():
    p = RunnerParams(fps=60, duration_s=6, width=320, height=180, noise_px=0.3)
    bus = Bus()
    steps, sums = [], []
    bus.subscribe(TOPIC_GAIT_STEP, steps.append)
    bus.subscribe(TOPIC_GAIT_SUMMARY, sums.append)
    src = FakeSource(int(6 * 60), 60)
    la = LiveAnalyzer(src, bus, 178, View.SIDE_LEFT, estimator=ReplayEstimator(poses_by_index(p)),
                      publish_every_s=0.5)
    la.start()
    bus.publish(TOPIC_TREADMILL, type("S", (), {"speed_kmh": 12.0})())
    deadline = time.perf_counter() + 15
    while la.running and time.perf_counter() < deadline:
        time.sleep(0.1)
    r = la.latest()
    la.stop()
    assert src.opened and not la.running
    assert r is not None and r.overlay.shape == (180, 320, 3) and r.pose is not None
    assert r.fps_camera == pytest.approx(60, rel=0.25)
    assert len(steps) >= 8
    ts = [s.t_contact for s in steps]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)        # each contact once
    assert sums and sums[-1].cadence_spm == pytest.approx(170, abs=4)
    assert la.speed_kmh == 12.0


def test_live_skips_frames_when_slow():
    p = RunnerParams(fps=60, duration_s=3, width=320, height=180)
    src = FakeSource(180, 60)
    la = LiveAnalyzer(src, None, 178, View.SIDE_LEFT, estimator=ReplayEstimator(poses_by_index(p), delay_s=0.05))
    la.start()
    time.sleep(1.5)
    r = la.latest()
    la.stop()
    assert r is not None
    assert r.dropped > 20
    assert r.fps_analysis < 30
    # never behind the camera: the newest result is from one of the last frames delivered
    assert r.frame.index >= int(1.5 * 60) - 10


def test_estimator_failure_is_reported():
    def broken():
        raise RuntimeError("modellen saknas")
    bus = Bus()
    logs = []
    bus.subscribe("log", logs.append)
    la = LiveAnalyzer(FakeSource(10, 30), bus, 178, View.SIDE_LEFT, estimator=broken)
    la.start()
    time.sleep(0.5)
    la.stop()
    assert la.error == "modellen saknas"
    assert any("modellen saknas" in x for x in logs)


def test_no_runner_status():
    la = LiveAnalyzer(FakeSource(20, 60), None, 178, View.REAR, estimator=ReplayEstimator({}))
    la.start()
    time.sleep(0.6)
    r = la.latest()
    la.stop()
    assert r is not None and r.pose is None and r.status == "Ingen löpare i bild"
