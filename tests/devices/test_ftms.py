"""FTMS treadmill: payload parsing per the spec, exact control point bytes, reconnect."""
from __future__ import annotations

import struct
import threading

import pytest

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, TOPIC_LOG, TOPIC_TREADMILL, Bus
from lopanalys.core.i18n import t
from lopanalys.devices import ftms
from lopanalys.devices.base import ConnState, DeviceInfo
from lopanalys.devices.errors import DeviceError
from lopanalys.devices.ftms import (
    MACHINE_STATUS, RESULT_CONTROL_NOT_PERMITTED, SUPPORTED_INCLINATION_RANGE,
    SUPPORTED_SPEED_RANGE, TREADMILL_DATA, FtmsTreadmill, parse_treadmill_data,
)

from .fakes import Collector, FakeBle

RECOIL = DeviceInfo("AA:BB:CC:DD:EE:FF", "SW1000EAI-0001", -60)
# Recoil ranges: 1.0-18.0 km/h step 0.1; 0-12 % step 0.5
RANGES = {
    SUPPORTED_SPEED_RANGE: struct.pack("<HHH", 100, 1800, 10),
    SUPPORTED_INCLINATION_RANGE: struct.pack("<hhH", 0, 120, 5),
}


# --- parsing ----------------------------------------------------------------------------------
def test_speed_only_packet():
    d = parse_treadmill_data(struct.pack("<HH", 0x0000, 1234))
    assert d.speed_kmh == pytest.approx(12.34)
    assert d.incline_pct is None and not d.truncated


def test_more_data_bit_means_no_speed():
    # bit 0 set: Instantaneous Speed absent; bit 2 total distance (uint24) present
    d = parse_treadmill_data(struct.pack("<H", 0x0005) + (70000).to_bytes(3, "little"))
    assert d.speed_kmh is None
    assert d.total_distance_m == 70000


def test_all_fields_in_spec_order():
    flags = 0x1FFE  # every optional field, speed present (bit 0 = 0)
    payload = struct.pack("<H", flags)
    payload += struct.pack("<H", 1050)                    # speed 10.50
    payload += struct.pack("<H", 990)                     # avg speed 9.90
    payload += (1234).to_bytes(3, "little")               # distance
    payload += struct.pack("<hh", -15, -9)                # incline -1.5 %, ramp -0.9 deg
    payload += struct.pack("<HH", 123, 45)                # elevation +12.3 m, -4.5 m
    payload += struct.pack("<BB", 57, 60)                 # pace 5.7, avg 6.0 km/min
    payload += struct.pack("<HHB", 321, 800, 13)          # energy
    payload += struct.pack("<B", 152)                     # heart rate
    payload += struct.pack("<B", 95)                      # MET 9.5
    payload += struct.pack("<HH", 600, 120)               # elapsed, remaining
    payload += struct.pack("<hh", -40, 250)               # force, power
    d = parse_treadmill_data(payload)
    assert (d.speed_kmh, d.avg_speed_kmh, d.total_distance_m) == (10.5, 9.9, 1234)
    assert (d.incline_pct, d.ramp_angle_deg) == (-1.5, -0.9)
    assert (d.elevation_gain_pos_m, d.elevation_gain_neg_m) == (12.3, 4.5)
    assert (d.pace_km_min, d.avg_pace_km_min) == (5.7, 6.0)
    assert (d.energy_total_kcal, d.energy_per_hour_kcal, d.energy_per_min_kcal) == (321, 800, 13)
    assert (d.heart_rate_bpm, d.met, d.elapsed_s, d.remaining_s) == (152, 9.5, 600, 120)
    assert (d.force_on_belt_n, d.power_w) == (-40, 250)
    assert not d.truncated


def test_incline_not_available_and_truncated():
    d = parse_treadmill_data(struct.pack("<HHhh", 0x0008, 800, 0x7FFF, 0x7FFF))
    assert d.speed_kmh == 8.0 and d.incline_pct is None and d.ramp_angle_deg is None
    t2 = parse_treadmill_data(struct.pack("<HH", 0x0108, 800))  # says incline + HR but stops early
    assert t2.speed_kmh == 8.0 and t2.truncated


def test_command_bytes():
    assert ftms.cmd_request_control() == b"\x00"
    assert ftms.cmd_reset() == b"\x01"
    assert ftms.cmd_set_target_speed(12.34) == bytes([0x02, 0xD2, 0x04])
    assert ftms.cmd_set_target_inclination(-1.5) == bytes([0x03, 0xF1, 0xFF])
    assert ftms.cmd_set_target_inclination(12.0) == bytes([0x03, 0x78, 0x00])
    assert ftms.cmd_start() == b"\x07"
    assert ftms.cmd_stop() == b"\x08\x01"
    assert ftms.cmd_stop(pause=True) == b"\x08\x02"
    assert ftms.parse_control_response(b"\x80\x02\x01") == (0x02, 0x01)
    assert ftms.parse_control_response(b"\x02\x01") is None


# --- device with the fake client -----------------------------------------------------------------
@pytest.fixture
def rig():
    bus = Bus()
    ble = FakeBle(reads=dict(RANGES))
    tm = FtmsTreadmill(bus, client_cls=ble.client_cls, keepalive_s=0, backoff_s=(0.02,), command_timeout_s=1.0)
    col = Collector(bus, TOPIC_TREADMILL, TOPIC_DEVICE_STATUS, TOPIC_LOG)
    yield bus, ble, tm, col
    tm.disconnect()


def test_connect_requests_control_and_reads_ranges(rig):
    _bus, ble, tm, col = rig
    tm.connect(RECOIL)
    assert ble.cp_writes()[0] == b"\x00"
    assert all(r is True for (_u, _d, r) in ble.writes)  # control point writes need a response
    assert (tm.min_speed_kmh, tm.max_speed_kmh, tm.max_incline_pct) == (1.0, 18.0, 12.0)
    assert col.get(TOPIC_DEVICE_STATUS)[-1].state == ConnState.CONNECTED
    assert col.get(TOPIC_DEVICE_STATUS)[-1].name == "SW1000EAI-0001"


def test_set_target_sends_clamped_bytes(rig):
    _bus, ble, tm, _col = rig
    tm.connect(RECOIL)
    tm.set_target(25.0, 15.0)
    ble.wait_for(lambda: len(ble.cp_writes()) >= 3)
    assert ble.cp_writes()[1:3] == [bytes([0x02, 0x08, 0x07]), bytes([0x03, 0x78, 0x00])]
    tm.set_target(12.04, 1.2)       # snaps to 0.1 km/h and 0.5 % increments
    ble.wait_for(lambda: len(ble.cp_writes()) >= 5)
    assert ble.cp_writes()[3:5] == [struct.pack("<BH", 2, 1200), struct.pack("<Bh", 3, 10)]


def test_start_and_stop_bytes(rig):
    _bus, ble, tm, col = rig
    tm.connect(RECOIL)
    tm.start()
    ble.wait_for(lambda: b"\x07" in ble.cp_writes())
    ble.wait_for(lambda: t("devices.ftms.press_start") in col.get(TOPIC_LOG))  # belt did not move
    tm.stop()
    ble.wait_for(lambda: b"\x08\x01" in ble.cp_writes())


def test_notifications_update_state(rig):
    _bus, ble, tm, col = rig
    tm.connect(RECOIL)
    tm.set_target(10.0, 1.0)
    ble.client.notify(TREADMILL_DATA, struct.pack("<HHhh", 0x0008, 1000, 10, 6))
    ble.wait_for(lambda: tm.state is not None and tm.state.speed_kmh == 10.0)
    # a split "More Data" packet carries only distance and keeps the speed
    ble.client.notify(TREADMILL_DATA, struct.pack("<H", 0x0005) + (512).to_bytes(3, "little"))
    ble.wait_for(lambda: tm.state.distance_m == 512.0)
    st = tm.state
    assert st.speed_kmh == 10.0 and st.incline_pct == 1.0 and st.running is True and st.measured
    assert st.target_speed_kmh == 10.0 and st.target_incline_pct == 1.0
    assert col.get(TOPIC_TREADMILL)[-1] == st


def test_machine_status_events(rig):
    _bus, ble, tm, col = rig
    tm.connect(RECOIL)
    ble.client.notify(MACHINE_STATUS, bytes([0x04]))
    ble.wait_for(lambda: tm.state is not None and tm.state.running is True)
    ble.client.notify(MACHINE_STATUS, bytes([0x03]))
    ble.wait_for(lambda: tm.state.running is False)
    assert t("devices.ftms.safety_key") in col.get(TOPIC_LOG)
    ble.client.notify(MACHINE_STATUS, struct.pack("<Bh", 0x06, 25))
    ble.wait_for(lambda: tm.state.target_incline_pct == 2.5)


def test_control_not_permitted_requests_control_again():
    bus = Bus()
    ble = FakeBle(reads=dict(RANGES), results={0x02: [RESULT_CONTROL_NOT_PERMITTED]})
    tm = FtmsTreadmill(bus, client_cls=ble.client_cls, keepalive_s=0)
    try:
        tm.connect(RECOIL)
        tm.set_target(9.0, 0.0)
        ble.wait_for(lambda: len(ble.cp_writes()) >= 5)
        speed = struct.pack("<BH", 2, 900)
        assert ble.cp_writes()[:5] == [b"\x00", speed, b"\x00", speed, struct.pack("<Bh", 3, 0)]
    finally:
        tm.disconnect()


def test_reconnects_and_reapplies_targets(rig):
    _bus, ble, tm, col = rig
    tm.connect(RECOIL)
    tm.set_target(11.0, 2.0)
    ble.wait_for(lambda: len(ble.cp_writes()) >= 3)
    ble.client.drop()
    ble.wait_for(lambda: len(ble.clients) == 2 and tm.connected)
    ble.wait_for(lambda: len(ble.cp_writes()) >= 6)
    assert ble.cp_writes()[3:6] == [b"\x00", struct.pack("<BH", 2, 1100), struct.pack("<Bh", 3, 20)]
    states = [s.state for s in col.get(TOPIC_DEVICE_STATUS)]
    assert states.count(ConnState.CONNECTED) == 2


def test_first_connect_failure_mentions_qz():
    ble = FakeBle(fail_connects=1)
    tm = FtmsTreadmill(Bus(), client_cls=ble.client_cls, keepalive_s=0)
    with pytest.raises(DeviceError) as exc:
        tm.connect(RECOIL)
    assert t("devices.ftms.close_qz") in str(exc.value)
    assert not tm.connected


def test_missing_ftms_characteristic():
    ble = FakeBle(missing_notify={TREADMILL_DATA})
    tm = FtmsTreadmill(Bus(), client_cls=ble.client_cls, keepalive_s=0)
    with pytest.raises(DeviceError) as exc:
        tm.connect(RECOIL)
    assert str(exc.value) == t("devices.ftms.not_ftms", name="SW1000EAI-0001")


def test_ranges_default_when_missing_and_keepalive_reads():
    ble = FakeBle(reads={SUPPORTED_SPEED_RANGE: struct.pack("<HHH", 50, 2000, 10)})
    tm = FtmsTreadmill(Bus(), client_cls=ble.client_cls, keepalive_s=0.05)
    reads = []
    orig = ble.client_cls.read_gatt_char

    async def counting(self, uuid, **kw):
        reads.append(uuid)
        return await orig(self, uuid, **kw)

    ble.client_cls.read_gatt_char = counting
    try:
        tm.connect(RECOIL)
        assert (tm.min_speed_kmh, tm.max_speed_kmh) == (0.5, 20.0)
        assert tm.max_incline_pct == ftms.DEFAULT_MAX_INCLINE
        ble.wait_for(lambda: reads.count(SUPPORTED_SPEED_RANGE) >= 3)
    finally:
        tm.disconnect()


def test_disconnect_stops_thread_and_is_idempotent():
    before = threading.active_count()
    ble = FakeBle(reads=dict(RANGES))
    tm = FtmsTreadmill(Bus(), client_cls=ble.client_cls, keepalive_s=0)
    tm.connect(RECOIL)
    tm.disconnect()
    tm.disconnect()
    tm.set_target(10, 1)  # after disconnect: stored, no crash
    assert threading.active_count() == before
    assert not ble.client.is_connected


def test_scan_filters_ftms(monkeypatch):
    from .fakes import make_scanner
    scanner = make_scanner([
        ("AA", "SW1000EAI-0001", -70, [ftms.FTMS_SERVICE]),
        ("BB", "Speaker", -40, ["0000110b-0000-1000-8000-00805f9b34fb"]),
        ("CC", "Other FTMS", -50, [ftms.FTMS_SERVICE.upper()]),
    ])
    tm = FtmsTreadmill(Bus(), scanner_cls=scanner)
    found = tm.scan(timeout_s=0.1)
    assert [d.address for d in found] == ["CC", "AA"]
    assert scanner.calls[0]["return_adv"] is True


def test_reconnect_after_stop_sends_no_speed(rig):
    """The program stopped the belt (a lactate pause) and the link dropped: the stage speed went
    out again on the reconnect, to a belt that stands still with the athlete on it."""
    _bus, ble, tm, _col = rig
    tm.connect(RECOIL)
    tm.start()
    tm.set_target(11.0, 2.0)
    tm.stop()
    ble.wait_for(lambda: b"\x08\x01" in ble.cp_writes())
    before = len(ble.cp_writes())
    ble.client.drop()
    ble.wait_for(lambda: len(ble.clients) == 2 and tm.connected)
    ble.wait_for(lambda: struct.pack("<Bh", 3, 20) in ble.cp_writes()[before:])   # the incline is kept
    assert struct.pack("<BH", 2, 1100) not in ble.cp_writes()[before:]
    tm.start()                                                                   # the pause is over
    ble.wait_for(lambda: struct.pack("<BH", 2, 1100) in ble.cp_writes()[before:])
