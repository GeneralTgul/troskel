"""Cycling and SkiErg: the protocols in watt, the runner with the simulated trainer
and SkiErg, thresholds with watt on the x-axis, FTP and the comparison between two tests."""
from __future__ import annotations

import pytest

from lopanalys.assessments import compare, lactate, library, library_power, max_hr, power, zones
from lopanalys.assessments.protocol import Phase, Protocol
from lopanalys.assessments.runner import TestRunner, stage_text
from lopanalys.core.bus import TOPIC_TEST_STATE, Bus
from lopanalys.core.models import StepRecord, TestKind, TimeSample
from lopanalys.core.sport import Sport, profile, selectable_sports
from lopanalys.devices import sim
from lopanalys.devices.manual import ManualErgometer


class Adapter:
    def __init__(self, mc):
        self.mc = mc

    def now(self):
        return self.mc()

    def sleep(self, dt):
        self.mc.advance(dt)


def rig(protocol, erg_cls, athlete=None, **erg_kw):
    bus = Bus()
    mc = sim.ManualClock(0.0)
    erg = erg_cls(bus, clock=mc, autorun=False, seed=1, **erg_kw)
    hr = sim.SimHeartRate(bus, treadmill=erg, clock=mc, autorun=False, seed=1, athlete=athlete)
    erg.connect()
    hr.connect()
    states = []
    bus.subscribe(TOPIC_TEST_STATE, states.append)
    r = TestRunner(protocol, erg, hr, bus, clock=Adapter(mc), threaded=False)

    def step(seconds, until=None):
        for _ in range(int(seconds / 0.25)):
            mc.advance(0.25)
            erg.tick()
            hr.tick()
            r.tick()
            if until is not None and until():
                return True
        return until is None

    return r, erg, hr, states, step


# --------------------------------------------------------------------------- sports and protocols
def test_all_three_sports_can_be_chosen():
    assert selectable_sports() == [Sport.RUN, Sport.BIKE, Sport.SKIERG]
    assert profile("bike").controllable and not profile("skierg").controllable
    assert "pm5" in profile("skierg").load_sources and "qz" not in profile("bike").load_sources


def test_bike_protocols_are_in_watt_with_five_minute_steps():
    lib = library.default_protocols(sport="bike")
    assert {p.sport for p in lib} == {"bike"} and {p.kind for p in lib} == {"lactate", "max_hr", "ftp", "bikefit"}
    p = library_power.bike_lactate_step_for_level("recreational")
    work = [s for s in p.stages if not s.is_warmup]
    assert [s.load for s in work] == [100, 125, 150, 175, 200, 225, 250]
    assert {s.duration_s for s in work} == {300} and {s.incline_pct for s in p.stages} == {0.0}
    assert p.lactate_pause_s == 0 and p.stages[0].is_warmup and p.stages[0].load == 80
    assert "100 W" in p.description and "25 W" in p.description and "km/h" not in p.description
    back = Protocol.from_dict(p.to_dict())
    assert back.sport == "bike" and back.builder == "bike_lactate_step"
    ramp = library_power.bike_ramp(100, 20)
    assert ramp.open_ended and ramp.increment_load == 20 and ramp.kind == TestKind.MAX_HR.value
    ftp = library_power.bike_ftp_20(240)
    assert ftp.kind == "ftp" and ftp.stages[-1].free and ftp.stages[-1].duration_s == 1200
    assert Protocol.from_dict(ftp.to_dict()).stages[-1].free


def test_skierg_protocols_show_the_pace_and_pause_for_the_sample():
    p = library_power.skierg_lactate_step_for_level("recreational")
    work = [s for s in p.stages if not s.is_warmup]
    assert [s.load for s in work] == [80, 95, 110, 125, 140, 155, 170]
    assert {s.duration_s for s in work} == {240} and p.lactate_pause_s == 30 and p.sport == "skierg"
    assert "/500 m" in p.description and "2:44" in p.description      # 80 W
    assert stage_text(202, 0, "skierg") == "202 W · 2:00 /500 m" and stage_text(200, 0, "bike") == "200 W"
    assert {x.kind for x in library.default_protocols(sport="skierg")} == {"lactate", "max_hr", "ski"}


def test_an_old_protocol_with_an_unknown_stage_key_still_loads():
    d = library_power.bike_ramp().to_dict()
    d["stages"][0]["from_a_later_version"] = 1
    assert Protocol.from_dict(d).stages[0].load == d["stages"][0]["speed_kmh"]


# --------------------------------------------------------------------------- the runner on a trainer
def test_bike_lactate_steps_in_erg_without_pauses():
    p = library_power.bike_lactate_step(100, 25, 4, stage_s=300, warmup_s=300)
    r, bike, _hr, states, step = rig(p, sim.SimBike)
    r.start()
    assert r.phase == Phase.COUNTDOWN and "100 W" not in states[-1].prompt and "80 W" in states[-1].prompt
    step(10.5)
    assert r.phase == Phase.RUNNING and bike.state.target_load == 80
    step(300 + 4 * 300 + 5, until=lambda: r.done)
    assert r.phase == Phase.FINISHED
    assert [x.load for x in r.records] == [100, 125, 150, 175]       # ERG: the load is the target
    assert all(x.hr_end for x in r.records) and [x.hr_end for x in r.records] == sorted(x.hr_end for x in r.records)
    stats = r.stage_stats
    assert [s["target"] for s in stats] == [80, 100, 125, 150, 175]
    assert all(abs(s["measured"] - s["target"]) < 3 and s["cadence"] == pytest.approx(85, abs=2) for s in stats)
    assert not any(s["self_paced"] for s in stats)
    smp = r.samples
    assert smp[400].load == pytest.approx(100, abs=3) and smp[400].cadence_spm == pytest.approx(85, abs=2)
    assert smp[400].cadence_source == "ergometer" and smp[400].incline_pct == 0
    run_state = next(s for s in states if s.phase == Phase.RUNNING and s.stage_index == 2)
    assert run_state.sport == "bike" and run_state.target_load == 125 and not run_state.self_paced
    assert bike.state.target_load is not None and not bike._running       # released when the test is over


def test_bike_ramp_ends_by_itself_when_the_rider_stops_pedalling():
    p = library_power.bike_ramp(100, 20, 60, warmup_s=120)
    r, bike, _hr, states, step = rig(p, sim.SimBike, max_power_w=290)
    r.start()
    assert step(10 + 120 + 60 * 14, until=lambda: r.phase == Phase.RECOVERY)
    assert r.exhaustion_t_s is not None and bike.state.target_load == 80        # easy spinning, not the last watt
    assert "trampa" in states[-1].prompt.lower()
    assert step(120, until=lambda: r.done) and r.phase == Phase.FINISHED
    loads = [x.load for x in r.records]
    assert loads[:10] == [100 + 20 * i for i in range(10)] and loads[-1] == 300
    res = power.analyze(r.samples, "max_hr", weight_kg=75)
    assert res.method == "ramp" and 270 <= res.map_w <= 290 and res.ftp_w == round(res.map_w * 0.75)
    assert res.ftp_w_per_kg == round(res.ftp_w / 75, 2) and [z.code for z in res.zones][0] == "z1"
    mx = max_hr.analyze(r.samples, end_t_s=r.exhaustion_t_s, records=r.records, age=35, sport="bike")
    assert mx.peak_hr and next(c for c in mx.criteria if c.code == "age").met is None


def test_a_rider_who_pauses_in_a_lactate_step_does_not_end_the_test():
    p = library_power.bike_lactate_step(100, 25, 2, stage_s=120, warmup_s=0)
    r, bike, _hr, _states, step = rig(p, sim.SimBike)
    r.start()
    step(40)
    bike.give_up()
    step(30)
    assert r.phase == Phase.RUNNING and bike.state.load == 0


def test_ftp_20_runs_free_and_takes_the_measured_power():
    p = library_power.bike_ftp_20(250, warmup_s=300)
    r, bike, _hr, states, step = rig(p, sim.SimBike, rider_power_w=262)
    r.start()
    step(10.5 + 300 + 30)
    st = states[-1]
    assert bike.state.free and st.self_paced and st.target_load is None and "250 W" in st.prompt
    assert step(1300, until=lambda: r.done) and r.phase == Phase.FINISHED
    rec = r.records[-1]
    assert rec.load == pytest.approx(262, abs=2) and rec.duration_s == pytest.approx(1200, abs=1)
    res = power.analyze(r.samples, "ftp", weight_kg=80)
    assert res.method == "ftp20" and res.mean_20min_w == pytest.approx(262, abs=2)
    assert res.ftp_w == pytest.approx(249, abs=2) and res.ftp_w_per_kg == pytest.approx(3.11, abs=0.03)
    assert [(z.code, z.low_w, z.high_w) for z in res.zones][3] == ("z4", round(res.ftp_w * 0.90), round(res.ftp_w * 1.05))
    assert res.hr_mean_20min and not res.warnings


def test_ftp_needs_the_full_twenty_minutes_and_measured_power():
    smp = [TimeSample(t_s=float(i), hr=150, speed_kmh=250.0) for i in range(900)]
    short = power.analyze(smp, "ftp", 70)
    assert short.ftp_w is None and short.warnings[0]["code"] == "short" and short.map_w == 250
    assert power.analyze(smp, "ftp", 70, measured=False).warnings[0]["code"] == "no_power"
    fade = [TimeSample(t_s=float(i), speed_kmh=280.0 if i < 600 else 230.0) for i in range(1200)]
    assert [w["code"] for w in power.analyze(fade, "ftp").warnings] == ["uneven"]
    assert power.w_per_kg(250, 72.5) == 3.45 and power.w_per_kg(250, None) is None


# --------------------------------------------------------------------------- the runner on a SkiErg
def test_skierg_steps_are_self_paced_and_take_the_measured_watt():
    p = library_power.skierg_lactate_step(100, 20, 3, stage_s=240, pause_s=30, warmup_s=120)
    r, ski, _hr, states, step = rig(p, sim.SimSkiErg, follow=0.97)
    r.start()
    assert r.phase == Phase.COUNTDOWN            # nobody has to confirm: the PM5 measures
    assert "80 W" in states[-1].prompt and "/500 m" in states[-1].prompt
    step(10.5 + 60)
    st = states[-1]
    assert r.phase == Phase.RUNNING and st.self_paced and not st.controllable and st.target_load == 80
    assert st.prompt.startswith("Håll 80 W") and st.speed_kmh == pytest.approx(77.6, abs=3) and st.cadence > 25
    assert step(400, until=lambda: r.phase == Phase.LACTATE_PAUSE)
    r.enter_lactate(0, 1.2)
    step(15)
    assert ski.state.load < 20                   # the skier rests in the pause
    for i in (1, 2, 3):
        assert step(60, until=lambda: r.phase == Phase.RUNNING)
        assert step(400, until=lambda: r.phase == Phase.LACTATE_PAUSE)
        r.enter_lactate(i, 1.0 + i)
    assert step(60, until=lambda: r.done) and r.phase == Phase.FINISHED
    loads = [x.load for x in r.records]
    assert loads == pytest.approx([97.0, 116.4, 135.8], abs=1.5)        # 97 % of 100, 120, 140: what was held
    stats = {s["index"]: s for s in r.stage_stats}
    assert stats[1]["target"] == 100 and stats[1]["self_paced"] and stats[1]["in_band"] is True
    assert 30 <= stats[2]["cadence"] <= 45


def test_skierg_test_is_put_on_the_monitor_as_a_workout():
    from lopanalys.core.bus import TOPIC_LOG
    p = library_power.skierg_lactate_step(100, 20, 3, stage_s=240, pause_s=30, warmup_s=120)
    r, ski, _hr, _states, step = rig(p, sim.SimSkiErg)
    said = []
    r.bus.subscribe(TOPIC_LOG, said.append)
    r.start()
    w = ski.workout
    assert ski.programmed and [(i.duration_s, i.rest_s, i.target_w) for i in w] == [
        (120, 30, 80), (240, 30, 100), (240, 30, 120), (240, 30, 140)]
    assert said == ["Testet är inlagt som pass på monitorn. Börja dra när nedräkningen på TV:n är slut."]
    # a ramp is open-ended: the steps it will generate are on the monitor too, as far as it takes them
    ramp_p = library_power.skierg_ramp(80, 15, 60, warmup_s=60)
    r2, ski2, _h, _s, _step = rig(ramp_p, sim.SimSkiErg)
    r2.start()
    assert len(ski2.workout) == 30 and [i.target_w for i in ski2.workout[:4]] == [65, 80, 95, 110]
    assert {i.rest_s for i in ski2.workout} == {0.0} and ski2.workout[-1].target_w == 80 + 15 * 28


def test_a_monitor_that_refuses_the_workout_does_not_stop_the_test():
    from lopanalys.core.bus import TOPIC_LOG
    p = library_power.skierg_lactate_step(100, 20, 2, stage_s=120, pause_s=0, warmup_s=0)
    r, ski, _hr, states, step = rig(p, sim.SimSkiErg, follow=0.97)
    ski.program_ok = False
    said = []
    r.bus.subscribe(TOPIC_LOG, said.append)
    r.start()
    assert ski.programmed is False and ski.workout == [] and "Just Ski" in said[0]
    assert step(300, until=lambda: r.done) and r.phase == Phase.FINISHED
    assert [x.load for x in r.records] == pytest.approx([97.0, 116.4], abs=1.5)

    class Broken(sim.SimSkiErg):
        def program_workout(self, intervals):
            raise OSError("Bluetooth")
    r3, _ski3, _h, _s, step3 = rig(p, Broken)
    r3.start()                                   # an error in the device never reaches the test
    assert step3(300, until=lambda: r3.done) and r3.phase == Phase.FINISHED
    # a trainer has no monitor to program, and neither has a manual SkiErg
    rb, bike, _h2, _s2, _st = rig(library_power.bike_ramp(100, 20, 60, warmup_s=0), sim.SimBike)
    rb.start()
    assert not hasattr(bike, "workout")


def test_skierg_stage_outside_the_band_is_marked_and_shown():
    p = library_power.skierg_lactate_step(120, 20, 1, stage_s=240, pause_s=0, warmup_s=0)
    r, _ski, _hr, states, step = rig(p, sim.SimSkiErg, follow=0.85)
    r.start()
    step(10.5 + 120)
    assert states[-1].off_target and states[-1].target_load == 120
    step(200, until=lambda: r.done)
    assert r.stage_stats[0]["in_band"] is False and r.records[0].load == pytest.approx(102, abs=1.5)


def test_skierg_ramp_ends_when_the_athlete_stops():
    p = library_power.skierg_ramp(80, 15, 60, warmup_s=60)
    r, _ski, _hr, states, step = rig(p, sim.SimSkiErg, max_power_w=190)
    r.start()
    assert step(10 + 60 + 60 * 12, until=lambda: r.phase == Phase.RECOVERY)
    assert "staka" in states[-1].prompt.lower() and step(120, until=lambda: r.done)
    assert r.records[-1].index >= 8 and power.analyze(r.samples, "max_hr").map_w > 150


def test_manual_trainer_waits_for_the_operator():
    p = library_power.bike_lactate_step(100, 25, 1, stage_s=60, warmup_s=0)
    erg = ManualErgometer(sport="bike")
    erg.connect()
    mc = sim.ManualClock(0.0)
    bus = Bus()
    states = []
    bus.subscribe(TOPIC_TEST_STATE, states.append)
    r = TestRunner(p, erg, None, bus, clock=Adapter(mc), threaded=False)
    r.start()
    assert r.phase == Phase.AWAIT_OPERATOR and states[-1].prompt == "Håll 100 W"
    assert r.operator_ready()
    for _ in range(300):
        mc.advance(0.25)
        r.tick()
    assert r.done and r.records[0].load == 100 and r.stage_stats[0]["measured"] is None


# --------------------------------------------------------------------------- thresholds in watt
BIKE = [(100, 1.1, 118), (125, 1.0, 127), (150, 1.2, 137), (175, 1.6, 147), (200, 2.3, 157), (225, 3.6, 167),
        (250, 5.9, 176)]


def recs(points):
    return [StepRecord(index=i + 1, speed_kmh=w, incline_pct=0.0, duration_s=300, hr_end=hr, lactate_mmol=la)
            for i, (w, la, hr) in enumerate(points)]


def test_thresholds_with_watt_on_the_x_axis():
    res = lactate.analyze(recs(BIKE), max_hr=186, sport="bike")
    assert res.ok and res.sport == "bike" and res.model in ("exp", "poly3") and res.r2 > 0.98
    assert 160 < res.lt1.load < 205 and 205 < res.lt2.load < 245 and res.lt1.load < res.lt2.load
    assert res.lt2.pace == "" and res.lt2.hr and 150 < res.lt2.hr < 176
    assert res.thresholds["obla_4"].load == pytest.approx(230, abs=6)
    assert "incline_varies" not in [w.code for w in res.warnings]
    back = lactate.LactateResult.from_dict(res.to_dict())
    assert back.lactate_at(200) == pytest.approx(res.lactate_at(200)) and back.speed_at(4.0) == pytest.approx(
        res.speed_at(4.0))


def test_the_thresholds_do_not_depend_on_the_unit_of_the_axis():
    """The same test in km/h and in "watt" (x times 20): every method lands on the same point."""
    run = [(8.0, 1.2, 120), (9.0, 1.1, 130), (10.0, 1.3, 140), (11.0, 1.7, 150), (12.0, 2.5, 160), (13.0, 3.9, 170),
           (14.0, 6.2, 178)]
    a = lactate.analyze(recs(run), sport="run", model="exp")
    b = lactate.analyze(recs([(v * 20, la, hr) for v, la, hr in run]), sport="bike", model="exp")
    assert set(a.thresholds) == set(b.thresholds)
    for m in a.thresholds:
        assert b.thresholds[m].load / 20 == pytest.approx(a.thresholds[m].load, abs=0.06), m
    assert (a.lt1_method, a.lt2_method) == (b.lt1_method, b.lt2_method)


def test_running_is_analysed_as_before():
    run = [(8.0, 1.2, 120), (9.0, 1.1, 130), (10.0, 1.3, 140), (11.0, 1.7, 150), (12.0, 2.5, 160), (13.0, 3.9, 170),
           (14.0, 6.2, 178)]
    res = lactate.analyze(recs(run))
    assert "sx" not in res.fit and res.lt2.speed_kmh == round(res.lt2.speed_kmh, 2)


def test_measured_loads_that_are_almost_the_same_are_one_point():
    pts = [(98, 1.2, 110), (117, 1.3, 120), (118.5, 1.5, 122), (136, 2.2, 135), (155, 3.4, 148), (171, 5.6, 160)]
    res = lactate.analyze(recs(pts), sport="skierg")
    assert len(res.points) == 5 and [p["speed_kmh"] for p in res.points][1] == 118.5
    assert "same_load" in [w.code for w in res.warnings]
    assert res.ok and res.lt2.pace and res.lt2.pace.startswith("2:")          # /500 m from the watt
    assert lactate.analyze(recs(pts), sport="run").points.__len__() == 6      # running: exact duplicates only


def test_zones_in_watt_and_the_top_zone_beyond_the_test():
    res = lactate.analyze(recs(BIKE), max_hr=186, sport="bike")              # ends below 6 mmol/L
    zs = zones.from_lactate(res, 186)
    assert [z.code for z in zs] == ["I1", "I2", "I3", "I4", "I5"] and {z.sport for z in zs} == {"bike"}
    assert zs[3].load_low == pytest.approx(res.lt2.load) and zs[3].pace_high == ""
    assert res.speed_at(max(6.0, res.lt2.lactate_mmol + 2.0)) is None
    assert zs[3].load_high == pytest.approx(res.lt2.load * 1.06, abs=0.6)   # the profile's share, not running's 5 %
    close = lactate.analyze(recs(BIKE[:-1]), max_hr=186, sport="bike")       # the test ended too early
    assert "close" in [w.code for w in close.warnings] and zones.from_lactate(close, 186)[0].load_high is None


def test_comparison_between_two_bike_tests_is_in_watt():
    old = lactate.analyze(recs(BIKE), sport="bike")
    new = lactate.analyze(recs([(w + 15, la, hr) for w, la, hr in BIKE]), sport="bike")
    c = compare.compare_lactate(new, old)
    assert c.shift == "right" and c.curve_shift_kmh == pytest.approx(15, abs=2)
    assert "W" in c.summary and "km/h" not in c.summary and "s/km" not in c.summary
    assert c.lt2.speed_delta == pytest.approx(15, abs=2) and c.lt2.pace_delta_s == 0
    assert [x["speed_kmh"] for x in c.lactate_at_speed][:2] == [125.0, 150.0]
    ski = compare.compare_lactate(lactate.analyze(recs([(w * 0.7 + 8, la, hr) for w, la, hr in BIKE]), sport="skierg"),
                                  lactate.analyze(recs([(w * 0.7, la, hr) for w, la, hr in BIKE]), sport="skierg"))
    assert "/500 m" in ski.summary and ski.lt2.pace_delta_s < 0
