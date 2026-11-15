"""BLE heart rate: 0x2A37 parsing per the spec, contact handling, battery, reconnect."""
from __future__ import annotations

import struct

import pytest

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, TOPIC_HEART_RATE, TOPIC_LOG, Bus
from lopanalys.core.i18n import t
from lopanalys.devices.base import ConnState, DeviceInfo
from lopanalys.devices.heart_rate import BATTERY_LEVEL, HR_MEASUREMENT, BleHeartRate, parse_heart_rate

from .fakes import Collector, FakeBle

HRM = DeviceInfo("11:22:33:44:55:66", "HRM-Pro+:123456", -55)


def test_uint8_without_contact_support():
    m = parse_heart_rate(bytes([0x00, 72]))
    assert (m.bpm, m.contact, m.energy_kj, m.rr_ms) == (72, None, None, [])


def test_uint16_with_contact():
    m = parse_heart_rate(struct.pack("<BH", 0x01 | 0x04 | 0x02, 301))
    assert m.bpm == 301 and m.contact is True
    m2 = parse_heart_rate(struct.pack("<BH", 0x01 | 0x04, 0))
    assert m2.contact is False


def test_energy_and_rr_intervals():
    # flags: contact supported+detected, energy present, RR present; two RR values in 1/1024 s
    payload = struct.pack("<BBHHH", 0x02 | 0x04 | 0x08 | 0x10, 150, 77, 410, 1024)
    m = parse_heart_rate(payload)
    assert (m.bpm, m.energy_kj) == (150, 77)
    assert m.rr_ms == [400, 1000]


def test_rr_without_energy_and_odd_trailing_byte():
    m = parse_heart_rate(struct.pack("<BBH", 0x10, 60, 1024) + b"\x05")
    assert m.rr_ms == [1000]


def test_bad_packet():
    with pytest.raises(ValueError):
        parse_heart_rate(b"\x01\x50")
    with pytest.raises(ValueError):
        parse_heart_rate(b"\x00")


@pytest.fixture
def rig():
    bus = Bus()
    ble = FakeBle(reads={BATTERY_LEVEL: bytes([12])})
    hr = BleHeartRate(bus, client_cls=ble.client_cls, backoff_s=(0.02,))
    col = Collector(bus, TOPIC_HEART_RATE, TOPIC_DEVICE_STATUS, TOPIC_LOG)
    yield ble, hr, col
    hr.disconnect()


def test_samples_published(rig):
    ble, hr, col = rig
    hr.connect(HRM)
    ble.client.notify(HR_MEASUREMENT, struct.pack("<BBH", 0x06 | 0x10, 142, 433))
    ble.wait_for(lambda: hr.last is not None)
    s = hr.last
    assert (s.bpm, s.rr_ms, s.contact) == (142, [423], True)
    assert col.get(TOPIC_HEART_RATE) == [s]


def test_battery_reported_and_low_warning(rig):
    _ble, hr, col = rig
    hr.connect(HRM)
    assert hr.battery_pct == 12
    st = col.get(TOPIC_DEVICE_STATUS)[-1]
    assert st.state == ConnState.CONNECTED and st.battery_pct == 12 and st.device == "heart_rate"
    assert t("devices.hr.battery_low", pct=12) in col.get(TOPIC_LOG)


def test_no_contact_publishes_no_sample(rig):
    ble, hr, col = rig
    hr.connect(HRM)
    ble.client.notify(HR_MEASUREMENT, bytes([0x04, 0]))
    ble.client.notify(HR_MEASUREMENT, bytes([0x04, 0]))
    ble.wait_for(lambda: t("devices.hr.no_contact") in col.get(TOPIC_LOG))
    assert col.get(TOPIC_LOG).count(t("devices.hr.no_contact")) == 1
    assert col.get(TOPIC_HEART_RATE) == []
    ble.client.notify(HR_MEASUREMENT, bytes([0x06, 99]))
    ble.wait_for(lambda: hr.last is not None and hr.last.bpm == 99)


def test_reconnect_after_drop(rig):
    ble, hr, _col = rig
    hr.connect(HRM)
    ble.client.drop()
    ble.wait_for(lambda: len(ble.clients) == 2 and hr.connected)
    ble.client.notify(HR_MEASUREMENT, bytes([0x00, 120]))
    ble.wait_for(lambda: hr.last is not None and hr.last.bpm == 120)


def test_strap_without_battery_service():
    ble = FakeBle()
    hr = BleHeartRate(Bus(), client_cls=ble.client_cls)
    try:
        hr.connect(HRM)
        assert hr.battery_pct is None and hr.connected
    finally:
        hr.disconnect()
