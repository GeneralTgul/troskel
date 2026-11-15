"""After a test on a belt the program drives: a minute at walking speed with a countdown to the stop,
then the belt stops (before 2026-11-15 it was left at 5 km/h)."""
from __future__ import annotations

import pytest

from lopanalys.assessments import library
from lopanalys.assessments.protocol import Phase, Protocol, Stage

from .test_runner import make
from .fakes import run, run_until


def test_last_stage_then_a_minute_of_walking_and_the_belt_stops():
    p = Protocol("t", "gait", [Stage(10, 1, 60), Stage(12, 1, 60)])
    r, clock, tm, states, cues = make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RECOVERY)
    assert tm.speed == 5.0 and tm.incline == 0.0 and tm.running
    st = r.state
    assert st.countdown_s == pytest.approx(60, abs=0.5)
    assert st.announcement == "Bandet stannar om 60 s"
    run(r, clock, 30)
    assert r.state.announcement == "Bandet stannar om 30 s"
    assert r.phase == Phase.RECOVERY and tm.running
    run_until(r, clock, lambda: r.done)
    assert r.phase == Phase.FINISHED
    assert tm.log[-1] == ("stop",) and not tm.running
    assert [c.kind for c in cues].count("finish") == 1
    assert r.samples[-1].t_s >= 10 + 120 + 59          # the recovery heart rate is recorded


def test_after_exhaustion_the_belt_stops_too():
    p = library.max_hr_ramp(12.0, warmup_s=60, step_s=60)
    r, clock, tm, states, cues = make(p)
    r.start()
    run(r, clock, 10 + 60 + 90)
    r.exhausted()
    assert r.state.announcement == "Bandet stannar om 60 s"
    run_until(r, clock, lambda: r.done)
    assert tm.log[-1] == ("stop",) and not tm.running


def test_ending_the_recovery_early_stops_the_belt():
    p = Protocol("t", "gait", [Stage(10, 1, 30)])
    r, clock, tm, states, cues = make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RECOVERY)
    r.stop()                       # "Avsluta återhämtningen"
    assert r.phase == Phase.FINISHED and not tm.running


def test_manual_belt_is_never_commanded():
    p = Protocol("t", "gait", [Stage(10, 1, 30)])
    r, clock, tm, states, cues = make(p, controllable=False)
    r.start()
    r.operator_ready()
    run_until(r, clock, lambda: r.done)
    assert tm.log == [] and r.phase == Phase.FINISHED
