"""Bluetooth FTMS treadmill (Fitness Machine Service 0x1826), written from the Bluetooth SIG spec.

What it does:
  - subscribes to Treadmill Data (0x2ACD) and parses every flag field (``parse_treadmill_data``),
  - reads Supported Speed Range (0x2AD4) and Supported Inclination Range (0x2AD5) and clamps every
    target to them,
  - drives the Fitness Machine Control Point (0x2AD9): Request Control, Reset, Set Target Speed,
    Set Target Inclination, Start/Resume, Stop/Pause; each write waits for the 0x80 response
    indication and its result code, and asks for control again if the machine says
    "control not permitted",
  - follows Fitness Machine Status (0x2ADA): started/stopped on the panel, safety key, target
    changes, control lost,
  - sends the target speed after Start/Resume and again when the belt is seen to start (also from
    the panel's START), and repeats it if the belt has not taken it after ``target_settle_s``:
    a belt that stands still may ignore Set Target Speed (stage 1 ran at the panel speed),
  - logs every control point result code at INFO,
  - reconnects with backoff, re-applies the last targets (no speed after ``stop()``), and touches the link every
    ``keepalive_s`` while the belt stands still so the machine does not fall asleep mid-test.

The treadmill ramps speed and incline itself; this module never ramps.

Two facts about the Gymkompaniet Recoil the program is tried on (1-18 km/h, 0-12 %):
  - The belt must be started with START on the treadmill. ``start()`` sends Start/Resume anyway,
    but the machine may ignore it; the app tells the operator to press START.
  - It accepts **one** BLE connection at a time. If QZ is running on the gym PC it holds the
    treadmill, and FTMS cannot connect: close QZ first. This module never closes QZ.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import threading
import time
from dataclasses import dataclass, replace
from typing import Any

from lopanalys.core.bus import TOPIC_TREADMILL, Bus
from lopanalys.core.i18n import t
from lopanalys.devices._runtime import Clock, SupervisedConnection, maybe_await, uuid16
from lopanalys.devices._scan import scan_sync
from lopanalys.devices.base import DeviceInfo, TreadmillState

log = logging.getLogger(__name__)

FTMS_SERVICE = uuid16(0x1826)
TREADMILL_DATA = uuid16(0x2ACD)
SUPPORTED_SPEED_RANGE = uuid16(0x2AD4)
SUPPORTED_INCLINATION_RANGE = uuid16(0x2AD5)
CONTROL_POINT = uuid16(0x2AD9)
MACHINE_STATUS = uuid16(0x2ADA)

# Control point op codes
OP_REQUEST_CONTROL = 0x00
OP_RESET = 0x01
OP_SET_TARGET_SPEED = 0x02
OP_SET_TARGET_INCLINATION = 0x03
OP_START_RESUME = 0x07
OP_STOP_PAUSE = 0x08
OP_RESPONSE = 0x80
STOP = 0x01
PAUSE = 0x02

# Control point result codes
RESULT_SUCCESS = 0x01
RESULT_NOT_SUPPORTED = 0x02
RESULT_INVALID_PARAMETER = 0x03
RESULT_FAILED = 0x04
RESULT_CONTROL_NOT_PERMITTED = 0x05
_RESULT_NAMES = {RESULT_SUCCESS: "success", RESULT_NOT_SUPPORTED: "not supported",
                 RESULT_INVALID_PARAMETER: "invalid parameter", RESULT_FAILED: "failed",
                 RESULT_CONTROL_NOT_PERMITTED: "control not permitted"}

# Fitness Machine Status op codes
ST_RESET = 0x01
ST_STOPPED_OR_PAUSED_BY_USER = 0x02
ST_STOPPED_BY_SAFETY_KEY = 0x03
ST_STARTED_OR_RESUMED_BY_USER = 0x04
ST_TARGET_SPEED_CHANGED = 0x05
ST_TARGET_INCLINE_CHANGED = 0x06
ST_CONTROL_PERMISSION_LOST = 0xFF

INCLINE_NOT_AVAILABLE = 0x7FFF

# Defaults when the machine does not expose its ranges (Recoil values).
DEFAULT_MIN_SPEED = 1.0
DEFAULT_MAX_SPEED = 18.0
DEFAULT_MIN_INCLINE = 0.0
DEFAULT_MAX_INCLINE = 12.0


# --------------------------------------------------------------------------------------------
# Pure protocol functions (no Bluetooth): parse and build payloads per the FTMS spec.
# --------------------------------------------------------------------------------------------
@dataclass
class TreadmillData:
    """One Treadmill Data notification. Fields not present in the packet are None."""
    flags: int = 0
    speed_kmh: float | None = None
    avg_speed_kmh: float | None = None
    total_distance_m: int | None = None
    incline_pct: float | None = None
    ramp_angle_deg: float | None = None
    elevation_gain_pos_m: float | None = None
    elevation_gain_neg_m: float | None = None
    pace_km_min: float | None = None
    avg_pace_km_min: float | None = None
    energy_total_kcal: int | None = None
    energy_per_hour_kcal: int | None = None
    energy_per_min_kcal: int | None = None
    heart_rate_bpm: int | None = None
    met: float | None = None
    elapsed_s: int | None = None
    remaining_s: int | None = None
    force_on_belt_n: int | None = None
    power_w: int | None = None
    truncated: bool = False


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = bytes(data)
        self.pos = 0

    def take(self, fmt: str) -> int:
        size = struct.calcsize(fmt)
        if self.pos + size > len(self.data):
            raise IndexError
        (v,) = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return int(v)

    def uint24(self) -> int:
        if self.pos + 3 > len(self.data):
            raise IndexError
        b = self.data[self.pos:self.pos + 3]
        self.pos += 3
        return b[0] | (b[1] << 8) | (b[2] << 16)


def parse_treadmill_data(data: bytes | bytearray) -> TreadmillData:
    """Parse a Treadmill Data (0x2ACD) payload. A truncated packet keeps what was readable."""
    r = _Reader(data)
    out = TreadmillData()
    try:
        flags = r.take("<H")
        out.flags = flags
        if not flags & 0x0001:            # bit 0 "More Data" = 0 -> Instantaneous Speed present
            out.speed_kmh = r.take("<H") / 100.0
        if flags & 0x0002:
            out.avg_speed_kmh = r.take("<H") / 100.0
        if flags & 0x0004:
            out.total_distance_m = r.uint24()
        if flags & 0x0008:
            inc = r.take("<h")
            ramp = r.take("<h")
            out.incline_pct = None if inc == INCLINE_NOT_AVAILABLE else inc / 10.0
            out.ramp_angle_deg = None if ramp == INCLINE_NOT_AVAILABLE else ramp / 10.0
        if flags & 0x0010:
            out.elevation_gain_pos_m = r.take("<H") / 10.0
            out.elevation_gain_neg_m = r.take("<H") / 10.0
        if flags & 0x0020:
            out.pace_km_min = r.take("<B") / 10.0
        if flags & 0x0040:
            out.avg_pace_km_min = r.take("<B") / 10.0
        if flags & 0x0080:
            out.energy_total_kcal = r.take("<H")
            out.energy_per_hour_kcal = r.take("<H")
            out.energy_per_min_kcal = r.take("<B")
        if flags & 0x0100:
            out.heart_rate_bpm = r.take("<B")
        if flags & 0x0200:
            out.met = r.take("<B") / 10.0
        if flags & 0x0400:
            out.elapsed_s = r.take("<H")
        if flags & 0x0800:
            out.remaining_s = r.take("<H")
        if flags & 0x1000:
            out.force_on_belt_n = r.take("<h")
            out.power_w = r.take("<h")
    except IndexError:
        out.truncated = True
    return out


@dataclass
class Range:
    minimum: float
    maximum: float
    increment: float


def parse_speed_range(data: bytes | bytearray) -> Range:
    """Supported Speed Range (0x2AD4): uint16 min, max, increment in 0.01 km/h."""
    lo, hi, inc = struct.unpack_from("<HHH", bytes(data))
    return Range(lo / 100.0, hi / 100.0, inc / 100.0)


def parse_inclination_range(data: bytes | bytearray) -> Range:
    """Supported Inclination Range (0x2AD5): sint16 min, sint16 max, uint16 increment in 0.1 %."""
    lo, hi, inc = struct.unpack_from("<hhH", bytes(data))
    return Range(lo / 10.0, hi / 10.0, inc / 10.0)


def cmd_request_control() -> bytes:
    return bytes([OP_REQUEST_CONTROL])


def cmd_reset() -> bytes:
    return bytes([OP_RESET])


def cmd_set_target_speed(kmh: float) -> bytes:
    """Set Target Speed: uint16, 0.01 km/h."""
    return struct.pack("<BH", OP_SET_TARGET_SPEED, max(0, min(0xFFFF, round(kmh * 100))))


def cmd_set_target_inclination(pct: float) -> bytes:
    """Set Target Inclination: sint16, 0.1 %."""
    return struct.pack("<Bh", OP_SET_TARGET_INCLINATION, max(-0x8000, min(0x7FFF, round(pct * 10))))


def cmd_start() -> bytes:
    return bytes([OP_START_RESUME])


def cmd_stop(pause: bool = False) -> bytes:
    return bytes([OP_STOP_PAUSE, PAUSE if pause else STOP])


def parse_control_response(data: bytes | bytearray) -> tuple[int, int] | None:
    """(request op code, result code) from a 0x80 response indication, else None."""
    b = bytes(data)
    if len(b) >= 3 and b[0] == OP_RESPONSE:
        return b[1], b[2]
    return None


def _snap(value: float, rng: Range) -> float:
    v = min(max(value, rng.minimum), rng.maximum)
    if rng.increment > 0:
        v = rng.minimum + round((v - rng.minimum) / rng.increment) * rng.increment
        v = min(max(v, rng.minimum), rng.maximum)
    return round(v, 2)


# --------------------------------------------------------------------------------------------
# The device
# --------------------------------------------------------------------------------------------
def _default_client_cls() -> Any:
    from bleak import BleakClient
    return BleakClient


@dataclass
class _Targets:
    speed_kmh: float | None = None
    incline_pct: float | None = None
    running_wanted: bool = False
    stopped: bool = False     # the program stopped the belt: no speed goes out until a new target or start


def require_ftms(client: Any) -> None:
    """Raise when the connected device has no FTMS service (a device found by its name or address, not
    by the service): the message says the device cannot be controlled, instead of a later timeout."""
    services = getattr(client, "services", None)
    get = getattr(services, "get_service", None)
    if get is None:
        return
    try:
        found = get(FTMS_SERVICE)
    except Exception:  # noqa: BLE001 - a backend that cannot list services: the characteristics decide
        return
    if found is None:
        raise RuntimeError("FTMS service 0x1826 missing after connecting")


class FtmsTreadmill(SupervisedConnection):
    """A treadmill over Bluetooth FTMS. Thread-safe sync API (``devices.base.Treadmill``).

    ``client_cls`` and ``scanner_cls`` default to bleak's and are injected in tests.
    """

    controllable = True
    device_kind = "treadmill"

    def __init__(self, bus: Bus | None = None, *, client_cls: Any = None, scanner_cls: Any = None,
                 clock: Clock = time.time, keepalive_s: float = 45.0, command_timeout_s: float = 4.0,
                 target_settle_s: float = 2.5, target_retries: int = 2, **kwargs: Any) -> None:
        super().__init__(bus, name=t("devices.ftms.default_name"), clock=clock, **kwargs)
        self._client_cls = client_cls
        self._scanner_cls = scanner_cls
        self._keepalive_s = keepalive_s
        self._command_timeout_s = command_timeout_s
        self._target_settle_s = target_settle_s   # time the belt gets to reach the target after it starts
        self._target_retries = target_retries
        self._client: Any = None
        self._device: DeviceInfo | None = None
        self._lock = threading.Lock()
        self._state: TreadmillState | None = None
        self._targets = _Targets()
        self._speed_range = Range(DEFAULT_MIN_SPEED, DEFAULT_MAX_SPEED, 0.1)
        self._incline_range = Range(DEFAULT_MIN_INCLINE, DEFAULT_MAX_INCLINE, 0.5)
        self._lost: asyncio.Event | None = None
        self._cmd_lock: asyncio.Lock | None = None
        self._waiters: dict[int, asyncio.Future[int]] = {}
        self._has_control = False
        self._keepalive_task: asyncio.Task[None] | None = None

    # --- Treadmill protocol ------------------------------------------------------------------
    @property
    def min_speed_kmh(self) -> float:
        return self._speed_range.minimum

    @property
    def max_speed_kmh(self) -> float:
        return self._speed_range.maximum

    @property
    def min_incline_pct(self) -> float:
        return self._incline_range.minimum

    @property
    def max_incline_pct(self) -> float:
        return self._incline_range.maximum

    @property
    def state(self) -> TreadmillState | None:
        with self._lock:
            return self._state

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        """Find FTMS treadmills nearby (they advertise service 0x1826)."""
        return scan_sync([FTMS_SERVICE], timeout_s, self._scanner_cls)

    def connect(self, device: DeviceInfo) -> None:
        """Connect and take control. Blocks until connected; raises DeviceError with a coach message."""
        self._device = device
        self._name = device.name or device.address
        self._start_session()

    def clamp_speed(self, kmh: float) -> float:
        """Clamp to the machine's speed range; 0 means stop and is kept as 0."""
        return 0.0 if kmh <= 0 else _snap(kmh, self._speed_range)

    def clamp_incline(self, pct: float) -> float:
        return _snap(pct, self._incline_range)

    def set_target(self, speed_kmh: float, incline_pct: float) -> None:
        """Send target speed and incline (clamped). Does not block; applied again after a reconnect."""
        speed = self.clamp_speed(speed_kmh)
        incline = self.clamp_incline(incline_pct)
        with self._lock:
            self._targets.speed_kmh = speed
            self._targets.incline_pct = incline
            self._targets.stopped = False
            if self._state is not None:
                self._state = replace(self._state, target_speed_kmh=speed, target_incline_pct=incline)
        self._schedule(self._apply_targets(), "set_target")

    def start(self) -> None:
        """Ask the belt to start. Many treadmills (the Recoil too) ignore this: press START on the panel."""
        with self._lock:
            self._targets.running_wanted = True
            self._targets.stopped = False
        self._schedule(self._start_async(), "start")

    def stop(self) -> None:
        """Stop the belt (FTMS Stop)."""
        with self._lock:
            self._targets.running_wanted = False
            self._targets.stopped = True
        self._schedule(self._command(cmd_stop(), "stop"), "stop")

    def pause(self) -> None:
        """Pause the belt (FTMS Pause); ``start()`` resumes."""
        with self._lock:
            self._targets.stopped = True
        self._schedule(self._command(cmd_stop(pause=True), "pause"), "pause")

    # --- connection ------------------------------------------------------------------------
    def _describe_error(self, exc: BaseException) -> str:
        text = str(exc).lower()
        if "characteristic" in text or "service" in text:
            return t("devices.ftms.not_ftms", name=self._name)
        if "not found" in text or type(exc).__name__ == "BleakDeviceNotFoundError":
            return t("devices.error.not_found", name=self._name) + " " + t("devices.ftms.close_qz")
        return super()._describe_error(exc) + " " + t("devices.ftms.close_qz")

    async def _open(self) -> None:
        assert self._device is not None
        self._lost = asyncio.Event()
        self._cmd_lock = asyncio.Lock()
        self._waiters.clear()
        self._has_control = False
        loop = asyncio.get_running_loop()
        lost = self._lost

        def on_disconnect(_client: Any) -> None:
            loop.call_soon_threadsafe(lost.set)

        cls = self._client_cls or _default_client_cls()
        self._client = cls(self._device.address, disconnected_callback=on_disconnect)
        await self._client.connect()
        require_ftms(self._client)
        await self._read_ranges()
        await self._client.start_notify(TREADMILL_DATA, self._on_treadmill_data)
        try:
            await self._client.start_notify(MACHINE_STATUS, self._on_status)
        except Exception:  # noqa: BLE001 - optional characteristic
            log.info("%s: no Fitness Machine Status", self._log_id)
        await self._client.start_notify(CONTROL_POINT, self._on_control_point)
        await self._request_control()
        await self._apply_targets()
        if self._keepalive_s > 0:
            self._keepalive_task = asyncio.create_task(self._keepalive())

    async def _wait_lost(self) -> None:
        assert self._lost is not None
        await self._lost.wait()
        self._log(t("devices.status.lost", name=self._name))

    async def _close(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        for fut in self._waiters.values():
            if not fut.done():
                fut.cancel()
        self._waiters.clear()
        client, self._client = self._client, None
        if client is not None:
            await maybe_await(client.disconnect())

    async def _read_ranges(self) -> None:
        try:
            self._speed_range = parse_speed_range(await self._client.read_gatt_char(SUPPORTED_SPEED_RANGE))
        except Exception:  # noqa: BLE001 - optional; keep defaults
            log.info("%s: no Supported Speed Range, using defaults", self._log_id)
        try:
            self._incline_range = parse_inclination_range(
                await self._client.read_gatt_char(SUPPORTED_INCLINATION_RANGE))
        except Exception:  # noqa: BLE001
            log.info("%s: no Supported Inclination Range, using defaults", self._log_id)

    async def _keepalive(self) -> None:
        """While the belt stands still, read a characteristic now and then to keep the link busy."""
        while True:
            await asyncio.sleep(self._keepalive_s)
            st = self.state
            if st is not None and st.running:
                continue
            try:
                if self._client is not None:
                    await self._client.read_gatt_char(SUPPORTED_SPEED_RANGE)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.debug("keep-alive read failed", exc_info=True)

    # --- control point -----------------------------------------------------------------------
    async def _request_control(self) -> bool:
        res = await self._command(cmd_request_control(), "request_control", retry_control=False)
        self._has_control = res == RESULT_SUCCESS
        return self._has_control

    async def _start_async(self) -> None:
        if not self._has_control:
            await self._request_control()
        await self._command(cmd_start(), "start")
        # The target after Start: many belts (the Recoil too) take no Set Target Speed while they
        # stand still, and then ran stage 1 at their own start speed (real hardware 2026-11-14).
        await self._apply_targets()
        st = self.state
        if st is None or not st.running:
            self._log(t("devices.ftms.press_start"))

    async def _belt_started(self) -> None:
        """The belt went from standing to running: send the stage target now, and again if the
        belt has not taken it after a moment (the panel's START runs the belt at its own speed)."""
        log.info("%s: belt started, sending the target", self._log_id)
        for attempt in range(1 + self._target_retries):
            await self._apply_targets()
            await asyncio.sleep(self._target_settle_s)
            st = self.state
            with self._lock:
                want, wanted = self._targets.speed_kmh, self._targets.running_wanted
            if st is None or not st.running or not wanted or want is None or st.speed_kmh is None:
                return
            if abs(st.speed_kmh - want) <= 0.3:
                return
            log.info("%s: belt at %.1f km/h, target %.1f km/h: sending again (%d)", self._log_id,
                     st.speed_kmh, want, attempt + 1)

    async def _apply_targets(self) -> None:
        if self._client is None:
            return
        with self._lock:
            speed, incline = self._targets.speed_kmh, self._targets.incline_pct
            stopped = self._targets.stopped
        # Not to a belt the program stopped (a reconnect during a lactate pause): the athlete stands on it.
        if speed is not None and speed > 0 and not stopped:
            await self._command(cmd_set_target_speed(speed), "speed")
        if incline is not None:
            await self._command(cmd_set_target_inclination(incline), "incline")

    async def _command(self, payload: bytes, what: str, retry_control: bool = True) -> int | None:
        """Write to the control point and wait for its response indication. Returns the result code."""
        if self._client is None or self._cmd_lock is None:
            return None
        op = payload[0]
        async with self._cmd_lock:
            result = await self._write_and_wait(op, payload)
        if result == RESULT_CONTROL_NOT_PERMITTED and retry_control and op != OP_REQUEST_CONTROL:
            if await self._request_control():
                async with self._cmd_lock:
                    result = await self._write_and_wait(op, payload)
            if result == RESULT_CONTROL_NOT_PERMITTED:
                self._log(t("devices.ftms.control_denied"))
        log.info("%s: control point %s -> %s", self._log_id, what,
                 "no response" if result is None else f"0x{result:02x} ({_RESULT_NAMES.get(result, '?')})")
        if result not in (RESULT_SUCCESS, None):
            if op != OP_START_RESUME and result != RESULT_CONTROL_NOT_PERMITTED:
                self._log(t("devices.ftms.command_failed", what=t(f"devices.ftms.cmd.{what}")))
        return result

    async def _write_and_wait(self, op: int, payload: bytes) -> int | None:
        fut: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._waiters[op] = fut
        try:
            await self._client.write_gatt_char(CONTROL_POINT, payload, response=True)
            return await asyncio.wait_for(fut, self._command_timeout_s)
        except asyncio.TimeoutError:
            log.info("%s: no response to op 0x%02x", self._log_id, op)
            return None
        finally:
            if self._waiters.get(op) is fut:
                del self._waiters[op]

    # --- notifications (run on the loop thread) ---------------------------------------------
    def _on_control_point(self, _char: Any, data: bytearray) -> None:
        parsed = parse_control_response(data)
        if parsed is None:
            return
        op, result = parsed
        fut = self._waiters.get(op)
        if fut is not None and not fut.done():
            fut.set_result(result)

    def _on_status(self, _char: Any, data: bytearray) -> None:
        b = bytes(data)
        if not b:
            return
        op = b[0]
        running: bool | None = None
        if op == ST_STARTED_OR_RESUMED_BY_USER:
            running = True
        elif op == ST_STOPPED_OR_PAUSED_BY_USER:
            running = False
            self._log(t("devices.ftms.stopped_by_user"))
        elif op == ST_STOPPED_BY_SAFETY_KEY:
            running = False
            self._log(t("devices.ftms.safety_key"))
        elif op == ST_CONTROL_PERMISSION_LOST:
            self._has_control = False
        elif op == ST_TARGET_SPEED_CHANGED and len(b) >= 3:
            self._update(target_speed_kmh=struct.unpack_from("<H", b, 1)[0] / 100.0)
        elif op == ST_TARGET_INCLINE_CHANGED and len(b) >= 3:
            self._update(target_incline_pct=struct.unpack_from("<h", b, 1)[0] / 10.0)
        elif op == ST_RESET:
            self._has_control = False
        if running is not None:
            self._update(running=running)

    def _on_treadmill_data(self, _char: Any, data: bytearray) -> None:
        d = parse_treadmill_data(data)
        fields: dict[str, Any] = {}
        if d.speed_kmh is not None:
            fields["speed_kmh"] = d.speed_kmh
            fields["running"] = d.speed_kmh > 0.05
        if d.incline_pct is not None:
            fields["incline_pct"] = d.incline_pct
        if d.total_distance_m is not None:
            fields["distance_m"] = float(d.total_distance_m)
        if fields:
            self._update(**fields)

    def _update(self, **fields: Any) -> None:
        with self._lock:
            base = self._state or TreadmillState(t=0.0, speed_kmh=None, incline_pct=None)
            fields.setdefault("target_speed_kmh", self._targets.speed_kmh)
            fields.setdefault("target_incline_pct", self._targets.incline_pct)
            st = replace(base, t=self._clock(), **fields)
            self._state = st
            started = st.running and not base.running and self._targets.running_wanted
        if started:
            self._schedule(self._belt_started(), "belt started")
        if self._bus is not None:
            self._bus.publish(TOPIC_TREADMILL, st)

