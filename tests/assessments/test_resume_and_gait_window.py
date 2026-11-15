"""Continuing a cut-off test from a later stage, and which stages of a max HR ramp are filmed
for the technique ("maxpuls med löpteknik")."""
from types import SimpleNamespace as NS

import pytest

from lopanalys.assessments import library
from lopanalys.assessments.max_hr import gait_window
from lopanalys.assessments.protocol import Phase
from lopanalys.core.models import StepRecord, TimeSample

from .fakes import run, run_until
from .test_runner import make


def test_start_from_a_later_stage_continues_the_clock():
    p = library.lactate_step(10.0, 1.0, 4, stage_s=240, pause_s=30, warmup_s=0)
    r, clock, tm, states, cues = make(p)
    r.start(from_index=2, t_offset_s=600.4)
    assert r.state.stage_index == 2 and r.phase == Phase.COUNTDOWN
    run(r, clock, 10.25)
    assert r.phase == Phase.RUNNING and tm.speed == 12.0
    run_until(r, clock, lambda: r.phase == Phase.LACTATE_PAUSE)
    assert [x.index for x in r.records] == [2]
    assert r.samples[0].t_s == 601.0                          # the series goes on after the saved one
    assert r.state.total_elapsed_s > 600 + 240


def test_start_index_out_of_range():
    p = library.lactate_step(10.0, 1.0, 2, warmup_s=0)
    r, *_ = make(p)
    with pytest.raises(ValueError):
        r.start(from_index=5)


def test_stop_keeps_a_partial_gait_stage():
    p = library.gait_check([10.0, 12.0], stage_s=60)
    r, clock, tm, states, cues = make(p)
    r.start()
    run(r, clock, 10.25 + 40)
    r.stop()
    assert r.phase == Phase.ABORTED
    (rec,) = r.records
    assert rec.index == 0 and rec.duration_s == pytest.approx(40, abs=0.5)


def test_stop_early_in_a_lactate_stage_keeps_nothing_partial():
    p = library.lactate_step(10.0, 1.0, 2, warmup_s=0)
    r, clock, *_ = make(p)
    r.start()
    run(r, clock, 10.25 + 100)
    r.stop()
    assert r.records == []


def _ramp(hrs, dur=60.0, first=1):
    return [StepRecord(index=first + i, speed_kmh=10 + 0.5 * i, incline_pct=1.0, duration_s=dur, hr_end=h)
            for i, h in enumerate(hrs)]


def test_gait_window_below_90_percent_and_not_the_last_two_minutes():
    recs = _ramp([150, 158, 165, 170, 175, 180, 185, 190])   # peak 190 -> 171 bpm
    end = 600 + 8 * 60.0                                      # warm-up 600 s, exhaustion at the end
    w = gait_window(recs, [], end)
    assert w["basis"] == "hr" and w["peak_hr"] == 190
    assert w["stages"] == [1, 2, 3, 4]                        # 170 is the last one under 171
    assert w["cutoff_s"] == 600 + 4 * 60


def test_gait_window_tail_wins_when_hr_stays_low():
    recs = _ramp([120, 121, 122, 123, 124], dur=60)
    w = gait_window(recs, [TimeSample(0, 190)], 300.0)        # the peak from the samples
    assert w["stages"] == [1, 2, 3]                           # the last 120 s left out
    assert w["cutoff_s"] == 180.0


def test_gait_window_without_hr_drops_the_last_three():
    recs = _ramp([None] * 7)
    w = gait_window(recs, [], 600 + 7 * 60.0)
    assert w["basis"] == "count" and w["stages"] == [1, 2, 3, 4]


def test_gait_window_only_warmup_when_the_first_stage_is_too_hard():
    recs = _ramp([180, 185, 190])
    w = gait_window(recs, [], 600 + 3 * 60.0)
    assert w["stages"] == [] and w["cutoff_s"] == 600.0
