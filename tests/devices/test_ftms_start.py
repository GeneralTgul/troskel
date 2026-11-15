"""Stage 1 runs at stage 1's speed (real hardware 2026-11-14): the belt ignored Set Target Speed
while it stood still, so the first target has to go after Start, and again when the belt starts."""
from __future__ import annotations

import logging
import struct
import time

import pytest

from lopanalys.assessments.protocol import Phase, Protocol, Stage
from lopanalys.assessments.runner import TestRunner
from lopanalys.core.bus import Bus
from lopanalys.devices.ftms import (CONTROL_POINT, OP_RESPONSE, RESULT_SUCCESS, SUPPORTED_INCLINATION_RANGE,
                                    SUPPORTED_SPEED_RANGE, TREADMILL_DATA, FtmsTreadmill)
from lopanalys.devices.base import DeviceInfo

from .fakes import FakeBle, FakeClient

RECOIL = DeviceInfo("AA:BB:CC:DD:EE:FF", "SW1000EAI-0001", -60)
RANGES = {SUPPORTED_SPEED_RANGE: struct.pack("<HHH", 100, 1800, 10),
          SUPPORTED_INCLINATION_RANGE: struct.pack("<hhH", 0, 120, 5)}


class BeltBle(FakeBle):
    """A belt like the Recoil: Set Target Speed while standing still is answered but ignored, the
    belt starts at ``start_kmh`` (Start, or ``panel_start()``), and only a running belt follows."""

    def __init__(self, start_kmh: float = 1.0, honour_start: bool = True) -> None:
        super().__init__(reads=dict(RANGES))
        self.speed = 0.0
        self.start_kmh = start_kmh
        self.honour_start = honour_start
        belt = self

        class _Client(FakeClient):
            registry = belt

            async def write_gatt_char(self, uuid, data, response=None):
                data = bytes(data)
                with belt.lock:
                    belt.writes.append((uuid, data, response))
                if uuid != CONTROL_POINT:
                    return
                if data[0] == 0x07 and belt.honour_start:
                    belt.loop_call(self, belt.start_kmh)
                elif data[0] == 0x02 and belt.speed > 0:
                    belt.loop_call(self, struct.unpack_from("<H", data, 1)[0] / 100.0)
                elif data[0] == 0x08:
                    belt.loop_call(self, 0.0)
                self.loop.call_soon(self.handlers[CONTROL_POINT], None, bytearray([OP_RESPONSE, data[0], RESULT_SUCCESS]))

        self.client_cls = _Client

    def loop_call(self, client, kmh: float) -> None:
        self.speed = kmh
        client.loop.call_soon(client.handlers[TREADMILL_DATA], None, bytearray(struct.pack("<HH", 0, round(kmh * 100))))

    def panel_start(self) -> None:
        self.speed = self.start_kmh
        self.client.notify(TREADMILL_DATA, struct.pack("<HH", 0, round(self.start_kmh * 100)))


def _wait(pred, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def belt_tm():
    made = []

    def make(**kw):
        ble = BeltBle(**kw)
        tm = FtmsTreadmill(Bus(), client_cls=ble.client_cls, keepalive_s=0, command_timeout_s=1.0,
                           target_settle_s=0.2)
        tm.connect(RECOIL)
        made.append(tm)
        return ble, tm
    yield make
    for tm in made:
        tm.disconnect()


def test_start_then_stage_target_like_the_runner(belt_tm, caplog):
    ble, tm = belt_tm()
    with caplog.at_level(logging.INFO, logger="lopanalys.devices"):
        tm.start()                     # the runner: start(), then set_target() for stage 1
        tm.set_target(10.0, 1.0)
        assert _wait(lambda: ble.speed == pytest.approx(10.0)), f"belt at {ble.speed} km/h"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "control point start -> 0x01 (success)" in text
    assert "control point speed -> 0x01 (success)" in text


def test_panel_start_gets_the_target(belt_tm):
    ble, tm = belt_tm(start_kmh=3.0, honour_start=False)   # START must be pressed on the belt
    tm.start()
    tm.set_target(11.0, 0.0)
    time.sleep(0.3)
    assert ble.speed == 0.0
    ble.panel_start()                                       # the runner presses START: belt at 3 km/h
    assert _wait(lambda: ble.speed == pytest.approx(11.0)), f"belt at {ble.speed} km/h"


def test_runner_stage_one_runs_at_its_speed(belt_tm):
    ble, tm = belt_tm()
    proto = Protocol(name="t", kind="lactate", stages=[Stage(speed_kmh=9.0, incline_pct=1.0, duration_s=30.0),
                                       Stage(speed_kmh=10.0, incline_pct=1.0, duration_s=30.0)])
    r = TestRunner(proto, tm, None, bus=Bus(), threaded=True, countdown_s=0.2)
    try:
        r.start()
        assert _wait(lambda: r.state is not None and r.state.phase == Phase.RUNNING)
        assert _wait(lambda: ble.speed == pytest.approx(9.0)), f"stage 1: belt at {ble.speed} km/h"
    finally:
        r.stop()
