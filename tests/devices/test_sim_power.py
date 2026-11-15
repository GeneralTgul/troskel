"""The simulated trainer and SkiErg, the heart rate on power, and the manager's ergometer per sport."""
from __future__ import annotations

import pytest

from lopanalys.core.bus import TOPIC_STROKE, Bus
from lopanalys.devices.base import DeviceInfo, ErgometerState
from lopanalys.devices.errors import DeviceError
from lopanalys.devices.manager import DeviceManager
from lopanalys.devices.manual import ManualErgometer
from lopanalys.devices.sim import ManualClock, SimBike, SimHeartRate, SimSkiErg


def run(devs, clock, seconds, step=0.5):
    for _ in range(int(seconds / step)):
        clock.advance(step)
        for d in devs:
            d.tick()


def test_sim_bike_holds_the_target_in_erg():
    clock = ManualClock()
    bike = SimBike(clock=clock, autorun=False)
    bike.connect()
    bike.start()
    bike.set_target(200, 0)
    run([bike], clock, 15)
    st = bike.state
    assert isinstance(st, ErgometerState) and st.load == pytest.approx(200, abs=2) and st.target_load == 200
    assert st.cadence == 85 and st.running and not st.low_cadence
    bike.cadence_rpm = 40                       # ERG below cadence 50: the power falls and the state says so
    run([bike], clock, 15)
    assert bike.state.load < 150 and bike.state.low_cadence


def test_sim_bike_free_stage_and_giving_up():
    clock = ManualClock()
    bike = SimBike(clock=clock, autorun=False, rider_power_w=255, max_power_w=300)
    bike.connect()
    bike.start()
    bike.set_free()
    run([bike], clock, 15)
    assert bike.state.free and bike.state.load == pytest.approx(255, abs=2) and bike.state.target_load is None
    bike.set_target(320, 0)                     # past what the rider can do: stops pedalling
    run([bike], clock, 20)
    assert bike.state.load == 0 and bike.state.cadence == 0 and not bike.state.running


def test_sim_skierg_follows_the_shown_target_and_cannot_be_set():
    bus = Bus()
    strokes = []
    bus.subscribe(TOPIC_STROKE, strokes.append)
    clock = ManualClock()
    ski = SimSkiErg(bus, clock=clock, autorun=False, follow=0.97)
    assert not ski.controllable and ski.measured
    ski.connect()
    ski.start()
    ski.set_target(150, 0)
    run([ski], clock, 60)
    st = ski.state
    assert st.load == pytest.approx(145.5, abs=1) and st.target_load == 150
    assert 30 <= st.cadence <= 50 and st.pace_500m_s == pytest.approx(134, abs=1.5)     # 2:14 /500 m
    assert 25 <= len(strokes) <= 50 and strokes[-1].drive_time_s < strokes[-1].recovery_time_s
    ski.stop()                                  # the pause for the blood sample
    run([ski], clock, 20)
    assert ski.state.load == 0 and ski.state.cadence == 0


def test_heart_rate_follows_the_power():
    clock = ManualClock()
    bike = SimBike(clock=clock, autorun=False)
    hr = SimHeartRate(treadmill=bike, clock=clock, autorun=False)
    bike.connect()
    hr.connect()
    bike.start()
    bike.set_target(120, 0)
    run([bike, hr], clock, 240)
    easy = hr.true_hr
    bike.set_target(300, 0)
    run([bike, hr], clock, 240)
    assert 95 < easy < 135 and hr.true_hr > easy + 30 and hr.last.cadence_spm is None


def test_manual_ergometer_reports_targets_not_readings():
    erg = ManualErgometer(sport="skierg")
    erg.connect()
    erg.set_target(140, 0)
    erg.start()
    st = erg.state
    assert not erg.controllable and not erg.measured and erg.sport == "skierg"
    assert st.load == 140 and st.measured is False and st.power_w is None and st.incline_pct == 0


def test_manager_connects_the_ergometer_of_the_sport():
    m = DeviceManager(Bus(), sim_time_scale=50)
    try:
        bike = m.connect_ergometer("bike", "sim")
        assert isinstance(bike, SimBike) and m.ergometer is bike and m.ergometer_sport == "bike"
        assert m.treadmill is bike and m.treadmill_mode == "sim"
        hr = m.connect_heart_rate("sim")
        assert hr._treadmill is bike
        ski = m.connect_ergometer("skierg", "sim")
        assert isinstance(ski, SimSkiErg) and not bike.connected and hr._treadmill is ski
        assert isinstance(m.connect_ergometer("skierg", "manual"), ManualErgometer)
        tm = m.connect_ergometer("run", "sim")
        assert m.ergometer_sport == "run" and tm is m.treadmill
        with pytest.raises(DeviceError):
            m.connect_ergometer("skierg", "ftms")       # a PM5 has no FTMS mode
        with pytest.raises(DeviceError):
            m.connect_ergometer("bike", "qz")
    finally:
        m.close()


def test_manager_remembers_the_trainer_and_refuses_while_qz_runs():
    import struct

    from lopanalys.devices.ftms_bike import SUPPORTED_POWER_RANGE

    from .fakes import FakeBle
    NEO = DeviceInfo("11:22:33:44:55:66", "Tacx Neo 2T 12345", -55)
    READS = {SUPPORTED_POWER_RANGE: struct.pack("<hhH", 0, 2200, 1)}
    saved: dict[str, DeviceInfo] = {}
    ble = FakeBle(reads=dict(READS))
    m = DeviceManager(Bus(), client_cls=ble.client_cls, remember=saved.__setitem__, recall=saved.get,
                      qz_check=lambda: False, settle_s=0)
    try:
        bike = m.connect_ergometer("bike", "ftms", NEO)
        assert saved["bike"] is NEO and bike.sport == "bike"
        assert m.connect_ergometer("bike", "ftms", NEO) is bike         # already connected: kept
        m.disconnect_treadmill()
        assert m.connect_ergometer("bike", "ftms").connected            # the remembered trainer
    finally:
        m.close()
    busy = DeviceManager(Bus(), client_cls=ble.client_cls, qz_check=lambda: True)
    try:
        with pytest.raises(DeviceError):
            busy.connect_ergometer("bike", "ftms", NEO)
    finally:
        busy.close()
