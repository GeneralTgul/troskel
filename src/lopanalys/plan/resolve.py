"""Zone to target values: the only place in plan/ that reads the fields of ``Zone`` and ``Anchors``.

The plan stores zone codes. Heart rate and pace are looked up here, from the athlete's latest test,
every time the plan is shown, printed or exported. A retest therefore moves every target and leaves
the plan itself untouched (``retest``). Pace is given only where the test measured it: at the
thresholds and between them. Nothing is extrapolated.

A zones test without lactate (``Capacity.speed_line``) gives the estimated zones a pace from the
relation heart rate -> speed of its steps, the same way the result page does (hr_zones.zone_speeds):
a border a little outside the steps is marked ``≈``, further out it has no pace.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from lopanalys.assessments import hr_zones
from lopanalys.assessments.lactate import pace_str
from lopanalys.assessments.zone_systems import DEFAULT_SYSTEM, convert
from lopanalys.core.i18n import t

from .models import Capacity, Plan, SpeedLine

APPROX = "≈"     # before a pace outside the measured steps of a zones test (report.builder.EXTRAPOLATED_MARK)


def _field(obj: Any, *names: str) -> Any:
    """The first of the names the object has: survives the renaming speed_kmh -> load (ombyggnad, step 0b)."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


@dataclass(frozen=True)
class Target:
    zone: str
    name: str
    hr_low: int | None = None
    hr_high: int | None = None
    speed_low_kmh: float | None = None     # the slower end
    speed_high_kmh: float | None = None
    estimated: bool = False                # no thresholds were measured: the borders come from max HR
    speed_low_approx: bool = False         # the pace is followed a little outside the measured steps
    speed_high_approx: bool = False
    speed_from_hr: bool = False            # the pace comes from heart rate (a zones test), not from lactate

    @property
    def hr_text(self) -> str:
        if self.hr_low and self.hr_high:
            return t("plan.target.hr", lo=self.hr_low, hi=self.hr_high)
        if self.hr_high:
            return t("plan.target.hr_below", hi=self.hr_high)
        return t("plan.target.hr_above", lo=self.hr_low) if self.hr_low else ""

    @property
    def pace_text(self) -> str:
        lo, hi = pace_str(self.speed_low_kmh), pace_str(self.speed_high_kmh)
        lo = (APPROX if lo and self.speed_low_approx else "") + lo
        hi = (APPROX if hi and self.speed_high_approx else "") + hi
        if lo and hi:
            return t("plan.target.pace", lo=lo, hi=hi)
        if hi:
            return t("plan.target.pace_slower", v=hi)
        return t("plan.target.pace_faster", v=lo) if lo else ""

    @property
    def text(self) -> str:
        """'170–174 slag/min · 4:21–4:10 min/km', or '' when nothing is known."""
        return " · ".join(x for x in (self.hr_text, self.pace_text) if x)


def targets(capacity: Capacity | None, system: str = DEFAULT_SYSTEM) -> dict[str, Target]:
    """Every zone of the system with its targets. {} when the athlete has no test and no max HR."""
    if capacity is None or capacity.anchors is None:
        return {}
    zones = convert(capacity.anchors, system)
    line = capacity.speed_line if capacity.speed_from_hr else None
    if line is not None:
        zones = hr_zones.zone_speeds(zones, _relation(line))
    out = {}
    for z in zones:
        out[z.code] = Target(z.code, z.name, z.hr_low, z.hr_high,
                             _field(z, "speed_low_kmh", "load_low"), _field(z, "speed_high_kmh", "load_high"),
                             bool(z.estimated), bool(_field(z, "speed_low_extrapolated", "load_low_extrapolated")),
                             bool(_field(z, "speed_high_extrapolated", "load_high_extrapolated")),
                             _field(z, "speed_basis", "load_basis") == "hr")
    return out


def _relation(line: SpeedLine) -> "hr_zones.HrSpeedResult":
    return hr_zones.HrSpeedResult(slope=line.slope, intercept=line.intercept, speed_min=line.speed_min,
                                  speed_max=line.speed_max, ramp_top_kmh=line.ramp_top_kmh,
                                  extrapolate_kmh=line.extrapolate_kmh)


def speed_line(relation: Any) -> SpeedLine | None:
    """The part of an hr_zones.HrSpeedResult the plan keeps, or None when the relation is not usable."""
    if relation is None or not getattr(relation, "ok", False) or relation.speed_min is None:
        return None
    return SpeedLine(float(relation.slope), float(relation.intercept), float(relation.speed_min),
                     float(relation.speed_max), relation.ramp_top_kmh, float(relation.extrapolate_kmh))


def target(capacity: Capacity | None, zone: str | None, system: str = DEFAULT_SYSTEM) -> Target | None:
    return targets(capacity, system).get(zone) if zone else None


def retest(plan: Plan, capacity: Capacity, from_date: date | None = None) -> Plan:
    """The same plan on a new test: every zone keeps its place, every target follows the new anchors.
    Weeks and workouts are shared with the old plan and never changed here: no workout moves.

    ``from_date``: the new targets apply from that day on. Workouts before it keep the targets of
    the test they were handed out with (``Plan.capacity_on``). None: the whole plan follows the new test.
    """
    old = plan.request.capacity_for(capacity.sport)
    others = tuple(c for c in plan.request.capacity if c.sport != capacity.sport)
    log = plan.capacity_log
    covered = max((until for until, c in log if c.sport == capacity.sport), default=plan.start)
    if from_date is not None and from_date > covered:
        log += ((from_date, old),)
    return replace(plan, request=replace(plan.request, capacity=others + (capacity,)), capacity_log=log,
                   based_on={**plan.based_on, capacity.sport: capacity.tested})
