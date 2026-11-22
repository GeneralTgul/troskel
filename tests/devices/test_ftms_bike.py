"""FTMS trainer: Indoor Bike Data per the spec, the exact control point bytes for ERG, reconnect."""
from __future__ import annotations

import struct

import pytest

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, TOPIC_ERGOMETER, TOPIC_LOG, Bus
from lopanalys.core.i18n import t
from lopanalys.devices import ftms_bike
from lopanalys.devices._scan import FTMS_UUID
from lopanalys.devices.base import ConnState, DeviceInfo, ErgometerState
from lopanalys.devices.ftms import RESULT_CONTROL_NOT_PERMITTED
from lopanalys.devices.ftms_bike import (INDOOR_BIKE_DATA, MACHINE_FEATURE, SUPPORTED_POWER_RANGE, FtmsBike,
                                         parse_indoor_bike_data)
from lopanalys.devices.manager import DeviceManager

from .fakes import Collector, FakeAdv, FakeBle, FakeDev

NEO = DeviceInfo("11:22:33:44:55:66", "Tacx Neo 2T 12345", -55)
READS = {
    SUPPORTED_POWER_RANGE: struct.pack("<hhH", 0, 2200, 1),
    MACHINE_FEATURE: struct.pack("<II", 0x0000_4086, ftms_bike.FEATURE_POWER_TARGET | ftms_bike.FEATURE_SIMULATION),
}


def bike_packet(power: int, cadence_rpm: float, speed_kmh: float = 30.0) -> bytes:
    # speed present (bit 0 = 0), cadence (bit 2), power (bit 6)
    return struct.pack("<HHHh", 0x0044, round(speed_kmh * 100), round(cadence_rpm * 2), power)


# --- parsing ----------------------------------------------------------------------------------
def test_power_and_cadence_packet():
    d = parse_indoor_bike_data(bike_packet(212, 87.5))
    assert (d.speed_kmh, d.cadence_rpm, d.power_w) == (30.0, 87.5, 212) and not d.truncated


def test_more_data_bit_means_no_speed():
    d = parse_indoor_bike_data(struct.pack("<Hh", 0x0041, -3))      # bit 0 set: no speed; power only
    assert d.speed_kmh is None and d.power_w == -3


def test_all_fields_in_spec_order():
    flags = 0x1FFE
    p = struct.pack("<H", flags)
    p += struct.pack("<HH", 3050, 2990)                 # speed, average speed
    p += struct.pack("<HH", 171, 168)                   # cadence 85.5, average 84
    p += (12345).to_bytes(3, "little")                  # distance
    p += struct.pack("<h", 40)                          # resistance level
    p += struct.pack("<hh", 250, 243)                   # power, average power
    p += struct.pack("<HHB", 321, 800, 13)              # energy
    p += struct.pack("<B", 152)                         # heart rate
    p += struct.pack("<B", 95)                          # MET
    p += struct.pack("<HH", 600, 120)                   # elapsed, remaining
    d = parse_indoor_bike_data(p)
    assert (d.speed_kmh, d.avg_speed_kmh, d.cadence_rpm, d.avg_cadence_rpm) == (30.5, 29.9, 85.5, 84.0)
    assert (d.total_distance_m, d.resistance_level, d.power_w, d.avg_power_w) == (12345, 40, 250, 243)
    assert (d.energy_total_kcal, d.heart_rate_bpm, d.met, d.elapsed_s, d.remaining_s) == (321, 152, 9.5, 600, 120)
    assert not d.truncated
    assert parse_indoor_bike_data(p[:9]).truncated and parse_indoor_bike_data(p[:9]).cadence_rpm == 85.5


def test_command_bytes():
    assert ftms_bike.cmd_set_target_power(200) == bytes([0x05, 0xC8, 0x00])
    assert ftms_bike.cmd_set_target_power(-5) == bytes([0x05, 0xFB, 0xFF])
    assert ftms_bike.cmd_set_target_resistance(12.3) == bytes([0x04, 123])
    assert ftms_bike.cmd_set_simulation(1.5) == struct.pack("<BhhBB", 0x11, 0, 150, 40, 51)
    r = ftms_bike.parse_power_range(struct.pack("<hhH", 0, 2200, 1))
    assert (r.minimum, r.maximum, r.increment) == (0.0, 2200.0, 1.0)


# --- device with the fake client -----------------------------------------------------------------
@pytest.fixture
def rig():
    bus = Bus()
    ble = FakeBle(reads=dict(READS))
    bike = FtmsBike(bus, client_cls=ble.client_cls, backoff_s=(0.02,), command_timeout_s=1.0)
    col = Collector(bus, TOPIC_ERGOMETER, TOPIC_DEVICE_STATUS, TOPIC_LOG)
    yield bus, ble, bike, col
    bike.disconnect()


def test_connect_takes_control_and_reads_the_power_range(rig):
    _bus, ble, bike, col = rig
    bike.connect(NEO)
    assert ble.cp_writes()[0] == b"\x00"
    assert (bike.min_speed_kmh, bike.max_speed_kmh, bike.max_incline_pct) == (0.0, 2200.0, 0.0)
    assert bike.erg_supported is True and bike.controllable and bike.sport == "bike"
    st = col.get(TOPIC_DEVICE_STATUS)[-1]
    assert st.state == ConnState.CONNECTED and st.name == "Tacx Neo 2T 12345" and st.device == "treadmill"


def test_erg_sends_start_then_the_target_power(rig):
    _bus, ble, bike, _col = rig
    bike.connect(NEO)
    bike.start()
    bike.set_target(200, 0)
    ble.wait_for(lambda: bytes([0x05, 0xC8, 0x00]) in ble.cp_writes())
    assert b"\x07" in ble.cp_writes()
    bike.set_target(5000, 0)                        # clamped to the trainer's range
    ble.wait_for(lambda: struct.pack("<Bh", 5, 2200) in ble.cp_writes())
    bike.stop()
    ble.wait_for(lambda: b"\x08\x01" in ble.cp_writes())


def test_the_state_carries_watt_as_the_load(rig):
    _bus, ble, bike, col = rig
    bike.connect(NEO)
    bike.set_target(200, 0)
    ble.client.notify(INDOOR_BIKE_DATA, bike_packet(198, 86))
    ble.wait_for(lambda: bike.state is not None and bike.state.power_w == 198)
    st = bike.state
    assert isinstance(st, ErgometerState) and st.load == 198 == st.speed_kmh and st.target_load == 200
    assert st.cadence == 86 and st.wheel_speed_kmh == 30.0 and st.running and st.measured and not st.low_cadence
    # a split packet with only the cadence keeps the power; below 50 rpm with a target ERG cannot hold
    ble.client.notify(INDOOR_BIKE_DATA, struct.pack("<HH", 0x0005, 90))
    ble.wait_for(lambda: bike.state.cadence == 45)
    assert bike.state.power_w == 198 and bike.state.low_cadence
    assert col.get(TOPIC_ERGOMETER)[-1].cadence == 45


def test_free_stage_sends_simulation_and_no_power_target(rig):
    _bus, ble, bike, _col = rig
    bike.connect(NEO)
    bike.set_target(150, 0)
    ble.wait_for(lambda: struct.pack("<Bh", 5, 150) in ble.cp_writes())
    n = len(ble.cp_writes())
    bike.set_free()
    ble.wait_for(lambda: len(ble.cp_writes()) > n)
    assert ble.cp_writes()[-1] == ftms_bike.cmd_set_simulation(0.0)
    ble.client.notify(INDOOR_BIKE_DATA, bike_packet(260, 40))
    ble.wait_for(lambda: bike.state is not None and bike.state.power_w == 260)
    assert bike.state.free and bike.state.target_load is None and not bike.state.low_cadence


def test_control_not_permitted_asks_again_with_the_trainers_words(rig):
    _bus, ble, bike, col = rig
    ble.results[0x05] = [RESULT_CONTROL_NOT_PERMITTED, RESULT_CONTROL_NOT_PERMITTED]
    bike.connect(NEO)
    bike.set_target(180, 0)
    ble.wait_for(lambda: t("devices.bike.control_denied") in col.get(TOPIC_LOG))
    assert ble.cp_writes().count(b"\x00") >= 2


def test_reconnect_sends_the_target_again(rig):
    _bus, ble, bike, _col = rig
    bike.connect(NEO)
    bike.start()
    bike.set_target(220, 0)
    ble.wait_for(lambda: struct.pack("<Bh", 5, 220) in ble.cp_writes())
    n = len(ble.cp_writes())
    ble.client.drop()
    ble.wait_for(lambda: len(ble.clients) == 2 and struct.pack("<Bh", 5, 220) in ble.cp_writes()[n:])
    assert b"\x07" in ble.cp_writes()[n:]


def test_not_a_trainer_gives_a_coach_message():
    ble = FakeBle(reads=dict(READS), missing_notify={INDOOR_BIKE_DATA})
    bike = FtmsBike(Bus(), client_cls=ble.client_cls, backoff_s=(0.02,))
    with pytest.raises(Exception) as e:
        bike.connect(NEO)
    assert str(e.value) == t("devices.bike.not_ftms", name=NEO.name)


# --- scanning: a trainer and a treadmill share the service ----------------------------------------
def _scanner(entries):
    class _S:
        @classmethod
        async def discover(cls, timeout=5.0, return_adv=False, **kw):
            return {a: (FakeDev(a, n), FakeAdv(n, r, [FTMS_UUID], {FTMS_UUID: data} if data else {}))
                    for a, n, r, data in entries}
    return _S


def test_scan_tells_trainers_from_treadmills():
    found = [("A", "Tacx Neo 2T", -50, bytes([0x01, 0x20, 0x00])),        # says indoor bike
             ("B", "SW1000EAI-0001", -60, bytes([0x01, 0x01, 0x00])),     # says treadmill
             ("C", "Unknown FTMS", -70, None)]                            # does not say
    m = DeviceManager(Bus(), scanner_cls=_scanner(found))
    try:
        assert [d.address for d in m.scan_ergometers("bike")] == ["A", "C"]
        assert [d.address for d in m.scan_treadmills()] == ["B", "C"]
        assert [d.address for d in m.scan_ergometers("run")] == ["B", "C"]
    finally:
        m.close()


def test_reconnect_after_stop_keeps_the_trainer_released(rig):
    """The trainer was released (stop) and the link dropped: the power target went out again."""
    _bus, ble, bike, _col = rig
    bike.connect(NEO)
    bike.start()
    bike.set_target(200)
    bike.stop()
    ble.wait_for(lambda: b"\x08\x01" in ble.cp_writes())
    before = len(ble.cp_writes())
    ble.client.drop()
    ble.wait_for(lambda: len(ble.clients) == 2 and bike.connected)
    assert ftms_bike.cmd_set_target_power(200) not in ble.cp_writes()[before:]
    bike.set_target(210)                                                         # the next stage
    ble.wait_for(lambda: ftms_bike.cmd_set_target_power(210) in ble.cp_writes()[before:])


def test_a_degenerate_power_range_falls_back_to_the_defaults():
    """A trainer that reports 0/0/0 as its Supported Power Range clamped every target to 0 W."""
    ble = FakeBle(reads=dict(READS, **{SUPPORTED_POWER_RANGE: struct.pack("<hhH", 0, 0, 0)}))
    bike = FtmsBike(Bus(), client_cls=ble.client_cls, backoff_s=(0.02,))
    try:
        bike.connect(NEO)
        assert (bike.min_speed_kmh, bike.max_speed_kmh) == (ftms_bike.DEFAULT_MIN_POWER, ftms_bike.DEFAULT_MAX_POWER)
        assert bike.clamp_power(180.0) == 180.0
    finally:
        bike.disconnect()
