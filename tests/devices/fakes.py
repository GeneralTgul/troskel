"""Fake bleak client and scanner for the device tests. No Bluetooth needed."""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from lopanalys.devices.ftms import CONTROL_POINT, OP_RESPONSE, RESULT_SUCCESS


class FakeBle:
    """Registry shared by every client a backend creates (one per (re)connect)."""

    def __init__(self, reads: dict[str, bytes] | None = None, results: dict[int, list[int]] | None = None,
                 fail_connects: int = 0, missing_notify: set[str] | None = None,
                 respond: bool = True) -> None:
        self.reads = reads or {}
        self.results = results or {}          # op -> queue of result codes (default success)
        self.fail_connects = fail_connects
        self.missing_notify = missing_notify or set()
        self.respond = respond
        self.clients: list[FakeClient] = []
        self.writes: list[tuple[str, bytes, bool | None]] = []
        self.lock = threading.Lock()
        reg = self

        class _Client(FakeClient):
            registry = reg

        self.client_cls = _Client

    @property
    def client(self) -> "FakeClient":
        return self.clients[-1]

    def cp_writes(self) -> list[bytes]:
        with self.lock:
            return [d for (u, d, _r) in self.writes if u == CONTROL_POINT]

    def wait_for(self, pred: Callable[[], bool], timeout: float = 3.0) -> None:
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            if pred():
                return
            time.sleep(0.01)
        raise AssertionError("condition not met in time")


class FakeClient:
    registry: FakeBle

    def __init__(self, address: str, disconnected_callback: Callable[[Any], None] | None = None,
                 **_kw: Any) -> None:
        self.address = address
        self.disconnected_callback = disconnected_callback
        self.handlers: dict[str, Callable[[Any, bytearray], Any]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.is_connected = False
        self.registry.clients.append(self)

    async def connect(self, **_kw: Any) -> None:
        self.loop = asyncio.get_running_loop()
        if self.registry.fail_connects > 0:
            self.registry.fail_connects -= 1
            raise RuntimeError("Device with address AA was not found")
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def start_notify(self, uuid: str, cb: Callable[[Any, bytearray], Any]) -> None:
        if uuid in self.registry.missing_notify:
            raise RuntimeError(f"Characteristic {uuid} was not found")
        self.handlers[uuid] = cb

    async def read_gatt_char(self, uuid: str, **_kw: Any) -> bytearray:
        if uuid not in self.registry.reads:
            raise RuntimeError(f"Characteristic {uuid} was not found")
        return bytearray(self.registry.reads[uuid])

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool | None = None) -> None:
        data = bytes(data)
        with self.registry.lock:
            self.registry.writes.append((uuid, data, response))
        if uuid == CONTROL_POINT and self.registry.respond and CONTROL_POINT in self.handlers:
            queue = self.registry.results.get(data[0])
            code = queue.pop(0) if queue else RESULT_SUCCESS
            assert self.loop is not None
            self.loop.call_soon(self.handlers[CONTROL_POINT], None, bytearray([OP_RESPONSE, data[0], code]))

    # --- test side ---------------------------------------------------------------------------
    def notify(self, uuid: str, payload: bytes) -> None:
        """Deliver a notification as bleak would, on the client's loop."""
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self.handlers[uuid], None, bytearray(payload))

    def drop(self) -> None:
        """Simulate the device going away."""
        self.is_connected = False
        assert self.loop is not None and self.disconnected_callback is not None
        self.loop.call_soon_threadsafe(self.disconnected_callback, self)


@dataclass
class FakeAdv:
    local_name: str | None
    rssi: int
    service_uuids: list[str] = field(default_factory=list)
    service_data: dict[str, bytes] = field(default_factory=dict)


@dataclass
class FakeDev:
    address: str
    name: str | None


def make_scanner(entries: list[tuple[str, str, int, list[str]]], fail: bool = False) -> type:
    """A BleakScanner stand-in. entries: (address, name, rssi, service uuids)."""

    class _Scanner:
        calls: list[dict[str, Any]] = []

        @classmethod
        async def discover(cls, timeout: float = 5.0, return_adv: bool = False, **kw: Any) -> dict[str, Any]:
            cls.calls.append({"timeout": timeout, "return_adv": return_adv, **kw})
            if fail:
                raise OSError("No Bluetooth adapters found.")
            return {a: (FakeDev(a, n), FakeAdv(n, r, list(u))) for (a, n, r, u) in entries}

    return _Scanner


class Collector:
    """Collects bus messages per topic, thread-safely."""

    def __init__(self, bus: Any, *topics: str) -> None:
        self.items: dict[str, list[Any]] = {tp: [] for tp in topics}
        self._lock = threading.Lock()
        for tp in topics:
            bus.subscribe(tp, lambda p, tp=tp: self._add(tp, p))

    def _add(self, tp: str, p: Any) -> None:
        with self._lock:
            self.items[tp].append(p)

    def get(self, tp: str) -> list[Any]:
        with self._lock:
            return list(self.items[tp])
