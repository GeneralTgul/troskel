"""A speed or incline set by hand on the belt's panel: the stage is saved as the belt ran it, the
change is said, and the next stage's target does not replace it unnoticed. In a test on 2026-11-15
18 km/h and 2 % were set by hand for the last minute and the stage was saved as 17,5 km/h and 1 %."""
from __future__ import annotations

import pytest

from lopanalys.assessments import hr_zones, library
from lopanalys.assessments.protocol import Phase, Protocol, Stage
from lopanalys.core.bus import TOPIC_LOG

from .fakes import run, run_until
from .test_runner import make


def test_a_stage_set_by_hand_is_saved_as_the_belt_ran_it():
    p = Protocol("t", "hr_zones", [Stage(17.0, 1, 60), Stage(17.5, 1, 60), Stage(17.5, 1, 60)])
    r, clock, tm, states, cues = make(p, recovery_s=0)
    said = []
    r.bus.subscribe(TOPIC_LOG, said.append)
    r.start()
    run_until(r, clock, lambda: r.state.stage_index == 1 and r.phase == Phase.RUNNING)
    run(r, clock, 2)
    tm.speed, tm.incline = 18.0, 2.0          # by hand on the panel, nearly the whole stage
    run_until(r, clock, lambda: r.state.stage_index == 2)
    rec = r.records[1]
    assert rec.speed_kmh == pytest.approx(18.0, abs=0.1) and rec.incline_pct == pytest.approx(2.0, abs=0.1)
    assert r.records[0].speed_kmh == 17.0 and r.records[0].incline_pct == 1.0   # as planned: the plan's numbers
    st = r.belt_stats
    assert st and st[0]["index"] == 1 and st[0]["by_hand"] and st[0]["target_kmh"] == 17.5
    assert any("för hand till 18,0 km/h · 2 %" in x for x in said)
    # the next stage's target replaces the change by hand, and the TV says so
    assert r.state.prompt == "Bandet gick på 18,0 km/h · 2 %, inställt för hand. Nu: 17,5 km/h · 1 %"
    assert tm.speed == 17.5


def test_a_change_at_the_end_is_kept_with_the_stage():
    p = Protocol("t", "hr_zones", [Stage(10.0, 1, 180), Stage(11.0, 1, 60)])
    r, clock, tm, states, cues = make(p, recovery_s=0)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RUNNING)
    run(r, clock, 120)
    tm.speed = 12.0                           # the last minute only
    run_until(r, clock, lambda: r.state.stage_index == 1)
    rec, st = r.records[0], r.belt_stats[0]
    assert rec.speed_kmh == pytest.approx(10.0 + 2.0 * 60 / 172, abs=0.15)   # time-weighted mean
    assert st["end_kmh"] == 12.0 and st["by_hand"]


def test_a_belt_that_reports_a_little_off_keeps_the_plan():
    p = Protocol("t", "hr_zones", [Stage(10.0, 1, 60)])
    r, clock, tm, states, cues = make(p, recovery_s=0)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RUNNING)
    tm.speed = 9.9
    run_until(r, clock, lambda: r.done)
    assert r.records[0].speed_kmh == 10.0 and r.belt_stats == []


def test_the_zones_use_the_measured_speed():
    recs = [type("R", (), dict(index=i + 1, speed_kmh=v, incline_pct=1.0, duration_s=180, hr_end=h))()
            for i, (v, h) in enumerate([(9.0, 130), (10.0, 140), (11.0, 150), (12.4, 164)])]
    res = hr_zones.analyze(recs, [])
    assert [p.speed_kmh for p in res.points] == [9.0, 10.0, 11.0, 12.4]
