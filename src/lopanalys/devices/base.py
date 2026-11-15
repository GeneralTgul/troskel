"""Treadmill and heart rate device interfaces. No Qt imports in devices/.

Treadmill backends (devices agent):
  - FtmsTreadmill: Bluetooth FTMS (0x1826) via bleak, written from the Bluetooth SIG spec.
  - QzTreadmill: talks to a running QZ (qdomyos-zwift) over its websocket as a separate program.
    QZ is GPL-3: never copy its code into this repo.
  - ManualTreadmill: no connection; the runner shows "set the treadmill to X km/h" and the
    operator confirms. Speed is then assumed to be the target.
  - SimTreadmill: for tests and demo mode.
Other ergometers (cycling and SkiErg):
  - FtmsBike (ftms_bike.py): a cycle trainer over FTMS, Indoor Bike Data and Set Target Power (ERG).
  - Pm5Ergometer (pm5.py): a Concept2 PM5 over Concept2's own service. Read only: a PM5 cannot be
    set, so the screen shows the target and the athlete holds it.
  - ManualErgometer, SimBike, SimSkiErg.
  They publish an ``ErgometerState`` on the same topic as the treadmill, and its ``speed_kmh`` holds
  the load in the unit of the sport (watt), so the test runner reads one field for every sport.
Heart rate: BleHeartRate (0x180D, any chest strap incl. Garmin HRM-Pro), SimHeartRate.
All backends run their own asyncio loop in a thread and publish on core.bus.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ConnState(str, Enum):
    DISCONNECTED = "disconnected"
    SCANNING = "scanning"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class DeviceStatus:
    device: str            # "treadmill" | "heart_rate"
    state: ConnState
    name: str = ""
    message: str = ""
    battery_pct: int | None = None   # heart rate strap battery, when the device reports it


@dataclass
class DeviceInfo:
    address: str           # BLE address, or "qz", "manual", "sim"
    name: str
    rssi: int | None = None


@dataclass
class TreadmillState:
    t: float
    speed_kmh: float | None
    incline_pct: float | None
    distance_m: float | None = None
    running: bool | None = None
    target_speed_kmh: float | None = None
    target_incline_pct: float | None = None
    measured: bool = True   # False in manual mode: values are targets, not readings

    @property
    def load(self) -> float | None:
        """The load in the unit of the sport: km/h on a treadmill, watt on a trainer or a SkiErg."""
        return self.speed_kmh

    @property
    def target_load(self) -> float | None:
        return self.target_speed_kmh


@dataclass
class ErgometerState(TreadmillState):
    """What a cycle trainer or a SkiErg reports. ``speed_kmh`` and ``target_speed_kmh`` hold the
    load in watt (read them as ``load`` and ``target_load``); ``incline_pct`` is 0."""
    power_w: float | None = None         # measured power
    cadence: float | None = None         # pedal revolutions/min (bike) or strokes/min (SkiErg)
    pace_500m_s: float | None = None     # SkiErg: seconds per 500 m as the monitor shows it
    wheel_speed_kmh: float | None = None  # bike: the trainer's own speed (flywheel), not used in a test
    drive_time_s: float | None = None    # SkiErg: the pull of the latest stroke
    recovery_time_s: float | None = None  # SkiErg: the return of the latest stroke
    stroke_count: int | None = None
    drag_factor: int | None = None       # SkiErg: the damper as the monitor measures it
    heart_rate_bpm: int | None = None    # the heart rate the machine itself sees, if any
    free: bool = False                   # bike: no power target, the rider chooses (FTP test)
    low_cadence: bool = False            # bike in ERG below ~50 rpm: the trainer cannot hold the power


@dataclass
class StrokeSample:
    """One SkiErg stroke as the PM5 reports it (published on core.bus.TOPIC_STROKE)."""
    t: float
    count: int | None = None
    drive_time_s: float | None = None
    recovery_time_s: float | None = None
    drive_length_m: float | None = None
    power_w: float | None = None
    peak_force_n: float | None = None
    avg_force_n: float | None = None


@dataclass
class HeartRateSample:
    t: float
    bpm: int
    rr_ms: list[int] | None = None
    contact: bool | None = None   # skin contact; None when the strap does not report it
    cadence_spm: float | None = None   # steps/min from the strap's RSC service (0x1814), when it has one


class Treadmill(Protocol):
    controllable: bool     # False for manual: the runner must prompt the operator
    min_speed_kmh: float
    max_speed_kmh: float
    max_incline_pct: float

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]: ...
    def connect(self, device: DeviceInfo) -> None: ...
    def disconnect(self) -> None: ...
    def set_target(self, speed_kmh: float, incline_pct: float) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    @property
    def state(self) -> TreadmillState | None: ...


class Ergometer(Protocol):
    """What the test runner needs from any ergometer (a Treadmill is one, with km/h as the load).

    ``controllable``: the program sets the load (FTMS treadmill, trainer in ERG). ``measured``: the
    state holds readings. A PM5 is measured but not controllable: the runner shows the target,
    lets the stage clock run and takes the stage's load from what was measured. Neither (manual):
    the operator sets the load and confirms. The load limits are ``min_speed_kmh`` and
    ``max_speed_kmh`` for every sport (named after the first one), in the unit of the sport.
    """
    sport: str             # core.sport.Sport value
    controllable: bool
    measured: bool
    min_speed_kmh: float
    max_speed_kmh: float
    max_incline_pct: float

    def connect(self, device: DeviceInfo) -> None: ...
    def disconnect(self) -> None: ...
    def set_target(self, load: float, incline_pct: float = 0.0) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    @property
    def state(self) -> TreadmillState | None: ...


class HeartRateMonitor(Protocol):
    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]: ...
    def connect(self, device: DeviceInfo) -> None: ...
    def disconnect(self) -> None: ...
    @property
    def last(self) -> HeartRateSample | None: ...
