"""Bluetooth heart rate strap (Heart Rate Service 0x180D), e.g. Garmin HRM-Pro+, Polar H10, Wahoo TICKR.

Heart Rate Measurement (0x2A37) notifications are parsed per the Bluetooth SIG spec: uint8 or
uint16 heart rate, sensor contact, energy expended and RR intervals (1/1024 s, converted to ms).
Battery Level (0x2A19, Battery Service 0x180F) is read and followed when the strap has it.
Running Speed and Cadence (RSC, service 0x1814, measurement 0x2A53) is followed when the strap has
it: the cadence goes on the heart rate samples (``HeartRateSample.cadence_spm``) while it is fresh.
Garmin says the HRM-Pro/HRM-Pro Plus sends running data over Bluetooth this way (not checked
against a source; to be confirmed on the real strap, see docs/PROV.md).
Samples go on ``TOPIC_HEART_RATE``; a strap without skin contact (bpm 0) publishes no sample but a
status message instead. Reconnects with backoff like the treadmill.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from lopanalys.core.bus import TOPIC_HEART_RATE, Bus
from lopanalys.core.i18n import t
from lopanalys.devices._runtime import Clock, SupervisedConnection, maybe_await, uuid16
from lopanalys.devices._scan import scan_sync
from lopanalys.devices.base import ConnState, DeviceInfo, HeartRateSample

log = logging.getLogger(__name__)

HR_SERVICE = uuid16(0x180D)
HR_MEASUREMENT = uuid16(0x2A37)
BATTERY_LEVEL = uuid16(0x2A19)
RSC_SERVICE = uuid16(0x1814)
RSC_MEASUREMENT = uuid16(0x2A53)
LOW_BATTERY_PCT = 15
CADENCE_FRESH_S = 5.0     # a strap cadence older than this is not shown


@dataclass
class RscMeasurement:
    speed_mps: float
    cadence_spm: int          # "instantaneous cadence", 1/min per the spec (steps/min on a running sensor)
    stride_m: float | None = None
    distance_m: float | None = None
    running: bool | None = None


STRIDE_CADENCE_MAX = 125   # below this an RSC cadence is strides (one foot) per minute, not steps


def steps_per_min(rsc_cadence: int) -> float:
    """Steps/min from an RSC cadence.

    The spec says "1/min" without saying whether a step or a stride. Garmin's HRM-Pro+ sends strides
    (one foot): a strap gave 84 at 10 km/h on real hardware 2026-11-14 while the camera counted 170 steps/min.
    Running is 150-200 steps/min, i.e. 75-100 strides/min, so a value under STRIDE_CADENCE_MAX is
    doubled and a higher one is already steps (sensors that send steps).
    """
    return float(rsc_cadence * 2 if rsc_cadence < STRIDE_CADENCE_MAX else rsc_cadence)


def parse_rsc(data: bytes | bytearray) -> RscMeasurement:
    """Parse an RSC Measurement (0x2A53) payload per the Bluetooth SIG spec. Raises ValueError.

    flags (uint8): bit 0 stride length present, bit 1 total distance present, bit 2 running (else
    walking); speed uint16 in 1/256 m/s; cadence uint8 per minute; stride uint16 in 1/100 m;
    distance uint32 in 1/10 m.
    """
    b = bytes(data)
    if len(b) < 4:
        raise ValueError("RSC packet too short")
    flags = b[0]
    speed = struct.unpack_from("<H", b, 1)[0] / 256.0
    cadence = b[3]
    pos = 4
    stride = distance = None
    if flags & 0x01:
        if pos + 2 > len(b):
            raise ValueError("RSC packet too short")
        stride = struct.unpack_from("<H", b, pos)[0] / 100.0
        pos += 2
    if flags & 0x02:
        if pos + 4 > len(b):
            raise ValueError("RSC packet too short")
        distance = struct.unpack_from("<I", b, pos)[0] / 10.0
    return RscMeasurement(speed, cadence, stride, distance, bool(flags & 0x04))


@dataclass
class HrMeasurement:
    bpm: int
    contact: bool | None = None          # None: the strap does not report contact
    energy_kj: int | None = None
    rr_ms: list[int] = field(default_factory=list)


def parse_heart_rate(data: bytes | bytearray) -> HrMeasurement:
    """Parse a Heart Rate Measurement (0x2A37) payload. Raises ValueError on a malformed packet."""
    b = bytes(data)
    if len(b) < 2:
        raise ValueError("heart rate packet too short")
    flags = b[0]
    pos = 1
    if flags & 0x01:
        if len(b) < 3:
            raise ValueError("heart rate packet too short")
        bpm = struct.unpack_from("<H", b, pos)[0]
        pos += 2
    else:
        bpm = b[pos]
        pos += 1
    contact: bool | None = bool(flags & 0x02) if flags & 0x04 else None
    energy = None
    if flags & 0x08 and pos + 2 <= len(b):
        energy = struct.unpack_from("<H", b, pos)[0]
        pos += 2
    rr: list[int] = []
    if flags & 0x10:
        while pos + 2 <= len(b):
            raw = struct.unpack_from("<H", b, pos)[0]
            rr.append(round(raw * 1000 / 1024))
            pos += 2
    return HrMeasurement(bpm=bpm, contact=contact, energy_kj=energy, rr_ms=rr)


def _default_client_cls() -> Any:
    from bleak import BleakClient
    return BleakClient


class BleHeartRate(SupervisedConnection):
    """A Bluetooth heart rate strap. Thread-safe sync API (``devices.base.HeartRateMonitor``)."""

    device_kind = "heart_rate"

    def __init__(self, bus: Bus | None = None, *, client_cls: Any = None, scanner_cls: Any = None,
                 clock: Clock = time.time, **kwargs: Any) -> None:
        super().__init__(bus, name=t("devices.hr.default_name"), clock=clock, **kwargs)
        self._client_cls = client_cls
        self._scanner_cls = scanner_cls
        self._client: Any = None
        self._device: DeviceInfo | None = None
        self._lock = threading.Lock()
        self._last: HeartRateSample | None = None
        self._battery: int | None = None
        self._contact_ok = True
        self._lost: asyncio.Event | None = None
        self._cadence: tuple[float, float] | None = None   # (steps/min, clock time) from RSC
        self.has_rsc = False

    @property
    def last(self) -> HeartRateSample | None:
        with self._lock:
            return self._last

    @property
    def battery_pct(self) -> int | None:
        return self._battery

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        """Find heart rate straps nearby (service 0x180D). Wet the strap so it wakes up."""
        return scan_sync([HR_SERVICE], timeout_s, self._scanner_cls)

    def connect(self, device: DeviceInfo) -> None:
        """Connect and subscribe. Blocks until connected; raises DeviceError with a coach message."""
        self._device = device
        self._name = device.name or device.address
        self._start_session()

    def _describe_error(self, exc: BaseException) -> str:
        text = str(exc).lower()
        if "not found" in text or type(exc).__name__ == "BleakDeviceNotFoundError":
            return t("devices.hr.not_found", name=self._name)
        return super()._describe_error(exc)

    def _status_extra(self) -> dict[str, Any]:
        return {"battery_pct": self._battery}

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
        await self._client.start_notify(HR_MEASUREMENT, self._on_measurement)
        try:
            self._set_battery(bytes(await self._client.read_gatt_char(BATTERY_LEVEL)))
            await self._client.start_notify(BATTERY_LEVEL, lambda _c, d: self._set_battery(bytes(d), publish=True))
        except Exception:  # noqa: BLE001 - not every strap has the battery service or notifies it
            log.info("%s: battery level not available", self._log_id)
        try:
            await self._client.start_notify(RSC_MEASUREMENT, self._on_rsc)
            self.has_rsc = True
            log.info("heart_rate: running cadence (RSC) available")
        except Exception:  # noqa: BLE001 - most straps have no RSC service: the camera gives cadence
            self.has_rsc = False
            log.info("heart_rate: no running cadence (RSC), the camera's cadence is used")

    async def _wait_lost(self) -> None:
        assert self._lost is not None
        await self._lost.wait()
        self._log(t("devices.status.lost", name=self._name))

    async def _close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await maybe_await(client.disconnect())

    def _set_battery(self, data: bytes, publish: bool = False) -> None:
        if not data:
            return
        self._battery = min(100, data[0])
        if self._battery <= LOW_BATTERY_PCT:
            self._log(t("devices.hr.battery_low", pct=self._battery))
        if publish and self.connected:
            self._publish_status(ConnState.CONNECTED, t("devices.status.connected", name=self._name),
                                 battery_pct=self._battery)

    def _on_measurement(self, _char: Any, data: bytearray) -> None:
        try:
            m = parse_heart_rate(data)
        except ValueError:
            log.debug("bad HR packet %s", bytes(data).hex())
            return
        has_contact = m.bpm > 0 and m.contact is not False
        if not has_contact:
            if self._contact_ok:
                self._contact_ok = False
                self._log(t("devices.hr.no_contact"))
            return
        self._contact_ok = True
        now = self._clock()
        cad = self._cadence
        sample = HeartRateSample(t=now, bpm=m.bpm, rr_ms=m.rr_ms or None, contact=m.contact,
                                 cadence_spm=cad[0] if cad and now - cad[1] <= CADENCE_FRESH_S else None)
        with self._lock:
            self._last = sample
        if self._bus is not None:
            self._bus.publish(TOPIC_HEART_RATE, sample)

    def _on_rsc(self, _char: Any, data: bytearray) -> None:
        try:
            m = parse_rsc(data)
        except ValueError:
            log.debug("bad RSC packet %s", bytes(data).hex())
            return
        # 0 = standing still (or the strap has not worked out the cadence yet): fall back to the camera
        self._cadence = (steps_per_min(m.cadence_spm), self._clock()) if m.cadence_spm > 0 else None
