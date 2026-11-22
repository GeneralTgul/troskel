"""Blocks and weeks: the skeleton of a plan, counted backwards from the goal.

The rules (docs/TRANINGSPLAN.md, "Planmotorn"):
  - With an A goal: taper (1 week, 2 for races of 25 km or more), peak (2 weeks, 1 in a plan under
    10 weeks, none under 6), build (4-8 weeks) and base (the rest). Short plans lose base first.
  - Without a goal: cycles of 8-12 weeks, base then build, each ending with a test week.
  - Load and easy weeks: 3+1 (2+1 for beginners), counted backwards from the taper or the test, so
    the weeks before the goal are load weeks and the easy week never lands in the peak.
    The plan never opens with an easy week: when the count does not come out even, the first run of load
    weeks is one week longer, and its first week is the volume the athlete already trains.
  - A retest in an easy week once 8 weeks have passed since the last test, at least 4 weeks before the goal, and
    not in a week with days of recovery after a race (recovery_days).
  - Volume starts at what the athlete trains now (or the level's default, kept below what the week
    holds), stands still in the peak and never passes what the core slots hold. It grows in base and
    build by the rate that reaches the full week at the last load week before the peak, never faster
    than the level's rate: a plan that starts near the full week grows slowly, one that starts far
    below grows at the level's rate for as many weeks as it needs.
  - The long run's ceiling (``long_cap``) is the level's, raised for a long goal: a race the athlete is out for
    more than four hours (its length at a usual race speed of the sport, ``RACE_KMH``) gets three quarters of
    every minute beyond the four hours on top, at most ``LONG_CAP_MAX``. A 90 km ski race gives a trained athlete
    4:30; a 10 km race or a marathon keeps the level's ceiling.
  - The coach's easy weeks (``PlanRequest.easy_weeks``, a holiday or a trip): a load week the coach marks becomes
    an easy week, and one of the counted easy weeks within the level's load weeks minus one of it (two weeks at
    3+1, one at 2+1) becomes a load week instead: the one that leaves the shortest run of load weeks, then the
    nearest, then the later, and none when the run of load weeks would be more than one longer than the level's.
    The first week, a test week without a goal and the taper are never changed, and a
    retest is placed after the move, among the easy weeks as they then lie.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from lopanalys.core.i18n import t

from .models import LEVELS, Block, Goal, Note, PlanRequest

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
# Recovery after a race (#666): one easy day per this many km of the race, by sport. Running's is the careful end of
# the rule of thumb "a day per 3-5 km" (docs/TRANINGSPLAN.md, *Återhämtning efter ett lopp*); the other sports'
# are the programme's own, scaled to how far a race in the sport usually goes.
RECOVERY_KM_PER_DAY = {"run": 3.0, "bike": 15.0, "skierg": 10.0, "ski": 10.0, "swim": 1.0}
RECOVERY_A_DEFAULT = 7        # days after an A race without a distance
RECOVERY_B_FROM = 7           # a B race gets a recovery period from this many days (a half marathon in running)
RECOVERY_MAX_DAYS = 21
# The long run for a long goal (``long_cap``): a usual race speed per sport for a trained amateur, km/h, the race
# time from which the long run's ceiling grows, the share of the minutes beyond it that it grows by, and the most.
RACE_KMH = {"run": 11.0, "bike": 28.0, "skierg": 15.0, "ski": 15.0, "swim": 3.5}
LONG_RACE_FROM = 240
LONG_RACE_SHARE = 0.75
LONG_CAP_MAX = 270


@dataclass(frozen=True)
class Level:
    start_minutes: int        # weekly volume when nothing is known
    growth: float             # per load week, below MAX_WEEKLY_INCREASE
    load_weeks: int           # load weeks before each easy week
    max_sessions: int
    first_step: int           # where the quality progressions start
    long_cap: int             # longest long run, minutes (raised for a long goal: long_cap)
    easy_max: int = 75        # longest easy run, minutes (the easy template's length up to the high level)
    recovery_max: int = 45    # longest recovery run (the day after a hard day, a day's second workout on a hard day)


LEVEL: dict[str, Level] = {
    "beginner": Level(120, 0.05, 2, 3, 0, 90),
    "recreational": Level(180, 0.07, 3, 5, 1, 150),
    "trained": Level(300, 0.08, 3, 7, 2, 180),
    # High volume: 11-12 h a week in up to 11 sessions, easy runs up to 1:45 and recovery runs up to an hour.
    "high": Level(420, 0.08, 3, 11, 2, 180, 105, 60),
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


def recovery_days(goal: Goal) -> int:
    """The easy days after an A or B race: a day per ``RECOVERY_KM_PER_DAY`` km of the race (rounded up, at most 21),
    a week for an A race without a distance. A B race gets them only from ``RECOVERY_B_FROM`` days (a long race),
    and 0 means the two easy days every A and B race already has."""
    if goal.priority not in ("A", "B"):
        return 0
    if goal.distance_km:
        days = math.ceil(goal.distance_km / RECOVERY_KM_PER_DAY.get(goal.sport, RECOVERY_KM_PER_DAY["run"]))
    else:
        days = RECOVERY_A_DEFAULT if goal.priority == "A" else 0
    days = min(days, RECOVERY_MAX_DAYS)
    if goal.priority == "B" and days < RECOVERY_B_FROM:
        return 0
    return days if days > 2 else 0


def plan_goal(request: PlanRequest) -> Goal | None:
    """The A goal the plan ends with (the latest), if any."""
    a = sorted((g for g in request.goals if g.priority == "A"), key=lambda g: g.date)
    return a[-1] if a else None


def race_minutes(goal: Goal) -> float:
    """How long the race takes at a usual race speed of its sport (RACE_KMH); 0 without a distance."""
    return 60 * (goal.distance_km or 0) / RACE_KMH.get(goal.sport, RACE_KMH["run"])


def long_cap(request: PlanRequest) -> int:
    """The longest long run of the plan: the level's ceiling, raised for a goal that takes more than
    ``LONG_RACE_FROM`` minutes by ``LONG_RACE_SHARE`` of the minutes beyond, at most ``LONG_CAP_MAX`` (and never
    below the level's)."""
    cap = LEVEL[request.level].long_cap
    goal = plan_goal(request)
    if goal is None:
        return cap
    over = race_minutes(goal) - LONG_RACE_FROM
    if over <= 0:
        return cap
    return max(cap, min(LONG_CAP_MAX, int(cap + LONG_RACE_SHARE * over) // 5 * 5))


def first_monday(d: date) -> date:
    return d + timedelta(days=(-d.weekday()) % 7)


def round5(minutes: float) -> int:
    return int(round(minutes / 5.0)) * 5


def core_slots(request: PlanRequest, sport: str | None = None) -> list:
    """The core slots for a sport, or (None) for any of the plan's sports: one per weekday (the longest),
    in weekday order. A slot for a sport the plan does not have is not used."""
    wanted = (sport,) if sport is not None else request.plan_sports
    best: dict[int, object] = {}
    for s in request.availability:
        if (s.role != "core" or not any(x in wanted for x in s.sports) or s.max_minutes <= 0
                or not 0 <= s.weekday <= 6):
            continue
        if s.weekday not in best or s.max_minutes > best[s.weekday].max_minutes:
            best[s.weekday] = s
    return [best[d] for d in sorted(best)]


def second_slots(request: PlanRequest, first: list | None = None) -> list:
    """The second slot of a weekday (#666, two workouts a day): beside the day's core slot (``core_slots``), another
    core slot at another clock time, when the two slots between them allow two different sports of the plan. At
    most one a day (the longest), in weekday order. A plan with one sport has none: two workouts in one day are
    always two different sports."""
    sports = request.plan_sports
    if len(sports) < 2:
        return []
    days = {s.weekday: s for s in (first if first is not None else core_slots(request))}
    best: dict[int, object] = {}
    for s in request.availability:
        p = days.get(s.weekday)
        if p is None or s is p or s.role != "core" or s.max_minutes <= 0:
            continue
        if s.start is None or p.start is None or s.start == p.start:
            continue
        mine = [x for x in s.sports if x in sports]
        theirs = [x for x in p.sports if x in sports]
        if not any(a != b for a in mine for b in theirs):
            continue
        if s.weekday not in best or s.max_minutes > best[s.weekday].max_minutes:
            best[s.weekday] = s
    return [best[d] for d in sorted(best)]


def long_slot(slots: list, main_sport: str):
    """The long run's slot all through the plan: the longest slot for the main sport, the later day on a tie
    (the longest slot of all when no slot has the main sport)."""
    own = [s for s in slots if main_sport in s.sports] or slots
    return max(own, key=lambda s: (s.max_minutes, s.weekday))


def fixed_minutes(request: PlanRequest) -> dict[int, int]:
    """Weekday -> the length the coach gave the day's group session (a fixed hard or easy day with minutes)."""
    out: dict[int, int] = {}
    for h in request.hard_days + request.easy_days:
        if h.minutes > 0 and 0 <= h.weekday <= 6:
            out[h.weekday] = max(out.get(h.weekday, 0), h.minutes)
    return out


def week_capacity(request: PlanRequest, sport: str | None = None) -> int:
    """The minutes a week can really hold: the level's number of sessions in the longest core slots (and the
    second slots of the days, ``second_slots``), the long run within the plan's longest long run (``long_cap``) and
    every other run within the level's easy run (``Level.easy_max``), or the length the coach gave the day's group
    session when that is longer (``fixed_minutes``)."""
    level = LEVEL[request.level]
    slots = core_slots(request, sport)
    if not slots:
        return 0
    long = long_slot(slots, request.main_sport)
    easy_max, cap = level.easy_max, long_cap(request)
    seconds = second_slots(request, slots) if sport is None else []
    fixed = fixed_minutes(request)
    held = sorted([min(s.max_minutes, cap) if s is long else min(s.max_minutes, max(easy_max, fixed.get(s.weekday, 0)))
                   for s in slots] + [min(s.max_minutes, easy_max) for s in seconds], reverse=True)
    return sum(held[:level.max_sessions])


def first_week_minutes(request: PlanRequest, sport: str | None = None) -> tuple[int, str]:
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


def _longest_load(kinds: list[str]) -> int:
    """The longest run of load weeks."""
    best = run = 0
    for k in kinds:
        run = run + 1 if k == "load" else 0
        best = max(best, run)
    return best


def coach_easy(kinds: list[str], marked: list[int], load_weeks: int) -> None:
    """Lays the coach's easy weeks (1-based indices) on the kinds, in place (the rule in the module's docstring)."""
    done: set[int] = set()
    for w in sorted(set(marked)):
        if w < 2 or w > len(kinds) or kinds[w - 1] != "load":
            continue
        kinds[w - 1] = "easy"
        done.add(w)
        near = [v for v in range(max(2, w - load_weeks + 1), min(len(kinds), w + load_weeks - 1) + 1)
                if v != w and v not in done and kinds[v - 1] == "easy"]
        if not near:
            continue

        def after(v: int) -> tuple[int, int, int]:
            trial = list(kinds)
            trial[v - 1] = "load"
            return (_longest_load(trial), abs(v - w), -v)

        best = min(near, key=after)
        if after(best)[0] <= load_weeks + 1:      # never a longer run of load weeks than one more than the level's
            kinds[best - 1] = "load"


def periodize(request: PlanRequest) -> Skeleton:
    if request.level not in LEVEL:
        raise ValueError(t("plan.error.level", level=request.level))
    slots = core_slots(request)
    if not slots:
        raise ValueError(t("plan.error.no_slots"))
    level = LEVEL[request.level]
    start = first_monday(request.start)
    notes: list[Note] = []

    goal = plan_goal(request)
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
    marked = [(d - start).days // 7 + 1 for d in request.easy_weeks if d >= start]
    if goal is not None:
        taper_first = next(b.first_week for b in blocks if b.kind == "taper")
        for w in range(2, taper_first):
            if (taper_first - w) % period == 0:
                kinds[w - 1] = "easy"
        for w in range(taper_first, n + 1):
            kinds[w - 1] = "taper"
        kinds[n - 1] = "race"
        coach_easy(kinds, [w for w in marked if w < taper_first], level.load_weeks)
        last_test = 0
        # No test in a week with days of recovery after a race (the engine makes them easy): the next easy week.
        rest = [(g.date, g.date + timedelta(days=recovery_days(g))) for g in request.goals if g is not goal]
        resting = {w for w in range(1, n + 1) for a, b in rest
                   if a < start + timedelta(weeks=w - 1, days=6) and b >= start + timedelta(weeks=w - 1) and b > a}
        for w in range(1, taper_first):
            if (kinds[w - 1] == "easy" and w - last_test >= RETEST_MIN_GAP and n - w >= RETEST_BEFORE_GOAL
                    and w not in resting):
                kinds[w - 1], last_test = "test", w
    else:
        prev_end = 0
        for end in cycle_ends:
            for w in range(prev_end + 2, end):
                if (end - w) % period == 0:
                    kinds[w - 1] = "easy"
            kinds[end - 1] = "test"
            prev_end = end
        coach_easy(kinds, marked, level.load_weeks)

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
        elif g.date < start and g.date + timedelta(days=recovery_days(g)) >= start:
            # a race just before the plan: the plan opens with its recovery (engine)
            until = g.date + timedelta(days=recovery_days(g))
            notes.append(Note.of("plan.note.recovery_before", name=g.name, until=until.isoformat()))
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
