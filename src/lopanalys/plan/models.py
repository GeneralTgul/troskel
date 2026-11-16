"""The plan's data model: plain dataclasses, as in core/models.py.

Three rules (docs/TRANINGSPLAN.md):
  1. A part of a workout has a zone, not a heart rate. Targets are resolved from ``Capacity`` on display.
  2. The plan points at the tests it is built on (``Plan.based_on``).
  3. Every block and every note carries its reason as a translatable text (``Note``).

Texts are stored as i18n keys, so one plan prints in Swedish and in English.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from lopanalys.assessments.zone_systems import DEFAULT_SYSTEM, Anchors
from lopanalys.core.i18n import t
from lopanalys.core.sport import Sport

# The sport keys: core.sport.Sport values ("run", "bike", "skierg") and the sports without a test in
# Tröskel ("ski", "swim"). The bike, the SkiErg and skis have their own templates (plan.library.SPORT_TEMPLATES);
# swimming uses the running templates with the sport's name until it gets its own (#666, step 3).
RUNNING = Sport.RUN.value
PLAN_SPORTS = (RUNNING, Sport.BIKE.value, Sport.SKIERG.value, "ski", "swim")
# A sport without its own test borrows the zones of another: skis and roller skis take the SkiErg's.
BORROWS = {"ski": Sport.SKIERG.value}
# The roles of a sport in a plan, and the share of the weekly time each one gets when none is given.
ROLES = ("main", "support", "maintenance")
ROLE_SHARE = {"main": 60, "support": 30, "maintenance": 10}
# The load of a workout: minutes x zone factor x sport factor (#666, decided 2026-11-15). A part without a zone
# (a test) counts as Z4; a race has no planned length.
ZONE_LOAD = {"Z1": 0.5, "Z2": 1.0, "Z3": 1.5, "Z4": 2.5, "Z5": 3.5}
SPORT_LOAD = {RUNNING: 1.0, Sport.BIKE.value: 0.7, Sport.SKIERG.value: 1.0, "ski": 1.0, "swim": 0.7, "strength": 0.8}
# Templates whose title says "running": in another sport they take the title without it.
_RUN_WORDED = ("easy", "recovery")

LEVELS = ("beginner", "recreational", "trained")
PRIORITIES = ("A", "B", "C")                      # A: the goal, B: a milestone (seeding race), C: a training race
BLOCK_KINDS = ("base", "build", "peak", "taper")
WEEK_KINDS = ("load", "easy", "test", "taper", "race")
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")            # the plan speaks the default system: the watch's five zones
EASY_ZONES = ("Z1", "Z2")                         # below LT1
HARD_ZONES = ("Z3", "Z4", "Z5")                   # from LT1 up: the minutes the overview counts as hard


@dataclass(frozen=True)
class Note:
    """A text in the plan as an i18n key with its values, e.g. why a block lies where it does."""
    key: str
    args: tuple[tuple[str, Any], ...] = ()

    @staticmethod
    def of(key: str, **args: Any) -> "Note":
        return Note(key, tuple(sorted(args.items())))

    @property
    def text(self) -> str:
        return t(self.key, **dict(self.args))


@dataclass(frozen=True)
class SpeedLine:
    """The relation heart rate -> speed of a zones test without lactate (assessments.hr_zones):
    HR = intercept + slope * speed, measured between ``speed_min`` and ``speed_max``. Only what the
    plan needs to turn a zone's heart rates into speeds; resolve.targets does that."""
    slope: float
    intercept: float
    speed_min: float
    speed_max: float
    ramp_top_kmh: float | None = None     # the fastest ramp stage run: no speed is given above it
    extrapolate_kmh: float = 2.0          # hr_zones.EXTRAPOLATE_KMH when the test was analysed


@dataclass(frozen=True)
class Capacity:
    """What the athlete can do in one sport right now: the anchors of the latest test.

    ``anchors`` None: nothing is known, the plan shows zone names and effort only. Without measured
    thresholds the zones come from max HR and are marked as estimated. ``speed_line``: a zones test
    gave the relation heart rate -> speed, so the estimated zones get a pace too (speed from heart
    rate, not from lactate).
    """
    sport: str = RUNNING
    anchors: Anchors | None = None
    tested: date | None = None
    source: str = ""                 # test name for the printout, e.g. "Laktattrappa"
    speed_line: SpeedLine | None = None

    @property
    def speed_from_hr(self) -> bool:
        """True when the zones' paces come from the heart rate -> speed relation of a zones test."""
        return self.speed_line is not None and self.estimated

    @property
    def estimated(self) -> bool:
        return self.anchors is None or not self.anchors.has_thresholds


@dataclass(frozen=True)
class Slot:
    """One occasion in the week when the athlete can train."""
    weekday: int                     # 0 = Monday
    max_minutes: int
    sports: tuple[str, ...] = (RUNNING,)
    start: time | None = None        # None: any time that day (an all-day calendar entry)
    role: str = "core"               # "core" | "extra"; only core slots are filled


@dataclass(frozen=True)
class Goal:
    name: str
    date: date
    sport: str = RUNNING
    distance_km: float | None = None
    priority: str = "A"
    target: str = ""                 # free text, e.g. "under 1:45"


@dataclass(frozen=True)
class SportRole:
    """One sport of the plan: its role and its share of the weekly time in percent (0: the role's default).
    The shares are weights: 60/30 gives two thirds and one third."""
    sport: str
    role: str = "main"               # ROLES
    share: int = 0

    @property
    def weight(self) -> int:
        return self.share if self.share > 0 else ROLE_SHARE.get(self.role, ROLE_SHARE["support"])


@dataclass(frozen=True)
class PlanRequest:
    """What the athlete and the coach state. Either an A goal or ``duration_weeks`` sets the length."""
    athlete_name: str
    start: date                      # the plan begins on the first Monday on or after this date
    availability: tuple[Slot, ...]
    capacity: tuple[Capacity, ...] = ()
    sports: tuple[str, ...] = (RUNNING,)
    goals: tuple[Goal, ...] = ()
    duration_weeks: int | None = None
    level: str = "recreational"
    recent_weekly_minutes: int | None = None   # mean of the last 4-6 weeks, when known
    roles: tuple[SportRole, ...] = ()          # () : ``sports``, the first (or the A goal's) as the main sport

    def own_capacity(self, sport: str) -> Capacity | None:
        return next((c for c in self.capacity if c.sport == sport), None)

    def capacity_for(self, sport: str) -> Capacity:
        """The sport's capacity; a sport without its own test borrows (BORROWS), else nothing is known."""
        own = self.own_capacity(sport)
        if own is None and sport in BORROWS:
            own = self.own_capacity(BORROWS[sport])
        return own if own is not None else Capacity(sport)

    def plan_roles(self) -> tuple[SportRole, ...]:
        """The plan's sports with their roles, the main sport first and each sport once.

        Without ``roles`` the sports in ``sports`` are used, the rest as supporting sports. Exactly one sport
        is the main sport: the first one given as main, else the sport of the A goal, else the first sport;
        another sport given as main counts as supporting."""
        given = self.roles or tuple(SportRole(sp, "support") for sp in self.sports)
        out: list[SportRole] = []
        for r in given:
            if r.sport not in [o.sport for o in out]:
                out.append(r if r.role in ROLES else SportRole(r.sport, "support", r.share))
        if not out:
            return ()
        main = next((r for r in out if r.role == "main"), None)
        if main is None:
            a = sorted((g for g in self.goals if g.priority == "A"), key=lambda g: g.date)
            main = next((r for r in out if a and r.sport == a[-1].sport), out[0])
        rest = [SportRole(r.sport, "support", r.share) if r.role == "main" else r for r in out if r is not main]
        return (SportRole(main.sport, "main", main.share), *rest)

    @property
    def plan_sports(self) -> tuple[str, ...]:
        return tuple(r.sport for r in self.plan_roles())

    @property
    def main_sport(self) -> str:
        roles = self.plan_roles()
        return roles[0].sport if roles else RUNNING


@dataclass(frozen=True)
class Part:
    """One part of a workout. ``repeat`` > 1 is an interval set with ``rest_s`` between the repeats."""
    kind: str                        # "warmup" | "steady" | "work" | "cooldown" | "test" | "race"
    duration_s: int
    zone: str | None                 # zone code; None for a test or a race
    rpe: int                         # effort 1-10, always shown
    repeat: int = 1
    rest_s: int = 0
    rest_zone: str = "Z1"            # where the rest counts: "Z1" standing or walking, "Z2" jogging

    @property
    def work_s(self) -> int:
        return self.repeat * self.duration_s

    @property
    def total_s(self) -> int:
        return self.work_s + (self.repeat - 1) * self.rest_s


@dataclass
class Workout:
    key: str                         # stable key "<plan>-<yy><ww>-<day>", survives changes and export
    date: date
    sport: str
    template_id: str
    family: str
    quality: bool                    # a hard day: quality session, test or race
    parts: list[Part]
    start: time | None = None
    goal: Goal | None = None         # set on a race
    status: str = "planned"          # "planned" | "done" | "moved" | "missed" | "skipped"

    @property
    def seconds(self) -> int:
        return sum(p.total_s for p in self.parts)

    @property
    def minutes(self) -> int:
        return round(self.seconds / 60)

    @property
    def title(self) -> str:
        """The template's title; in a sport other than running with the sport's name first."""
        if self.goal:
            return self.goal.name
        if self.sport == RUNNING:
            return t(f"plan.workout.{self.template_id}.title")
        suffix = ".title_any" if self.template_id in _RUN_WORDED else ".title"
        return t("plan.workout.in_sport", sport=t(f"plan.sport.{self.sport}"),
                 title=t(f"plan.workout.{self.template_id}{suffix}"))

    @property
    def load(self) -> float:
        """Minutes x zone factor x sport factor (ZONE_LOAD, SPORT_LOAD): comparable between sports."""
        zoned = sum(s / 60 * ZONE_LOAD[z] for z, s in self.zone_seconds().items())
        test = sum(p.total_s / 60 * ZONE_LOAD["Z4"] for p in self.parts if p.zone is None and p.kind == "test")
        return (zoned + test) * SPORT_LOAD.get(self.sport, 1.0)

    @property
    def purpose(self) -> str:
        return t(f"plan.workout.{self.template_id}.purpose")

    @property
    def how(self) -> str:
        return t(f"plan.workout.{self.template_id}.how")

    def zone_seconds(self) -> dict[str, int]:
        """Seconds per zone; rests count in their rest zone. Parts without a zone are left out."""
        out: dict[str, int] = {}
        for p in self.parts:
            if p.zone is None:
                continue
            out[p.zone] = out.get(p.zone, 0) + p.work_s
            if p.repeat > 1 and p.rest_s:
                out[p.rest_zone] = out.get(p.rest_zone, 0) + (p.repeat - 1) * p.rest_s
        return out


@dataclass(frozen=True)
class WeekSketch:
    """A week beyond the detail horizon: what the engine filled it with, as sums, without the workouts.
    The workouts are made when the plan rolls forward to the week (plan.rolling.roll_forward)."""
    minutes: int
    sessions: int                    # workouts, races not counted
    quality: int                     # of them hard days (quality sessions and tests)
    longest: int                     # the longest run, minutes
    zones: tuple[tuple[str, float], ...] = ()    # minutes per zone


@dataclass
class Week:
    index: int                       # 1-based
    monday: date
    block: str                       # BLOCK_KINDS
    kind: str                        # WEEK_KINDS
    target_minutes: int              # the ceiling the week was filled under
    workouts: list[Workout] = field(default_factory=list)   # a sketched week: only its races
    notes: list[Note] = field(default_factory=list)
    sketch: WeekSketch | None = None  # None: the week is detailed, every workout is in ``workouts``

    @property
    def detailed(self) -> bool:
        return self.sketch is None

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def minutes(self) -> int:
        """Planned minutes. A race has no planned length and counts as zero."""
        if self.sketch is not None:
            return self.sketch.minutes
        return sum(w.minutes for w in self.workouts)

    @property
    def sessions(self) -> int:
        """Workouts in the week, races not counted."""
        if self.sketch is not None:
            return self.sketch.sessions
        return len([w for w in self.workouts if w.goal is None])

    @property
    def quality_sessions(self) -> int:
        """Hard days in the week (quality sessions and tests), races not counted."""
        if self.sketch is not None:
            return self.sketch.quality
        return len([w for w in self.workouts if w.goal is None and w.quality])

    def zone_minutes(self) -> dict[str, float]:
        if self.sketch is not None:
            return dict(self.sketch.zones)
        out: dict[str, float] = {}
        for w in self.workouts:
            for z, s in w.zone_seconds().items():
                out[z] = out.get(z, 0.0) + s / 60
        return out

    def sport_minutes(self) -> dict[str, int]:
        """Planned minutes per sport (a detailed week; a sketch has only the sums)."""
        out: dict[str, int] = {}
        for w in self.workouts:
            if w.goal is None:
                out[w.sport] = out.get(w.sport, 0) + w.minutes
        return out

    @property
    def load(self) -> float:
        return sum(w.load for w in self.workouts if w.goal is None)

    @property
    def longest_minutes(self) -> int:
        """The longest planned run of the week, in minutes. A race has no planned length and is left out."""
        if self.sketch is not None:
            return self.sketch.longest
        return max((w.minutes for w in self.workouts if w.goal is None), default=0)

    @property
    def hard_minutes(self) -> float:
        """Minutes in zones 3-5: the hard part of the quality sessions (and of a progressive long run)."""
        zm = self.zone_minutes()
        return sum(zm.get(z, 0.0) for z in HARD_ZONES)

    @property
    def easy_share(self) -> float:
        """Share of the zoned time below LT1 (zones 1-2)."""
        zm = self.zone_minutes()
        total = sum(zm.values())
        return sum(zm.get(z, 0.0) for z in EASY_ZONES) / total if total else 1.0


@dataclass
class Block:
    kind: str
    first_week: int                  # 1-based index of its first week
    weeks: int
    start: date
    reason: Note

    @property
    def last_week(self) -> int:
        return self.first_week + self.weeks - 1

    @property
    def name(self) -> str:
        return t(f"plan.block.{self.kind}")


@dataclass
class Plan:
    id: str
    request: PlanRequest
    created: datetime
    engine_version: str
    blocks: list[Block]
    weeks: list[Week]
    notes: list[Note] = field(default_factory=list)
    zone_system: str = DEFAULT_SYSTEM
    based_on: dict[str, date | None] = field(default_factory=dict)   # sport -> date of the test
    status: str = "draft"            # "draft" | "active" | "replaced"
    # Earlier tests, after a retest: (first day of the newer test's targets, the capacity before it).
    # Workouts dated before that day keep the targets they were handed out with (resolve.retest).
    capacity_log: tuple[tuple[date, Capacity], ...] = ()
    # The last week with workouts (1-based); the weeks after it are sketches (Week.sketch). None: every
    # week is detailed, as in plans saved before the rolling plan and plans built without a day.
    horizon: int | None = None

    def capacity_on(self, sport: str, day: date) -> Capacity:
        """The capacity a workout on ``day`` is shown with: the test that applied on that day."""
        for until, cap in sorted(self.capacity_log, key=lambda x: x[0]):
            if cap.sport == sport and day < until:
                return cap
        return self.request.capacity_for(sport)

    @property
    def start(self) -> date:
        return self.weeks[0].monday

    @property
    def end(self) -> date:
        return self.weeks[-1].sunday

    @property
    def workouts(self) -> list[Workout]:
        """The workouts of the detailed weeks (a sketched week's races are left out: they are in no calendar yet)."""
        return [w for wk in self.weeks if wk.detailed for w in wk.workouts]

    @property
    def detailed_weeks(self) -> list[Week]:
        return [wk for wk in self.weeks if wk.detailed]

    @property
    def sketch_weeks(self) -> list[Week]:
        return [wk for wk in self.weeks if not wk.detailed]

    @property
    def detailed_until(self) -> Week:
        """The last detailed week."""
        return self.detailed_weeks[-1] if self.detailed_weeks else self.weeks[0]

    @property
    def goal(self) -> Goal | None:
        """The A goal the plan ends with, if any."""
        a = [g for g in self.request.goals if g.priority == "A"]
        return max(a, key=lambda g: g.date) if a else None

    @property
    def easy_share(self) -> float:
        zm: dict[str, float] = {}
        for wk in self.weeks:
            for z, m in wk.zone_minutes().items():
                zm[z] = zm.get(z, 0.0) + m
        total = sum(zm.values())
        return sum(zm.get(z, 0.0) for z in EASY_ZONES) / total if total else 1.0
