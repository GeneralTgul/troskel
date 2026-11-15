"""The ramp goes on past the treadmill's limits, the guide's plan, and the
automatic final sprint. FakeTreadmill: 18 km/h and 12 % like a Gymkompaniet Recoil."""
import pytest

from lopanalys.assessments import library, ramp
from lopanalys.assessments.library import TreadmillLimits
from lopanalys.assessments.protocol import Phase, Protocol
from lopanalys.assessments.runner import TOPIC_LOG, TestRunner
from lopanalys.core.bus import Bus

from .fakes import FakeClock, FakeHR, FakeTreadmill, run, run_until

RECOIL = TreadmillLimits(max_speed_kmh=18.0, max_incline_pct=12.0)


def make(p, tm=None, hr_max=192, **kw):
    clock = FakeClock()
    tm = tm or FakeTreadmill()
    hr = FakeHR(clock, lambda: (tm.speed, tm.incline), max_hr=hr_max)
    bus = Bus()
    logs = []
    bus.subscribe(TOPIC_LOG, logs.append)
    r = TestRunner(p, tm, hr, bus, clock=clock, threaded=False, **kw)
    return r, clock, tm, logs


def test_next_step_rules():
    p = library.max_hr_ramp(12.0, warmup_s=0, limits=RECOIL)
    assert ramp.next_step(12.0, 11.0, p, RECOIL) == (12.0, 12.0, "")
    assert ramp.next_step(12.0, 12.0, p, RECOIL) == (12.5, 12.0, "speed")
    assert ramp.next_step(18.0, 12.0, p, RECOIL) == (18.0, 12.0, "at_max")
    sp = library.max_hr_ramp(10.0, mode="speed", step_s=30, warmup_s=0, limits=RECOIL)
    assert ramp.next_step(17.5, 1.0, sp, RECOIL) == (18.0, 1.0, "")
    assert ramp.next_step(18.0, 1.0, sp, RECOIL) == (18.0, 2.0, "incline")


def test_incline_ramp_goes_on_with_speed_at_12_percent():
    p = library.max_hr_ramp(12.0, warmup_s=0, step_s=60, start_incline_pct=1.0)
    r, clock, tm, logs = make(p)
    r.start()
    run(r, clock, 10 + 60 * 14 + 30)          # step 15
    recs = r.records
    assert [x.incline_pct for x in recs][:12] == [float(i) for i in range(1, 13)]
    assert [(x.speed_kmh, x.incline_pct) for x in recs[11:]] == [(12.0, 12.0), (12.5, 12.0), (13.0, 12.0)]
    assert tm.speed == 13.5 and tm.incline == 12.0
    assert any("högsta lutningen (12 %)" in m and "0,5 km/h" in m for m in logs)
    # the TV said it when it happened
    assert r.phase == Phase.RUNNING


def test_speed_ramp_goes_on_with_incline_and_says_when_all_is_at_max():
    p = library.max_hr_ramp(16.0, mode="speed", step_s=30, warmup_s=0, start_incline_pct=1.0)
    r, clock, tm, logs = make(p)
    r.start()
    run_until(r, clock, lambda: len(r.records) >= 5)
    assert [x.speed_kmh for x in r.records][:5] == [16.0, 16.5, 17.0, 17.5, 18.0]
    run_until(r, clock, lambda: len(r.records) >= 7)
    assert [(x.speed_kmh, x.incline_pct) for x in r.records[5:7]] == [(18.0, 2.0), (18.0, 3.0)]
    assert any("högsta farten (18,0 km/h)" in m for m in logs)
    run_until(r, clock, lambda: len(r.records) >= 18)
    assert r.records[-1].incline_pct == 12.0 and r.records[-1].speed_kmh == 18.0
    assert any("max fart och lutning" in m for m in logs)
    assert sum("max fart och lutning" in m for m in logs) == 1      # said once
    assert r.phase == Phase.RUNNING                                  # goes on until stopped


def test_coach_setting_narrows_what_the_device_reports():
    # QZ reports 20 km/h and 15 % for every treadmill; the setting says the belt has 12 %.
    tm = FakeTreadmill(max_speed_kmh=20.0, max_incline_pct=15.0)
    p = library.max_hr_ramp(12.0, warmup_s=0, step_s=60)
    r, clock, tm, logs = make(p, tm=tm, limits=TreadmillLimits(max_speed_kmh=18.0, max_incline_pct=12.0))
    r.start()
    run(r, clock, 10 + 60 * 13 + 5)
    assert max(x.incline_pct for x in r.records) == 12.0
    assert r.records[-1].speed_kmh == 12.5
    unset = ramp.narrower(TreadmillLimits(max_speed_kmh=20, max_incline_pct=15),
                          TreadmillLimits(max_speed_kmh=0, max_incline_pct=0))
    assert (unset.max_speed_kmh, unset.max_incline_pct) == (20, 15)


def test_step_n_has_n_percent_and_the_warmup_the_start_incline():
    p = library.max_hr_ramp(12.0, warmup_s=120, step_s=60, start_incline_pct=1.0)
    assert p.stages[0].incline_pct == p.stages[1].incline_pct == 1.0
    assert "1 % lutning på steg 1" in p.description
    r, clock, tm, logs = make(p)
    r.start()
    run(r, clock, 10 + 120 + 60 * 12 + 30)
    assert [(x.index, x.incline_pct) for x in r.records][:12] == [(i, float(i)) for i in range(1, 13)]
    flat = library.max_hr_ramp(12.0, start_incline_pct=0.0)
    assert flat.stages[0].incline_pct == 0.0                         # the warm-up follows the start
    assert library.max_hr_ramp(10.0, mode="speed", start_incline_pct=2.0).stages[0].incline_pct == 1.0


def test_combined_ramp_goes_on_with_speed():
    p = library.lactate_to_max(10.0, 1.0, 2, stage_s=60, pause_s=0, warmup_s=0, limits=RECOIL)
    assert "högsta lutning" in p.description
    r, clock, tm, logs = make(p)
    r.start()
    run(r, clock, 10 + 120 + 60 * 13 + 30)
    ramp_recs = [x for x in r.records if x.index >= 2]
    assert [x.incline_pct for x in ramp_recs][:12] == [float(i) for i in range(1, 13)]   # step N: N %
    assert ramp_recs[12].speed_kmh == 11.5 and ramp_recs[12].incline_pct == 12.0


def test_plan_times_and_warnings():
    p = library.max_hr_ramp(12.0, warmup_s=600, step_s=60, start_incline_pct=1.0, limits=RECOIL)
    pl = ramp.plan(p, RECOIL)
    assert pl.to_incline_max_min == 11.0          # step 12 (12 %) starts after 11 min
    assert pl.to_speed_max_min == 23.0            # then 12 more steps of 0.5 km/h
    assert pl.to_full_max_min == 23.0
    assert pl.expected_min is None and pl.warning == ""
    # a fit runner: VO2 of 12 km/h at 12 % is ~61; one with VO2max 62 needs 13 min -> too long
    vo2_12 = ramp.acsm_running_vo2(12.0, 12.0)
    long = ramp.plan(p, RECOIL, target_vo2=vo2_12 + 1)
    assert long.expected_min > 12 and long.warning == "long"
    short = ramp.plan(p, RECOIL, target_vo2=ramp.acsm_running_vo2(12.0, 4.0))
    assert short.expected_min == 4.0 and short.warning == "short"
    fit = vo2_12 + 1
    v = ramp.suggest_speed(lambda s: library.max_hr_ramp(s, warmup_s=600, limits=RECOIL), fit, RECOIL)
    ok = ramp.plan(library.max_hr_ramp(v, warmup_s=600, limits=RECOIL), RECOIL, fit)
    assert v > 12.0 and 8 <= ok.expected_min <= 12 and ok.warning == ""


def test_plan_of_lactate_to_max_starts_at_the_ramp():
    p = library.lactate_to_max(10.0, 1.0, 6, limits=RECOIL)
    st = ramp.steps(p, RECOIL)
    assert st[0].speed_kmh == 15.0 and st[0].incline_pct == 1.0
    assert ramp.plan(p, RECOIL).to_incline_max_min == 11.0


def test_auto_sprint_after_12_min_without_a_press():
    p = library.max_hr_ramp(10.0, warmup_s=0, step_s=60, start_incline_pct=0.0,
                            final_sprint_s=30, final_sprint_auto=True)
    assert p.final_sprint_auto
    r, clock, tm, logs = make(p, hr_max=240)          # heart rate never levels off
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RECOVERY)
    recs = r.records
    assert len(recs) == 13 and recs[-1].duration_s == pytest.approx(30, abs=0.5)
    assert recs[-1].speed_kmh == recs[-2].speed_kmh + 2.0
    assert sum(x.duration_s for x in recs[:-1]) == pytest.approx(720, abs=1)
    assert r.exhaustion_t_s is not None


def test_auto_sprint_when_heart_rate_levels_off():
    p = library.max_hr_ramp(12.0, warmup_s=0, step_s=60, start_incline_pct=1.0,
                            final_sprint_s=30, final_sprint_auto=True)
    r, clock, tm, logs = make(p, hr_max=175)          # FakeHR reaches 175 at about 8 % and stays
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RECOVERY)
    recs = r.records
    assert recs[-1].speed_kmh == 14.0 and recs[-1].duration_s == pytest.approx(30, abs=0.5)
    ramp_min = sum(x.duration_s for x in recs[:-1]) / 60
    assert 4 <= ramp_min < 12


def test_no_auto_sprint_by_default_and_none_for_the_speed_ramp():
    lib = library.default_protocols(RECOIL)
    incline = next(p for p in lib if p.builder == "max_hr_ramp" and p.params["mode"] == "incline")
    speed = next(p for p in lib if p.builder == "max_hr_ramp" and p.params["mode"] == "speed")
    assert speed.final_sprint_auto and speed.final_sprint_s == 30
    assert not incline.final_sprint_auto and incline.final_sprint_s == 0
    old = Protocol.from_dict({k: v for k, v in speed.to_dict().items()
                              if k not in ("final_sprint_auto", "overflow_speed_kmh")})
    assert not old.final_sprint_auto and old.overflow_speed_kmh == 0.5


def test_sprint_outlook_says_how_near_the_automatic_sprint_is():
    """The coach's page shows the ramp time, the rise of the heart rate over the last minute,
    the limit and the time left to 12 min. Reading it changes nothing."""
    p = library.max_hr_ramp(10.0, warmup_s=120, step_s=60, start_incline_pct=0.0,
                            final_sprint_s=30, final_sprint_auto=True)
    r, clock, tm, logs = make(p, hr_max=240)          # heart rate never levels off
    r.start()
    run(r, clock, 10 + 60)                            # countdown, half the warm-up
    o = r.sprint_outlook()
    assert not o.in_ramp and o.ramp_elapsed_s == 0 and o.remaining_s is None and o.started_s is None
    assert (o.limit_bpm, o.min_ramp_s, o.after_s) == (2.0, 240.0, 720.0)
    run(r, clock, 60 + 5 * 60 + 30)                   # 5:30 into the ramp
    recs, stages = len(r.records), len(r.stages)
    o = r.sprint_outlook()
    assert o == r.sprint_outlook() and (len(r.records), len(r.stages)) == (recs, stages)
    assert o.in_ramp and o.ramp_elapsed_s == pytest.approx(330, abs=0.5)
    assert o.remaining_s == pytest.approx(390, abs=0.5)
    assert o.hr_rise_bpm is not None and o.hr_rise_bpm > 2.0
    assert r.state.sprint is not None and r.state.sprint.in_ramp
    run_until(r, clock, lambda: r.phase == Phase.RECOVERY)
    o = r.sprint_outlook()
    assert o.started_s == pytest.approx(720, abs=0.5) and o.reason == "time"


def test_sprint_outlook_names_the_plateau_and_the_button():
    p = library.max_hr_ramp(12.0, warmup_s=0, step_s=60, start_incline_pct=1.0,
                            final_sprint_s=30, final_sprint_auto=True)
    r, clock, tm, logs = make(p, hr_max=175)          # levels off at about 8 %
    r.start()
    run_until(r, clock, lambda: r.sprint_outlook().started_s is not None)
    o = r.sprint_outlook()
    assert o.reason == "plateau" and 240 <= o.started_s < 720
    r2, clock2, *_ = make(p, hr_max=240)
    r2.start()
    run(r2, clock2, 10 + 150)
    assert r2.sprint_outlook().hr_rise_bpm is not None
    assert r2.final_sprint()
    o = r2.sprint_outlook()
    assert o.reason == "manual" and o.started_s == pytest.approx(150, abs=0.5)
    # a test without the automatic sprint has no outlook
    q = library.max_hr_ramp(12.0, warmup_s=0, final_sprint_s=30)
    r3, *_ = make(q)
    assert r3.sprint_outlook() is None
