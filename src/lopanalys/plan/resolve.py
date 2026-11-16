"""Zone to target values: the only place in plan/ that reads the fields of ``Zone`` and ``Anchors``.

The plan stores zone codes. Heart rate and pace are looked up here, from the athlete's latest test,
every time the plan is shown, printed or exported. A retest therefore moves every target and leaves
the plan itself untouched (``retest``). Pace is given only where the test measured it: at the
thresholds and between them. Nothing is extrapolated.

A zones test without lactate (``Capacity.speed_line``) gives the estimated zones a pace from the
relation heart rate -> speed of its steps, the same way the result page does (hr_zones.zone_speeds):
a border a little outside the steps is marked ``≈``, further out it has no pace.

Each sport gets the target of its test's load (#666, step 2): running a pace in min/km, the bike watt, and
the SkiErg watt and the time per 500 m (Concept2's formula, core.sport). Skis borrow the SkiErg's zones but
not its watt: on snow the plan gives heart rate and effort. A sport without a test gives zone and effort only.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from lopanalys.assessments import hr_zones
from lopanalys.assessments.lactate import pace_str
from lopanalys.assessments.zone_systems import DEFAULT_SYSTEM, convert
from lopanalys.core.i18n import t
from lopanalys.core.sport import Sport, profile

from .models import RUNNING, Capacity, Plan, SpeedLine

APPROX = "≈"     # before a pace outside the measured steps of a zones test (report.builder.EXTRAPOLATED_MARK)
POWER_SPORTS = (Sport.BIKE.value, Sport.SKIERG.value)   # the test's load is watt: the targets are watt
SPLIT_SPORTS = (Sport.SKIERG.value,)                    # ... and the time per 500 m


def _field(obj: Any, *names: str) -> Any:
    """The first of the names the object has: survives the renaming speed_kmh -> load (ombyggnad, step 0b)."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def split_str(watt: float | None) -> str:
    """Time per 500 m as 'm:ss' at a power on the SkiErg ('' without one)."""
    return profile(Sport.SKIERG).pace_str(watt)


_PACE = ("plan.target.pace", "plan.target.pace_slower", "plan.target.pace_faster")
_POWER = ("plan.target.power", "plan.target.power_slower", "plan.target.power_faster")
_SPLIT = ("plan.target.split", "plan.target.split_slower", "plan.target.split_faster")


def _range(lo: str, hi: str, keys: tuple[str, str, str]) -> str:
    """'lo–hi', or one end ('… or easier', '… or harder'): the texts (both ends, the easier, the harder)."""
    if lo and hi:
        return t(keys[0], lo=lo, hi=hi)
    if hi:
        return t(keys[1], v=hi)
    return t(keys[2], v=lo) if lo else ""


def _mark(text: str, approx: bool) -> str:
    return (APPROX if text and approx else "") + text


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
    # The bike and the SkiErg: watt from the test (whole watts, the easier end first), and the SkiErg's time
    # per 500 m from it. ``speed_*_approx`` and ``speed_from_hr`` mark the watt in the same way.
    power_low_w: int | None = None
    power_high_w: int | None = None
    sport: str = RUNNING                   # the workout's sport: what the load is shown as

    @property
    def hr_text(self) -> str:
        if self.hr_low and self.hr_high:
            return t("plan.target.hr", lo=self.hr_low, hi=self.hr_high)
        if self.hr_high:
            return t("plan.target.hr_below", hi=self.hr_high)
        return t("plan.target.hr_above", lo=self.hr_low) if self.hr_low else ""

    @property
    def pace_text(self) -> str:
        """Running: '4:21–4:10 min/km'. The SkiErg: '2:05–1:58 /500 m'. The bike has no pace."""
        if self.sport in SPLIT_SPORTS:
            return _range(_mark(split_str(self.power_low_w), self.speed_low_approx),
                          _mark(split_str(self.power_high_w), self.speed_high_approx), _SPLIT)
        if self.sport != RUNNING:
            return ""
        return _range(_mark(pace_str(self.speed_low_kmh), self.speed_low_approx),
                      _mark(pace_str(self.speed_high_kmh), self.speed_high_approx), _PACE)

    @property
    def power_text(self) -> str:
        """'180–215 W', or '' when the sport's load is not watt or the test gave none."""
        lo = str(self.power_low_w) if self.power_low_w else ""
        hi = str(self.power_high_w) if self.power_high_w else ""
        return _range(_mark(lo, self.speed_low_approx), _mark(hi, self.speed_high_approx), _POWER)

    @property
    def text(self) -> str:
        """'170–174 slag/min · 4:21–4:10 min/km', '… · 180–215 W' or '… · 180–215 W · 2:05–1:58 /500 m'."""
        return " · ".join(x for x in (self.hr_text, self.power_text, self.pace_text) if x)


def _watt(v: float | None) -> int | None:
    return int(round(v)) if v else None


def targets(capacity: Capacity | None, system: str = DEFAULT_SYSTEM, sport: str | None = None) -> dict[str, Target]:
    """Every zone of the system with its targets. {} when the athlete has no test and no max HR.

    ``sport``: the sport of the workouts the targets are for (None: the test's). Watt is given only in the
    test's own sport, so skis on the SkiErg's test get heart rate only."""
    if capacity is None or capacity.anchors is None:
        return {}
    sport = sport or capacity.sport
    zones = convert(capacity.anchors, system)
    line = capacity.speed_line if capacity.speed_from_hr else None
    if line is not None:
        zones = hr_zones.zone_speeds(zones, _relation(line))
    load = sport == capacity.sport and sport in (RUNNING, *POWER_SPORTS)
    out = {}
    for z in zones:
        if not load:
            out[z.code] = Target(z.code, z.name, z.hr_low, z.hr_high, estimated=bool(z.estimated), sport=sport)
            continue
        low, high = _field(z, "speed_low_kmh", "load_low"), _field(z, "speed_high_kmh", "load_high")
        flags = dict(estimated=bool(z.estimated),
                     speed_low_approx=bool(_field(z, "speed_low_extrapolated", "load_low_extrapolated")),
                     speed_high_approx=bool(_field(z, "speed_high_extrapolated", "load_high_extrapolated")),
                     speed_from_hr=_field(z, "speed_basis", "load_basis") == "hr", sport=sport)
        if sport == RUNNING:
            out[z.code] = Target(z.code, z.name, z.hr_low, z.hr_high, low, high, **flags)
        else:
            out[z.code] = Target(z.code, z.name, z.hr_low, z.hr_high, power_low_w=_watt(low), power_high_w=_watt(high),
                                 **flags)
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


def target(capacity: Capacity | None, zone: str | None, system: str = DEFAULT_SYSTEM,
           sport: str | None = None) -> Target | None:
    return targets(capacity, system, sport).get(zone) if zone else None


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
