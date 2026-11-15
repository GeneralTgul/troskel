"""Simulated treadmill and heart rate for tests and the demo mode.

``SimTreadmill`` behaves like a real belt: it accelerates about 0.5 km/h per second, brakes about
1 km/h per second and changes incline 0.1 % per second. ``SimHeartRate`` is a small physiological
model: heart rate follows the running demand (speed plus incline, as in the ACSM running equation)
with first-order kinetics (faster up than down), cardiac drift during long work, a slow component
above threshold, and optionally a soft plateau at max HR. With a fast ``SimClock`` a full lactate
test or max HR test runs in seconds.

``SimBike`` is a trainer: in ERG the power follows the target with a couple of seconds' lag, the
cadence lies around 85, and below cadence 50 the power falls. Without a target (``set_free``) the
rider holds ``rider_power_w``. ``SimSkiErg`` cannot be set: its skier follows the target that was
shown, a little off, with strokes at 30-50 per minute and the pace from Concept2's formula. Both
stop when the target passes ``max_power_w`` (the athlete is exhausted), so a ramp ends by itself.
With one of them the heart rate follows the power instead of the speed.

Clocks: every device takes ``clock`` (a callable returning seconds). ``SimClock(scale)`` runs
``scale`` times faster than real time; ``ManualClock`` only moves when ``advance()`` is called,
for deterministic tests: create the devices with ``autorun=False`` and call ``tick()``.
"""
from __future__ import annotations

import math
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from lopanalys.core.bus import (TOPIC_DEVICE_STATUS, TOPIC_HEART_RATE, TOPIC_LOG, TOPIC_STROKE, TOPIC_TREADMILL,
                                Bus)
from lopanalys.core.i18n import t
from lopanalys.devices.base import (ConnState, DeviceInfo, DeviceStatus, ErgometerState, HeartRateSample,
                                    StrokeSample, TreadmillState)

Clock = Callable[[], float]


class SimClock:
    """Wall clock running ``scale`` times faster than real time. Call it to read the time."""

    def __init__(self, scale: float = 1.0, start: float | None = None) -> None:
        self.scale = scale
        self._t0 = time.time() if start is None else start
        self._m0 = time.perf_counter()

    def __call__(self) -> float:
        return self._t0 + (time.perf_counter() - self._m0) * self.scale

    def sleep(self, sim_seconds: float) -> None:
        """Sleep for a duration given in simulated seconds."""
        time.sleep(max(0.0, sim_seconds) / self.scale)


class ManualClock:
    """A clock that only moves on ``advance()``; for deterministic tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _Ticker:
    """Calls ``tick()`` from a daemon thread every ``period_s`` real seconds."""

    def __init__(self, fn: Callable[[], None], period_s: float, name: str) -> None:
        self._fn = fn
        self._period = period_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._period):
            self._fn()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(2.0)


class SimTreadmill:
    """A simulated, controllable treadmill (``devices.base.Treadmill``)."""

    controllable = True

    def __init__(self, bus: Bus | None = None, *, clock: Clock | None = None, time_scale: float = 1.0,
                 accel_kmh_s: float = 0.5, decel_kmh_s: float = 1.0, incline_rate_pct_s: float = 0.1,
                 min_speed_kmh: float = 1.0, max_speed_kmh: float = 18.0, max_incline_pct: float = 12.0,
                 noise_kmh: float = 0.0, publish_hz: float = 2.0, autorun: bool = True,
                 tick_s: float = 0.05, seed: int | None = None) -> None:
        self._bus = bus
        self.clock: Clock = clock or SimClock(time_scale)
        self.accel = accel_kmh_s
        self.decel = decel_kmh_s
        self.incline_rate = incline_rate_pct_s
        self.min_speed_kmh = min_speed_kmh
        self.max_speed_kmh = max_speed_kmh
        self.max_incline_pct = max_incline_pct
        self.min_incline_pct = 0.0
        self.noise = noise_kmh
        self._publish_period = 1.0 / publish_hz
        self._autorun = autorun
        self._tick_s = tick_s
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._speed = 0.0
        self._incline = 0.0
        self._target_speed = 0.0
        self._target_incline = 0.0
        self._running = False
        self._distance = 0.0
        self._last_t: float | None = None
        self._last_pub = -math.inf
        self._state: TreadmillState | None = None
        self._ticker: _Ticker | None = None

    @property
    def name(self) -> str:
        return t("devices.sim.treadmill_name")

    @property
    def connected(self) -> bool:
        return self._last_t is not None

    @property
    def state(self) -> TreadmillState | None:
        with self._lock:
            return self._state

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        return [DeviceInfo(address="sim", name=self.name)]

    def connect(self, device: DeviceInfo | None = None) -> None:
        self._last_t = self.clock()
        self._publish(force=True)
        if self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("treadmill", ConnState.CONNECTED, self.name))
        if self._autorun and self._ticker is None:
            self._ticker = _Ticker(self.tick, self._tick_s, "sim-treadmill")
            self._ticker.start()

    def disconnect(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        if self._last_t is not None and self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("treadmill", ConnState.DISCONNECTED, self.name))
        self._last_t = None

    def set_target(self, speed_kmh: float, incline_pct: float) -> None:
        with self._lock:
            self._target_speed = 0.0 if speed_kmh <= 0 else min(max(speed_kmh, self.min_speed_kmh),
                                                                 self.max_speed_kmh)
            self._target_incline = min(max(incline_pct, self.min_incline_pct), self.max_incline_pct)
        self._publish(force=True)

    def start(self) -> None:
        with self._lock:
            self._running = True
        self._publish(force=True)

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._publish(force=True)

    def tick(self) -> None:
        """Advance the physics to ``clock()`` and publish if a publish period has passed."""
        if self._last_t is None:
            return
        now = self.clock()
        with self._lock:
            dt = max(0.0, now - self._last_t)
            self._last_t = now
            goal = self._target_speed if self._running else 0.0
            if goal > 0 and self._speed < self.min_speed_kmh and self._running:
                self._speed = max(self._speed, min(self.min_speed_kmh, goal))
            self._speed = _approach(self._speed, goal, self.accel * dt, self.decel * dt)
            self._incline = _approach(self._incline, self._target_incline, self.incline_rate * dt,
                                      self.incline_rate * dt)
            self._distance += self._speed / 3.6 * dt
        self._publish()

    def _publish(self, force: bool = False) -> None:
        now = self.clock()
        with self._lock:
            if not force and now - self._last_pub < self._publish_period:
                return
            self._last_pub = now
            measured = self._speed
            if self.noise > 0 and measured > 0:
                measured = max(0.0, measured + self._rng.gauss(0.0, self.noise))
            st = TreadmillState(t=now, speed_kmh=round(measured, 2), incline_pct=round(self._incline, 1),
                                distance_m=self._distance, running=self._running and self._speed > 0.05,
                                target_speed_kmh=self._target_speed, target_incline_pct=self._target_incline)
            self._state = st
        if self._bus is not None:
            self._bus.publish(TOPIC_TREADMILL, st)


class _SimPower:
    """What the simulated trainer and SkiErg share: clock, ticker, target and publishing."""

    sport = ""
    controllable = True
    measured = True
    min_speed_kmh = 0.0            # the load limits, in watt
    max_speed_kmh = 2000.0
    min_incline_pct = 0.0
    max_incline_pct = 0.0
    name_key = ""

    def __init__(self, bus: Bus | None = None, *, clock: Clock | None = None, time_scale: float = 1.0,
                 noise_w: float = 0.0, publish_hz: float = 2.0, autorun: bool = True, tick_s: float = 0.05,
                 seed: int | None = None, max_power_w: float | None = None) -> None:
        self._bus = bus
        self.clock: Clock = clock or SimClock(time_scale)
        self.noise = noise_w
        self.max_power_w = max_power_w          # the athlete gives up when the target passes this
        self._publish_period = 1.0 / publish_hz
        self._autorun = autorun
        self._tick_s = tick_s
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._power = 0.0
        self._target = 0.0
        self._running = False
        self._gave_up = False
        self._last_t: float | None = None
        self._last_pub = -math.inf
        self._state: ErgometerState | None = None
        self._ticker: _Ticker | None = None

    @property
    def name(self) -> str:
        return t(self.name_key)

    @property
    def connected(self) -> bool:
        return self._last_t is not None

    @property
    def state(self) -> ErgometerState | None:
        with self._lock:
            return self._state

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        return [DeviceInfo(address="sim", name=self.name)]

    def connect(self, device: DeviceInfo | None = None) -> None:
        self._last_t = self.clock()
        self._publish(force=True)
        if self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("treadmill", ConnState.CONNECTED, self.name))
        if self._autorun and self._ticker is None:
            self._ticker = _Ticker(self.tick, self._tick_s, f"sim-{self.sport}")
            self._ticker.start()

    def disconnect(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        if self._last_t is not None and self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("treadmill", ConnState.DISCONNECTED, self.name))
        self._last_t = None

    def set_target(self, load: float, incline_pct: float = 0.0) -> None:
        with self._lock:
            self._target = 0.0 if load <= 0 else min(max(load, self.min_speed_kmh), self.max_speed_kmh)
            self._on_target()
        self._publish(force=True)

    def _on_target(self) -> None:
        pass

    def start(self) -> None:
        with self._lock:
            self._running = True
        self._publish(force=True)

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._publish(force=True)

    def give_up(self) -> None:
        """The athlete stops (tests): the power falls to 0 until the next ``start()``."""
        with self._lock:
            self._gave_up = True

    def tick(self) -> None:
        if self._last_t is None:
            return
        now = self.clock()
        with self._lock:
            dt = max(0.0, now - self._last_t)
            self._last_t = now
            if self.max_power_w and self._target > self.max_power_w:
                self._gave_up = True
            self._advance(dt, now)
        self._publish()

    def _advance(self, dt: float, now: float) -> None:
        raise NotImplementedError

    def _fields(self) -> dict[str, object]:
        return {}

    def _publish(self, force: bool = False) -> None:
        now = self.clock()
        with self._lock:
            if not force and now - self._last_pub < self._publish_period:
                return
            self._last_pub = now
            power = self._power
            if self.noise > 0 and power > 0:
                power = max(0.0, power + self._rng.gauss(0.0, self.noise))
            st = ErgometerState(t=now, speed_kmh=round(power, 1), incline_pct=0.0, running=power > 1.0,
                                target_speed_kmh=self._target or None, power_w=round(power, 1), **self._fields())
            self._state = st
        if self._bus is not None:
            self._bus.publish(TOPIC_TREADMILL, st)


class SimBike(_SimPower):
    """A simulated trainer in ERG mode (``devices.base.Ergometer``)."""

    sport = "bike"
    name_key = "devices.sim.bike_name"

    def __init__(self, bus: Bus | None = None, *, cadence_rpm: float = 85.0, rider_power_w: float = 240.0,
                 lag_s: float = 2.0, **kw: object) -> None:
        super().__init__(bus, **kw)  # type: ignore[arg-type]
        self.cadence_rpm = cadence_rpm       # what the rider holds; set it below 50 to see ERG fail
        self.rider_power_w = rider_power_w   # what the rider holds without a power target
        self.lag_s = lag_s
        self._free = False
        self._cadence = 0.0

    def set_free(self, grade_pct: float = 0.0) -> None:
        with self._lock:
            self._free = True
            self._target = 0.0
        self._publish(force=True)

    def _on_target(self) -> None:
        self._free = False

    def _advance(self, dt: float, now: float) -> None:
        pedalling = self._running and not self._gave_up
        cad = self.cadence_rpm if pedalling else 0.0
        self._cadence += (cad - self._cadence) * (1.0 - math.exp(-dt / 1.5))
        goal = (self.rider_power_w if self._free else self._target) if pedalling else 0.0
        if not self._free and 0 < self._cadence < 50.0:      # ERG below cadence 50: the power falls
            goal *= self._cadence / 50.0 * 0.8
        self._power += (goal - self._power) * (1.0 - math.exp(-dt / max(self.lag_s, 1e-3)))
        if self._power < 0.5 and goal == 0:
            self._power = 0.0

    def _fields(self) -> dict[str, object]:
        cad = self._cadence
        if self.noise > 0 and cad > 1:
            cad += self._rng.gauss(0.0, 1.0)
        return {"cadence": round(cad) if cad > 1 else 0.0, "free": self._free,
                "low_cadence": bool(not self._free and self._target and 1 < cad < 50.0),
                "wheel_speed_kmh": round(3.6 * (self._power / 0.2) ** (1 / 3), 1) if self._power > 0 else 0.0}


class SimSkiErg(_SimPower):
    """A simulated SkiErg with a PM5: measured, not controllable. The skier follows the target shown."""

    sport = "skierg"
    controllable = False
    name_key = "devices.sim.skierg_name"
    max_speed_kmh = 1000.0

    def __init__(self, bus: Bus | None = None, *, follow: float = 0.98, lag_s: float = 4.0,
                 **kw: object) -> None:
        super().__init__(bus, **kw)  # type: ignore[arg-type]
        self.follow = follow         # the power the skier holds, as a share of the target
        self.lag_s = lag_s
        self.program_ok = True       # False: the monitor refuses the workout (tests)
        self.workout: list[Any] = []  # the intervals a test put on the "monitor"
        self.programmed: bool | None = None
        self._next_stroke: float | None = None
        self._strokes = 0
        self._rate = 0.0
        self._drive = 0.0
        self._recovery = 0.0

    def program_workout(self, intervals: list[Any]) -> None:
        """As Pm5Ergometer.program_workout: the stages as a workout on the monitor, or a refusal."""
        self.programmed = bool(self.program_ok and intervals)
        self.workout = list(intervals) if self.programmed else []
        if self._bus is not None:
            self._bus.publish(TOPIC_LOG, t("devices.pm5.programmed" if self.programmed else "devices.pm5.program_failed"))

    def _advance(self, dt: float, now: float) -> None:
        pulling = self._running and not self._gave_up and self._target > 0
        goal = self._target * self.follow if pulling else 0.0
        self._power += (goal - self._power) * (1.0 - math.exp(-dt / max(self.lag_s, 1e-3)))
        if self._power < 1.0 and goal == 0:
            self._power, self._rate, self._next_stroke = 0.0, 0.0, None
            return
        self._rate = min(55.0, 28.0 + self._power / 12.0)      # 100 W: 36 strokes/min, 250 W: 49
        period = 60.0 / self._rate
        if self._next_stroke is None:
            self._next_stroke = now + period
        while now >= self._next_stroke:
            self._next_stroke += period
            self._strokes += 1
            self._drive, self._recovery = round(period * 0.4, 2), round(period * 0.6, 2)
            if self._bus is not None:
                self._bus.publish(TOPIC_STROKE, StrokeSample(now, self._strokes, self._drive, self._recovery, 1.1,
                                                             round(self._power, 1)))

    def _fields(self) -> dict[str, object]:
        p = self._power
        return {"cadence": round(self._rate) if p > 1 else 0.0,
                "pace_500m_s": round(500.0 * (2.80 / p) ** (1 / 3), 1) if p > 1 else None,
                "drive_time_s": self._drive or None, "recovery_time_s": self._recovery or None,
                "stroke_count": self._strokes, "drag_factor": 95}


def _approach(value: float, goal: float, up: float, down: float) -> float:
    if value < goal:
        return min(goal, value + up)
    return max(goal, value - down)


@dataclass
class SimAthlete:
    """Parameters of the simulated runner."""
    resting_hr: float = 55.0
    max_hr: float = 192.0
    vmax_kmh: float = 18.0          # running demand (flat-equivalent km/h) that would need max HR
    threshold_frac: float = 0.85    # fraction of HR reserve at LT2; above it the slow component grows
    tau_up_s: float = 25.0
    tau_down_s: float = 45.0
    pmax_bike_w: float = 400.0      # cycling power that would need max HR
    pmax_skierg_w: float = 270.0    # the same for double poling (less muscle at work)
    drift_bpm_per_min: float = 0.25  # cardiac drift at threshold, scaled by intensity above 50 %
    slow_bpm_per_min: float = 2.5    # extra rise per minute above threshold (slow component)
    plateau: bool = True            # soft HR max plateau (levels off before max)
    noise_bpm: float = 0.0


class SimHeartRate:
    """Simulated heart rate strap (``devices.base.HeartRateMonitor``) driven by a treadmill.

    The treadmill's state is read from ``treadmill`` if given, else from ``TOPIC_TREADMILL``.
    """

    def __init__(self, bus: Bus | None = None, *, treadmill: Any = None,
                 athlete: SimAthlete | None = None, clock: Clock | None = None, time_scale: float = 1.0,
                 publish_hz: float = 1.0, autorun: bool = True, tick_s: float = 0.05,
                 seed: int | None = None, rsc: bool | None = None) -> None:
        self._bus = bus
        self._treadmill = treadmill
        # running cadence like a strap with RSC (Garmin HRM-Pro); None: env LOPANALYS_SIM_RSC=1
        self.rsc = rsc if rsc is not None else os.environ.get("LOPANALYS_SIM_RSC") == "1"
        self.athlete = athlete or SimAthlete()
        self.clock: Clock = clock or (treadmill.clock if treadmill is not None else SimClock(time_scale))
        self._publish_period = 1.0 / publish_hz
        self._autorun = autorun
        self._tick_s = tick_s
        self._rng = random.Random(seed)
        self._hr = self.athlete.resting_hr
        self._drift = 0.0
        self._speed = 0.0
        self._incline = 0.0
        self._on_power = False            # driven by a trainer or a SkiErg: no running cadence
        self._last_t: float | None = None
        self._last_pub = -math.inf
        self._last: HeartRateSample | None = None
        self._unsub: Callable[[], None] | None = None
        self._ticker: _Ticker | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return t("devices.sim.hr_name")

    @property
    def connected(self) -> bool:
        return self._last_t is not None

    @property
    def last(self) -> HeartRateSample | None:
        return self._last

    @property
    def true_hr(self) -> float:
        """The model's heart rate without noise (for tests)."""
        return self._hr + self._drift

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        return [DeviceInfo(address="sim", name=self.name)]

    def connect(self, device: DeviceInfo | None = None) -> None:
        self._last_t = self.clock()
        if self._treadmill is None and self._bus is not None and self._unsub is None:
            self._unsub = self._bus.subscribe(TOPIC_TREADMILL, self._on_treadmill)
        if self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("heart_rate", ConnState.CONNECTED, self.name,
                                                                battery_pct=100))
        if self._autorun and self._ticker is None:
            self._ticker = _Ticker(self.tick, self._tick_s, "sim-hr")
            self._ticker.start()

    def disconnect(self) -> None:
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._last_t is not None and self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus("heart_rate", ConnState.DISCONNECTED, self.name))
        self._last_t = None

    def follow(self, treadmill: Any) -> None:
        """Read speed and incline from this treadmill directly (None: from the bus)."""
        self._treadmill = treadmill
        if treadmill is None and self._bus is not None and self._unsub is None and self.connected:
            self._unsub = self._bus.subscribe(TOPIC_TREADMILL, self._on_treadmill)

    def _on_treadmill(self, st: TreadmillState) -> None:
        power = getattr(st, "power_w", None)
        if power is not None:
            # a trainer or a SkiErg: the demand follows the power, as the running speed that asks as much
            a = self.athlete
            pmax = a.pmax_skierg_w if getattr(st, "pace_500m_s", None) is not None or \
                getattr(st, "stroke_count", None) is not None else a.pmax_bike_w
            self._on_power = True
            self._speed = a.vmax_kmh * max(power, 0.0) / pmax
            self._incline = 0.0
            return
        self._on_power = False
        self._speed = st.speed_kmh or 0.0
        self._incline = st.incline_pct or 0.0

    def demand_frac(self, speed_kmh: float, incline_pct: float) -> float:
        """Steady-state fraction of heart rate reserve for a speed and incline."""
        if speed_kmh < 0.5:
            return 0.05
        flat_equiv = speed_kmh * (1.0 + 0.045 * max(incline_pct, -5.0))  # ACSM: 0.9*grade / 0.2
        frac = 0.2 + 0.8 * flat_equiv / self.athlete.vmax_kmh
        knee = self.athlete.threshold_frac
        if self.athlete.plateau and frac > knee:
            span = 1.0 - knee
            frac = knee + span * (1.0 - math.exp(-(frac - knee) / span))
        return max(0.0, min(frac, 1.0))

    def steady_state_hr(self, speed_kmh: float, incline_pct: float) -> float:
        a = self.athlete
        return a.resting_hr + (a.max_hr - a.resting_hr) * self.demand_frac(speed_kmh, incline_pct)

    def tick(self) -> None:
        """Advance the model to ``clock()`` and publish once per publish period."""
        if self._last_t is None:
            return
        now = self.clock()
        if self._treadmill is not None and self._treadmill.state is not None:
            self._on_treadmill(self._treadmill.state)
        with self._lock:
            dt = max(0.0, now - self._last_t)
            self._last_t = now
            # integrate in sub-steps of at most 1 s so a big jump in a fast clock stays accurate
            while dt > 0:
                h = min(dt, 1.0)
                self._step(h)
                dt -= h
            if now - self._last_pub < self._publish_period:
                return
            self._last_pub = now
            bpm = self._hr + self._drift
            if self.athlete.noise_bpm > 0:
                bpm += self._rng.gauss(0.0, self.athlete.noise_bpm)
            bpm = int(round(min(max(bpm, 30.0), self.athlete.max_hr)))
            rr = [int(round(60000.0 / bpm + self._rng.gauss(0.0, 8.0 if self.athlete.noise_bpm else 0.0)))]
            sample = HeartRateSample(t=now, bpm=bpm, rr_ms=rr, contact=True, cadence_spm=self.sim_cadence())
            self._last = sample
        if self._bus is not None:
            self._bus.publish(TOPIC_HEART_RATE, sample)

    def sim_cadence(self) -> float | None:
        """Steps/min as an RSC strap would send it (None without RSC or when not running)."""
        if not self.rsc or self._speed < 3.0 or self._on_power:
            return None
        cad = 150.0 + 2.6 * self._speed
        if self.athlete.noise_bpm > 0:
            cad += self._rng.gauss(0.0, 1.5)
        return float(round(cad))

    def _step(self, h: float) -> None:
        a = self.athlete
        target = self.steady_state_hr(self._speed, self._incline)
        tau = a.tau_up_s if target > self._hr else a.tau_down_s
        self._hr += (target - self._hr) * (1.0 - math.exp(-h / tau))
        frac = (self._hr - a.resting_hr) / max(1.0, a.max_hr - a.resting_hr)
        if frac > 0.5:
            rate = a.drift_bpm_per_min * (frac - 0.5) / 0.5
            if frac > a.threshold_frac:
                rate += a.slow_bpm_per_min * (frac - a.threshold_frac) / (1.0 - a.threshold_frac)
            self._drift += rate * h / 60.0
        else:
            self._drift *= math.exp(-h / 120.0)
        # never above max HR
        self._drift = min(self._drift, max(0.0, a.max_hr - self._hr))


def make_sim_pair(bus: Bus | None = None, *, time_scale: float = 1.0, athlete: SimAthlete | None = None,
                  noise: bool = True, seed: int | None = None,
                  rsc: bool | None = None) -> tuple[SimTreadmill, SimHeartRate]:
    """A treadmill and heart rate sharing one clock, as the demo mode uses them."""
    clock = SimClock(time_scale)
    tm = SimTreadmill(bus, clock=clock, noise_kmh=0.03 if noise else 0.0, seed=seed)
    ath = athlete or SimAthlete(noise_bpm=1.0 if noise else 0.0)
    hr = SimHeartRate(bus, treadmill=tm, athlete=ath, clock=clock, seed=seed, rsc=rsc)
    return tm, hr


__all__ = ["SimClock", "ManualClock", "SimTreadmill", "SimBike", "SimSkiErg", "SimHeartRate", "SimAthlete",
           "make_sim_pair"]
