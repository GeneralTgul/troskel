"""Fake clock, treadmill and heart-rate strap for runner tests (no hardware, no real time)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from lopanalys.devices.base import HeartRateSample, TreadmillState


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


@dataclass
class FakeTreadmill:
    controllable: bool = True
    min_speed_kmh: float = 1.0
    max_speed_kmh: float = 18.0
    max_incline_pct: float = 12.0
    fail_on_set: bool = False
    log: list[tuple] = field(default_factory=list)
    speed: float = 0.0
    incline: float = 0.0
    running: bool = False

    def scan(self, timeout_s: float = 8.0):
        return []

    def connect(self, device) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def set_target(self, speed_kmh: float, incline_pct: float) -> None:
        if self.fail_on_set:
            raise ConnectionError("BLE lost")
        self.log.append(("set", speed_kmh, incline_pct))
        self.speed, self.incline = speed_kmh, incline_pct

    def start(self) -> None:
        self.log.append(("start",))
        self.running = True

    def stop(self) -> None:
        self.log.append(("stop",))
        self.running = False
        self.speed = 0.0

    @property
    def state(self) -> TreadmillState | None:
        if not self.controllable:
            return None
        return TreadmillState(t=0, speed_kmh=self.speed, incline_pct=self.incline, running=self.running)


class FakeHR:
    """HR as a function of the current belt speed/incline, with first-order lag towards the target."""

    def __init__(self, clock: FakeClock, belt: Callable[[], tuple[float, float]], max_hr: int = 192,
                 rest: int = 60) -> None:
        self.clock, self.belt, self.max_hr, self.rest = clock, belt, max_hr, rest
        self.hr = float(rest)
        self._t = clock.now()

    def _target(self) -> float:
        v, inc = self.belt()
        if v <= 0:
            return self.rest + 20
        # Simple steady-state model: ~7 bpm per km/h, ~4 bpm per % incline, capped at max.
        return min(self.max_hr, 60 + 7.5 * v + 4.0 * inc)

    @property
    def last(self) -> HeartRateSample:
        now = self.clock.now()
        dt = now - self._t
        self._t = now
        if dt > 0:
            k = min(1.0, dt / 25.0)
            self.hr += (self._target() - self.hr) * k
        return HeartRateSample(t=now, bpm=int(round(self.hr)))


def run(runner, clock: FakeClock, seconds: float, dt: float = 0.25) -> None:
    """Advance the fake clock and tick the runner."""
    n = int(round(seconds / dt))
    for _ in range(n):
        clock.t += dt
        runner.tick()


def run_until(runner, clock: FakeClock, pred, max_s: float = 7200, dt: float = 0.25) -> float:
    start = clock.t
    while not pred():
        if clock.t - start > max_s:
            raise AssertionError("condition not reached")
        clock.t += dt
        runner.tick()
    return clock.t - start
