"""DeviceManager: one place for the UI and the test runner to find, connect and drop devices.

Treadmill modes: ``"ftms"`` (Bluetooth FTMS), ``"qz"`` (through a running QZ), ``"manual"``
(the operator sets the belt), ``"sim"`` (simulator). Heart rate: a scanned ``DeviceInfo``,
``"sim"`` or ``"qz"`` (QZ's heart rate, only with the QZ treadmill mode).

Other sports: ``connect_ergometer(sport, mode, device)`` connects the ergometer of a
sport and keeps it in the same place as the treadmill (one ergometer at a time). Cycling:
``"ftms"`` (a trainer in ERG mode), ``"manual"``, ``"sim"``. SkiErg: ``"pm5"`` (Concept2 PM5, read
only), ``"manual"``, ``"sim"``. The modes of a sport are ``core.sport.profile(sport).load_sources``.
The last used trainer and PM5 are remembered under the kinds ``"bike"`` and ``"skierg"``.

The manager does not touch the database. The last used devices are handed to ``remember(kind,
info)`` and read back with ``recall(kind)``, where ``kind`` is ``"treadmill"`` or ``"heart_rate"``;
the UI wires these to ``storage.db.Database.set_setting/get_setting``.

FTMS and QZ exclude each other on the gym PC: the treadmill takes one Bluetooth connection, and
QZ holds it while QZ runs. ``connect_treadmill("ftms")`` therefore refuses with
"Stäng QZ först, bandet tar bara en anslutning åt gången" when QZ is running. QZ is never closed
by the app.

One connection per device at a time (real hardware 2026-11-14): connects run one after the other
under the manager's lock, a new connection first disconnects the old one completely (and gives a
Bluetooth device a moment before it is opened again), and "connect" on a device that is already
connected keeps that connection instead of dropping it. Connects and disconnects are logged at INFO
with the kind and mode only, never the device name or address (they can carry a person's name).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Literal

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, Bus
from lopanalys.core.i18n import t
from lopanalys.devices.base import DeviceInfo, DeviceStatus
from lopanalys.devices.errors import DeviceError
from lopanalys.core.sport import Sport, profile, sport_of
from lopanalys.devices.ftms import FTMS_SERVICE, FtmsTreadmill
from lopanalys.devices.ftms_bike import FtmsBike, is_bike, not_only_bike
from lopanalys.devices.heart_rate import HR_SERVICE, BleHeartRate
from lopanalys.devices.manual import ManualErgometer, ManualTreadmill
from lopanalys.devices.pm5 import Pm5Ergometer, scan_pm5
from lopanalys.devices.qz import QzHeartRate, QzTreadmill, qz_running
from lopanalys.devices.sim import SimBike, SimClock, SimHeartRate, SimSkiErg, SimTreadmill
from lopanalys.devices._scan import scan_sync

log = logging.getLogger(__name__)

TreadmillMode = Literal["ftms", "qz", "manual", "sim"]
Kind = Literal["treadmill", "heart_rate"]
MODES: tuple[str, ...] = ("ftms", "qz", "manual", "sim")


class DeviceManager:
    """Finds and connects the treadmill and the heart rate strap; keeps at most one of each."""

    def __init__(self, bus: Bus, *, remember: Callable[[str, DeviceInfo], None] | None = None,
                 recall: Callable[[str], DeviceInfo | None] | None = None,
                 client_cls: Any = None, scanner_cls: Any = None, ws_connect: Any = None,
                 qz_host: str = "127.0.0.1", qz_port: int | None = None,
                 sim_time_scale: float = 1.0, sim_clock: Callable[[], float] | None = None,
                 qz_check: Callable[[], bool] | None = None, settle_s: float = 1.0) -> None:
        self._bus = bus
        self._remember = remember
        self._recall = recall
        self._client_cls = client_cls
        self._scanner_cls = scanner_cls
        self._ws_connect = ws_connect
        self._qz_host = qz_host
        self._qz_port = qz_port
        self._sim_clock = sim_clock or SimClock(sim_time_scale)
        self._qz_check = qz_check or (lambda: qz_running(self._qz_host, self._qz_port))
        self._settle_s = settle_s   # pause between dropping a Bluetooth device and opening it again
        self._lock = threading.RLock()
        self._treadmill: Any = None
        self._treadmill_mode: str | None = None
        self._heart_rate: Any = None
        self._status: dict[str, DeviceStatus] = {}
        self._unsub = bus.subscribe(TOPIC_DEVICE_STATUS, self._on_status)

    # --- state ---------------------------------------------------------------------------------
    @property
    def treadmill(self) -> Any:
        """The connected treadmill (``devices.base.Treadmill``) or None."""
        return self._treadmill

    @property
    def treadmill_mode(self) -> str | None:
        return self._treadmill_mode

    @property
    def heart_rate(self) -> Any:
        """The connected heart rate monitor (``devices.base.HeartRateMonitor``) or None."""
        return self._heart_rate

    @property
    def sim_clock(self) -> Callable[[], float]:
        """The clock the simulators run on; the test runner should use it in demo mode."""
        return self._sim_clock

    def status(self, kind: Kind) -> DeviceStatus | None:
        """Latest DeviceStatus seen on the bus for ``"treadmill"`` or ``"heart_rate"``."""
        return self._status.get(kind)

    def last_device(self, kind: Kind) -> DeviceInfo | None:
        """The device remembered for ``kind``, if the app gave a ``recall`` callback."""
        if self._recall is None:
            return None
        try:
            return self._recall(kind)
        except Exception:  # noqa: BLE001 - a broken setting must not stop the app
            log.exception("recall(%s) failed", kind)
            return None

    def qz_running(self) -> bool:
        """True while QZ runs on this PC (then FTMS cannot reach the treadmill)."""
        try:
            return bool(self._qz_check())
        except Exception:  # noqa: BLE001
            return False

    def _on_status(self, st: DeviceStatus) -> None:
        self._status[st.device] = st

    # --- scanning --------------------------------------------------------------------------------
    def scan_treadmills(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        """FTMS treadmills nearby, strongest first: those advertising the service 0x1826, and the treadmill
        used before even when its advertisement leaves the service out (the Recoil before START).

        While QZ runs it holds the treadmill, so the scan would find nothing: it says so instead."""
        if self.qz_running():
            raise DeviceError(t("devices.ftms.qz_has_it"))
        known = [d for d in (self.last_device("treadmill"),) if d is not None]
        return scan_sync([FTMS_SERVICE], timeout_s, self._scanner_cls, accept=not_only_bike, known=known)

    def scan_ergometers(self, sport: Any = None, timeout_s: float = 8.0) -> list[DeviceInfo]:
        """The Bluetooth ergometers of a sport nearby, strongest first: FTMS treadmills, FTMS
        trainers (cycling) or Concept2 PM5 monitors (SkiErg)."""
        sp = sport_of(sport)
        if sp is Sport.BIKE:
            return scan_sync([FTMS_SERVICE], timeout_s, self._scanner_cls, accept=is_bike)
        if sp is Sport.SKIERG:
            return scan_pm5(timeout_s, self._scanner_cls)
        return self.scan_treadmills(timeout_s)

    def scan_heart_rate(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        """Heart rate straps nearby (service 0x180D), strongest first."""
        return scan_sync([HR_SERVICE], timeout_s, self._scanner_cls)

    # --- treadmill --------------------------------------------------------------------------------
    def connect_treadmill(self, mode: TreadmillMode | str, device: DeviceInfo | None = None) -> Any:
        """Connect a treadmill in ``mode``; replaces any connected one. Raises DeviceError."""
        if mode not in MODES:
            raise DeviceError(t("devices.error.unknown_mode", mode=mode))
        with self._lock:
            old = self._treadmill
            if old is not None and mode == self._treadmill_mode and _same(old, device) and _is_up(old):
                log.info("treadmill (%s): already connected, keeping the connection", mode)
                return old
            if mode == "ftms" and self.qz_running():     # refused before the old one is dropped: it stays
                raise DeviceError(t("devices.ftms.qz_has_it"))
            if old is not None:
                log.info("treadmill (%s): disconnecting before a new %s connection", self._treadmill_mode, mode)
            self.disconnect_treadmill()
            tm: Any
            log.info("treadmill (%s): connecting", mode)
            if mode == "ftms":
                device = device or self.last_device("treadmill") or self._first(self.scan_treadmills(),
                                                                                 "treadmill")
                if isinstance(old, FtmsTreadmill):
                    self._settle()
                tm = FtmsTreadmill(self._bus, client_cls=self._client_cls, scanner_cls=self._scanner_cls)
                try:
                    tm.connect(device)
                except Exception as exc:
                    log.info("treadmill (ftms): connect failed (%s)", type(exc).__name__)
                    raise
                self._store("treadmill", device)
            elif mode == "qz":
                tm = QzTreadmill(self._bus, host=self._qz_host, port=self._qz_port, ws_connect=self._ws_connect)
                tm.connect()
            elif mode == "manual":
                tm = ManualTreadmill(self._bus)
                tm.connect()
            else:
                tm = SimTreadmill(self._bus, clock=self._sim_clock, noise_kmh=0.03)
                tm.connect()
            self._treadmill, self._treadmill_mode = tm, mode
            log.info("treadmill (%s): connected", mode)
            if isinstance(self._heart_rate, SimHeartRate):
                self._heart_rate.follow(tm if isinstance(tm, SimTreadmill) else None)
            return tm

    # --- the ergometer of any sport ----------------------------------------------------------------
    @property
    def ergometer(self) -> Any:
        """The connected ergometer (a treadmill, a trainer or a SkiErg) or None."""
        return self._treadmill

    @property
    def ergometer_sport(self) -> str | None:
        """The sport of the connected ergometer (None when nothing is connected)."""
        return None if self._treadmill is None else str(getattr(self._treadmill, "sport", Sport.RUN.value))

    def connect_ergometer(self, sport: Any, mode: str, device: DeviceInfo | None = None) -> Any:
        """Connect the ergometer of ``sport`` in ``mode``; replaces any connected one. Raises DeviceError."""
        sp = sport_of(sport)
        if sp is Sport.RUN:
            return self.connect_treadmill(mode, device)
        if mode not in profile(sp).load_sources:
            raise DeviceError(t("devices.error.unknown_mode", mode=mode))
        kind = sp.value
        with self._lock:
            old = self._treadmill
            if (old is not None and mode == self._treadmill_mode and getattr(old, "sport", "") == kind
                    and _same(old, device) and _is_up(old)):
                log.info("%s (%s): already connected, keeping the connection", kind, mode)
                return old
            if mode == "ftms" and self.qz_running():     # refused before the old one is dropped: it stays
                raise DeviceError(t("devices.bike.close_apps"))
            self.disconnect_treadmill()
            log.info("%s (%s): connecting", kind, mode)
            erg: Any
            if mode in ("ftms", "pm5"):
                device = device or self.last_device(kind) or self._first(self.scan_ergometers(sp), kind)
                if isinstance(old, (FtmsTreadmill, Pm5Ergometer)):
                    self._settle()
                cls = FtmsBike if mode == "ftms" else Pm5Ergometer
                erg = cls(self._bus, client_cls=self._client_cls, scanner_cls=self._scanner_cls)
                try:
                    erg.connect(device)
                except Exception as exc:
                    log.info("%s (%s): connect failed (%s)", kind, mode, type(exc).__name__)
                    raise
                self._store(kind, device)
            elif mode == "manual":
                erg = ManualErgometer(self._bus, sport=kind)
                erg.connect()
            else:
                cls = SimBike if sp is Sport.BIKE else SimSkiErg
                erg = cls(self._bus, clock=self._sim_clock, noise_w=2.0)
                erg.connect()
            self._treadmill, self._treadmill_mode = erg, mode
            log.info("%s (%s): connected", kind, mode)
            if isinstance(self._heart_rate, SimHeartRate):
                self._heart_rate.follow(erg if mode == "sim" else None)
            return erg

    def disconnect_treadmill(self) -> None:
        with self._lock:
            tm, mode = self._treadmill, self._treadmill_mode
            self._treadmill, self._treadmill_mode = None, None
            if isinstance(self._heart_rate, QzHeartRate):
                self.disconnect_heart_rate()
            # Inside the lock: a connect that waits for it starts only when the old link is gone.
            if tm is not None:
                _safe_disconnect(tm)
                log.info("treadmill (%s): disconnected", mode)

    # --- heart rate --------------------------------------------------------------------------------
    def connect_heart_rate(self, device: DeviceInfo | str | None = None) -> Any:
        """Connect a strap (DeviceInfo), ``"sim"`` or ``"qz"``; None uses the remembered or nearest one."""
        with self._lock:
            old = self._heart_rate
            if old is not None and _same(old, device) and _is_up(old):
                log.info("heart rate (%s): already connected, keeping the connection", _hr_kind(old))
                return old
            if old is not None:
                log.info("heart rate (%s): disconnecting before a new connection", _hr_kind(old))
            self.disconnect_heart_rate()
            hr: Any
            log.info("heart rate: connecting")
            if device == "sim" or (isinstance(device, DeviceInfo) and device.address == "sim"):
                tm = self._treadmill if isinstance(self._treadmill, (SimTreadmill, SimBike, SimSkiErg)) else None
                hr = SimHeartRate(self._bus, treadmill=tm, clock=self._sim_clock)
                hr.athlete.noise_bpm = 1.0
                hr.connect()
            elif device == "qz" or (isinstance(device, DeviceInfo) and device.address == "qz"):
                if not isinstance(self._treadmill, QzTreadmill):
                    raise DeviceError(t("devices.qz.hr_needs_qz"))
                hr = QzHeartRate(self._treadmill, self._bus)
                hr.connect()
            else:
                info = device if isinstance(device, DeviceInfo) else (
                    self.last_device("heart_rate") or self._first(self.scan_heart_rate(), "heart_rate"))
                if isinstance(old, BleHeartRate):
                    self._settle()
                hr = BleHeartRate(self._bus, client_cls=self._client_cls, scanner_cls=self._scanner_cls)
                try:
                    hr.connect(info)
                except Exception as exc:
                    log.info("heart rate (ble): connect failed (%s)", type(exc).__name__)
                    raise
                self._store("heart_rate", info)
            self._heart_rate = hr
            log.info("heart rate (%s): connected", _hr_kind(hr))
            return hr

    def disconnect_heart_rate(self) -> None:
        with self._lock:
            hr, self._heart_rate = self._heart_rate, None
            if hr is not None:
                _safe_disconnect(hr)
                log.info("heart rate (%s): disconnected", _hr_kind(hr))

    def disconnect_all(self) -> None:
        """Disconnect both devices and stop their threads. Safe to call at any time, also twice."""
        self.disconnect_heart_rate()
        self.disconnect_treadmill()

    def close(self) -> None:
        """Disconnect everything and stop listening on the bus."""
        self.disconnect_all()
        self._unsub()

    # --- helpers --------------------------------------------------------------------------------
    def _first(self, found: list[DeviceInfo], kind: str) -> DeviceInfo:
        if not found:
            if kind == "treadmill":
                raise DeviceError(t("devices.error.no_treadmill_found") + " " + t("devices.ftms.close_qz"))
            if kind == "bike":
                raise DeviceError(t("devices.bike.none_found") + " " + t("devices.bike.close_apps"))
            if kind == "skierg":
                raise DeviceError(t("devices.pm5.none_found") + " " + t("devices.pm5.close_apps"))
            raise DeviceError(t("devices.hr.none_found"))
        return found[0]

    def _settle(self) -> None:
        """Windows needs a moment after a Bluetooth disconnect before the same device connects again."""
        if self._settle_s > 0:
            time.sleep(self._settle_s)

    def _store(self, kind: str, info: DeviceInfo) -> None:
        if self._remember is None:
            return
        try:
            self._remember(kind, info)
        except Exception:  # noqa: BLE001
            log.exception("remember(%s) failed", kind)


def _same(dev: Any, wanted: DeviceInfo | str | None) -> bool:
    """True when ``wanted`` names the device ``dev`` already talks to (None: the default one)."""
    have = getattr(dev, "_device", None)
    if wanted is None:
        return True
    if isinstance(wanted, str):
        return (wanted == "sim" and isinstance(dev, SimHeartRate)) or (wanted == "qz" and isinstance(dev, QzHeartRate))
    if wanted.address in ("sim", "qz"):
        return _same(dev, wanted.address)
    return have is not None and getattr(have, "address", None) == wanted.address


def _is_up(dev: Any) -> bool:
    """Connected right now; a device that is reconnecting after a loss is connected again from scratch."""
    up = getattr(dev, "connected", None)
    return True if up is None else bool(up)


def _hr_kind(hr: Any) -> str:
    return {SimHeartRate: "sim", QzHeartRate: "qz", BleHeartRate: "ble"}.get(type(hr), type(hr).__name__)


def _safe_disconnect(dev: Any) -> None:
    try:
        dev.disconnect()
    except Exception:  # noqa: BLE001 - disconnect must never raise into the UI
        log.exception("disconnect failed")
