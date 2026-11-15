"""Manual treadmill: no connection. The operator sets the belt by hand when the app asks.

``controllable`` is False, so the test runner shows "Sätt bandet på 12,0 km/h" and waits for the
operator. The state then reports the targets with ``measured=False``: they are what the belt
should do, not readings.
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, TOPIC_TREADMILL, Bus
from lopanalys.core.i18n import t
from lopanalys.devices._runtime import Clock
from lopanalys.devices.base import ConnState, DeviceInfo, DeviceStatus, ErgometerState, TreadmillState


class ManualTreadmill:
    """A treadmill the operator sets by hand. Distance is integrated from the targets."""

    controllable = False

    def __init__(self, bus: Bus | None = None, *, clock: Clock = time.time, min_speed_kmh: float = 0.5,
                 max_speed_kmh: float = 25.0, max_incline_pct: float = 20.0) -> None:
        self._bus = bus
        self._clock = clock
        self.min_speed_kmh = min_speed_kmh
        self.max_speed_kmh = max_speed_kmh
        self.max_incline_pct = max_incline_pct
        self.min_incline_pct = 0.0
        self._lock = threading.Lock()
        self._speed = 0.0
        self._incline = 0.0
        self._running = False
        self._distance_m = 0.0
        self._since: float | None = None
        self._state: TreadmillState | None = None

    @property
    def name(self) -> str:
        return t("devices.manual.name")

    @property
    def connected(self) -> bool:
        return self._state is not None

    @property
    def state(self) -> TreadmillState | None:
        with self._lock:
            if self._state is None:
                return None
            self._integrate()
            return replace(self._state, t=self._clock(), distance_m=self._distance_m)

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        return [DeviceInfo(address="manual", name=self.name)]

    def connect(self, device: DeviceInfo | None = None) -> None:
        self._publish(running=False)
        if self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("treadmill", ConnState.CONNECTED, self.name,
                                                                t("devices.manual.status")))

    def disconnect(self) -> None:
        with self._lock:
            was = self._state is not None
            self._state = None
        if was and self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("treadmill", ConnState.DISCONNECTED, self.name))

    def set_target(self, speed_kmh: float, incline_pct: float) -> None:
        """Record what the operator was asked to set."""
        with self._lock:
            self._integrate()
            self._speed = 0.0 if speed_kmh <= 0 else min(max(speed_kmh, self.min_speed_kmh), self.max_speed_kmh)
            self._incline = min(max(incline_pct, self.min_incline_pct), self.max_incline_pct)
        self._publish()

    def start(self) -> None:
        self._publish(running=True)

    def stop(self) -> None:
        self._publish(running=False)

    def _integrate(self) -> None:
        now = self._clock()
        if self._since is not None and self._running:
            self._distance_m += self._speed / 3.6 * max(0.0, now - self._since)
        self._since = now

    def _publish(self, running: bool | None = None) -> None:
        with self._lock:
            self._integrate()
            if running is not None:
                self._running = running
            speed = self._speed if self._running else 0.0
            st = TreadmillState(t=self._clock(), speed_kmh=speed, incline_pct=self._incline,
                                distance_m=self._distance_m, running=self._running,
                                target_speed_kmh=self._speed, target_incline_pct=self._incline, measured=False)
            self._state = st
        if self._bus is not None:
            self._bus.publish(TOPIC_TREADMILL, st)


class ManualErgometer(ManualTreadmill):
    """A trainer or a SkiErg without a connection: the screen shows the watt and the operator
    confirms that the athlete holds it. The state reports the targets with ``measured=False``."""

    measured = False

    def __init__(self, bus: Bus | None = None, *, sport: str = "bike", clock: Clock = time.time) -> None:
        super().__init__(bus, clock=clock, min_speed_kmh=0.0, max_speed_kmh=2000.0, max_incline_pct=0.0)
        self.sport = sport

    @property
    def name(self) -> str:
        return t("devices.manual.name")

    def connect(self, device: DeviceInfo | None = None) -> None:
        self._publish(running=False)
        if self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("treadmill", ConnState.CONNECTED, self.name,
                                                                t(f"devices.manual.status_{self.sport}")))

    def _integrate(self) -> None:
        self._since = self._clock()         # watt has no distance

    def _publish(self, running: bool | None = None) -> None:
        with self._lock:
            if running is not None:
                self._running = running
            load = self._speed if self._running else 0.0
            st: TreadmillState = ErgometerState(t=self._clock(), speed_kmh=load, incline_pct=0.0, running=self._running,
                                                target_speed_kmh=self._speed or None, measured=False, power_w=None)
            self._state = st
        if self._bus is not None:
            self._bus.publish(TOPIC_TREADMILL, st)
