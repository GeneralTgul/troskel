"""Simulators: belt dynamics, the heart rate model in a lactate step test and a max HR ramp."""
from __future__ import annotations

import time

import pytest

from lopanalys.core.bus import TOPIC_HEART_RATE, TOPIC_TREADMILL, Bus
from lopanalys.devices.manual import ManualTreadmill
from lopanalys.devices.sim import ManualClock, SimAthlete, SimClock, SimHeartRate, SimTreadmill, make_sim_pair


def run(clock: ManualClock, devices, seconds: float, dt: float = 1.0) -> None:
    for _ in range(int(round(seconds / dt))):
        clock.advance(dt)
        for d in devices:
            d.tick()


def rig(**athlete):
    clock = ManualClock(1000.0)
    bus = Bus()
    tm = SimTreadmill(bus, clock=clock, autorun=False, publish_hz=20)
    hr = SimHeartRate(bus, treadmill=tm, clock=clock, autorun=False, athlete=SimAthlete(**athlete))
    tm.connect()
    hr.connect()
    return clock, bus, tm, hr


def test_treadmill_accelerates_about_half_kmh_per_second():
    clock, _bus, tm, _hr = rig()
    tm.set_target(10.0, 3.0)
    tm.start()
    run(clock, [tm], 4.0, 0.1)
    assert tm.state.speed_kmh == pytest.approx(3.0, abs=0.06)   # 1.0 at start + 0.5 * 4 s
    assert tm.state.incline_pct == pytest.approx(0.4, abs=0.06)
    run(clock, [tm], 30.0, 0.1)
    assert tm.state.speed_kmh == pytest.approx(10.0) and tm.state.incline_pct == pytest.approx(3.0)
    tm.stop()
    run(clock, [tm], 5.0, 0.1)
    assert tm.state.speed_kmh == pytest.approx(5.0, abs=0.06)   # brakes 1 km/h/s
    run(clock, [tm], 6.0, 0.1)
    assert tm.state.speed_kmh == 0.0 and tm.state.running is False


def test_treadmill_clamps_and_integrates_distance():
    clock, _bus, tm, _hr = rig()
    tm.set_target(40.0, 30.0)
    assert tm.state.target_speed_kmh == 18.0 and tm.state.target_incline_pct == 12.0
    tm.set_target(12.0, 0.0)
    tm.start()
    run(clock, [tm], 30.0, 0.1)
    d0 = tm.state.distance_m
    run(clock, [tm], 60.0, 0.1)
    assert tm.state.distance_m - d0 == pytest.approx(200.0, rel=0.01)   # 12 km/h for a minute


def test_heart_rate_at_rest_and_kinetics():
    clock, _bus, tm, hr = rig()
    run(clock, [tm, hr], 60)
    assert hr.last.bpm == pytest.approx(55 + 0.05 * (192 - 55), abs=2)
    tm.set_target(12.0, 1.0)
    tm.start()
    run(clock, [tm, hr], 30)
    mid = hr.last.bpm
    run(clock, [tm, hr], 180)
    end = hr.last.bpm
    ss = hr.steady_state_hr(12.0, 1.0)
    assert mid < end and end == pytest.approx(ss, abs=4)


def test_lactate_step_test_shape():
    clock, _bus, tm, hr = rig()
    tm.start()
    ends = []
    for speed in [8, 9, 10, 11, 12, 13, 14, 15, 16]:
        tm.set_target(speed, 1.0)
        run(clock, [tm, hr], 240)
        ends.append(hr.last.bpm)
        tm.set_target(0.0, 1.0)      # 30 s pause for the blood sample
        run(clock, [tm, hr], 30)
    assert all(b > a for a, b in zip(ends, ends[1:]))
    assert 115 <= ends[0] <= 145
    assert ends[-1] <= 192
    rises = [b - a for a, b in zip(ends, ends[1:])]
    assert all(3 <= r <= 12 for r in rises), rises   # realistic bpm per km/h step


def test_max_hr_ramp_plateaus_at_max():
    clock, _bus, tm, hr = rig(max_hr=188)
    tm.set_target(10.0, 1.0)
    tm.start()
    run(clock, [tm, hr], 300)
    speed = 10.0
    trace = []
    while speed <= 20.0:
        tm.set_target(speed, 1.0)
        for _ in range(60):
            run(clock, [tm, hr], 1)
            trace.append(hr.last.bpm)
        speed += 0.5
    assert max(trace) <= 188
    assert max(trace) >= 184
    last_two_min = trace[-120:]
    assert max(last_two_min) - min(last_two_min) <= 4   # plateau


def test_cardiac_drift_at_steady_pace():
    clock, _bus, tm, hr = rig()
    tm.set_target(13.0, 1.0)
    tm.start()
    run(clock, [tm, hr], 300)
    at5 = hr.true_hr
    run(clock, [tm, hr], 1200)
    assert 2.0 < hr.true_hr - at5 < 15.0


def test_noise_and_rr():
    clock = ManualClock()
    tm = SimTreadmill(None, clock=clock, autorun=False)
    hr = SimHeartRate(None, treadmill=tm, clock=clock, autorun=False, seed=1,
                      athlete=SimAthlete(noise_bpm=2.0))
    tm.connect()
    hr.connect()
    vals = []
    for _ in range(50):
        run(clock, [tm, hr], 1)
        vals.append(hr.last.bpm)
    assert len(set(vals)) > 1
    assert hr.last.rr_ms and 600 < hr.last.rr_ms[0] < 1500


def test_fast_clock_with_threads():
    bus = Bus()
    tm, hr = make_sim_pair(bus, time_scale=200.0, noise=False, seed=3)
    hrs, tms = [], []
    bus.subscribe(TOPIC_HEART_RATE, hrs.append)
    bus.subscribe(TOPIC_TREADMILL, tms.append)
    tm.connect()
    hr.connect()
    try:
        tm.set_target(12.0, 1.0)
        tm.start()
        time.sleep(0.5)              # ~100 simulated seconds
        assert tm.state.speed_kmh == pytest.approx(12.0)
        assert hrs and hrs[-1].bpm > 120
        assert hrs[-1].t - hrs[0].t > 50
    finally:
        hr.disconnect()
        tm.disconnect()


def test_sim_clock_scale():
    c = SimClock(scale=100.0, start=0.0)
    time.sleep(0.05)
    assert 3.0 < c() < 30.0


def test_manual_treadmill_reports_targets():
    clock = ManualClock()
    bus = Bus()
    got = []
    bus.subscribe(TOPIC_TREADMILL, got.append)
    tm = ManualTreadmill(bus, clock=clock)
    assert tm.controllable is False
    tm.connect()
    tm.set_target(12.0, 1.0)
    assert got[-1].speed_kmh == 0.0 and got[-1].target_speed_kmh == 12.0 and got[-1].measured is False
    tm.start()
    assert got[-1].speed_kmh == 12.0 and got[-1].running is True
    clock.advance(30)
    assert tm.state.distance_m == pytest.approx(100.0)
    tm.stop()
    assert got[-1].speed_kmh == 0.0 and got[-1].running is False
    tm.disconnect()
    assert tm.state is None
