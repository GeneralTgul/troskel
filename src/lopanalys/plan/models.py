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

# The sport keys are core.sport.Sport values ("run", "bike", "skierg"); the plans are built for running only.
RUNNING = Sport.RUN.value

LEVELS = ("beginner", "recreational", "trained")
PRIORITIES = ("A", "B", "C")                      # A: the goal, B: a milestone (seeding race), C: a training race
BLOCK_KINDS = ("base", "build", "peak", "taper")
WEEK_KINDS = ("load", "easy", "test", "taper", "race")
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")            # the plan speaks the default system: the watch's five zones
EASY_ZONES = ("Z1", "Z2")                         # below LT1


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
class Capacity:
    """What the athlete can do in one sport right now: the anchors of the latest test.

    ``anchors`` None: nothing is known, the plan shows zone names and effort only. Without measured
    thresholds the zones come from max HR and are marked as estimated.
    """
    sport: str = RUNNING
    anchors: Anchors | None = None
    tested: date | None = None
    source: str = ""                 # test name for the printout, e.g. "Laktattrappa"

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

    def capacity_for(self, sport: str) -> Capacity:
        for c in self.capacity:
            if c.sport == sport:
                return c
        return Capacity(sport)


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
        return self.goal.name if self.goal else t(f"plan.workout.{self.template_id}.title")

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


@dataclass
class Week:
    index: int                       # 1-based
    monday: date
    block: str                       # BLOCK_KINDS
    kind: str                        # WEEK_KINDS
    target_minutes: int              # the ceiling the week was filled under
    workouts: list[Workout] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    @property
    def sunday(self) -> date:
        return self.monday + timedelta(days=6)

    @property
    def minutes(self) -> int:
        """Planned minutes. A race has no planned length and counts as zero."""
        return sum(w.minutes for w in self.workouts)

    def zone_minutes(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for w in self.workouts:
            for z, s in w.zone_seconds().items():
                out[z] = out.get(z, 0.0) + s / 60
        return out

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
        return [w for wk in self.weeks for w in wk.workouts]

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
