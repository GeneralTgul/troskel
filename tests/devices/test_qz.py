"""QzTreadmill against a fake QZ: a real websocket server speaking QZ's JSON messages."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest
from websockets.asyncio.server import serve

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, TOPIC_HEART_RATE, TOPIC_LOG, TOPIC_TREADMILL, Bus
from lopanalys.core.i18n import t
from lopanalys.devices.errors import DeviceError
from lopanalys.devices.qz import QzHeartRate, QzTreadmill, qz_running


class FakeQz:
    """Speaks like QZ's web server: a 'workout' broadcast and R_* replies, to every client."""

    def __init__(self, device: bool = True) -> None:
        self.device = device
        self.received: list[dict[str, Any]] = []
        self.speed = 0.0
        self.incline = 0.0
        self.paused = False
        self.heart = 141
        self.reply_delay_s = 0.0
        self.port = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[Any] = set()
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)

    def start(self) -> "FakeQz":
        self._thread.start()
        assert self._ready.wait(5)
        return self

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(5)

    def kick_clients(self) -> None:
        async def close_all() -> None:
            for ws in list(self._clients):
                await ws.close()
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(close_all(), self._loop).result(5)

    def workout(self) -> dict[str, Any]:
        if not self.device:
            return {"msg": "workout", "content": {"deviceConnected": False}}
        return {"msg": "workout", "content": {
            "deviceConnected": True, "devicePaused": self.paused, "deviceType": 1,
            "deviceName": "SW1000EAI-0001", "speed": self.speed, "inclination": self.incline,
            "target_speed": self.speed, "target_inclination": self.incline, "heart": self.heart,
            "distance": 1.25, "elapsed_s": 3, "elapsed_m": 0, "elapsed_h": 0, "training_row_index": -1}}

    async def _handler(self, ws: Any) -> None:
        self._clients.add(ws)
        try:
            async for raw in ws:
                msg = json.loads(raw)
                self.received.append(msg)
                name, content = msg["msg"], msg.get("content") or {}
                reply: dict[str, Any] = {"msg": "R_" + name}
                if name == "setspeed":
                    value = content.get("value") if self.device else None
                    if value is not None:
                        self.speed = float(value)
                    reply["content"] = {"value": value}
                elif name == "setresistance":
                    self.incline = float(content["value"])
                    reply["content"] = {"value": self.incline}
                elif name == "pause":
                    self.paused = True
                elif name == "start":
                    self.paused = False
                if self.reply_delay_s:
                    await asyncio.sleep(self.reply_delay_s)
                # QZ sends replies with QJsonDocument::toJson(): indented JSON, to every client
                for c in list(self._clients):
                    await c.send(json.dumps(reply, indent=4))
        finally:
            self._clients.discard(ws)

    async def _broadcast(self) -> None:
        while True:
            for c in list(self._clients):
                try:
                    await c.send(json.dumps(self.workout()))
                except Exception:
                    pass
            await asyncio.sleep(0.05)

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with serve(self._handler, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            task = asyncio.create_task(self._broadcast())
            self._ready.set()
            await self._stop.wait()
            task.cancel()


def wait_for(pred, timeout=3.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


@pytest.fixture
def qz():
    server = FakeQz().start()
    yield server
    server.stop()


def test_sets_speed_and_incline_and_reads_live_data(qz):
    bus = Bus()
    seen: list[Any] = []
    bus.subscribe(TOPIC_TREADMILL, seen.append)
    tm = QzTreadmill(bus, port=qz.port)
    try:
        tm.connect()
        tm.set_target(12.0, 2.0)
        wait_for(lambda: tm.state is not None and tm.state.speed_kmh == 12.0 and tm.state.incline_pct == 2.0)
        sent = [m for m in qz.received if m["msg"] in ("setspeed", "setresistance")]
        assert sent[:2] == [{"msg": "setspeed", "content": {"value": 12.0}},
                            {"msg": "setresistance", "content": {"value": 2.0}}]
        st = tm.state
        assert st.running is True and st.distance_m == 1250.0 and st.measured
        assert st.target_speed_kmh == 12.0
        assert seen and seen[-1].speed_kmh == 12.0
    finally:
        tm.disconnect()


def test_clamps_and_stop_pauses(qz):
    tm = QzTreadmill(Bus(), port=qz.port, max_speed_kmh=18.0, max_incline_pct=12.0)
    try:
        tm.connect()
        tm.set_target(30.0, 20.0)
        wait_for(lambda: qz.speed == 18.0 and qz.incline == 12.0)
        tm.start()
        tm.stop()
        wait_for(lambda: any(m["msg"] == "pause" for m in qz.received))
        assert not any(m["msg"] == "stop" for m in qz.received)  # never ends QZ's session
        assert [m["msg"] for m in qz.received if m["msg"] in ("start", "pause")] == ["start", "pause"]
        wait_for(lambda: tm.state is not None and tm.state.running is False)
    finally:
        tm.disconnect()


def test_heart_rate_from_qz(qz):
    bus = Bus()
    hrs: list[Any] = []
    bus.subscribe(TOPIC_HEART_RATE, hrs.append)
    tm = QzTreadmill(bus, port=qz.port)
    hr = QzHeartRate(tm, bus)
    with pytest.raises(DeviceError):
        hr.connect()             # QZ not connected yet
    try:
        tm.connect()
        hr.connect()
        wait_for(lambda: hr.last is not None)
        assert hr.last.bpm == 141 and hrs
    finally:
        hr.disconnect()
        tm.disconnect()


def test_qz_without_treadmill_says_so():
    server = FakeQz(device=False).start()
    bus = Bus()
    logs: list[str] = []
    statuses: list[Any] = []
    bus.subscribe(TOPIC_LOG, logs.append)
    bus.subscribe(TOPIC_DEVICE_STATUS, statuses.append)
    tm = QzTreadmill(bus, port=server.port)
    try:
        tm.connect()
        tm.set_target(10.0, 1.0)
        wait_for(lambda: t("devices.qz.rejected") in logs)
        wait_for(lambda: any(s.message == t("devices.qz.no_treadmill") for s in statuses))
        assert tm.state is None
    finally:
        tm.disconnect()
        server.stop()


def test_not_running_gives_clear_error():
    server = FakeQz().start()
    port = server.port
    server.stop()
    assert not qz_running(port=port)
    tm = QzTreadmill(Bus(), port=port, connect_timeout_s=8)
    with pytest.raises(DeviceError) as exc:
        tm.connect()
    assert str(exc.value) == t("devices.qz.not_running")


def test_no_port_on_linux():
    tm = QzTreadmill(Bus())  # no port and no registry here
    with pytest.raises(DeviceError) as exc:
        tm.connect()
    assert str(exc.value) == t("devices.qz.no_port")


def test_reconnects_when_qz_drops_the_socket(qz):
    tm = QzTreadmill(Bus(), port=qz.port, backoff_s=(0.05,))
    try:
        tm.connect()
        tm.set_target(9.0, 0.0)
        wait_for(lambda: qz.speed == 9.0)
        assert qz_running(port=qz.port)
        qz.kick_clients()
        qz.speed = 0.0
        # after reconnecting the targets are sent again
        wait_for(lambda: qz.speed == 9.0, timeout=5)
        wait_for(lambda: tm.connected, timeout=5)  # the flag is set just after the targets are resent
    finally:
        tm.disconnect()


def test_two_targets_in_a_row_end_at_the_last_one(qz):
    """A second target while QZ has not answered the first: the first one's incline was sent last
    (its wait for the reply was taken over by the second), so the belt ended at a stale incline."""
    tm = QzTreadmill(Bus(), port=qz.port, reply_timeout_s=0.4)
    try:
        tm.connect()
        qz.reply_delay_s = 0.1
        tm.set_target(10.0, 1.0)
        tm.set_target(12.0, 2.0)
        wait_for(lambda: len([m for m in qz.received if m["msg"] == "setresistance"]) >= 2, timeout=5)
        time.sleep(0.3)
        sent = [(m["msg"], m["content"]["value"]) for m in qz.received if m["msg"].startswith("set")]
        assert sent[-2:] == [("setspeed", 12.0), ("setresistance", 2.0)]
        assert (qz.speed, qz.incline) == (12.0, 2.0)
    finally:
        tm.disconnect()
