"""Cycle trainer over Bluetooth FTMS (Fitness Machine Service 0x1826), for tests in watt.

What it does:
  - subscribes to Indoor Bike Data (0x2AD2) and parses every flag field
    (``parse_indoor_bike_data``). Trainers split the fields over several packets ("More Data"), so
    the latest value of each field is kept,
  - reads Fitness Machine Feature (0x2ACC) and Supported Power Range (0x2AD8) and clamps every
    target to the range,
  - drives the Fitness Machine Control Point (0x2AD9): Request Control, Start/Resume, **Set Target
    Power** (0x05, ERG: the trainer holds the watt whatever the cadence), Set Indoor Bike
    Simulation Parameters (0x11, for a stage where the rider chooses the power: the 20 minute FTP
    test) and Stop/Pause,
  - follows Fitness Machine Status (0x2ADA): target power changed, started, stopped, control lost,
  - marks ``low_cadence`` in the state when the rider falls below ``LOW_CADENCE_RPM`` with a power
    target: in ERG the resistance then rises and the trainer cannot hold the power,
  - reconnects with backoff and sends the last target again (``_runtime.SupervisedConnection``).

The control point machinery (write, wait for the 0x80 indication, ask for control again on
"control not permitted") is ``FtmsTreadmill``'s: this class inherits it and replaces what is a
treadmill's. ``ftms.py`` itself is untouched until the treadmill is tried on the real belt.

The state is an ``ErgometerState`` whose ``speed_kmh`` holds the power in watt (the load of the
sport), published on the same bus topic as the treadmill.

Not tried on a real trainer: the Tacx NEO 2T is said to have FTMS in reviews, not
in Tacx's own documentation. It takes two Bluetooth connections at a time, so Zwift, QZ and other
apps are closed during a test. The byte layouts are written from the Bluetooth SIG
specification (FTMS 1.0) from memory; see docs/PROV.md for what must be tried.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, replace
from typing import Any

from lopanalys.core.bus import TOPIC_ERGOMETER, Bus
from lopanalys.core.i18n import t
from lopanalys.devices._runtime import Clock, uuid16
from lopanalys.devices._scan import FTMS_INDOOR_BIKE, FTMS_TREADMILL, ftms_machine_types, scan_sync
from lopanalys.devices.base import DeviceInfo, ErgometerState
from lopanalys.devices.ftms import (_RESULT_NAMES, CONTROL_POINT, FTMS_SERVICE, MACHINE_STATUS, OP_REQUEST_CONTROL,
                                    OP_START_RESUME, OP_STOP_PAUSE, RESULT_CONTROL_NOT_PERMITTED, RESULT_SUCCESS,
                                    ST_CONTROL_PERMISSION_LOST, ST_RESET,
                                    ST_STARTED_OR_RESUMED_BY_USER, ST_STOPPED_BY_SAFETY_KEY,
                                    ST_STOPPED_OR_PAUSED_BY_USER, FtmsTreadmill, Range, _default_client_cls,
                                    _Reader, _snap, cmd_start, cmd_stop, require_ftms)

log = logging.getLogger(__name__)

INDOOR_BIKE_DATA = uuid16(0x2AD2)
MACHINE_FEATURE = uuid16(0x2ACC)
SUPPORTED_RESISTANCE_RANGE = uuid16(0x2AD6)
SUPPORTED_POWER_RANGE = uuid16(0x2AD8)

OP_SET_TARGET_RESISTANCE = 0x04
OP_SET_TARGET_POWER = 0x05
OP_SET_SIMULATION = 0x11

ST_TARGET_RESISTANCE_CHANGED = 0x07
ST_TARGET_POWER_CHANGED = 0x08
ST_SIMULATION_CHANGED = 0x12

# Target Setting Features (the second four bytes of 0x2ACC)
FEATURE_POWER_TARGET = 1 << 3
FEATURE_SIMULATION = 1 << 13

DEFAULT_MIN_POWER = 0.0
DEFAULT_MAX_POWER = 2000.0
LOW_CADENCE_RPM = 50.0
# A flat road for a stage without a power target: no wind, 0 % grade, Zwift's usual rolling and air values.
SIM_CRR = 0.004
SIM_CW = 0.51


# --------------------------------------------------------------------------------------------
# Pure protocol functions (no Bluetooth)
# --------------------------------------------------------------------------------------------
@dataclass
class IndoorBikeData:
    """One Indoor Bike Data notification. Fields not present in the packet are None."""
    flags: int = 0
    speed_kmh: float | None = None
    avg_speed_kmh: float | None = None
    cadence_rpm: float | None = None
    avg_cadence_rpm: float | None = None
    total_distance_m: int | None = None
    resistance_level: int | None = None
    power_w: int | None = None
    avg_power_w: int | None = None
    energy_total_kcal: int | None = None
    energy_per_hour_kcal: int | None = None
    energy_per_min_kcal: int | None = None
    heart_rate_bpm: int | None = None
    met: float | None = None
    elapsed_s: int | None = None
    remaining_s: int | None = None
    truncated: bool = False


def parse_indoor_bike_data(data: bytes | bytearray) -> IndoorBikeData:
    """Parse an Indoor Bike Data (0x2AD2) payload. A truncated packet keeps what was readable."""
    r = _Reader(data)
    out = IndoorBikeData()
    try:
        flags = r.take("<H")
        out.flags = flags
        if not flags & 0x0001:            # bit 0 "More Data" = 0 -> Instantaneous Speed present
            out.speed_kmh = r.take("<H") / 100.0
        if flags & 0x0002:
            out.avg_speed_kmh = r.take("<H") / 100.0
        if flags & 0x0004:
            out.cadence_rpm = r.take("<H") / 2.0
        if flags & 0x0008:
            out.avg_cadence_rpm = r.take("<H") / 2.0
        if flags & 0x0010:
            out.total_distance_m = r.uint24()
        if flags & 0x0020:
            out.resistance_level = r.take("<h")
        if flags & 0x0040:
            out.power_w = r.take("<h")
        if flags & 0x0080:
            out.avg_power_w = r.take("<h")
        if flags & 0x0100:
            out.energy_total_kcal = r.take("<H")
            out.energy_per_hour_kcal = r.take("<H")
            out.energy_per_min_kcal = r.take("<B")
        if flags & 0x0200:
            out.heart_rate_bpm = r.take("<B")
        if flags & 0x0400:
            out.met = r.take("<B") / 10.0
        if flags & 0x0800:
            out.elapsed_s = r.take("<H")
        if flags & 0x1000:
            out.remaining_s = r.take("<H")
    except IndexError:
        out.truncated = True
    return out


def parse_power_range(data: bytes | bytearray) -> Range:
    """Supported Power Range (0x2AD8): sint16 min, sint16 max, uint16 increment, in watt."""
    lo, hi, inc = struct.unpack_from("<hhH", bytes(data))
    return Range(float(lo), float(hi), float(inc))


def parse_features(data: bytes | bytearray) -> tuple[int, int]:
    """Fitness Machine Feature (0x2ACC): (machine features, target setting features)."""
    machine, target = struct.unpack_from("<II", bytes(data))
    return machine, target


def cmd_set_target_power(watt: float) -> bytes:
    """Set Target Power: sint16, 1 W."""
    return struct.pack("<Bh", OP_SET_TARGET_POWER, max(-0x8000, min(0x7FFF, round(watt))))


def cmd_set_target_resistance(level: float) -> bytes:
    """Set Target Resistance Level: uint8, 0.1 (unitless)."""
    return struct.pack("<BB", OP_SET_TARGET_RESISTANCE, max(0, min(0xFF, round(level * 10))))


def cmd_set_simulation(grade_pct: float = 0.0, wind_mps: float = 0.0, crr: float = SIM_CRR,
                       cw: float = SIM_CW) -> bytes:
    """Set Indoor Bike Simulation Parameters: wind sint16 (0.001 m/s), grade sint16 (0.01 %),
    rolling resistance uint8 (0.0001), wind resistance uint8 (0.01 kg/m)."""
    return struct.pack("<BhhBB", OP_SET_SIMULATION, max(-0x8000, min(0x7FFF, round(wind_mps * 1000))),
                       max(-0x8000, min(0x7FFF, round(grade_pct * 100))), max(0, min(0xFF, round(crr * 10000))),
                       max(0, min(0xFF, round(cw * 100))))


# --------------------------------------------------------------------------------------------
# Scanning: a trainer and a treadmill advertise the same service
# --------------------------------------------------------------------------------------------
def is_bike(_dev: Any, adv: Any) -> bool:
    """True for an FTMS device that may be a bike: it says so, or it does not say what it is."""
    kinds = ftms_machine_types(adv)
    return kinds is None or bool(kinds & FTMS_INDOOR_BIKE) or not kinds & FTMS_TREADMILL


def not_only_bike(_dev: Any, adv: Any) -> bool:
    """False only for a device that says it is a bike and not a treadmill (kept out of the treadmill list)."""
    kinds = ftms_machine_types(adv)
    return kinds is None or bool(kinds & FTMS_TREADMILL) or not kinds & FTMS_INDOOR_BIKE


# --------------------------------------------------------------------------------------------
# The device
# --------------------------------------------------------------------------------------------
class FtmsBike(FtmsTreadmill):
    """A cycle trainer over Bluetooth FTMS in ERG mode. Thread-safe sync API (``devices.base.Ergometer``)."""

    sport = "bike"
    controllable = True
    measured = True
    device_kind = "treadmill"      # the ergometer's place in DeviceStatus and in the UI

    def __init__(self, bus: Bus | None = None, *, client_cls: Any = None, scanner_cls: Any = None,
                 clock: Clock = time.time, command_timeout_s: float = 4.0, **kwargs: Any) -> None:
        super().__init__(bus, client_cls=client_cls, scanner_cls=scanner_cls, clock=clock, keepalive_s=0,
                         command_timeout_s=command_timeout_s, **kwargs)
        self._name = t("devices.bike.default_name")
        self._power_range = Range(DEFAULT_MIN_POWER, DEFAULT_MAX_POWER, 1.0)
        self._features: tuple[int, int] | None = None
        self._free = False                 # no power target: the rider chooses (simulation, flat road)
        self._grade = 0.0
        self._fields: dict[str, Any] = {}  # the latest value of every Indoor Bike Data field

    # --- Ergometer protocol ------------------------------------------------------------------
    @property
    def min_speed_kmh(self) -> float:      # the load limits, in watt (named after the first sport)
        return self._power_range.minimum

    @property
    def max_speed_kmh(self) -> float:
        return self._power_range.maximum

    @property
    def min_incline_pct(self) -> float:
        return 0.0

    @property
    def max_incline_pct(self) -> float:
        return 0.0

    @property
    def erg_supported(self) -> bool | None:
        """True when the trainer says it takes a power target (None: it did not say)."""
        return None if self._features is None else bool(self._features[1] & FEATURE_POWER_TARGET)

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        """FTMS trainers nearby: service 0x1826, without the machines that say they are treadmills only."""
        return scan_sync([FTMS_SERVICE], timeout_s, self._scanner_cls, accept=is_bike)

    def clamp_power(self, watt: float) -> float:
        return 0.0 if watt <= 0 else _snap(watt, self._power_range)

    def set_target(self, load: float, incline_pct: float = 0.0) -> None:
        """Hold ``load`` watt (ERG). Does not block; sent again after a reconnect. 0 releases nothing:
        use ``stop()`` or ``set_free()``."""
        power = self.clamp_power(load)
        with self._lock:
            self._free = False
            self._targets.speed_kmh = power
            self._targets.incline_pct = 0.0
            self._targets.stopped = False
            self._stop_unsent = False
            if self._state is not None:
                self._state = replace(self._state, target_speed_kmh=power, free=False)
        self._schedule(self._apply_targets(), "set_target")

    def set_free(self, grade_pct: float = 0.0) -> None:
        """No power target: the trainer behaves like a road with this grade and the rider chooses
        the power with the legs and the gears (the 20 minute FTP test)."""
        with self._lock:
            self._free = True
            self._grade = float(grade_pct)
            self._targets.speed_kmh = None
            self._targets.stopped = False
            self._stop_unsent = False
            if self._state is not None:
                self._state = replace(self._state, target_speed_kmh=None, free=True)
        self._schedule(self._apply_targets(), "set_free")

    def start(self) -> None:
        with self._lock:
            self._targets.running_wanted = True
            self._targets.stopped = False
            self._stop_unsent = False
        self._schedule(self._start_async(), "start")

    def stop(self) -> None:
        """Release the trainer (FTMS Stop): the wheel runs free."""
        with self._lock:
            self._targets.running_wanted = False
            self._targets.stopped = True
        self._schedule(self._stop_async(), "stop")

    # --- connection ------------------------------------------------------------------------
    def _describe_error(self, exc: BaseException) -> str:
        text = str(exc).lower()
        if "characteristic" in text or "service" in text:
            return t("devices.bike.not_ftms", name=self._name)
        if "not found" in text or type(exc).__name__ == "BleakDeviceNotFoundError":
            return t("devices.error.not_found", name=self._name) + " " + t("devices.bike.close_apps")
        return t("devices.error.connect_failed", name=self._name,
                 reason=str(exc) or type(exc).__name__) + " " + t("devices.bike.close_apps")

    async def _open(self) -> None:
        assert self._device is not None
        self._lost = asyncio.Event()
        self._cmd_lock = asyncio.Lock()
        self._waiters.clear()
        self._has_control = False
        self._fields = {}
        loop = asyncio.get_running_loop()
        lost = self._lost

        def on_disconnect(_client: Any) -> None:
            loop.call_soon_threadsafe(lost.set)

        cls = self._client_cls or _default_client_cls()
        self._client = cls(self._device.address, disconnected_callback=on_disconnect)
        await self._client.connect()
        require_ftms(self._client)
        await self._read_ranges()
        await self._client.start_notify(INDOOR_BIKE_DATA, self._on_bike_data)
        try:
            await self._client.start_notify(MACHINE_STATUS, self._on_status)
        except Exception:  # noqa: BLE001 - optional characteristic
            log.info("%s: no Fitness Machine Status", self._log_id)
        await self._client.start_notify(CONTROL_POINT, self._on_control_point)
        await self._request_control()
        await self._resend_stop()
        with self._lock:
            wanted = self._targets.running_wanted
        if wanted:                               # a reconnect in the middle of a test
            await self._command(cmd_start(), "start")
        await self._apply_targets()

    async def _read_ranges(self) -> None:
        try:
            self._features = parse_features(await self._client.read_gatt_char(MACHINE_FEATURE))
            log.info("%s: features 0x%08x, targets 0x%08x (ERG %s)", self._log_id, self._features[0],
                     self._features[1], "yes" if self.erg_supported else "no")
            if not self.erg_supported:
                self._log(t("devices.bike.no_erg", name=self._name))
        except Exception:  # noqa: BLE001 - optional
            log.info("%s: no Fitness Machine Feature", self._log_id)
        try:
            rng = parse_power_range(await self._client.read_gatt_char(SUPPORTED_POWER_RANGE))
            if rng.maximum > rng.minimum and rng.maximum > 0:
                self._power_range = rng
                log.info("%s: power range %.0f-%.0f W", self._log_id, rng.minimum, rng.maximum)
            else:       # 0/0/0 or min above max: every target would be clamped to it; the defaults stay
                log.warning("%s: Supported Power Range %.0f-%.0f W is not usable, using defaults", self._log_id,
                            rng.minimum, rng.maximum)
        except Exception:  # noqa: BLE001
            log.info("%s: no Supported Power Range, using defaults", self._log_id)

    # --- control point -----------------------------------------------------------------------
    async def _start_async(self) -> None:
        if not self._has_control:
            await self._request_control()
        await self._command(cmd_start(), "start")   # some trainers want it before the first target
        await self._apply_targets()

    async def _apply_targets(self) -> None:
        if self._client is None:
            return
        with self._lock:
            power, free, grade = self._targets.speed_kmh, self._free, self._grade
            stopped = self._targets.stopped
        if stopped:          # released by the program: a reconnect does not put the load back
            return
        if free:
            await self._command(cmd_set_simulation(grade), "simulation")
        elif power is not None and power > 0:
            await self._command(cmd_set_target_power(power), "power")

    async def _command(self, payload: bytes, what: str, retry_control: bool = True) -> int | None:
        """As the treadmill's, with the trainer's words in the messages to the coach."""
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
                self._log(t("devices.bike.control_denied"))
        log.info("%s: control point %s -> %s", self._log_id, what,
                 "no response" if result is None else f"0x{result:02x} ({_RESULT_NAMES.get(result, '?')})")
        if result not in (RESULT_SUCCESS, None, RESULT_CONTROL_NOT_PERMITTED) and op not in (OP_START_RESUME,
                                                                                            OP_STOP_PAUSE):
            self._log(t("devices.bike.command_failed", what=t(f"devices.bike.cmd.{what}")))
        return result

    # --- notifications (run on the loop thread) ---------------------------------------------
    def _on_status(self, _char: Any, data: bytearray) -> None:
        b = bytes(data)
        if not b:
            return
        op = b[0]
        if op in (ST_STOPPED_OR_PAUSED_BY_USER, ST_STOPPED_BY_SAFETY_KEY):
            self._log(t("devices.bike.stopped_by_user"))
        elif op in (ST_CONTROL_PERMISSION_LOST, ST_RESET):
            self._has_control = False
            if op == ST_CONTROL_PERMISSION_LOST:
                self._log(t("devices.bike.control_lost"))
        elif op == ST_TARGET_POWER_CHANGED and len(b) >= 3:
            self._update(target_speed_kmh=float(struct.unpack_from("<h", b, 1)[0]))
        elif op == ST_STARTED_OR_RESUMED_BY_USER:
            log.info("%s: started", self._log_id)

    def _on_bike_data(self, _char: Any, data: bytearray) -> None:
        d = parse_indoor_bike_data(data)
        f = self._fields
        for k in ("speed_kmh", "cadence_rpm", "power_w", "total_distance_m", "heart_rate_bpm"):
            v = getattr(d, k)
            if v is not None:
                f[k] = v
        power, cadence = f.get("power_w"), f.get("cadence_rpm")
        if power is None and cadence is None:
            return
        with self._lock:
            target, free = self._targets.speed_kmh, self._free
        moving = (cadence or 0) > 0 or (power or 0) > 0
        self._update(speed_kmh=None if power is None else float(max(power, 0)),
                     power_w=None if power is None else float(power), cadence=cadence,
                     wheel_speed_kmh=f.get("speed_kmh"), running=moving,
                     distance_m=None if f.get("total_distance_m") is None else float(f["total_distance_m"]),
                     heart_rate_bpm=f.get("heart_rate_bpm") or None,
                     low_cadence=bool(not free and target and cadence is not None and 0 < cadence < LOW_CADENCE_RPM))

    def _update(self, **fields: Any) -> None:
        with self._lock:
            base = self._state or ErgometerState(t=0.0, speed_kmh=None, incline_pct=0.0)
            if not self._free:
                fields.setdefault("target_speed_kmh", self._targets.speed_kmh)
            fields.setdefault("free", self._free)
            st = replace(base, t=self._clock(), **fields)
            self._state = st
        if self._bus is not None:
            self._bus.publish(TOPIC_ERGOMETER, st)


__all__ = ["FtmsBike", "IndoorBikeData", "parse_indoor_bike_data", "parse_power_range", "parse_features",
           "cmd_set_target_power", "cmd_set_target_resistance", "cmd_set_simulation", "is_bike", "not_only_bike",
           "INDOOR_BIKE_DATA", "MACHINE_FEATURE", "SUPPORTED_POWER_RANGE", "LOW_CADENCE_RPM"]
