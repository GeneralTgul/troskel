"""Running cadence from the heart rate strap: RSC (0x2A53) parsing, the strap and the simulator."""
from __future__ import annotations

import struct

import pytest

from lopanalys.core.bus import TOPIC_HEART_RATE, Bus
from lopanalys.devices.base import DeviceInfo
from lopanalys.devices.heart_rate import (CADENCE_FRESH_S, HR_MEASUREMENT, RSC_MEASUREMENT, BleHeartRate,
                                          parse_rsc)
from lopanalys.devices.sim import ManualClock, SimAthlete, SimHeartRate, SimTreadmill

from .fakes import Collector, FakeBle

HRM = DeviceInfo("11:22:33:44:55:66", "HRM-Pro+:123456", -55)


def test_parse_minimal_and_full():
    m = parse_rsc(struct.pack("<BHB", 0x04, 3 * 256, 172))
    assert (m.speed_mps, m.cadence_spm, m.stride_m, m.distance_m, m.running) == (3.0, 172, None, None, True)
    m = parse_rsc(struct.pack("<BHBHI", 0x01 | 0x02, 512, 90, 105, 12345))
    assert (m.cadence_spm, m.stride_m, m.distance_m, m.running) == (90, 1.05, 1234.5, False)


def test_parse_bad():
    with pytest.raises(ValueError):
        parse_rsc(b"\x00\x01\x02")
    with pytest.raises(ValueError):
        parse_rsc(struct.pack("<BHB", 0x02, 0, 0) + b"\x01")   # distance flagged, two bytes missing


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_strap_cadence_goes_on_the_samples():
    ble, clock, bus = FakeBle(), _Clock(), Bus()
    hr = BleHeartRate(bus, client_cls=ble.client_cls, clock=clock)
    col = Collector(bus, TOPIC_HEART_RATE)
    try:
        hr.connect(HRM)
        assert hr.has_rsc
        ble.client.notify(RSC_MEASUREMENT, struct.pack("<BHB", 0x04, 800, 174))
        ble.client.notify(HR_MEASUREMENT, bytes([0x06, 150]))
        ble.wait_for(lambda: len(col.get(TOPIC_HEART_RATE)) == 1)
        assert col.get(TOPIC_HEART_RATE)[0].cadence_spm == 174.0
        clock.now += CADENCE_FRESH_S + 1          # no RSC for a while: the cadence is stale
        ble.client.notify(HR_MEASUREMENT, bytes([0x06, 151]))
        ble.wait_for(lambda: len(col.get(TOPIC_HEART_RATE)) == 2)
        assert col.get(TOPIC_HEART_RATE)[1].cadence_spm is None
        ble.client.notify(RSC_MEASUREMENT, struct.pack("<BHB", 0x04, 0, 0))   # standing: no cadence
        ble.client.notify(HR_MEASUREMENT, bytes([0x06, 152]))
        ble.wait_for(lambda: len(col.get(TOPIC_HEART_RATE)) == 3)
        assert col.get(TOPIC_HEART_RATE)[2].cadence_spm is None
    finally:
        hr.disconnect()


def test_strap_without_rsc_gives_no_cadence():
    ble, bus = FakeBle(missing_notify={RSC_MEASUREMENT}), Bus()
    hr = BleHeartRate(bus, client_cls=ble.client_cls)
    col = Collector(bus, TOPIC_HEART_RATE)
    try:
        hr.connect(HRM)
        assert hr.connected and not hr.has_rsc
        ble.client.notify(HR_MEASUREMENT, bytes([0x06, 140]))
        ble.wait_for(lambda: hr.last is not None)
        assert hr.last.cadence_spm is None
    finally:
        hr.disconnect()


@pytest.mark.parametrize("rsc", [True, False])
def test_simulator_can_send_rsc(rsc, monkeypatch):
    monkeypatch.delenv("LOPANALYS_SIM_RSC", raising=False)
    clock = ManualClock(1000.0)
    tm = SimTreadmill(None, clock=clock, autorun=False, publish_hz=20)
    hr = SimHeartRate(None, treadmill=tm, clock=clock, autorun=False, athlete=SimAthlete(noise_bpm=0), rsc=rsc)
    tm.connect()
    hr.connect()
    tm.set_target(12.0, 1.0)
    tm.start()
    for _ in range(60):
        clock.advance(1.0)
        tm.tick()
        hr.tick()
    assert hr.last is not None
    assert hr.last.cadence_spm == (pytest.approx(150 + 2.6 * 12, abs=1) if rsc else None)


def test_simulator_rsc_from_the_environment(monkeypatch):
    monkeypatch.setenv("LOPANALYS_SIM_RSC", "1")
    assert SimHeartRate(None).rsc
    monkeypatch.setenv("LOPANALYS_SIM_RSC", "0")
    assert not SimHeartRate(None).rsc


def test_rsc_strides_become_steps():
    """Garmin's HRM-Pro+ sends strides: 84 at 10 km/h is 168 steps/min (real hardware 2026-11-14)."""
    from lopanalys.devices.heart_rate import steps_per_min
    assert steps_per_min(84) == 168.0
    assert steps_per_min(174) == 174.0    # a sensor that already sends steps
