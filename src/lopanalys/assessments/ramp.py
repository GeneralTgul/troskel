"""The open-ended ramp past the treadmill's limits, and how long a ramp will take.

A fit runner in a max test on a treadmill with at most 12 % incline can reach step 12, where the
incline cannot rise any more, and the load stops growing while the runner could go on. So:

- An incline ramp that reaches the highest incline goes on with the speed instead
  (``Protocol.overflow_speed_kmh`` per step, 0.5 km/h by default).
- A speed ramp that reaches the highest speed goes on with the incline instead
  (``Protocol.overflow_incline_pct`` per step, 1 % by default).
- When both are at the top, the step stays the same and the program says so ("at_max").

:func:`next_step` is used by the runner for every generated stage; :func:`plan` gives the guide the
time from the first ramp step to the treadmill's limits and the expected length of the ramp.
No Qt imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lopanalys.assessments.protocol import Protocol, Stage
from lopanalys.assessments.vo2max import acsm_running_vo2

EPS = 1e-6
# A max test should take 8-12 min. Outside 6-12 min the guide warns.
TARGET_MIN, TARGET_MAX = 8.0, 12.0
WARN_SHORT_MIN, WARN_LONG_MIN = 6.0, 12.0
TARGET_MID_MIN = 10.0
PLAN_CAP_MIN = 40.0


def next_step(speed_kmh: float, incline_pct: float, p: Protocol, limits: Any) -> tuple[float, float, str]:
    """Speed and incline of the step after (speed, incline) in an open-ended ramp.

    Returns (speed, incline, how): how is "" (the protocol's own increment), "speed" (the incline is at
    the top, the speed rises instead), "incline" (the speed is at the top, the incline rises instead)
    or "at_max" (neither can rise: the step stays the same).
    """
    v = limits.speed(speed_kmh + p.increment_speed_kmh)
    inc = limits.incline(incline_pct + p.increment_incline_pct)
    if v > speed_kmh + EPS or inc > incline_pct + EPS:
        return v, inc, ""
    if p.increment_incline_pct > 0 and p.overflow_speed_kmh > 0:
        v2 = limits.speed(speed_kmh + p.overflow_speed_kmh)
        if v2 > speed_kmh + EPS:
            return v2, inc, "speed"
    if p.increment_speed_kmh > 0 and p.overflow_incline_pct > 0:
        i2 = limits.incline(incline_pct + p.overflow_incline_pct)
        if i2 > incline_pct + EPS:
            return v, i2, "incline"
    return v, inc, "at_max"


@dataclass
class RampPlan:
    """How a protocol's ramp meets the treadmill (minutes from the start of the first ramp step)."""
    step_s: float
    to_incline_max_min: float | None    # when the highest incline is reached (None: never in the plan)
    to_speed_max_min: float | None      # when the highest speed is reached
    to_full_max_min: float | None       # when both are at the top
    expected_min: float | None = None   # expected length of the ramp (from a previous test), else None
    warning: str = ""                   # "long" | "short" | ""


def ramp_start_index(p: Protocol) -> int | None:
    """Index of the first ramp step: the last protocol stage of an open-ended protocol, or the first
    work stage of a max test (where every work stage is a ramp step)."""
    if not p.open_ended or not p.stages:
        return None
    work = [i for i, s in enumerate(p.stages) if not s.is_warmup]
    if not work:
        return None
    if p.builder == "max_hr_ramp" or not any(s.sample_lactate for s in p.stages):
        return work[0]
    return len(p.stages) - 1


def steps(p: Protocol, limits: Any, cap_min: float = PLAN_CAP_MIN) -> list[Stage]:
    """The ramp steps as the runner will generate them, from the first ramp step, up to `cap_min`."""
    i0 = ramp_start_index(p)
    if i0 is None:
        return []
    first = p.stages[i0]
    out = [Stage(limits.speed(first.speed_kmh), limits.incline(first.incline_pct), first.duration_s, first.label)]
    out += [Stage(limits.speed(s.speed_kmh), limits.incline(s.incline_pct), s.duration_s, s.label)
            for s in p.stages[i0 + 1:]]
    t = sum(s.duration_s for s in out)
    while t < cap_min * 60:
        last = out[-1]
        v, inc, how = next_step(last.speed_kmh, last.incline_pct, p, limits)
        if how == "at_max":
            break
        out.append(Stage(v, inc, last.duration_s))
        t += last.duration_s
    return out


def plan(p: Protocol, limits: Any, target_vo2: float | None = None) -> RampPlan | None:
    """Time to the treadmill's limits and, with `target_vo2` (ml/kg/min, e.g. the VO2max estimated in a
    previous test), the expected length of the ramp: the end of the first step whose oxygen cost
    (ACSM running equation) reaches `target_vo2`. Warns when that is over 12 or under 6 minutes."""
    st = steps(p, limits)
    if not st:
        return None
    to_inc = to_v = to_full = None
    t = 0.0
    expected = None
    for s in st:
        at_inc = s.incline_pct >= limits.max_incline_pct - EPS
        at_v = s.speed_kmh >= limits.max_speed_kmh - EPS
        if at_inc and to_inc is None:
            to_inc = t / 60
        if at_v and to_v is None:
            to_v = t / 60
        if at_inc and at_v and to_full is None:
            to_full = t / 60
        if target_vo2 and expected is None and acsm_running_vo2(s.speed_kmh, s.incline_pct) >= target_vo2:
            expected = (t + s.duration_s) / 60
        t += s.duration_s
    if target_vo2 and expected is None:
        expected = t / 60 if to_full is not None else None   # the belt's top does not reach it
        if expected is None:
            expected = PLAN_CAP_MIN
    warning = ""
    if expected is not None:
        if expected > WARN_LONG_MIN + EPS:
            warning = "long"
        elif expected < WARN_SHORT_MIN - EPS:
            warning = "short"
    return RampPlan(st[0].duration_s, _r(to_inc), _r(to_v), _r(to_full),
                    _r(expected), warning)


def _r(v: float | None) -> float | None:
    return round(v, 1) if v is not None else None


def suggest_speed(build: Any, target_vo2: float, limits: Any, lo: float = 6.0) -> float | None:
    """Start speed (0.5 km/h steps) whose ramp is expected to last closest to 10 min.

    `build(speed) -> Protocol` builds the protocol with that start speed (the guide's builder).
    """
    best: tuple[float, float] | None = None
    v = lo
    while v <= limits.max_speed_kmh + EPS:
        pl = plan(build(v), limits, target_vo2)
        if pl is not None and pl.expected_min is not None:
            d = abs(pl.expected_min - TARGET_MID_MIN)
            if best is None or d < best[0] - EPS:
                best = (d, v)
        v += 0.5
    return best[1] if best else None


def narrower(a: Any, b: Any) -> Any:
    """The tighter of two TreadmillLimits: the device's own and the coach's setting for the belt.

    QZ cannot report a treadmill's range (it assumes 20 km/h and 15 %), so on an 18 km/h, 12 % belt
    the program commanded inclines the belt never reached. A limit of 0 in `b` means "not set".
    """
    from lopanalys.assessments.library import TreadmillLimits
    return TreadmillLimits(
        min_speed_kmh=max(a.min_speed_kmh, b.min_speed_kmh or 0.0),
        max_speed_kmh=min(a.max_speed_kmh, b.max_speed_kmh) if b.max_speed_kmh else a.max_speed_kmh,
        max_incline_pct=min(a.max_incline_pct, b.max_incline_pct) if b.max_incline_pct else a.max_incline_pct,
        min_incline_pct=max(a.min_incline_pct, b.min_incline_pct or 0.0),
    )
