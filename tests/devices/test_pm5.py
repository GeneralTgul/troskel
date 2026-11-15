"""Concept2 PM5: the parsers, short packets, and the read-only ergometer with a fake client."""
from __future__ import annotations

import pytest

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, TOPIC_ERGOMETER, TOPIC_STROKE, Bus
from lopanalys.core.i18n import t
from lopanalys.devices import pm5
from lopanalys.devices.base import ConnState, DeviceInfo
from lopanalys.devices.manager import DeviceManager
from lopanalys.devices.pm5 import (ADDITIONAL_STATUS_1, ADDITIONAL_STROKE_DATA, GENERAL_STATUS, STROKE_DATA,
                                   Pm5Ergometer)

from .fakes import Collector, FakeAdv, FakeBle, FakeDev

PM5 = DeviceInfo("C0:FF:EE:00:00:01", "PM5 430123456", -58)


def u(v: int, size: int) -> bytes:
    return int(v).to_bytes(size, "little")


def general(elapsed_s=60.0, distance_m=250.0, rowing=1, stroke=2, drag=95) -> bytes:
    return (u(round(elapsed_s * 100), 3) + u(round(distance_m * 10), 3) + bytes([1, 0, 1, rowing, stroke])
            + u(0, 3) + u(0, 3) + bytes([0, drag]))


def status1(rate=38, pace_s=125.0, hr=255, speed=4.0) -> bytes:
    return (u(6000, 3) + u(round(speed * 1000), 2) + bytes([rate, hr]) + u(round(pace_s * 100), 2)
            + u(round(pace_s * 100), 2) + u(0, 2) + u(0, 3) + bytes([128]))


def stroke(count=12, drive_s=0.62, recovery_s=0.95) -> bytes:
    return (u(6000, 3) + u(2500, 3) + bytes([112, round(drive_s * 100)]) + u(round(recovery_s * 100), 2)
            + u(850, 2) + u(900, 2) + u(600, 2) + u(4200, 2) + u(count, 2))


def stroke_power(watt=179, count=12) -> bytes:
    return u(6000, 3) + u(watt, 2) + u(900, 2) + u(count, 2) + u(0, 3) + u(0, 3)


# --- parsing ----------------------------------------------------------------------------------
def test_uuids_are_on_concept2s_base():
    assert pm5.ROWING_SERVICE == "ce060030-43e5-11e4-916c-0800200c9a66"
    assert GENERAL_STATUS.startswith("ce060031-") and ADDITIONAL_STROKE_DATA.startswith("ce060036-")


def test_general_status():
    d = pm5.parse_general_status(general(61.25, 251.3))
    assert (d.elapsed_s, d.distance_m, d.rowing_state, d.stroke_state, d.drag_factor) == (
        pytest.approx(61.25), pytest.approx(251.3), 1, 2, 95)
    assert len(general()) == 19 and not d.truncated


def test_additional_status_1_and_the_missing_strap():
    d = pm5.parse_additional_status_1(status1(38, 125.0, 255))
    assert (d.stroke_rate, d.pace_500m_s, d.heart_rate_bpm, d.speed_mps, d.machine_type) == (38, 125.0, None, 4.0, 128)
    assert pm5.parse_additional_status_1(status1(hr=142)).heart_rate_bpm == 142
    assert not pm5.parse_additional_status_1(status1()[:16]).truncated       # older firmware: 16 bytes


def test_stroke_packets():
    s = pm5.parse_stroke_data(stroke(12, 0.62, 0.95))
    assert (s.stroke_count, s.drive_time_s, s.recovery_time_s, s.drive_length_m) == (
        12, pytest.approx(0.62), pytest.approx(0.95), pytest.approx(1.12))
    assert s.peak_force_n == pytest.approx(90.0 * pm5.LBS_TO_N)
    p = pm5.parse_additional_stroke_data(stroke_power(179, 12))
    assert (p.power_w, p.stroke_count) == (179, 12)


def test_a_short_packet_keeps_what_could_be_read():
    one = pm5.parse_general_status(b"\x10")
    assert one.truncated and one.elapsed_s is None and one.drag_factor is None
    half = pm5.parse_additional_status_1(status1()[:7])
    assert half.truncated and half.stroke_rate == 38 and half.pace_500m_s is None


def test_concept2s_formula_between_pace_and_watt():
    assert pm5.watt_from_pace(120.0) == pytest.approx(202.5, abs=0.1)     # 2:00 /500 m
    assert pm5.pace_from_watt(179) == pytest.approx(125.0, abs=0.2)       # 2:05
    assert pm5.watt_from_pace(0) is None and pm5.pace_from_watt(None) is None


# --- the ergometer with the fake client ---------------------------------------------------------
class Tick:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def rig():
    bus = Bus()
    ble = FakeBle()
    clock = Tick()
    erg = Pm5Ergometer(bus, client_cls=ble.client_cls, clock=clock, backoff_s=(0.02,))
    col = Collector(bus, TOPIC_ERGOMETER, TOPIC_DEVICE_STATUS, TOPIC_STROKE)
    yield ble, erg, col, clock
    erg.disconnect()


def test_reads_watt_rate_and_pace_and_writes_nothing(rig):
    ble, erg, col, _clock = rig
    erg.connect(PM5)
    assert col.get(TOPIC_DEVICE_STATUS)[-1].state == ConnState.CONNECTED
    assert not erg.controllable and erg.measured and erg.sport == "skierg"
    erg.set_target(180, 0)
    erg.start()
    c = ble.client
    c.notify(GENERAL_STATUS, general())
    c.notify(ADDITIONAL_STATUS_1, status1(38, 125.0, 142))
    c.notify(ADDITIONAL_STROKE_DATA, stroke_power(179))
    c.notify(STROKE_DATA, stroke(12))
    ble.wait_for(lambda: erg.state is not None and erg.state.stroke_count == 12)
    st = erg.state
    assert st.load == 179 == st.power_w and st.target_load == 180 and st.cadence == 38
    assert st.pace_500m_s == 125.0 and st.heart_rate_bpm == 142 and st.drag_factor == 95
    assert st.drive_time_s == pytest.approx(0.62) and st.recovery_time_s == pytest.approx(0.95) and st.running
    strokes = col.get(TOPIC_STROKE)
    assert strokes and strokes[-1].count == 12 and strokes[-1].power_w == 179
    assert ble.writes == []                       # read only: nothing is ever written to the monitor


def test_power_from_the_pace_until_a_stroke_power_arrives(rig):
    ble, erg, _col, _clock = rig
    erg.connect(PM5)
    ble.client.notify(ADDITIONAL_STATUS_1, status1(36, 120.0))
    ble.wait_for(lambda: erg.state is not None and erg.state.power_w is not None)
    assert erg.state.power_w == pytest.approx(202.5, abs=0.1)


def test_no_stroke_for_a_while_means_the_athlete_stopped(rig):
    ble, erg, _col, clock = rig
    erg.connect(PM5)
    ble.client.notify(ADDITIONAL_STATUS_1, status1(38, 125.0))
    ble.client.notify(ADDITIONAL_STROKE_DATA, stroke_power(179))
    ble.wait_for(lambda: erg.state is not None and erg.state.power_w == 179)
    clock.now += pm5.IDLE_S + 1
    st = erg.state
    assert st.power_w == 0 and st.load == 0 and st.cadence == 0 and not st.running
    ble.client.notify(ADDITIONAL_STROKE_DATA, stroke_power(150))          # pulling again
    ble.wait_for(lambda: erg.state.power_w == 150)


def test_short_notifications_do_not_break_the_stream(rig):
    ble, erg, _col, _clock = rig
    erg.connect(PM5)
    ble.client.notify(ADDITIONAL_STROKE_DATA, stroke_power(160))
    ble.client.notify(GENERAL_STATUS, b"\x01")
    ble.client.notify(ADDITIONAL_STATUS_1, b"\x01")
    ble.client.notify(STROKE_DATA, b"\x01")
    ble.client.notify(ADDITIONAL_STATUS_1, status1(40, 128.0))
    ble.wait_for(lambda: erg.state is not None and erg.state.cadence == 40)
    assert erg.state.power_w == 160


def test_not_a_pm5_gives_a_coach_message():
    ble = FakeBle(missing_notify={ADDITIONAL_STATUS_1})
    erg = Pm5Ergometer(Bus(), client_cls=ble.client_cls, backoff_s=(0.02,))
    with pytest.raises(Exception) as e:
        erg.connect(PM5)
    assert str(e.value) == t("devices.pm5.not_pm5", name=PM5.name)


def test_scan_finds_the_monitor_by_uuid_or_name():
    class _S:
        calls: list = []

        @classmethod
        async def discover(cls, timeout=5.0, return_adv=False, **kw):
            cls.calls.append(kw)
            return {"A": (FakeDev("A", "PM5 430123456"), FakeAdv("PM5 430123456", -60, [])),
                    "B": (FakeDev("B", None), FakeAdv(None, -50, [pm5.PM5_DISCOVERY.upper()])),
                    "C": (FakeDev("C", "HRM-Pro+"), FakeAdv("HRM-Pro+", -40, ["0000180d-0000-1000-8000-00805f9b34fb"]))}

    m = DeviceManager(Bus(), scanner_cls=_S)
    try:
        assert [d.address for d in m.scan_ergometers("skierg")] == ["B", "A"]
        assert "service_uuids" not in _S.calls[-1]          # found by name too, so the scan is not filtered
    finally:
        m.close()
