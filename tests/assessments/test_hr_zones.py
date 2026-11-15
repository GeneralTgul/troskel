"""Puls- och fartzoner without lactate: the protocol, the relation heart rate -> speed from the
steady steps, the zone speeds, the warning for a heart rate that has not settled, and the end of the
test when the belt is stopped on the panel. Run with the simulated runner, whose heart rate lags."""
import pytest

from lopanalys.assessments import hr_zones, library, max_hr, vo2max, zone_systems
from lopanalys.assessments.protocol import Phase
from lopanalys.assessments.runner import TestRunner
from lopanalys.core.bus import Bus
from lopanalys.core.models import StepRecord, TestKind, TimeSample
from lopanalys.devices import sim


class Adapter:
    def __init__(self, mc):
        self.mc = mc

    def now(self):
        return self.mc()

    def sleep(self, dt):
        self.mc.advance(dt)


class Rig:
    """The runner with the simulated treadmill and runner on one manual clock."""

    def __init__(self, protocol, athlete=None):
        self.bus = Bus()
        self.mc = sim.ManualClock(0.0)
        self.tm = sim.SimTreadmill(self.bus, clock=self.mc, autorun=False, seed=1)
        self.hr = sim.SimHeartRate(self.bus, treadmill=self.tm, clock=self.mc, autorun=False, seed=1,
                                   athlete=athlete or sim.SimAthlete())
        self.tm.connect()
        self.hr.connect()
        self.r = TestRunner(protocol, self.tm, self.hr, self.bus, clock=Adapter(self.mc), threaded=False)

    def step(self, seconds):
        for _ in range(int(seconds / 0.25)):
            self.mc.advance(0.25)
            self.tm.tick()
            self.hr.tick()
            self.r.tick()

    def until(self, pred, max_s=7200):
        t0 = self.mc()
        while not pred():
            assert self.mc() - t0 < max_s, "condition not reached"
            self.step(0.25)


def _run_to_max(protocol, athlete=None, ramp_stages=8):
    rig = Rig(protocol, athlete)
    rig.r.start()
    n = len(protocol.stages)
    rig.until(lambda: rig.r.phase == Phase.RUNNING and rig.r._idx >= n - 1 + ramp_stages)
    rig.step(20)
    rig.r.exhausted()
    rig.until(lambda: rig.r.done)
    return rig


def test_protocol_steps_then_speed_ramp_on_constant_incline():
    p = library.hr_zones(8.0)
    assert p.kind == TestKind.HR_ZONES.value and p.builder == "hr_zones" and p.open_ended
    work = [s for s in p.stages if not s.is_warmup]
    assert [s.speed_kmh for s in work] == [8.0, 9.0, 10.0, 11.0, 12.0, 12.5]
    assert [s.duration_s for s in work] == [180] * 5 + [60]          # 3 min steps (4 min before 2026-11-15)
    assert not any(s.sample_lactate for s in p.stages) and p.lactate_pause_s == 0
    assert {s.incline_pct for s in p.stages} == {1.0}
    assert p.increment_speed_kmh == 0.5 and p.increment_incline_pct == 0.0
    assert p.stages[0].is_warmup and p.stages[0].duration_s == 360   # 6 min warm-up
    assert hr_zones.step_indexes(p.to_dict()) == {1, 2, 3, 4, 5}
    four = library.hr_zones(8.0, stage_s=240)                          # 4 min can still be chosen
    assert [s.duration_s for s in four.stages if not s.is_warmup][:5] == [240] * 5


def test_start_speed_from_level_or_an_earlier_threshold():
    # the last of five steps half a km/h below the estimated threshold
    assert library.hr_zones_for_level("recreational").params["start_kmh"] == 8.0
    assert library.hr_zones_for_level("trained").params["start_kmh"] == 10.5
    p = library.hr_zones_for_level(estimated_lt2_kmh=14.0)
    assert p.params["start_kmh"] == 9.5 and p.name == "Puls- och fartzoner, utifrån förra testet"
    assert [x.builder for x in library.default_protocols()].count("hr_zones") == 4


def test_technique_only_with_even_speed_and_incline_for_a_minute():
    """A max HR test is for max HR and is not bent to fit the film."""
    from lopanalys.assessments.protocol import GAIT_MIN_STAGE_S, gait_compatible
    assert GAIT_MIN_STAGE_S == 60
    lib = {(p.builder, (p.params or {}).get("mode")): p for p in library.default_protocols()}
    assert not gait_compatible(lib[("max_hr_ramp", "incline")])          # the incline changes every stage
    assert not gait_compatible(lib[("max_hr_ramp", "speed")])            # 30 s stages
    assert lib[("max_hr_ramp", "speed")].stages[-1].duration_s == 30     # the max test itself is left as it is
    assert library.default_protocols()[3].params["mode"] == "incline"    # and the incline ramp is still first
    assert gait_compatible(library.max_hr_ramp(10.0, mode="speed", step_s=60))
    assert gait_compatible(library.lactate_step(10.0))
    assert not gait_compatible(library.lactate_step(10.0, stage_s=45))
    assert not gait_compatible(library.lactate_to_max(10.0))             # ends in an incline ramp
    assert gait_compatible(library.hr_zones(8.0))                        # 4-min steps, then 60 s stages, 1 % all through
    assert not gait_compatible(library.hr_zones(8.0, ramp_step_s=30))
    assert gait_compatible(library.gait_check([10, 12, 14])) and not gait_compatible(library.gait_check([10], stage_s=30))


def test_simulated_runner_gives_the_relation_and_the_zone_speeds():
    p = library.hr_zones(8.0)
    rig = _run_to_max(p)
    r = rig.r
    assert r.phase == Phase.FINISHED and not any(x.lactate_mmol for x in r.records)
    res = hr_zones.analyze(r.records, r.samples, end_t_s=r.exhaustion_t_s, steps=hr_zones.step_indexes(p.to_dict()))
    assert res.ok and len(res.used) == 5 and res.ramp_reached and res.quality == "good"
    assert all(pt.stable for pt in res.points) and not [w for w in res.warnings]
    assert res.r2 > 0.99 and res.see_bpm < 2 and (res.speed_min, res.speed_max) == (8.0, 12.0)
    # the stable heart rate of each step is the simulated runner's steady state (plus a little drift)
    for pt in res.points:
        assert pt.hr == pytest.approx(rig.hr.steady_state_hr(pt.speed_kmh, 1.0), abs=3.0)
    a = rig.hr.athlete
    truth = (a.max_hr - a.resting_hr) * 0.8 * 1.045 / a.vmax_kmh       # bpm per km/h in the model
    assert res.slope == pytest.approx(truth, rel=0.08)

    peak = max_hr.analyze(r.samples, end_t_s=r.exhaustion_t_s, records=r.records).peak_hr
    assert 180 <= peak <= 192
    zones = hr_zones.zone_speeds(zone_systems.nominal_zones(peak, system="garmin"), res)
    assert [z.code for z in zones] == ["Z1", "Z2", "Z3", "Z4", "Z5"]

    def true_speed(hr):          # the model below its knee: HR = rest + reserve * (0.2 + 0.8 * 1.045 v / vmax)
        return ((hr - a.resting_hr) / (a.max_hr - a.resting_hr) - 0.2) * a.vmax_kmh / (0.8 * 1.045)
    for z in zones[1:4]:
        for hr, v in ((z.hr_low, z.speed_low_kmh), (z.hr_high, z.speed_high_kmh)):
            if v is not None:
                assert v == pytest.approx(true_speed(hr), abs=0.5)
    z3 = zones[2]
    assert z3.speed_low_kmh and z3.speed_high_kmh and z3.speed_basis == "hr" and z3.estimated
    assert not z3.speed_high_extrapolated
    # zone 1 lies far below the slowest step and the top of zone 5 far above the fastest: no speed there
    assert zones[0].speed_low_kmh is None and zones[4].speed_high_kmh is None
    marked = [z for z in zones if z.speed_low_extrapolated or z.speed_high_extrapolated]
    assert marked, "a border outside 8-12 km/h must be marked as extrapolated"
    for z in zones:
        for v, x in ((z.speed_low_kmh, z.speed_low_extrapolated), (z.speed_high_kmh, z.speed_high_extrapolated)):
            if v is not None:
                assert x == (not 8.0 <= v <= 12.0)
                assert 8.0 - hr_zones.EXTRAPOLATE_KMH <= v <= 12.0 + hr_zones.EXTRAPOLATE_KMH
    # estimated VO2max by the max test's rules: the one-minute ramp stages are too short for the
    # final-stage equations, so it comes from the settled steps drawn out to max HR
    v = vo2max.estimate(r.records, peak, 55, to_exhaustion=True, stage_durations=[s.duration_s for s in p.stages])
    assert v is not None and v.best.method == "hr_extrapolation" and "short" in v.reasons


def test_one_minute_stages_alone_would_put_the_speeds_wrong():
    """Why the test has 4-minute steps: in the ramp the heart rate lags the speed."""
    p = library.hr_zones(8.0)
    rig = _run_to_max(p)
    ramp = [x for x in rig.r.records if x.index >= len(p.stages) - 1][:4]
    for x in ramp:
        assert x.hr_end < rig.hr.steady_state_hr(x.speed_kmh, 1.0) + rig.hr._drift - 1.0


def _synthetic(rises, speeds=(8.0, 9.0, 10.0, 11.0, 12.0), base=120.0, per_kmh=10.0, ramp=4):
    """Records and 1 Hz samples: every step ends at base + per_kmh * (v - 8) and rises `rise` bpm
    during its last minute; then a ramp of one-minute stages."""
    recs, smp, t0 = [], [], 0.0
    for i, (v, rise) in enumerate(zip(speeds, rises)):
        end_hr = base + per_kmh * (v - speeds[0])
        for k in range(240):
            hr = end_hr - rise / 2 + rise * (k - 210) / 60 if k >= 180 else end_hr - rise / 2 - 8 * (180 - k) / 180
            smp.append(TimeSample(t0 + k + 1, int(round(hr)), v, 1.0))
        t0 += 240
        recs.append(StepRecord(1 + i, v, 1.0, 240.0, hr_end=int(end_hr)))
    for j in range(ramp):
        v = speeds[-1] + 0.5 * (j + 1)
        for k in range(60):
            smp.append(TimeSample(t0 + k + 1, int(base + per_kmh * 4 + 3 * (j + 1)), v, 1.0))
        t0 += 60
        recs.append(StepRecord(1 + len(speeds) + j, v, 1.0, 60.0, hr_end=int(base + per_kmh * 4 + 3 * (j + 1))))
    return recs, smp, t0


def test_unsettled_step_warns_and_weighs_less():
    recs, smp, end = _synthetic([0, 0, 0, 0, 9.0])
    # the unsettled step also reads too high: move it off the line to see that its pull is small
    for s in smp:
        if s.speed_kmh == 12.0:
            s.hr += 8
    res = hr_zones.analyze(recs, smp, end_t_s=end, steps={1, 2, 3, 4, 5})
    last = res.points[-1]
    assert last.stable is False and last.note == "unstable" and 0 < last.weight < 0.5
    assert last.rise_bpm == pytest.approx(9.0, abs=1.0)
    assert [p.weight for p in res.points[:4]] == [1.0] * 4
    w = [x for x in res.warnings if x["code"] == "unstable"]
    assert w and "12,0" in w[0]["text"] and "stabiliserades inte" in w[0]["text"]
    # down-weighted, the line stays closer to the four settled steps (10 bpm per km/h) than a plain fit would
    import numpy as np
    plain = np.polyfit([p.speed_kmh for p in res.points], [p.hr for p in res.points], 1)[0]
    assert abs(res.slope - 10.0) < abs(plain - 10.0)


def test_settled_steps_do_not_warn_and_small_rise_is_fine():
    recs, smp, end = _synthetic([0.5, 1.0, 1.5, 2.0, 2.5])
    res = hr_zones.analyze(recs, smp, end_t_s=end, steps={1, 2, 3, 4, 5})
    assert all(p.stable for p in res.points) and not res.warnings
    assert res.slope == pytest.approx(10.0, abs=0.3)
    assert res.speed_at(140)[0] == pytest.approx(10.0, abs=0.15)
    assert res.speed_at(140)[1] is False
    assert res.speed_at(110) == (pytest.approx(7.0, abs=0.15), True)     # one km/h below the slowest step
    assert res.speed_at(80) == (None, False)                             # too far out
    d = hr_zones.HrSpeedResult.from_dict(res.to_dict())
    assert d.slope == res.slope and len(d.points) == 5 and d.ok


def test_too_few_steps_and_no_ramp_are_said():
    recs, smp, end = _synthetic([0, 0], speeds=(8.0, 9.0), ramp=0)
    res = hr_zones.analyze(recs, smp, end_t_s=end, steps={1, 2})
    assert not res.ok and not res.ramp_reached
    assert {w["code"] for w in res.warnings} == {"too_few", "no_ramp"}
    assert hr_zones.zone_speeds(zone_systems.nominal_zones(185), res)[2].speed_low_kmh is None


def test_windows_found_by_belt_speed_when_the_end_is_unknown():
    recs, smp, _ = _synthetic([0, 0, 0, 0, 0])
    shifted = [TimeSample(s.t_s + 610, s.hr, s.speed_kmh, s.incline_pct) for s in smp]   # warm-up before
    res = hr_zones.analyze(recs, shifted, end_t_s=None, steps={1, 2, 3, 4, 5})
    assert res.ok and res.slope == pytest.approx(10.0, abs=0.3) and all(p.rise_bpm is not None for p in res.points)


def test_stopping_the_belt_on_the_panel_ends_the_test_by_itself():
    """The runner is alone: presses STOP on the treadmill in the ramp, nobody presses Utmattad."""
    p = library.hr_zones(8.0)
    rig = Rig(p)
    rig.r.start()
    n = len(p.stages)
    rig.until(lambda: rig.r.phase == Phase.RUNNING and rig.r._idx >= n + 5)
    rig.step(30)
    real = rig.tm.set_target
    sent = []
    rig.tm.set_target = lambda *a: (sent.append(a), real(*a))[1]
    rig.tm.stop()                                   # the panel: the belt slows down and stands still
    rig.until(lambda: rig.r.phase == Phase.RECOVERY, max_s=60)
    assert rig.r.exhaustion_t_s is not None
    rig.until(lambda: rig.r.done, max_s=120)
    assert rig.r.phase == Phase.FINISHED and sent == []      # the belt is never commanded again
    res = hr_zones.analyze(rig.r.records, rig.r.samples, end_t_s=rig.r.exhaustion_t_s,
                           steps=hr_zones.step_indexes(p.to_dict()))
    assert res.ok and res.ramp_reached and len(res.used) == 5
    assert max_hr.analyze(rig.r.samples, end_t_s=rig.r.exhaustion_t_s).peak_hr >= 175


def test_panel_stop_in_a_steady_step_is_not_a_max():
    p = library.hr_zones(8.0)
    rig = Rig(p)
    rig.r.start()
    rig.until(lambda: rig.r.phase == Phase.RUNNING and rig.r._idx == 4)
    rig.step(60)
    rig.tm.stop()
    rig.until(lambda: rig.r.done, max_s=200)
    res = hr_zones.analyze(rig.r.records, rig.r.samples, end_t_s=rig.r.exhaustion_t_s,
                           steps=hr_zones.step_indexes(p.to_dict()))
    assert not res.ramp_reached and "no_ramp" in {w["code"] for w in res.warnings}
    assert [pt.note for pt in res.points] == ["", "", "", "short"] and res.ok
