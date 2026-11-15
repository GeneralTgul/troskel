"""The test as a workout on the Concept2 PM5: CSAFE frames, the write to the control service, and
what happens when the monitor does not take it (the test goes on with reading only)."""
from __future__ import annotations

import pytest

from lopanalys.core.bus import TOPIC_LOG, Bus
from lopanalys.core.i18n import t
from lopanalys.devices import csafe
from lopanalys.devices.base import DeviceInfo
from lopanalys.devices.csafe import Interval
from lopanalys.devices.pm5 import ADDITIONAL_STATUS_1, CONTROL_RX, CONTROL_TX, Pm5Ergometer

from .fakes import Collector, FakeBle

PM5 = DeviceInfo("C0:FF:EE:00:00:01", "PM5 430123456", -58)


# --- frames (the framing is checked against open code; the PM commands are from memory) ---------
def test_frame_has_flags_and_an_xor_checksum():
    assert csafe.frame(bytes([0x91])) == bytes([0xF1, 0x91, 0x91, 0xF2])
    f = csafe.frame(bytes([0x1A, 0x03, 0x05, 0x01, 0x02]))
    assert f[0] == 0xF1 and f[-1] == 0xF2 and f[-2] == 0x1A ^ 0x03 ^ 0x05 ^ 0x01 ^ 0x02
    assert csafe.unframe(f) == bytes([0x1A, 0x03, 0x05, 0x01, 0x02])
    assert csafe.unframe(f[:-2] + b"\x00\xf2") is None and csafe.unframe(b"\xf1\xf2") is None


def test_flag_bytes_in_the_contents_are_stuffed():
    assert csafe.stuff(bytes([0xF0, 0x10, 0xF3])) == bytes([0xF3, 0x00, 0x10, 0xF3, 0x03])
    body = bytes([0x20, 0xF1, 0xF2, 0x7F])
    f = csafe.frame(body)
    assert f.count(0xF1) == 1 and f.count(0xF2) == 1 and csafe.unframe(f) == body
    assert csafe.frame(bytes([0xF2]))[1:-1] == bytes([0xF3, 0x02, 0xF3, 0x02])      # the checksum is stuffed too


def test_an_interval_carries_length_rest_and_target_pace():
    body = csafe.unframe(csafe.interval_frame(2, Interval(240, 30, 100)))
    assert body[0] == csafe.PM_WRAPPER and body[1] == len(body) - 2
    inner = body[2:]
    assert inner[:3] == bytes([csafe.PM_SET_WORKOUTINTERVALCOUNT, 1, 2])
    i = inner.index(bytes([csafe.PM_SET_WORKOUTDURATION, 5]))
    assert inner[i + 2] == 0 and int.from_bytes(inner[i + 3:i + 7], csafe.PM_BYTE_ORDER) == 24000      # 240 s in 0.01 s
    j = inner.index(bytes([csafe.PM_SET_RESTDURATION, 2]))
    assert int.from_bytes(inner[j + 2:j + 4], csafe.PM_BYTE_ORDER) == 30
    k = inner.index(bytes([csafe.PM_SET_TARGETPACETIME, 4]))
    assert int.from_bytes(inner[k + 2:k + 6], csafe.PM_BYTE_ORDER) == pytest.approx(15183, abs=2)     # 2:32 /500 m
    assert inner[-3:] == bytes([csafe.PM_CONFIGURE_WORKOUT, 1, 1])
    free = csafe.unframe(csafe.interval_frame(0, Interval(1200)))
    assert bytes([csafe.PM_SET_TARGETPACETIME, 4]) not in free


def test_a_workout_is_one_frame_per_interval_and_the_screen_last():
    frames = csafe.workout_frames([Interval(240, 30, 80 + 15 * i) for i in range(7)])
    assert len(frames) == 8 and all(csafe.unframe(f) for f in frames)
    assert csafe.unframe(frames[-1])[2] == csafe.PM_SET_SCREENSTATE
    assert len(csafe.workout_frames([Interval(60, 0, 100)] * 80)) == csafe.MAX_INTERVALS + 1
    assert csafe.workout_frames([]) == []
    assert all(len(c) <= 20 for c in csafe.chunks(frames[0])) and b"".join(csafe.chunks(frames[0])) == frames[0]


def test_the_answer_says_whether_the_frame_was_taken():
    assert csafe.accepted(csafe.frame(bytes([0x01])))               # previous frame ok
    assert csafe.accepted(csafe.frame(bytes([0x81, 0x1A, 0x00])))    # the toggle bit does not matter
    assert not csafe.accepted(csafe.frame(bytes([0x11])))           # rejected
    assert not csafe.accepted(csafe.frame(bytes([0x21]))) and not csafe.accepted(b"\x01")


# --- the monitor with the fake client -----------------------------------------------------------
def monitor(answer: bytes | None):
    """A fake PM5 whose control service answers every whole frame with ``answer`` (None: silence)."""
    ble = FakeBle()
    base = ble.client_cls

    class Client(base):
        async def write_gatt_char(self, uuid, data, response=None):
            await super().write_gatt_char(uuid, data, response)
            if uuid == CONTROL_TX and bytes(data)[-1] == csafe.STOP and answer is not None:
                self.loop.call_soon(self.handlers[CONTROL_RX], None, bytearray(answer))

    ble.client_cls = Client
    return ble


def test_the_workout_is_written_to_the_control_service_in_small_pieces():
    bus = Bus()
    col = Collector(bus, TOPIC_LOG)
    ble = monitor(csafe.frame(bytes([0x01])))
    erg = Pm5Ergometer(bus, client_cls=ble.client_cls, backoff_s=(0.02,))
    try:
        erg.connect(PM5)
        steps = [Interval(240, 30, 80 + 15 * i) for i in range(3)]
        erg.program_workout(steps)
        ble.wait_for(lambda: erg.programmed is not None)
        assert erg.programmed is True and t("devices.pm5.programmed") in col.get(TOPIC_LOG)
        writes = [(u, d) for u, d, _r in ble.writes]
        assert {u for u, _ in writes} == {CONTROL_TX} and all(len(d) <= 20 for _, d in writes)
        assert b"".join(d for _, d in writes) == b"".join(csafe.workout_frames(steps))
    finally:
        erg.disconnect()


@pytest.mark.parametrize("answer", [None, csafe.frame(bytes([0x11]))], ids=["no answer", "rejected"])
def test_a_monitor_that_does_not_take_the_workout_is_still_read(answer):
    bus = Bus()
    col = Collector(bus, TOPIC_LOG)
    ble = monitor(answer)
    erg = Pm5Ergometer(bus, client_cls=ble.client_cls, backoff_s=(0.02,), csafe_timeout_s=0.2)
    try:
        erg.connect(PM5)
        erg.program_workout([Interval(240, 30, 100), Interval(240, 30, 120)])
        ble.wait_for(lambda: erg.programmed is not None)
        assert erg.programmed is False and t("devices.pm5.program_failed") in col.get(TOPIC_LOG)
        assert len([1 for u, d, _ in ble.writes if d[-1] == csafe.STOP]) == 1      # it stops at the first refusal
        # the reading goes on as before
        ble.client.notify(ADDITIONAL_STATUS_1, bytes(3) + (4000).to_bytes(2, "little") + bytes([36, 255])
                          + (12000).to_bytes(2, "little") * 2 + bytes(5))
        ble.wait_for(lambda: erg.state is not None and erg.state.cadence == 36)
        assert erg.connected and erg.state.power_w == pytest.approx(202.5, abs=0.1)
    finally:
        erg.disconnect()


def test_a_pm5_without_the_control_service_connects_and_says_so():
    bus = Bus()
    col = Collector(bus, TOPIC_LOG)
    ble = FakeBle(missing_notify={CONTROL_RX})
    erg = Pm5Ergometer(bus, client_cls=ble.client_cls, backoff_s=(0.02,), csafe_timeout_s=0.2)
    try:
        erg.connect(PM5)
        erg.program_workout([Interval(60, 0, 100)])
        ble.wait_for(lambda: erg.programmed is not None)
        assert erg.programmed is False and erg.connected and t("devices.pm5.program_failed") in col.get(TOPIC_LOG)
    finally:
        erg.disconnect()
