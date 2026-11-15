"""Live values of a bike fit and double poling: the analysis after the test on a window of
poses, the median of the newest revolutions or strokes, "–" until there are enough, and the live
analyzer that publishes them instead of running values."""
from __future__ import annotations

import time

import numpy as np
import pytest

from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.core.bus import TOPIC_GAIT_SUMMARY, TOPIC_TECH_SUMMARY, Bus
from lopanalys.pose import technique_live as tl
from lopanalys.pose.bike import analysis as bike
from lopanalys.pose.bike import synthetic as bs
from lopanalys.pose.estimator import ReplayEstimator
from lopanalys.pose.live import LiveAnalyzer
from lopanalys.pose.ski import analysis as ski
from lopanalys.pose.ski import synthetic as ss
from lopanalys.pose.types import View


def _cyclist(view=View.SIDE_LEFT, fps=30.0, seconds=12.0, **kw):
    if view == View.FRONT:
        return bs.CyclistParams(height_cm=180, view=view, fps=fps, duration_s=seconds, width=540, height=960,
                                knee_lateral_cm=5.5)
    return bs.with_knee(bs.CyclistParams(height_cm=180, view=view, fps=fps, duration_s=seconds, width=960,
                                         height=540, **kw), 50.0)


def test_bikefit_side_values_are_the_reports_with_its_ranges():
    p = _cyclist(elbow_flex_deg=4.0, shoulder_angle_deg=101.0)
    poses, times = bs.generate(p)
    live = tl.summarize("bikefit", poses, times, 180, "side_left")
    truth = bs.truth(p)
    assert list(live.values) == tl.metrics("bikefit", "side_left")
    for key in ("knee_flex_bottom_deg", "hip_angle_top_deg", "trunk_angle_deg", "elbow_flex_deg"):
        assert live.values[key] == pytest.approx(truth[key], abs=1.5), key
    R = bike.refs(View.SIDE_LEFT)
    assert tl.refs("bikefit", "side_left")["knee_flex_bottom_deg"] is not None
    assert tl.refs("bikefit", "side_left")["knee_flex_bottom_deg"].text() == R["knee_flex_bottom_deg"].text() == "33–43°"
    for key, val in live.values.items():
        assert live.status[key] == R[key].classify(val).value
    assert live.status["knee_flex_bottom_deg"] == "watch"          # 50 deg: the saddle is low
    assert live.cycles >= tl.LAST_CYCLES and live.cadence == pytest.approx(90, abs=1)


def test_the_median_is_over_the_newest_revolutions():
    early = _cyclist(seconds=12.0)
    poses, times = bs.generate(early)
    rows = bike.analyze_core(poses, 180, View.SIDE_LEFT, times=times).analysis.cycles
    want = float(np.median([r["knee_flex_bottom_deg"] for r in rows[-tl.LAST_CYCLES:]]))
    assert tl.summarize("bikefit", poses, times, 180, "side_left").values["knee_flex_bottom_deg"] == pytest.approx(want)


def test_nothing_until_enough_revolutions():
    poses, times = bs.generate(_cyclist(seconds=12.0))
    n = int(1.5 * 30)                                              # 1.5 s at 90 rpm: two revolutions at most
    live = tl.summarize("bikefit", poses[:n], times[:n], 180, "side_left")
    assert live.cycles < tl.MIN_CYCLES and all(v is None for v in live.values.values())
    assert all(s is None for s in live.status.values())
    empty = tl.summarize("bikefit", [None] * 30, [i / 30 for i in range(30)], 180, "side_left")
    assert empty.values == {k: None for k in tl.metrics("bikefit", "side_left")}


def test_bikefit_front_shows_the_knee_track_per_leg():
    poses, times = bs.generate(_cyclist(View.FRONT))
    live = tl.summarize("bikefit", poses, times, 180, "front")
    assert list(live.values) == ["knee_lateral_left_cm", "knee_lateral_right_cm"]
    assert all(v == pytest.approx(5.5, abs=0.5) for v in live.values.values())
    assert set(live.status.values()) == {"watch"}                  # over 3 cm, a rule of thumb


def test_double_poling_values_are_the_reports():
    q = ss.SkierParams(height_cm=180, view=View.SIDE_LEFT, fps=30, duration_s=20, width=720, height=1280,
                       trunk_lean_end_deg=38.0, trunk_lead_ms=30.0, knee_flex_end_deg=56.0)
    poses, times = ss.generate(q)
    live = tl.summarize("ski", poses, times, 180, "side_left")
    truth = ss.truth(q)
    assert list(live.values) == ["elbow_angle_start_deg", "trunk_lean_end_deg", "hip_flex_end_deg", "knee_flex_end_deg"]
    for key, val in live.values.items():
        assert val == pytest.approx(truth[key], abs=1.5), key
        assert live.status[key] == ski.refs()[key].classify(val).value
    assert live.status["trunk_lean_end_deg"] == "watch" and live.status["elbow_angle_start_deg"] == "ok"
    assert tl.metrics("ski", "front") == [] and tl.window_s("ski") > tl.window_s("bikefit")


class _Source:
    def __init__(self, n: int, fps: float) -> None:
        self.info = SourceInfo(SourceKind.SYSTEM, "0", "fake", 960, 540, fps)
        self.n, self.fps = n, fps

    def open(self) -> None:
        pass

    def frames(self):
        img = np.zeros((540, 960, 3), np.uint8)
        t0 = time.perf_counter()
        for i in range(self.n):
            wait = t0 + i / self.fps - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            yield Frame(img, i / self.fps, i)

    def close(self) -> None:
        pass


def test_live_analyzer_publishes_the_bike_fit_values_and_no_running_values():
    p = _cyclist(fps=30.0, seconds=5.0)
    poses, _ = bs.generate(p)
    bus = Bus()
    tech, gait = [], []
    bus.subscribe(TOPIC_TECH_SUMMARY, tech.append)
    bus.subscribe(TOPIC_GAIT_SUMMARY, gait.append)
    la = LiveAnalyzer(_Source(len(poses), 30.0), bus, 180, View.SIDE_LEFT,
                      estimator=ReplayEstimator(dict(enumerate(poses))), publish_every_s=0.5, technique="bikefit")
    assert not la.labels and la.window_s >= tl.window_s("bikefit")
    la.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not (tech and tech[-1].values["knee_flex_bottom_deg"] is not None):
            time.sleep(0.1)
    finally:
        la.stop()
    assert tech and tech[-1].values["knee_flex_bottom_deg"] == pytest.approx(50.0, abs=2.0)
    assert la.tech_summary is tech[-1] and not gait and la.summary is None


def test_the_demo_skier_keeps_poling_after_the_length_of_its_strokes():
    from lopanalys.ui.demo import TechniqueAthlete
    a = TechniqueAthlete("ski", "side_left", 720, 1280)
    wrist = [float(a.landmarks(t)[15][1]) for t in np.arange(40.0, 43.0, 0.1)]
    assert max(wrist) - min(wrist) > 100                           # the hand still goes up and down
