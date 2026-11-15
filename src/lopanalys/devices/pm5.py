"""Concept2 PM5 over Bluetooth: watt, stroke rate, pace per 500 m and the strokes are read, and the
test can be put on the monitor as a workout.

A PM5 (the monitor on a SkiErg, RowErg or BikeErg) has Concept2's own services on the base
``CE06xxxx-43E5-11E4-916C-0800200C9A66``: information 0x0010, control 0x0020 and rowing 0x0030, the
last one also on a SkiErg. This module subscribes to five characteristics of the rowing service:

  ====== ======================== =====================================================
  0x0031 General Status           elapsed time, distance, workout and stroke state, drag factor
  0x0032 Additional Status 1      speed, stroke rate, heart rate, current and average pace
  0x0033 Additional Status 2      average power, calories, split values
  0x0035 Stroke Data              one packet per stroke: drive length and time, recovery time, forces
  0x0036 Additional Stroke Data   the power of the stroke
  ====== ======================== =====================================================

**A PM5 cannot be set.** The resistance is air: the athlete decides the power with how hard the
pull is. ``controllable`` is False and ``measured`` is True, so the test runner shows the target
on the TV ("Håll 150 W · 2:13 /500 m"), lets the stage clock run and takes the stage's load from
the measured watts. ``set_target`` only remembers the target for the state.

**The workout can be programmed** (a phone app can connect to the monitor and start a workout,
and so can the program). ``program_workout(intervals)`` writes CSAFE frames (``devices/csafe.py``) to the
control service, 0x0021, and reads the answers on 0x0022: one interval per stage with its length,
rest and target pace, so that the monitor shows the stage and the target in front of the athlete.
It does not block and never stops a test: when the monitor does not take a frame, a message goes
out on the log topic and the test goes on as before, with reading only (the athlete then chooses
*Just Ski*). The CSAFE commands are largely from memory and unproven, see ``csafe.py``.

The power is the stroke power (0x0036) while strokes arrive; without that packet it is computed
from the pace with Concept2's formula, W = 2.80 / (s/m)^3. When no stroke has come for
``IDLE_S`` seconds, or the monitor says the stroke state is idle, the power is 0.

Belagt och inte: the UUIDs are checked against ``pm5-ble.js`` in
ergarcade/pm5-base. The **field order and sizes** below are written from memory of Concept2's
*PM5 Bluetooth Smart Interface Definition* and are not checked against it, and nothing here has
met a real PM5. A packet shorter than expected keeps what could be read (a notification of 1 byte
instead of 19 has been reported). See docs/PROV.md. The force curve (0x003D) is not read.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Any

from lopanalys.core.bus import TOPIC_ERGOMETER, TOPIC_STROKE, Bus
from lopanalys.core.i18n import t
from lopanalys.devices._runtime import Clock, SupervisedConnection, maybe_await
from lopanalys.devices import csafe
from lopanalys.devices._scan import scan_sync
from lopanalys.devices.base import DeviceInfo, ErgometerState, StrokeSample

log = logging.getLogger(__name__)


def pm5_uuid(short: int) -> str:
    return f"ce06{short:04x}-43e5-11e4-916c-0800200c9a66"


PM5_DISCOVERY = pm5_uuid(0x0000)      # the UUID a PM5 advertises
INFO_SERVICE = pm5_uuid(0x0010)
CONTROL_SERVICE = pm5_uuid(0x0020)
CONTROL_TX = pm5_uuid(0x0021)         # CSAFE frames to the monitor
CONTROL_RX = pm5_uuid(0x0022)         # its answers
ROWING_SERVICE = pm5_uuid(0x0030)
GENERAL_STATUS = pm5_uuid(0x0031)
ADDITIONAL_STATUS_1 = pm5_uuid(0x0032)
ADDITIONAL_STATUS_2 = pm5_uuid(0x0033)
SAMPLE_RATE = pm5_uuid(0x0034)
STROKE_DATA = pm5_uuid(0x0035)
ADDITIONAL_STROKE_DATA = pm5_uuid(0x0036)
FORCE_CURVE = pm5_uuid(0x003D)

CONCEPT2_K = 2.80          # W = 2.80 / (s/m)^3
IDLE_S = 6.0               # no stroke for this long: the power is 0
LBS_TO_N = 4.4482216
HR_INVALID = 255
STROKE_STATE_IDLE = (0, 1)   # waiting for the wheel to reach minimum speed / to accelerate


def watt_from_pace(pace_500m_s: float | None) -> float | None:
    """Concept2's power for a pace in seconds per 500 m (None without a pace)."""
    if not pace_500m_s or pace_500m_s <= 0:
        return None
    return CONCEPT2_K / (pace_500m_s / 500.0) ** 3


def pace_from_watt(watt: float | None) -> float | None:
    """Seconds per 500 m for a power (None without a positive power)."""
    if not watt or watt <= 0:
        return None
    return 500.0 * (CONCEPT2_K / watt) ** (1.0 / 3.0)


# --------------------------------------------------------------------------------------------
# Pure parsers. Every multi-byte value is little-endian; a short packet gives ``truncated``.
# --------------------------------------------------------------------------------------------
class _Bytes:
    def __init__(self, data: bytes | bytearray) -> None:
        self.b = bytes(data)
        self.truncated = False

    def u(self, pos: int, size: int) -> int | None:
        if pos + size > len(self.b):
            self.truncated = True
            return None
        return int.from_bytes(self.b[pos:pos + size], "little")

    def scaled(self, pos: int, size: int, scale: float) -> float | None:
        v = self.u(pos, size)
        return None if v is None else v * scale


@dataclass
class GeneralStatus:
    """0x0031, 19 bytes."""
    elapsed_s: float | None = None
    distance_m: float | None = None
    workout_type: int | None = None
    interval_type: int | None = None
    workout_state: int | None = None
    rowing_state: int | None = None       # 0 inactive, 1 active
    stroke_state: int | None = None       # 0-1 waiting, 2 driving, 3 dwelling, 4 recovery
    drag_factor: int | None = None
    truncated: bool = False


def parse_general_status(data: bytes | bytearray) -> GeneralStatus:
    r = _Bytes(data)
    out = GeneralStatus(elapsed_s=r.scaled(0, 3, 0.01), distance_m=r.scaled(3, 3, 0.1), workout_type=r.u(6, 1),
                        interval_type=r.u(7, 1), workout_state=r.u(8, 1), rowing_state=r.u(9, 1),
                        stroke_state=r.u(10, 1), drag_factor=r.u(18, 1))
    out.truncated = r.truncated
    return out


@dataclass
class AdditionalStatus1:
    """0x0032, 16 bytes (17 with the machine type in later firmware)."""
    elapsed_s: float | None = None
    speed_mps: float | None = None
    stroke_rate: int | None = None        # strokes per minute
    heart_rate_bpm: int | None = None     # None when the monitor has no strap (255)
    pace_500m_s: float | None = None      # current pace
    avg_pace_500m_s: float | None = None
    rest_distance_m: int | None = None
    rest_time_s: float | None = None
    machine_type: int | None = None
    truncated: bool = False


def parse_additional_status_1(data: bytes | bytearray) -> AdditionalStatus1:
    r = _Bytes(data)
    hr = r.u(6, 1)
    out = AdditionalStatus1(elapsed_s=r.scaled(0, 3, 0.01), speed_mps=r.scaled(3, 2, 0.001), stroke_rate=r.u(5, 1),
                            heart_rate_bpm=None if hr in (None, HR_INVALID, 0) else hr,
                            pace_500m_s=r.scaled(7, 2, 0.01), avg_pace_500m_s=r.scaled(9, 2, 0.01),
                            rest_distance_m=r.u(11, 2), rest_time_s=r.scaled(13, 3, 0.01))
    out.truncated = r.truncated
    if len(r.b) >= 17:
        out.machine_type = r.b[16]
    return out


@dataclass
class AdditionalStatus2:
    """0x0033, 20 bytes."""
    elapsed_s: float | None = None
    interval_count: int | None = None
    avg_power_w: int | None = None
    total_calories: int | None = None
    split_avg_pace_500m_s: float | None = None
    split_avg_power_w: int | None = None
    truncated: bool = False


def parse_additional_status_2(data: bytes | bytearray) -> AdditionalStatus2:
    r = _Bytes(data)
    out = AdditionalStatus2(elapsed_s=r.scaled(0, 3, 0.01), interval_count=r.u(3, 1), avg_power_w=r.u(4, 2),
                            total_calories=r.u(6, 2), split_avg_pace_500m_s=r.scaled(8, 2, 0.01),
                            split_avg_power_w=r.u(10, 2))
    out.truncated = r.truncated
    return out


@dataclass
class StrokeData:
    """0x0035, 20 bytes: one stroke."""
    elapsed_s: float | None = None
    distance_m: float | None = None
    drive_length_m: float | None = None
    drive_time_s: float | None = None
    recovery_time_s: float | None = None
    stroke_distance_m: float | None = None
    peak_force_n: float | None = None
    avg_force_n: float | None = None
    work_j: float | None = None
    stroke_count: int | None = None
    truncated: bool = False


def parse_stroke_data(data: bytes | bytearray) -> StrokeData:
    r = _Bytes(data)
    out = StrokeData(elapsed_s=r.scaled(0, 3, 0.01), distance_m=r.scaled(3, 3, 0.1),
                     drive_length_m=r.scaled(6, 1, 0.01), drive_time_s=r.scaled(7, 1, 0.01),
                     recovery_time_s=r.scaled(8, 2, 0.01), stroke_distance_m=r.scaled(10, 2, 0.01),
                     peak_force_n=r.scaled(12, 2, 0.1 * LBS_TO_N), avg_force_n=r.scaled(14, 2, 0.1 * LBS_TO_N),
                     work_j=r.scaled(16, 2, 0.1), stroke_count=r.u(18, 2))
    out.truncated = r.truncated
    return out


@dataclass
class AdditionalStrokeData:
    """0x0036, 15 bytes: the power of the stroke."""
    elapsed_s: float | None = None
    power_w: int | None = None
    calories_per_hour: int | None = None
    stroke_count: int | None = None
    truncated: bool = False


def parse_additional_stroke_data(data: bytes | bytearray) -> AdditionalStrokeData:
    r = _Bytes(data)
    out = AdditionalStrokeData(elapsed_s=r.scaled(0, 3, 0.01), power_w=r.u(3, 2), calories_per_hour=r.u(5, 2),
                               stroke_count=r.u(7, 2))
    out.truncated = r.truncated
    return out


def is_pm5(dev: Any, adv: Any) -> bool:
    """True for a Concept2 monitor in a scan: it advertises Concept2's UUID, or its name starts with PM5."""
    uuids = {u.lower() for u in (getattr(adv, "service_uuids", None) or [])}
    name = (getattr(adv, "local_name", None) or getattr(dev, "name", None) or "").strip().upper()
    return PM5_DISCOVERY in uuids or ROWING_SERVICE in uuids or name.startswith("PM5")


def scan_pm5(timeout_s: float = 8.0, scanner_cls: Any = None) -> list[DeviceInfo]:
    """Concept2 monitors nearby, strongest first. The monitor must be awake (press a button on it)."""
    return scan_sync([], timeout_s, scanner_cls, accept=is_pm5, filter_scan=False)


def _default_client_cls() -> Any:
    from bleak import BleakClient
    return BleakClient


# --------------------------------------------------------------------------------------------
# The device
# --------------------------------------------------------------------------------------------
class Pm5Ergometer(SupervisedConnection):
    """A Concept2 PM5 (``devices.base.Ergometer`` with ``controllable = False``): the load is read,
    never set; the test's stages can be shown on the monitor with ``program_workout``."""

    sport = "skierg"
    controllable = False
    measured = True
    programmable = True
    device_kind = "treadmill"      # the ergometer's place in DeviceStatus and in the UI
    min_speed_kmh = 0.0            # the load limits, in watt
    max_speed_kmh = 1000.0
    min_incline_pct = 0.0
    max_incline_pct = 0.0

    def __init__(self, bus: Bus | None = None, *, client_cls: Any = None, scanner_cls: Any = None,
                 clock: Clock = time.time, idle_s: float = IDLE_S, csafe_timeout_s: float = 2.0,
                 **kwargs: Any) -> None:
        super().__init__(bus, name=t("devices.pm5.default_name"), clock=clock, **kwargs)
        self._client_cls = client_cls
        self._scanner_cls = scanner_cls
        self._idle_s = idle_s
        self._client: Any = None
        self._device: DeviceInfo | None = None
        self._lock = threading.Lock()
        self._state: ErgometerState | None = None
        self._target: float | None = None
        self._lost: asyncio.Event | None = None
        self._stroke_power: float | None = None
        self._last_stroke_t: float | None = None
        self._idle_state = False
        self._short = 0                    # truncated notifications seen (logged once in a while)
        self._csafe_timeout_s = csafe_timeout_s
        self._answer: asyncio.Future[bytes] | None = None
        self._rx = bytearray()
        self.programmed: bool | None = None     # the last workout: True taken, False refused, None not tried

    @property
    def state(self) -> ErgometerState | None:
        with self._lock:
            st = self._state
            if st is None:
                return None
            if st.running and self._gone_idle():      # no packet says "stopped": time does
                st = self._state = replace(st, t=self._clock(), speed_kmh=0.0, power_w=0.0, cadence=0.0,
                                           running=False)
            return st

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        return scan_pm5(timeout_s, self._scanner_cls)

    def connect(self, device: DeviceInfo) -> None:
        """Connect and start listening. Blocks until connected; raises DeviceError with a coach message."""
        self._device = device
        self._name = device.name or device.address
        self._start_session()

    def set_target(self, load: float, incline_pct: float = 0.0) -> None:
        """Remember the target the athlete was shown. Nothing is sent: a PM5 cannot be set."""
        with self._lock:
            self._target = float(load) if load and load > 0 else None
            if self._state is not None:
                self._state = replace(self._state, target_speed_kmh=self._target)

    def program_workout(self, intervals: list[csafe.Interval]) -> None:
        """Put the stages on the monitor as a variable interval workout. Does not block; the outcome
        is ``programmed`` and a line on the log topic. A refusal never touches the reading."""
        self.programmed = None
        self._schedule(self._program(list(intervals)), "program workout")

    async def _program(self, intervals: list[csafe.Interval]) -> None:
        ok = False
        try:
            ok = bool(intervals) and self._client is not None
            frames = csafe.workout_frames(intervals) if ok else []
            for n, fr in enumerate(frames, 1):
                if not await self._send_frame(fr):
                    log.info("%s: frame %d of %d not taken", self._log_id, n, len(frames))
                    ok = False
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the control service may be missing or busy
            log.info("%s: workout not programmed (%s)", self._log_id, type(exc).__name__)
            ok = False
        self.programmed = ok
        log.info("%s: workout of %d intervals %s", self._log_id, min(len(intervals), csafe.MAX_INTERVALS),
                 "programmed" if ok else "refused")
        self._log(t("devices.pm5.programmed" if ok else "devices.pm5.program_failed"))

    async def _send_frame(self, fr: bytes) -> bool:
        """Write one CSAFE frame (in 20-byte pieces) and wait for the monitor to say it was taken."""
        self._rx.clear()
        self._answer = asyncio.get_running_loop().create_future()
        try:
            for part in csafe.chunks(fr):
                await self._client.write_gatt_char(CONTROL_TX, part, response=True)
            return csafe.accepted(await asyncio.wait_for(self._answer, self._csafe_timeout_s))
        except asyncio.TimeoutError:
            return False
        finally:
            self._answer = None

    def _on_control(self, _char: Any, data: bytearray) -> None:
        self._rx += bytes(data)
        if self._rx and self._rx[-1] == csafe.STOP and self._answer is not None and not self._answer.done():
            self._answer.set_result(bytes(self._rx))

    def start(self) -> None:
        """Nothing to start: the wheel turns when the athlete pulls."""

    def stop(self) -> None:
        """Nothing to stop."""

    # --- connection ------------------------------------------------------------------------
    def _describe_error(self, exc: BaseException) -> str:
        text = str(exc).lower()
        if "characteristic" in text or "service" in text:
            return t("devices.pm5.not_pm5", name=self._name)
        if "not found" in text or type(exc).__name__ == "BleakDeviceNotFoundError":
            return t("devices.error.not_found", name=self._name) + " " + t("devices.pm5.close_apps")
        return super()._describe_error(exc) + " " + t("devices.pm5.close_apps")

    async def _open(self) -> None:
        assert self._device is not None
        self._lost = asyncio.Event()
        loop = asyncio.get_running_loop()
        lost = self._lost

        def on_disconnect(_client: Any) -> None:
            loop.call_soon_threadsafe(lost.set)

        cls = self._client_cls or _default_client_cls()
        self._client = cls(self._device.address, disconnected_callback=on_disconnect)
        await self._client.connect()
        await self._client.start_notify(ADDITIONAL_STATUS_1, self._on_status_1)   # the one that must exist
        for uuid, handler in ((GENERAL_STATUS, self._on_general), (ADDITIONAL_STATUS_2, self._on_status_2),
                              (STROKE_DATA, self._on_stroke), (ADDITIONAL_STROKE_DATA, self._on_stroke_power)):
            try:
                await self._client.start_notify(uuid, handler)
            except Exception:  # noqa: BLE001 - keep what the firmware has
                log.info("%s: characteristic %s missing", self._log_id, uuid[4:8])
        try:      # the answers to CSAFE frames; without it the monitor is only read
            await self._client.start_notify(CONTROL_RX, self._on_control)
        except Exception:  # noqa: BLE001
            log.info("%s: no control service, the workout cannot be programmed", self._log_id)
        self._update()

    async def _wait_lost(self) -> None:
        assert self._lost is not None
        await self._lost.wait()
        self._log(t("devices.status.lost", name=self._name))

    async def _close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await maybe_await(client.disconnect())

    # --- notifications (run on the loop thread) ---------------------------------------------
    def _note_short(self, truncated: bool) -> None:
        if truncated:
            self._short += 1
            if self._short in (1, 10, 100) or self._short % 1000 == 0:
                log.info("%s: %d short notifications so far", self._log_id, self._short)

    def _on_general(self, _char: Any, data: bytearray) -> None:
        d = parse_general_status(data)
        self._note_short(d.truncated)
        fields: dict[str, Any] = {}
        if d.distance_m is not None:
            fields["distance_m"] = d.distance_m
        if d.drag_factor:
            fields["drag_factor"] = d.drag_factor
        if d.stroke_state is not None:
            self._idle_state = d.stroke_state in STROKE_STATE_IDLE and d.rowing_state == 0
        self._update(**fields)

    def _on_status_1(self, _char: Any, data: bytearray) -> None:
        d = parse_additional_status_1(data)
        self._note_short(d.truncated)
        fields: dict[str, Any] = {}
        if d.stroke_rate is not None:
            fields["cadence"] = float(d.stroke_rate)
        if d.pace_500m_s is not None:
            fields["pace_500m_s"] = d.pace_500m_s or None
        if d.heart_rate_bpm is not None:
            fields["heart_rate_bpm"] = d.heart_rate_bpm
        if d.machine_type is not None and self._state is None:
            log.info("%s: machine type %d", self._log_id, d.machine_type)
        self._update(**fields)

    def _on_status_2(self, _char: Any, data: bytearray) -> None:
        self._note_short(parse_additional_status_2(data).truncated)

    def _on_stroke(self, _char: Any, data: bytearray) -> None:
        d = parse_stroke_data(data)
        self._note_short(d.truncated)
        if d.drive_time_s is None:
            return
        now = self._clock()
        self._last_stroke_t = now
        fields: dict[str, Any] = {"drive_time_s": d.drive_time_s}
        if d.recovery_time_s:
            fields["recovery_time_s"] = d.recovery_time_s
        if d.stroke_count is not None:
            fields["stroke_count"] = d.stroke_count
        self._update(**fields)
        if self._bus is not None:
            self._bus.publish(TOPIC_STROKE, StrokeSample(now, d.stroke_count, d.drive_time_s, d.recovery_time_s,
                                                         d.drive_length_m, self._stroke_power, d.peak_force_n,
                                                         d.avg_force_n))

    def _on_stroke_power(self, _char: Any, data: bytearray) -> None:
        d = parse_additional_stroke_data(data)
        self._note_short(d.truncated)
        if d.power_w is None:
            return
        self._stroke_power = float(d.power_w)
        self._last_stroke_t = self._clock()
        self._update()

    def _gone_idle(self) -> bool:
        last = self._last_stroke_t
        return self._idle_state or (last is not None and self._clock() - last > self._idle_s)

    def _update(self, **fields: Any) -> None:
        with self._lock:
            base = self._state or ErgometerState(t=0.0, speed_kmh=None, incline_pct=0.0)
            st = replace(base, t=self._clock(), target_speed_kmh=self._target, **fields)
            power = self._stroke_power
            if power is None:                         # no stroke power yet: from the pace
                power = watt_from_pace(st.pace_500m_s)
            if self._gone_idle() or (st.cadence is not None and st.cadence <= 0 and self._last_stroke_t is None):
                power = 0.0
            st = replace(st, speed_kmh=None if power is None else round(power, 1),
                         power_w=None if power is None else round(power, 1),
                         running=bool(power and power > 0))
            if not st.running:
                st = replace(st, cadence=0.0 if st.cadence is not None else None)
            self._state = st
        if self._bus is not None:
            self._bus.publish(TOPIC_ERGOMETER, st)


__all__ = ["Pm5Ergometer", "scan_pm5", "is_pm5", "pm5_uuid", "watt_from_pace", "pace_from_watt",
           "parse_general_status", "parse_additional_status_1", "parse_additional_status_2", "parse_stroke_data",
           "parse_additional_stroke_data", "GENERAL_STATUS", "ADDITIONAL_STATUS_1", "ADDITIONAL_STATUS_2",
           "STROKE_DATA", "ADDITIONAL_STROKE_DATA", "ROWING_SERVICE", "PM5_DISCOVERY", "CONTROL_TX", "CONTROL_RX"]
