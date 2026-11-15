"""The live recording keeps the camera's capture time per frame (real hardware 2026-11-14, cadence 233)."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import numpy as np

from lopanalys.camera.base import Frame
from lopanalys.ui.live_controller import _RecorderThread


class _Analyzer:
    """Hands out frames with known capture times, like LiveAnalyzer.latest()."""

    def __init__(self, stamps: list[float]) -> None:
        self.source = SimpleNamespace(info=SimpleNamespace(fps=30.0))
        self._stamps = stamps
        self._i = 0
        self._lock = threading.Lock()
        self.done = threading.Event()

    def latest(self):
        with self._lock:
            i = min(self._i, len(self._stamps) - 1)
            self._i += 1
            if self._i >= len(self._stamps):
                self.done.set()
        img = np.zeros((36, 64, 3), np.uint8)
        return SimpleNamespace(frame=Frame(img, self._stamps[i], i))


def test_sidecar_has_the_capture_times(tmp_path):
    stamps = [100.0 + k * 0.04 for k in range(12)]      # 25 fps, unlike the poll interval (1/60 s)
    an = _Analyzer(stamps)
    rec = _RecorderThread(an, tmp_path / "live.mp4")
    rec.start()
    assert an.done.wait(5)
    time.sleep(0.05)
    rec.stop()
    side = json.loads((tmp_path / "live.mp4.json").read_text(encoding="utf-8"))
    assert side["t"] == [round(s - stamps[0], 6) for s in stamps[:len(side["t"])]]
    assert len(side["t"]) == len(stamps)
    assert abs(side["measured_fps"] - 25.0) < 0.01
