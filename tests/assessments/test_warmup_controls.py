"""The warm-up: *Börja testet nu* ends it when it is enough, and the start speed can be moved while
it runs (every stage after it moves by the same amount)."""
from __future__ import annotations

import pytest

from lopanalys.assessments import library
from lopanalys.assessments.protocol import Phase

from .fakes import run, run_until
from .test_runner import make


def zones():
    return library.hr_zones(9.0, 1.0, 4, stage_s=180, warmup_s=360)


def test_start_now_goes_to_the_first_stage():
    r, clock, tm, states, cues = make(zones())
    r.start()
    run(r, clock, 10 + 120)
    assert r.in_warmup and r.state.stage_index == 0
    assert r.start_now()
    assert r.state.stage_index == 1 and r.phase == Phase.RUNNING and not r.in_warmup
    assert tm.speed == 9.0
    assert r.warmup_records[0].duration_s == pytest.approx(120, abs=0.5)
    assert not r.start_now()                       # only in the warm-up


def test_start_now_keeps_the_baseline_lactate_sample():
    p = library.lactate_step(10.0, 1.0, 3, stage_s=240, pause_s=30, warmup_s=600)
    r, clock, tm, states, cues = make(p)
    r.start()
    run(r, clock, 10 + 200)
    assert r.start_now()
    assert r.phase == Phase.LACTATE_PAUSE and r.state.pending_lactate == [0]


def test_shift_start_moves_every_stage():
    r, clock, tm, states, cues = make(zones())
    r.start()
    run(r, clock, 30)
    assert r.shift_start(0.5) == 0.5
    assert r.shift_start(0.5) == 1.0
    assert r.shift_start(-0.5) == 0.5
    speeds = [s.speed_kmh for s in r.stages]
    assert speeds[0] == zones().stages[0].speed_kmh      # the warm-up itself is not moved
    assert speeds[1:] == [9.5, 10.5, 11.5, 12.5, 13.0]
    run_until(r, clock, lambda: r.state.stage_index == 1 and r.phase == Phase.RUNNING)
    assert tm.speed == 9.5
    assert r.shift_start(0.5) == 0.5                     # after the warm-up nothing moves
    run_until(r, clock, lambda: r.state.stage_index == 5)
    assert r.state.next_stage.speed_kmh == 13.5          # the ramp goes on from the moved steps


def test_shift_start_stays_within_the_belt():
    r, clock, tm, states, cues = make(library.hr_zones(6.0, 1.0, 4, stage_s=180, warmup_s=360))
    r.start()
    run(r, clock, 30)
    assert r.shift_start(-5.5) == 0.0                    # below the belt's lowest speed: not made
    assert r.stages[1].speed_kmh == 6.0


def test_the_tv_says_when_the_ramp_begins():
    r, clock, tm, states, cues = make(library.hr_zones(9.0, 1.0, 3, stage_s=180, warmup_s=60))
    r.start()
    run_until(r, clock, lambda: r.state.stage_index == 3 and r.phase == Phase.RUNNING)
    assert r.state.prompt == ""                                   # an ordinary step says nothing
    run_until(r, clock, lambda: r.state.stage_index == 4)          # the ramp's first step
    assert r.state.prompt == "Trappan: +0,5 km/h varje minut"
    assert any(c.kind == "pre_announce" and c.text == "Trappan: +0,5 km/h varje minut" for c in cues)
    run(r, clock, 25)
    assert r.state.prompt == ""                                   # shown for 20 s
    run_until(r, clock, lambda: r.state.stage_index == 5)
    assert r.state.prompt == ""                                   # once, not at every ramp step


def test_the_combined_test_says_its_incline_ramp():
    p = library.lactate_to_max(10.0, 1.0, 2, stage_s=120, pause_s=0, warmup_s=0)
    r, clock, tm, states, cues = make(p)
    r.start()
    run_until(r, clock, lambda: r.state.stage_index == 2 and r.phase == Phase.RUNNING)
    assert r.state.prompt == "Ta laktatprov · Trappan: +1 % varje minut"   # the sample on the move is still said


def test_the_zones_test_has_the_final_sprint():
    p = library.hr_zones(9.0, 1.0, 3, stage_s=180, warmup_s=60)
    assert p.final_sprint_s == 30 and p.final_sprint_kmh == 2.0 and not p.final_sprint_auto
    r, clock, tm, states, cues = make(p)
    r.start()
    run_until(r, clock, lambda: r.state.stage_index == 5 and r.phase == Phase.RUNNING)
    v = tm.speed
    assert r.final_sprint()
    assert tm.speed == v + 2.0 and r.state.stage.label == "Slutspurt"
    run_until(r, clock, lambda: r.phase == Phase.RECOVERY)


def test_a_sprint_at_the_highest_speed_raises_the_incline():
    # the belt's highest speed is 18 km/h (FakeTreadmill): a ramp from 17,5 reaches it
    p = library.hr_zones(14.5, 1.0, 3, stage_s=180, warmup_s=0)
    r, clock, tm, states, cues = make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RUNNING and tm.speed == 18.0)
    inc = tm.incline
    assert r.final_sprint()
    assert tm.speed == 18.0 and tm.incline == inc + 2.0
