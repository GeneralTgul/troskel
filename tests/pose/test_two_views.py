"""Two camera views at once: independent analyses on one clock (pose.multiview, LiveAnalyzer)."""
from __future__ import annotations

import dataclasses
import logging
import random
import threading
import time

import numpy as np
import pytest

from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.core.bus import (TOPIC_GAIT_STEP, TOPIC_GAIT_STEP_2, TOPIC_GAIT_SUMMARY, TOPIC_GAIT_SUMMARY_2,
                                Bus)
from lopanalys.pose.estimator import ReplayEstimator
from lopanalys.pose.live import LiveAnalyzer
from lopanalys.pose.multiview import (SECOND_VIEW_MAX_FPS, SECOND_VIEW_MIN_FPS, balance_second_rate,
                                      contacts_of, match_analyses, match_steps)
from lopanalys.pose.offline import analyze_core
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import View


class ClockSource:
    """Blank frames at ``fps`` in real time, stamped with the shared clock (``time.perf_counter()``)
    like every live source. ``fail_after``: the camera is lost after that many frames."""

    def __init__(self, n: int, fps: float, t0: float, fail_after: int | None = None) -> None:
        self.info = SourceInfo(SourceKind.SYSTEM, f"cam{fps:.0f}", f"fake {fps:.0f}", 320, 180, fps)
        self.n, self.fps, self.t0, self.fail_after = n, fps, t0, fail_after
        self.closed = threading.Event()

    def open(self) -> None:
        pass

    def frames(self):
        img = np.zeros((180, 320, 3), np.uint8)
        for i in range(self.n):
            if self.closed.is_set():
                return
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("kameran försvann")
            wait = self.t0 + i / self.fps - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            yield Frame(img, self.t0 + i / self.fps, i)

    def close(self) -> None:
        self.closed.set()


def replay(p: RunnerParams, t0: float) -> ReplayEstimator:
    """The synthetic runner's poses by frame index (the estimator stamps them with the frame's time)."""
    poses, _ = generate(p)
    return ReplayEstimator(dict(enumerate(poses)))


def test_two_views_run_side_by_side_at_different_rates(caplog):
    caplog.set_level(logging.INFO, logger="lopanalys.pose.live")
    side = RunnerParams(fps=60, duration_s=6, width=320, height=180, noise_px=0.3, view=View.SIDE_LEFT)
    rear = dataclasses.replace(side, fps=30, view=View.REAR, pelvic_drop_deg=6.0)
    bus = Bus()
    got: dict[str, list] = {k: [] for k in (TOPIC_GAIT_STEP, TOPIC_GAIT_SUMMARY, TOPIC_GAIT_STEP_2, TOPIC_GAIT_SUMMARY_2)}
    for k in got:
        bus.subscribe(k, got[k].append)
    t0 = time.perf_counter() + 0.2
    est_a, est_b = replay(side, t0), replay(rear, t0)
    a = LiveAnalyzer(ClockSource(360, 60, t0), bus, 178, View.SIDE_LEFT, estimator=est_a,
                     publish_every_s=0.5, name="sidan")
    b = LiveAnalyzer(ClockSource(180, 30, t0), bus, 178, View.REAR, estimator=est_b,
                     publish_every_s=0.5, name="bakifrån", topics=(TOPIC_GAIT_STEP_2, TOPIC_GAIT_SUMMARY_2),
                     max_fps=12)
    a.start()
    b.start()
    time.sleep(3.0)
    newest_seen, newest_analysed = b.latest_frame().index, b.latest().frame.index
    status_b = b.latest().status
    assert all(r > 0 for r in a.rates() + b.rates())
    deadline = time.perf_counter() + 12
    while (a.running or b.running) and time.perf_counter() < deadline:
        time.sleep(0.1)
    a.stop()
    b.stop()
    # each view has its own steps and its own measures, on its own topics
    assert len(got[TOPIC_GAIT_STEP]) >= 8 and len(got[TOPIC_GAIT_STEP_2]) >= 6
    s1, s2 = got[TOPIC_GAIT_SUMMARY][-1], got[TOPIC_GAIT_SUMMARY_2][-1]
    assert s1.view == "side_left" and s1.overstride_cm is not None and s1.pelvic_drop_deg is None
    assert s2.view == "rear" and s2.pelvic_drop_deg is not None and s2.overstride_cm is None
    assert s1.cadence_spm == pytest.approx(170, abs=5) and s2.cadence_spm == pytest.approx(170, abs=8)
    # different rates: the main view takes every frame, the second one at most 12 a second
    assert est_a.calls > 270                    # 6 s at 60 a second, hardly any left out
    assert 55 <= est_b.calls <= 76              # 6 s at 12 a second, of the 180 the camera gave
    assert b.dropped == 0                       # leaving frames on purpose is not "the computer is slow"
    assert status_b == ""
    assert newest_seen >= newest_analysed       # the recorder's frame is never older than the analysed one
    # the same contacts in both views, at the same time on the shared clock
    m = match_steps(contacts_of(got[TOPIC_GAIT_STEP]), contacts_of(got[TOPIC_GAIT_STEP_2]))
    assert m.matched >= 5 and abs(m.lag_s) < 0.13      # 12 frames a second: a contact is known to about a frame (83 ms)
    # the log says which view each rate belongs to
    lines = [r.getMessage() for r in caplog.records if "analysis" in r.getMessage()]
    assert any(x.startswith("live[sidan]: camera") for x in lines)
    assert any(x.startswith("live[bakifrån]: camera") and "(at most 12)" in x for x in lines)


def test_one_view_drops_the_other_goes_on():
    side = RunnerParams(fps=30, duration_s=4, width=320, height=180, noise_px=0.3)
    rear = dataclasses.replace(side, view=View.REAR)
    bus = Bus()
    sums1, sums2, logs = [], [], []
    bus.subscribe(TOPIC_GAIT_SUMMARY, sums1.append)
    bus.subscribe(TOPIC_GAIT_SUMMARY_2, sums2.append)
    bus.subscribe("log", logs.append)
    t0 = time.perf_counter() + 0.1
    a = LiveAnalyzer(ClockSource(120, 30, t0), bus, 178, View.SIDE_LEFT, estimator=replay(side, t0), publish_every_s=0.5)
    b = LiveAnalyzer(ClockSource(120, 30, t0, fail_after=25), bus, 178, View.REAR, estimator=replay(rear, t0),
                     publish_every_s=0.5, name="telefonen", topics=(TOPIC_GAIT_STEP_2, TOPIC_GAIT_SUMMARY_2))
    a.start()
    b.start()
    deadline = time.perf_counter() + 10
    while a.running and time.perf_counter() < deadline:
        time.sleep(0.1)
    assert b.error == "kameran försvann" and not b.running
    assert b.source_lost and not a.source_lost or not a.running     # the second view knows its camera is gone
    assert any("Kameran slutade" in x for x in logs)
    assert a.error is None and sums1 and sums1[-1].steps >= 6
    assert a.latest().frame.index > 100                      # the main view ran to its end
    a.stop()
    b.stop()


def test_match_steps_finds_the_lag_of_the_second_camera():
    rnd = random.Random(3)
    first = [(i * 0.35, "left" if i % 2 == 0 else "right") for i in range(60)]
    second = [(t + 0.18 + rnd.uniform(-0.012, 0.012), s) for t, s in first if rnd.random() > 0.15]
    m = match_steps(first, second)
    assert m.lag_s == pytest.approx(0.18, abs=0.01)
    assert m.matched >= 45 and m.share > 0.9 and m.spread_ms < 15
    assert all(a_side == b_side for a_side, b_side in ((s, s) for _, _, s in m.pairs))
    d = m.to_dict()
    assert d["matched"] == m.matched and d["lag_s"] == pytest.approx(0.18, abs=0.01)


def test_match_steps_edge_cases():
    first = [(i * 0.35, "left" if i % 2 == 0 else "right") for i in range(40)]
    assert match_steps([], first).matched == 0 and match_steps(first, []).spread_ms is None
    # the second view starts later, and a few of its contacts carry the wrong foot
    late = [(t + 0.05, s if i % 7 else ("right" if s == "left" else "left")) for i, (t, s) in enumerate(first[10:])]
    m = match_steps(first, late)
    assert 20 <= m.matched <= 27 and m.lag_s == pytest.approx(0.05, abs=0.02) and m.first <= 31
    # no overlap in time: nothing to pair
    assert match_steps(first, [(t + 100, s) for t, s in first]).matched == 0


def test_two_recordings_match_through_the_offset_between_the_films():
    side = RunnerParams(fps=60, duration_s=8, width=640, height=360, noise_px=0.5)
    rear = dataclasses.replace(side, fps=30, view=View.REAR, knee_valgus_deg=8.0)
    ps, _ = generate(side)
    pr, _ = generate(rear)
    a = analyze_core(ps, 178, View.SIDE_LEFT, 12.0, times=[p.t for p in ps]).analysis.to_dict()
    # The second film started 1.5 s after the first: its own clock starts at 0 there.
    late = [dataclasses.replace(p, t=p.t - 1.5) for p in pr if p.t >= 1.5]
    b = analyze_core(late, 178, View.REAR, 12.0, times=[p.t for p in late]).analysis.to_dict()
    assert a["summary"]["overstride_cm"] is not None and b["summary"]["knee_valgus_deg"] is not None
    m = match_analyses(a, b, offset_s=1.5)
    assert m is not None and m.matched >= 10 and abs(m.lag_s) < 0.05 and m.share > 0.8
    assert match_analyses(a, None) is None and match_analyses({"steps": []}, b) is None
    wrong = match_analyses(a, b, offset_s=0.0)       # without the offset the films do not line up
    assert wrong is not None and (wrong.matched < m.matched or abs(wrong.lag_s) > 0.05)


def test_balance_second_rate():
    assert balance_second_rate(30, 29.5, 15) == 15                      # all is well, already at the ceiling
    assert balance_second_rate(30, 20, 15) == pytest.approx(11.2, abs=0.1)   # the main view lags: give way
    v = 15.0
    for _ in range(10):
        v = balance_second_rate(30, 15, v)
    assert v == SECOND_VIEW_MIN_FPS
    assert balance_second_rate(30, 29.5, 8) == 9 and balance_second_rate(30, 26, 8) == 8
    assert balance_second_rate(0, 0, 12) == 12 and SECOND_VIEW_MAX_FPS == 15
