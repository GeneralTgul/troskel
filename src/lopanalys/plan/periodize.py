"""Blocks and weeks: the skeleton of a plan, counted backwards from the goal.

The rules (docs/TRANINGSPLAN.md, "Planmotorn"):
  - With an A goal: taper (1 week, 2 for races of 25 km or more), peak (2 weeks, 1 in a plan under
    10 weeks, none under 6), build (4-8 weeks) and base (the rest). Short plans lose base first.
  - Without a goal: cycles of 8-12 weeks, base then build, each ending with a test week.
  - Load and easy weeks: 3+1 (2+1 for beginners), counted backwards from the taper or the test, so
    the weeks before the goal are load weeks and the easy week never lands in the peak.
    The plan never opens with an easy week: when the count does not come out even, the first run of load
    weeks is one week longer, and its first week is the volume the athlete already trains.
  - A retest in an easy week once 8 weeks have passed since the last test, at least 4 weeks before the goal.
  - Volume starts at what the athlete trains now (or the level's default, kept below what the week
    holds), stands still in the peak and never passes what the core slots hold. It grows in base and
    build by the rate that reaches the full week at the last load week before the peak, never faster
    than the level's rate: a plan that starts near the full week grows slowly, one that starts far
    below grows at the level's rate for as many weeks as it needs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from lopanalys.core.i18n import t

from . import library
from .models import LEVELS, RUNNING, Block, Goal, Note, PlanRequest

DEFAULT_WEEKS = 12
MIN_WEEKS = 4                 # without a goal
MAX_WEEKLY_INCREASE = 0.10    # a ceiling, not a target
EASY_FACTOR = 0.65            # an easy week against the load week before it
B_RACE_FACTOR = 0.75          # the week of a milestone race
TAPER_FACTORS = {1: (0.55,), 2: (0.70, 0.50)}
LONG_RACE_KM = 25.0           # from here the taper is two weeks
RETEST_MIN_GAP = 8            # weeks between tests
RETEST_BEFORE_GOAL = 4        # no retest closer to the goal than this
CYCLE_MAX = 12                # weeks in a cycle without a goal
DEFAULT_SHARE = 0.70          # with nothing known, the plan starts at most at this share of the full week


@dataclass(frozen=True)
class Level:
    start_minutes: int        # weekly volume when nothing is known
    growth: float             # per load week, below MAX_WEEKLY_INCREASE
    load_weeks: int           # load weeks before each easy week
    max_sessions: int
    first_step: int           # where the quality progressions start
    long_cap: int             # longest long run, minutes


LEVEL: dict[str, Level] = {
    "beginner": Level(120, 0.05, 2, 3, 0, 90),
    "recreational": Level(180, 0.07, 3, 5, 1, 150),
    "trained": Level(300, 0.08, 3, 7, 2, 180),
}
assert set(LEVEL) == set(LEVELS)


@dataclass
class WeekSpec:
    index: int
    monday: date
    block: str
    kind: str
    target_minutes: int
    goals: list[Goal] = field(default_factory=list)


@dataclass
class Skeleton:
    weeks: list[WeekSpec]
    blocks: list[Block]
    notes: list[Note]
    goal: Goal | None


def first_monday(d: date) -> date:
    return d + timedelta(days=(-d.weekday()) % 7)


def round5(minutes: float) -> int:
    return int(round(minutes / 5.0)) * 5


def core_slots(request: PlanRequest, sport: str = RUNNING) -> list:
    """The core slots for a sport, one per weekday (the longest), in weekday order."""
    best: dict[int, object] = {}
    for s in request.availability:
        if s.role != "core" or sport not in s.sports or s.max_minutes <= 0 or not 0 <= s.weekday <= 6:
            continue
        if s.weekday not in best or s.max_minutes > best[s.weekday].max_minutes:
            best[s.weekday] = s
    return [best[d] for d in sorted(best)]


def week_capacity(request: PlanRequest, sport: str = RUNNING) -> int:
    """The minutes a week can really hold: the level's number of sessions in the longest core slots,
    the long run within the level's longest long run and every other run within an easy run's length."""
    level = LEVEL[request.level]
    slots = core_slots(request, sport)
    if not slots:
        return 0
    long_slot = max(slots, key=lambda s: (s.max_minutes, s.weekday))
    easy_max = library.get("easy").max_minutes
    held = sorted((min(s.max_minutes, level.long_cap) if s is long_slot else min(s.max_minutes, easy_max)
                   for s in slots), reverse=True)
    return sum(held[:level.max_sessions])


def first_week_minutes(request: PlanRequest, sport: str = RUNNING) -> tuple[int, str]:
    """The weekly volume of the plan's first week and where it comes from:
    "recent" (what the athlete trains now), "capped" (more than the week holds: the full week) or
    "default" (nothing known: the level's default, at most ``DEFAULT_SHARE`` of the full week)."""
    cap = week_capacity(request, sport)
    recent = request.recent_weekly_minutes
    if recent:
        return (cap, "capped") if recent > cap else (int(recent), "recent")
    return min(LEVEL[request.level].start_minutes, round5(DEFAULT_SHARE * cap)), "default"


def growth_rate(start: float, cap: float, increments: int, level_rate: float) -> float:
    """The growth per load week: the rate that reaches ``cap`` after ``increments`` steps, never above
    the level's rate (nor the ten percent ceiling), and none when the plan already starts at the cap."""
    if start >= cap or increments <= 0 or start <= 0:
        return 0.0
    return min(level_rate, MAX_WEEKLY_INCREASE, (cap / start) ** (1 / increments) - 1)


def _cycles(n: int) -> list[int]:
    """Split n weeks into cycles of at most 12 weeks, as even as possible (not below 8 where it can be avoided)."""
    k = max(1, math.ceil(n / CYCLE_MAX))
    if k > 1 and n / k < RETEST_MIN_GAP:
        k -= 1
    base, extra = divmod(n, k)
    return [base + (1 if i < extra else 0) for i in range(k)]


def _goal_blocks(n: int, goal: Goal) -> list[tuple[str, int]]:
    long_race = (goal.distance_km or 0) >= LONG_RACE_KM
    taper = 2 if long_race and n >= 8 else 1
    peak = 0 if n < 6 else (2 if n >= 10 else 1)
    rest = n - taper - peak
    build = rest if rest <= 4 else min(8, max(4, rest // 2))
    return [("base", rest - build), ("build", build), ("peak", peak), ("taper", taper)]


def periodize(request: PlanRequest) -> Skeleton:
    if request.level not in LEVEL:
        raise ValueError(t("plan.error.level", level=request.level))
    slots = core_slots(request)
    if not slots:
        raise ValueError(t("plan.error.no_slots"))
    level = LEVEL[request.level]
    start = first_monday(request.start)
    notes: list[Note] = []

    a_goals = sorted((g for g in request.goals if g.priority == "A"), key=lambda g: g.date)
    goal = a_goals[-1] if a_goals else None
    if goal is not None:
        if goal.date < start + timedelta(days=7):
            raise ValueError(t("plan.error.goal_too_close", name=goal.name))
        n = (goal.date - start).days // 7 + 1
        layout = _goal_blocks(n, goal)
        cycle_ends: list[int] = []
        if n < 6:
            notes.append(Note.of("plan.note.short", weeks=n))
    else:
        n = request.duration_weeks or DEFAULT_WEEKS
        if n < MIN_WEEKS:
            raise ValueError(t("plan.error.too_short", weeks=MIN_WEEKS))
        layout, cycle_ends, w = [], [], 0
        for length in _cycles(n):
            layout += [("base", math.ceil(length / 2)), ("build", length - math.ceil(length / 2))]
            w += length
            cycle_ends.append(w)

    # ---- blocks and the block of every week
    blocks: list[Block] = []
    block_of: list[str] = []
    first = 1
    for kind, weeks in layout:
        if weeks <= 0:
            continue
        last = first + weeks - 1
        if goal is not None:
            reason = Note.of(f"plan.reason.{kind}", weeks=weeks, goal=goal.name)
        else:
            test_week = next(e for e in cycle_ends if e >= last)
            reason = Note.of(f"plan.reason.{kind}_cycle", weeks=weeks, test_week=test_week)
        blocks.append(Block(kind, first, weeks, start + timedelta(weeks=first - 1), reason))
        block_of += [kind] * weeks
        first = last + 1

    # ---- load, easy, test, taper and race weeks
    period = level.load_weeks + 1
    kinds = ["load"] * n
    if goal is not None:
        taper_first = next(b.first_week for b in blocks if b.kind == "taper")
        for w in range(2, taper_first):
            if (taper_first - w) % period == 0:
                kinds[w - 1] = "easy"
        for w in range(taper_first, n + 1):
            kinds[w - 1] = "taper"
        kinds[n - 1] = "race"
        last_test = 0
        for w in range(1, taper_first):
            if kinds[w - 1] == "easy" and w - last_test >= RETEST_MIN_GAP and n - w >= RETEST_BEFORE_GOAL:
                kinds[w - 1], last_test = "test", w
    else:
        prev_end = 0
        for end in cycle_ends:
            for w in range(prev_end + 2, end):
                if (end - w) % period == 0:
                    kinds[w - 1] = "easy"
            kinds[end - 1] = "test"
            prev_end = end

    # ---- weekly volume
    cap = week_capacity(request)
    first_minutes, source = first_week_minutes(request)
    if source == "capped":
        notes.append(Note.of("plan.note.capped", minutes=cap))
    # Every load week after the first grows, except in the peak: the full week comes with the last of them.
    steps = sum(1 for k, b in zip(kinds, block_of) if k == "load" and b != "peak") - 1
    rate = growth_rate(first_minutes, cap, steps, level.growth)
    by_week: dict[int, list[Goal]] = {}
    for g in sorted(request.goals, key=lambda g: g.date):
        idx = (g.date - start).days // 7 + 1 if g.date >= start else 0
        if 1 <= idx <= n:
            by_week.setdefault(idx, []).append(g)
        else:
            notes.append(Note.of("plan.note.goal_outside", name=g.name))

    weeks: list[WeekSpec] = []
    load: float | None = None
    taper_i = 0
    taper_len = sum(1 for k in kinds if k in ("taper", "race"))
    for i, (kind, block) in enumerate(zip(kinds, block_of), start=1):
        if kind == "load":
            if load is None:
                load = float(first_minutes)
            elif block != "peak":
                load = min(float(cap), load * (1 + rate))
            minutes = load
        elif kind in ("easy", "test"):
            minutes = EASY_FACTOR * (load if load is not None else first_minutes)
        else:
            minutes = TAPER_FACTORS[min(taper_len, 2)][min(taper_i, 1)] * (load if load is not None else first_minutes)
            taper_i += 1
        goals = by_week.get(i, [])
        if kind == "load" and any(g.priority in ("A", "B") for g in goals):
            minutes *= B_RACE_FACTOR
        weeks.append(WeekSpec(i, start + timedelta(weeks=i - 1), block, kind, round5(minutes), goals))
    return Skeleton(weeks, blocks, notes, goal)
