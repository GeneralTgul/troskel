"""Fills the skeleton with workouts: which slot gets which template, and how long.

The rules (docs/TRANINGSPLAN.md):
  - Only the athlete's core slots are used, never more minutes than a slot holds.
  - One long run a week in the longest slot, never the day after a race. It grows by at most 15 minutes
    from one load week to the next.
  - Quality sessions: one in base, easy and taper weeks, two in build and peak with four sessions or
    more. Never two hard days in a row (also across the week boundary), never the day before the long
    run, at least two easy days before and after an A or B race, one around a C race.
  - A beginner gets nothing above LT2 during the first four weeks.
  - At most a quarter of a week's time above LT1; the engine steps the quality down, never up, to hold it.
  - A week never holds more than 10 % above the biggest week before it.
  - The same request always gives the same plan: no randomness anywhere.
  - Several sports (#666, decided 2026-11-15): the week is filled as above over every sport at once, so the
    hard days, the long run and the ceilings count over all sports. Then every workout gets its sport
    (``_Filler._sports``): the long run and the test in the main sport, the rest to the sport furthest
    behind its share of the week among the sports its slot allows. The shares shift towards the main sport
    in the peak (half of the rest) and the taper (three quarters). A maintenance sport gets at most one
    workout a week and never a hard one, and running grows by at most 10 % a week of its own minutes.
    The week is filled with the running templates; a workout in the bike, the SkiErg or on skis then takes
    the sport's own template in the same place, just as long (library.in_sport), and the week is checked
    again (the hard share) with them.
  - Two workouts a day (#666): a day with a second slot at another clock time (periodize.second_slots) gets a
    second workout only when every day of the week already has one and the week wants more sessions. The second
    workout is always easy (a recovery workout on a hard day), in another sport than the day's first, never on the
    long run's day or the days around a race, and on the days without a hard session or a long run first. A day
    with two workouts is a hard day when one of them is.
  - A smaller long workout in a supporting sport (#666): every other load week in base and build without a race,
    the first supporting sport gets a long, easy workout in its longest slot that allows it, never on the main sport's
    long run day, the days before and after it or the day after a hard day. It is at most three quarters of the long run and the sport's part
    of the week, at least 60 minutes, and counts in the sport's share and the week's ceilings.
  - Recovery after a race (#666): after an A race that is not the plan's goal, and after a long B race, the days of
    recovery (periodize.recovery_days: a day per 3 km in running) are easy: no quality session, no long run, no
    second workout, every workout at most 30 minutes in their first half and 45 in the second, and no strength in
    the first half. A race just before the
    plan's start gives the plan's first days the same. The weeks they fall in say so (plan.week.recovery).
  - Strength (#666, step 3): the coach sets 0-3 sessions a week (PlanRequest.strength_sessions). They come on
    top of the endurance week, which is filled first and never changes for them (``_Filler._strength``):
    never on a race day, the day before a race or the three days before an A or B race; at most one in the race
    week; and on the days that are easiest for the legs: first never the day before a hard day or the long run,
    then at least a day between two strength sessions, then on a day without endurance training, then after an
    easy one. A strength session is not a hard day and holds no zone time. The taper and the race week get the
    lighter maintenance session. A missed strength session is never made up for: no rule moves a workout.
  - Fixed hard days (PlanRequest.hard_days, a group session): the coach's weekdays are always among the week's
    slots, and the week's quality sessions go there first: of the legal choices (the rules above: never two hard
    days in a row, never the day before the long run, around a race) the one with the most fixed days wins, then
    the one with the most room. A week with fewer quality sessions than fixed days uses the ones given first, and a
    fixed day that is not a legal hard day that week is an easy day. With a sport the quality session is in that
    sport when the slot allows it, even a maintenance sport (the test stays in the main sport).
  - The level sets the longest easy run (Level.easy_max: 75 minutes, 105 at the high level) and recovery run
    (Level.recovery_max: 45 minutes, 60 at the high level), and the plan's goal the longest long run
    (periodize.long_cap).
  - The coach's choices (all off by default: a request without them gives the plan it gave before):
    * The number of quality sessions (PlanRequest.quality_sessions, 1-3): a load week gets that many, in every
      block, at most half its sessions. The rules above still hold (never two hard days in a row, the long run,
      races, a quarter of the time above LT1), so a week gets fewer when they do not fit. The base block takes the
      next families of its rotation, the build block short hills as its third, the peak hills (a long goal) or
      fartlek. Easy, test, taper and race weeks keep their one.
    * A group session's length (HardDay.minutes): the quality session of a fixed hard day is filled out towards
      that length with easy time, half before and half after, as far as the week's time allows: the fill shares
      the easy minutes with the easy workouts. A test keeps its length.
    * Fixed easy days (PlanRequest.easy_days, the group's easy session): the day is always among the week's
      slots, never a hard day (a fixed hard day on the same weekday wins) and never a long workout, and its workout
      is an easy one, not a recovery workout, in the day's sport when the slot allows it. With minutes it grows
      towards that length, first among the easy workouts (FIXED_WEIGHT), also past the level's longest easy run.
      The limits around a race and in the race week hold.
    * Two long workouts in a row (PlanRequest.second_long): in every load, easy and test week with a long run,
      the day after it gets a long, easy workout in the first supporting sport when that day's slot allows the
      sport: at most three quarters of the long run, the level's longest easy run, the slot and the sport's part
      of the week, and in an easy or a test week three quarters of the last load week's, at least 60 minutes
      (else the day is as before). The day is then no hard day, the day after it
      counts as the day after a long run, and the smaller long workout every other week is not used.
  - The rolling plan: given the day it is built (``today``), only the weeks up to the detail horizon
    (the week of that day and the three after it) keep their workouts. The weeks after are sketches:
    block, kind, time and number of sessions (Week.sketch). plan.rolling.roll_forward details them
    later, with the same filling, so a week gets the same workouts whenever it is detailed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from itertools import combinations

from . import library
from .models import (BORROWS, EASY_ZONES, RUNNING, STRENGTH, WEEKDAY_KEYS, Goal, HardDay, Note, Plan, PlanRequest, Slot,
                     SportRole, Week, WeekSketch, Workout)
from .periodize import (LEVEL, MAX_WEEKLY_INCREASE, Level, Skeleton, WeekSpec, core_slots, long_cap, long_slot, periodize,
                        round5, recovery_days, second_slots)

ENGINE_VERSION = "9"           # 2: the volume grows from what the athlete trains; 3: the rolling plan; 4: several sports;
                               # 5: the bike's and the SkiErg's own workouts; 6: strength and swimming;
                               # 7: each sport's own test, and the sweet spot as a band of the threshold power;
                               # 8: two workouts a day, a smaller long workout in a supporting sport, recovery
                               #    after a race; 9: the high level, the long run after the goal's length and
                               #    fixed hard days
MAX_HARD_SHARE = 0.25         # of a week's zoned time above LT1
MIN_SESSION = 30              # minutes of weekly volume per session before another slot is used
LONG_SHARE = 0.33             # the long run's share of the week
LONG_MIN = 30
LONG_STEP = 15                # minutes the long run may grow per load week
QUALITY_MIN_SLOT = 40
PRE_RACE_MAX = (30, 40)       # minutes the day before and two days before an A or B race
RACE_WEEK_MAX = 45            # the longest easy run in the week of the goal
BEGINNER_EARLY_WEEKS = 4
PROGRESSIVE_FROM_KM = 20.0    # goals from here get race-pace minutes in the long run of the peak
STEP_BACK = 2                 # steps an easy or taper week goes back in a progression
DETAIL_WEEKS = 4              # weeks with workouts in a rolling plan: the week of the day and three more
SHARE_SHIFT = {"peak": 0.5, "taper": 0.75}   # the part of the other sports' share the main sport takes
STRENGTH_CLEAR_DAYS = 3       # no strength in the days before an A or B race
SECOND = "-2"                 # the key suffix of a day's second workout (periodize.second_slots)
SECOND_WEIGHT = 0.7           # a second workout's weight when the easy minutes are shared, as a recovery run's
SUPPORT_LONG_MIN = 60         # a supporting sport's long workout: at least this long, else none
SUPPORT_LONG_OF_MAIN = 0.75   # at most this share of the main sport's long run
RECOVERY_MAX = (30, 45)       # the longest workout in the first and the second half of the recovery after a race
FIXED_WEIGHT = 2.0            # a fixed easy day with a length, when the easy minutes are shared (an easy run: 1)
FILL = 14                     # the key offset of a quality session's fill in the sharing (a second workout: 7)
QUALITY_BASE = ("fartlek", "hills", "threshold")     # the base block's rotation


def floor5(minutes: float) -> int:
    return int(minutes // 5) * 5


@dataclass
class _State:
    steps: dict[str, int] = field(default_factory=dict)
    last_long: int | None = None
    max_minutes: int | None = None     # the biggest week so far
    base_i: int = 0
    build_i: int = 0
    sunday_hard: bool = False          # last week ended with a hard day
    load_target: int = 0               # the last load week: its target and what it really held
    load_actual: int = 0
    max_run: int = 0                   # the most running minutes in a week so far
    support_i: int = 0                 # load weeks in base and build so far: a supporting long workout every other
    sunday_pair: bool = False          # last week ended with the second long workout (PlanRequest.second_long)
    sunday_long: int = 0               # with second_long: last week's long run when it was on the Sunday, minutes
    last_pair: int = 0                 # the second long workout of the last load week that had one, minutes


def _gap(days: list[int]) -> int:
    """Smallest distance in days between any two of the weekdays, around the week."""
    if len(days) < 2:
        return 7
    d = sorted(days)
    return min([b - a for a, b in zip(d, d[1:])] + [7 - d[-1] + d[0]])


def _spread(slots: list[Slot], n: int, seeds: list[Slot]) -> list[Slot]:
    """n of the slots, as evenly spread over the week as possible (ties: the earlier weekday). ``seeds``: slots that
    are always among them (the long run's and the fixed hard days'), the first ones when n is smaller."""
    chosen = list(seeds[:n]) if seeds else slots[:1]
    while len(chosen) < n:
        rest = [s for s in slots if s not in chosen]
        chosen.append(max(rest, key=lambda s: (_gap([c.weekday for c in chosen] + [s.weekday]), -s.weekday)))
    return sorted(chosen, key=lambda s: s.weekday)


def _share(total: int, items: list[tuple[int, float, int, int]]) -> dict[int, int]:
    """Split minutes over easy sessions (weekday, weight, min, max). Sessions are dropped, lightest and
    latest first, until each gets its minimum. Never hands out more than ``total``."""
    items = list(items)
    while items and total < sum(lo for _, _, lo, _ in items):
        items.remove(min(items, key=lambda it: (it[1], -it[0])))
    out: dict[int, int] = {}
    rem = total
    pool = list(items)
    while pool:
        sw = sum(w for _, w, _, _ in pool)
        full = [it for it in pool if rem * it[1] / sw >= it[3]]
        if not full:
            break
        for it in full:
            out[it[0]] = floor5(it[3])
            rem -= out[it[0]]
            pool.remove(it)
    sw = sum(w for _, w, _, _ in pool)
    for wd, w, lo, _ in pool:
        out[wd] = max(lo, floor5(rem * w / sw))
    lows = {wd: lo for wd, _, lo, _ in items}
    highs = {wd: floor5(hi) for wd, _, _, hi in items}
    weights = {wd: w for wd, w, _, _ in items}
    while total - sum(out.values()) >= 5:            # hand out what the rounding left, heaviest session first
        room = [wd for wd in out if out[wd] + 5 <= highs[wd]]
        if not room:
            break
        out[min(room, key=lambda k: (out[k] / weights[k], k))] += 5
    while sum(out.values()) > total:
        wd = max(out, key=lambda k: (out[k] - lows[k], k))
        if out[wd] - 5 >= lows[wd]:
            out[wd] -= 5
        else:
            del out[wd]
    return out


def _quality_count(spec: WeekSpec, n: int, final: Goal | None, asked: int = 0) -> int:
    """The week's quality sessions. ``asked``: the coach's number for a load week (PlanRequest.quality_count)."""
    if n < 2:
        return 0
    if spec.kind == "easy":
        q = 1 if n >= 3 else 0
    elif spec.kind in ("test", "taper", "race"):
        q = 1
    elif asked:
        q = min(asked, max(1, n // 2))
    else:
        q = 1 if spec.block == "base" or n < 4 else 2
    races = [g for g in spec.goals if g is not final]
    if any(g.priority in ("A", "B") for g in races):
        q = min(q, 1)
    return max(0, q - sum(1 for g in races if g.priority == "C"))


def _families(spec: WeekSpec, q: int, state: _State, goal: Goal | None, early_beginner: bool) -> list[str]:
    if q <= 0:
        return []
    if spec.kind in ("test", "race", "taper", "easy"):
        return [{"test": "test", "race": "opener", "taper": "threshold", "easy": "fartlek"}[spec.kind]]
    if spec.block == "base":     # more than one only when the coach asks for it (PlanRequest.quality_sessions)
        fams = [QUALITY_BASE[(state.base_i + k) % 3] for k in range(q)]
    elif spec.block == "build":
        fams = ["vo2max", "threshold", "hills"][:q] if q >= 2 else [("threshold", "vo2max")[state.build_i % 2]]
    elif goal is not None and (goal.distance_km or 0) >= PROGRESSIVE_FROM_KM:
        fams = ["threshold", "fartlek", "hills"][:q]
    else:
        fams = ["vo2max", "threshold", "fartlek"][:q]
    if early_beginner:   # nothing above LT2 yet
        fams = ["fartlek" if library.get(f).top_zone == "Z5" else f for f in fams]
        if len(fams) == 2 and fams[0] == fams[1]:
            fams[1] = "threshold" if fams[0] == "fartlek" else "fartlek"
        fams = list(dict.fromkeys(fams))     # a family once (three asked for: two)
    return fams


def shares(roles: tuple[SportRole, ...], block: str) -> dict[str, float]:
    """Each sport's share of a week in a block: the roles' weights, shifted towards the main sport (the
    first) near the goal (SHARE_SHIFT)."""
    total = sum(r.weight for r in roles)
    k = SHARE_SHIFT.get(block, 0.0)
    out: dict[str, float] = {}
    for i, r in enumerate(roles):
        b = r.weight / total
        out[r.sport] = b + k * (1 - b) if i == 0 else b * (1 - k)
    return out


def _hard_share(workouts: list[Workout]) -> float:
    zs: dict[str, int] = {}
    for w in workouts:
        for z, s in w.zone_seconds().items():
            zs[z] = zs.get(z, 0) + s
    total = sum(zs.values())
    return 1.0 - sum(zs.get(z, 0) for z in EASY_ZONES) / total if total else 0.0


class _Filler:
    def __init__(self, request: PlanRequest, skeleton: Skeleton, plan_id: str) -> None:
        self.request, self.plan_id, self.goal = request, plan_id, skeleton.goal
        self.level: Level = LEVEL[request.level]
        self.roles = request.plan_roles()
        self.main = request.main_sport
        self.slots = core_slots(request)
        # Strength: the days of the slots that allow it, else every day with an endurance slot (as a session of its own).
        own = {}
        for s in request.availability:
            if s.role == "core" and STRENGTH in s.sports and s.max_minutes > 0 and 0 <= s.weekday <= 6:
                if s.weekday not in own or s.max_minutes > own[s.weekday].max_minutes:
                    own[s.weekday] = s
        self.strength_slots: dict[int, Slot | None] = (dict(sorted(own.items())) if own
                                                       else {s.weekday: None for s in self.slots})
        # The long run has one home all through the plan: the longest slot of the main sport, the later day on a tie.
        self.long_slot = long_slot(self.slots, self.main)
        self.long_cap = long_cap(request)
        self.seconds = second_slots(request, self.slots)
        # Fixed hard days: weekday -> the sport of its quality session ("": any), the first given for a weekday.
        self.hard: dict[int, str] = {}
        self.hard_minutes: dict[int, int] = {}     # the group session's length on a fixed hard day, when given
        for h in request.hard_days:
            if 0 <= h.weekday <= 6 and h.weekday not in self.hard:
                self.hard[h.weekday] = h.sport
                if h.minutes > 0:
                    self.hard_minutes[h.weekday] = h.minutes
        self.hard_rank = {wd: i for i, wd in enumerate(self.hard)}
        # The coach's choices: the fixed easy days (the first given for a weekday; a fixed hard day on the same
        # weekday wins) and the weekday of the second long workout, the day after the long run.
        self.easy: dict[int, HardDay] = {}
        for h in request.easy_days:
            if 0 <= h.weekday <= 6 and h.weekday not in self.hard and h.weekday not in self.easy:
                self.easy[h.weekday] = h
        self.support = next((r.sport for r in self.roles[1:] if r.role == "support"), None)
        self.pair_wd = (self.long_slot.weekday + 1) % 7 if request.second_long and self.support else None
        self.state = _State()
        first, last = skeleton.weeks[0].monday, skeleton.weeks[-1].monday + timedelta(days=6)
        self.blocked: set[date] = set()       # no quality session on these days
        self.capped: dict[date, int] = {}
        self.race_dates = {g.date for g in request.goals}
        for g in request.goals:
            if not first <= g.date <= last:
                continue
            major = g.priority in ("A", "B")
            for d in range(1, 3 if major else 2):
                self.blocked |= {g.date - timedelta(days=d), g.date + timedelta(days=d)}
            if major:
                for d, minutes in enumerate(PRE_RACE_MAX, start=1):
                    self.capped[g.date - timedelta(days=d)] = minutes
        # Recovery after a race that is not the plan's goal, also one just before the plan: easy days, none long.
        self.recovery: list[tuple[Goal, date]] = []      # the race and its last day of recovery
        self.recovering: set[date] = set()
        for g in sorted(request.goals, key=lambda g: g.date):
            days = recovery_days(g)
            if g is self.goal or days == 0 or g.date > last or g.date + timedelta(days=days) < first:
                continue
            self.recovery.append((g, g.date + timedelta(days=days)))
            for k in range(1, days + 1):
                d = g.date + timedelta(days=k)
                cap = RECOVERY_MAX[0] if k <= (days + 1) // 2 else RECOVERY_MAX[1]
                self.recovering.add(d)
                self.blocked.add(d)
                self.capped[d] = min(self.capped.get(d, cap), cap)

    def _key(self, day: date, suffix: str = "") -> str:
        iso = day.isocalendar()
        return f"{self.plan_id}-{iso.year % 100:02d}{iso.week:02d}-{WEEKDAY_KEYS[day.weekday()]}{suffix}"

    def _workout(self, day: date, template_id: str, slot: Slot | None, *, minutes: int | None = None,
                 step: int = 0, goal: Goal | None = None, suffix: str = "") -> Workout:
        tpl = library.get(template_id)
        sport = goal.sport if goal else self.main     # _sports sets the sport of the others
        return Workout(self._key(day, "-race" if goal else suffix), day, sport, template_id, tpl.family, tpl.quality,
                       library.build(template_id, minutes=minutes, step=step), slot.start if slot else None, goal)

    def _place(self, cands: list[Slot], q: int, fixed_hard: list[int]) -> list[Slot] | None:
        """The q quality days with the most fixed hard days (PlanRequest.hard_days, the ones given first before the
        others), then the most room between hard days; None when no legal choice exists."""
        best, best_score = None, None
        for combo in combinations(cands, q):
            days = [s.weekday for s in combo]
            if _gap(days) < 2:
                continue
            fixed = [self.hard_rank[d] for d in days if d in self.hard_rank]
            score = (len(fixed), -sum(fixed), _gap(days + fixed_hard))
            if best_score is None or score > best_score:
                best, best_score = list(combo), score
        return best if q else []

    def fill(self, spec: WeekSpec) -> Week:
        st, level = self.state, self.level
        final = self.goal if self.goal in spec.goals and spec.kind == "race" else None
        race_days = {g.date.weekday() for g in spec.goals}
        avail = [s for s in self.slots if s.weekday not in race_days and (final is None or s.weekday < final.date.weekday())]
        ceiling = spec.target_minutes
        if st.max_minutes is not None:
            ceiling = min(ceiling, floor5((1 + MAX_WEEKLY_INCREASE) * st.max_minutes))
        if spec.kind != "load" and st.load_target:
            # Easy, test and taper weeks are a share of what the last load week really held.
            ceiling = min(ceiling, round5(spec.target_minutes * st.load_actual / st.load_target))
        avail_days = {s.weekday for s in avail}
        seconds = [s for s in self.seconds if s.weekday in avail_days] if spec.kind not in ("taper", "race") else []
        n = min(len(avail) + len(seconds), level.max_sessions, max(1, ceiling // MIN_SESSION))
        major_race = any(g.priority in ("A", "B") for g in spec.goals)
        long_day = spec.monday + timedelta(days=self.long_slot.weekday)
        want_long = (n >= 2 and self.long_slot in avail and not major_race
                     and long_day - timedelta(days=1) not in self.race_dates    # no long run the day after a race
                     and long_day not in self.recovering)
        # The second long workout this week: the day after this week's long run, or after last week's on a Sunday.
        pair = (self.pair_wd is not None and spec.kind in ("load", "easy", "test") and not major_race
                and (want_long if self.pair_wd else st.sunday_long > 0))
        seeds = ([self.long_slot] if want_long else []) + [s for s in avail if s.weekday in self.hard
                                                             and not (want_long and s is self.long_slot)]
        seeds += [s for s in avail if s not in seeds and ((pair and s.weekday == self.pair_wd) or s.weekday in self.easy)]
        chosen = _spread(avail, min(n, len(avail)), seeds) if avail else []
        doubles = n - len(chosen)     # second workouts of a day: only once every day has one
        day = {s.weekday: spec.monday + timedelta(days=s.weekday) for s in chosen}
        long_wd = self.long_slot.weekday
        early = self.request.level == "beginner" and spec.index <= BEGINNER_EARLY_WEEKS
        cands = [s for s in chosen
                 if not (want_long and s is self.long_slot) and day[s.weekday] not in self.blocked
                 and s.max_minutes >= QUALITY_MIN_SLOT and (long_wd - s.weekday) % 7 != 1
                 and not (s.weekday == 0 and st.sunday_hard)
                 and s.weekday not in self.easy and not (pair and s.weekday == self.pair_wd)]
        fixed_hard = sorted(race_days | ({long_wd} if want_long else set()))
        support_long = (self.support if want_long and spec.kind == "load" and spec.block in ("base", "build")
                        and not spec.goals and st.support_i % 2 == 0 and not self.request.second_long else None)

        result: tuple[list[Workout], dict[str, int], int | None] | None = None
        asked = self.request.quality_count
        for q in range(min(_quality_count(spec, n, final, asked), len(cands)), -1, -1):
            q_slots = self._place(cands, q, fixed_hard)
            if q_slots is None:
                continue
            fams = _families(spec, q, st, self.goal, early)
            for down in range(max(len(library.get(f).steps) for f in fams) if fams else 1):
                result = self._assemble(spec, chosen, day, q_slots, fams, down, ceiling, want_long, seconds, doubles,
                                        support_long, pair)
                if result is not None:
                    break
            if result is not None:
                break
        workouts, used_steps, long_minutes = result if result else ([], {}, None)
        for g in spec.goals:
            workouts.append(self._workout(g.date, "race", None, goal=g))
        workouts += self._strength(spec, workouts)
        workouts.sort(key=lambda w: (w.date, w.key))

        week = Week(spec.index, spec.monday, spec.block, spec.kind, spec.target_minutes, workouts,
                    [Note.of(f"plan.week.focus.{spec.block if spec.kind == 'load' else spec.kind}")])
        for g in spec.goals:
            if g is not final:
                week.notes.append(Note.of(f"plan.week.race_{g.priority.lower()}", name=g.name))
        for g, until in self.recovery:
            if g.date + timedelta(days=1) <= spec.monday + timedelta(days=6) and until >= spec.monday:
                week.notes.append(Note.of("plan.week.recovery", name=g.name, until=until.isoformat()))
        # ---- what the next week needs to know
        if spec.kind == "load":
            for fam, step in used_steps.items():
                st.steps[fam] = min(step + 1, library.last_step(fam))
            if long_minutes:
                st.last_long = long_minutes
            st.load_target, st.load_actual = spec.target_minutes, week.minutes
            st.base_i += spec.block == "base"
            st.build_i += spec.block == "build"
            st.support_i += spec.block in ("base", "build") and not spec.goals
        st.max_minutes = max(st.max_minutes or 0, week.minutes)
        st.max_run = max(st.max_run, week.sport_minutes().get(RUNNING, 0))
        st.sunday_hard = any(w.quality and w.date.weekday() == 6 for w in workouts)
        if self.pair_wd is not None:
            st.sunday_long = (long_minutes or 0) if self.long_slot.weekday == 6 else 0
            st.sunday_pair = self.pair_wd == 6 and any(
                w.family == "long" and w.date.weekday() == 6 and w.goal is None for w in workouts)
            second = [w.minutes for w in workouts if w.family == "long" and w.goal is None
                      and w.date.weekday() == self.pair_wd]
            if spec.kind == "load" and second:
                st.last_pair = second[0]
        return week

    def _strength(self, spec: WeekSpec, workouts: list[Workout]) -> list[Workout]:
        """The week's strength sessions on the days easiest for the legs (the rule in the module's docstring).

        Among the allowed days the n days are chosen by, in order: the fewest that come the day before a hard day
        or the long run, the most days between them (two is enough), the least endurance on the days themselves
        (none, then easy, then hard or long), the earliest days. The legs session takes the chosen day with the
        most room, the other sessions follow in date order."""
        n = self.request.strength_count
        if spec.kind == "race":
            n = min(n, 1)
        if n == 0:
            return []
        days = {wd: spec.monday + timedelta(days=wd) for wd in self.strength_slots}
        closed: set[date] = set()
        for g in self.request.goals:
            closed |= {g.date, g.date - timedelta(days=1)}
            if g.priority in ("A", "B"):
                closed |= {g.date - timedelta(days=k) for k in range(1, STRENGTH_CLEAR_DAYS + 1)}
            if g is self.goal:     # nothing after the plan's goal
                closed |= {g.date + timedelta(days=k) for k in range(1, 7)}
        for g, until in self.recovery:     # the first half of a recovery
            closed |= {g.date + timedelta(days=k) for k in range(1, ((until - g.date).days + 1) // 2 + 1)}
        cands = [wd for wd in sorted(days) if days[wd] not in closed]
        if not cands:
            return []
        on: dict[date, list[Workout]] = {}
        for w in workouts:
            if w.goal is None:
                on.setdefault(w.date, []).append(w)
        heavy = {w.date for w in workouts if w.quality or w.family == "long" or w.goal is not None}
        before_hard = {wd: days[wd] + timedelta(days=1) in heavy for wd in cands}

        def own(wd: int) -> int:      # two workouts in a day count as a hard day
            ws = on.get(days[wd], [])
            return 0 if not ws else 2 if len(ws) > 1 or any(w.quality or w.family == "long" for w in ws) else 1

        best, best_key = None, None
        for combo in combinations(cands, min(n, len(cands))):
            key = (sum(before_hard[wd] for wd in combo), -min(_gap(list(combo)), 2) if len(combo) > 1 else 0,
                   sum(own(wd) for wd in combo))
            if best_key is None or key < best_key:
                best, best_key = combo, key
        assert best is not None
        if spec.kind in ("taper", "race"):
            ids = [library.STRENGTH_TAPER] * len(best)
        else:
            ids = list(library.STRENGTH_WEEK[len(best)])
        roomiest = min(best, key=lambda wd: (before_hard[wd], own(wd), wd))
        order = [roomiest] + [wd for wd in best if wd != roomiest]
        out = []
        for wd, tid in zip(order, ids):
            slot = self.strength_slots[wd]
            tpl = library.get(tid)
            out.append(Workout(self._key(days[wd], "-strength"), days[wd], STRENGTH, tid, tpl.family, tpl.quality,
                               library.build(tid), slot.start if slot else None))
        return out

    def _sports(self, spec: WeekSpec, workouts: list[Workout], slot_of: dict[int, Slot],
                second_of: dict[int, Slot], pinned: dict[str, str]) -> None:
        """Gives every workout of the week (races not among them) its sport, in place, and then the sport's
        template in the place of the running one (library.in_sport: the same minutes).

        The long run and the test first, in the main sport; then the hard sessions by date and the easy ones,
        longest first: each to the sport furthest behind its share of the week's minutes, among the sports its
        slot allows (ties: the order of the roles). A maintenance sport is never hard and gets one workout a
        week, and running is left out once it would pass 10 % above its biggest week, as long as the slot
        allows another sport. Two workouts in a day get two sports: a day's second workout that cannot is dropped.
        ``pinned``: workouts whose sport is set already (a supporting sport's long workout), by key."""
        if len(self.roles) >= 2:
            self._assign(spec, workouts, slot_of, second_of, pinned)
        for w in workouts:
            w.template_id, w.parts = library.in_sport(w.parts, w.template_id, w.sport)

    def _assign(self, spec: WeekSpec, workouts: list[Workout], slot_of: dict[int, Slot],
                second_of: dict[int, Slot], pinned: dict[str, str]) -> None:
        share = shares(self.roles, spec.block)
        total = sum(w.minutes for w in workouts)
        done = {r.sport: 0 for r in self.roles}
        count = {r.sport: 0 for r in self.roles}
        run_cap = floor5((1 + MAX_WEEKLY_INCREASE) * self.state.max_run) if self.state.max_run else None
        main_first = lambda w: (w.family == "long" and w.key not in pinned) or w.template_id == "test"
        order = sorted(workouts, key=lambda w: (0 if main_first(w) or w.key in pinned else 1 if w.quality else 2,
                                                0 if main_first(w) or w.quality else -w.minutes, w.date))
        day_of: dict[date, list[Workout]] = {}
        for w in workouts:
            day_of.setdefault(w.date, []).append(w)
        assigned: set[str] = set()

        def allowed_of(w: Workout) -> list[SportRole]:
            slot = (second_of if w.key.endswith(SECOND) else slot_of)[w.date.weekday()]
            return [r for r in self.roles if r.sport in slot.sports]

        for w in order:
            allowed = allowed_of(w)
            others = [o for o in day_of[w.date] if o is not w]
            if others:    # not the sport of the day's other workout, and leave that one a sport of its own
                free = [r for r in allowed if all(o.key not in assigned or o.sport != r.sport for o in others)]
                free = [r for r in free if all(o.key in assigned or any(x.sport != r.sport for x in allowed_of(o))
                                               for o in others)] or free
                allowed = free or allowed
            cands = ([r for r in allowed if r.role != "maintenance"] if w.quality else allowed) or allowed
            cands = [r for r in cands if r.role != "maintenance" or count[r.sport] == 0] or cands
            cands = [r for r in cands if r.sport != RUNNING or run_cap is None
                     or done[RUNNING] + w.minutes <= run_cap] or cands
            if w.key in pinned:
                pick = pinned[w.key]
            elif main_first(w) and any(r.sport == self.main for r in cands):
                pick = self.main
            else:
                pick = max(cands, key=lambda r: (share[r.sport] * total - done[r.sport], -self.roles.index(r))).sport
            w.sport = pick
            done[pick] += w.minutes
            count[pick] += 1
            assigned.add(w.key)
        for w in [w for w in workouts if w.key.endswith(SECOND)]:
            if any(o is not w and o.sport == w.sport for o in day_of[w.date]):
                workouts.remove(w)

    def _assemble(self, spec: WeekSpec, chosen: list[Slot], day: dict[int, date], q_slots: list[Slot],
                  fams: list[str], down: int, ceiling: int, want_long: bool, seconds: list[Slot], doubles: int,
                  support_long: str | None = None, pair: bool = False
                  ) -> tuple[list[Workout], dict[str, int], int | None] | None:
        st, level = self.state, self.level
        workouts: list[Workout] = []
        used: dict[str, int] = {}
        pinned: dict[str, str] = {}       # workouts whose sport is set already, by key (_sports)
        back = STEP_BACK if spec.kind in ("easy", "taper") else 0
        for slot, fam in zip(q_slots, fams):
            step = max(0, min(st.steps.get(fam, level.first_step), library.last_step(fam)) - back - down)
            while step > 0 and library.minutes_of(fam, step) > slot.max_minutes:
                step -= 1
            if library.minutes_of(fam, step) > slot.max_minutes:
                return None
            used[fam] = step
            w = self._workout(day[slot.weekday], fam, slot, step=step)
            sport = self.hard.get(slot.weekday, "")
            if sport and sport in slot.sports and sport in self.request.plan_sports and fam != "test":
                pinned[w.key] = sport     # the coach's sport on a fixed hard day
            workouts.append(w)
        left = ceiling - sum(library.minutes_of(w.template_id, used[w.family]) for w in workouts)
        if left < 0:
            return None

        long_minutes = None
        if want_long:
            want = min(self.long_slot.max_minutes, self.long_cap, max(45, round5(LONG_SHARE * ceiling)))
            if spec.kind == "load" and st.last_long:
                want = min(want, st.last_long + LONG_STEP)
            elif spec.kind in ("easy", "test"):
                want = round5(0.75 * (st.last_long or want))
            elif spec.kind == "taper":
                want = round5(0.60 * (st.last_long or want))
            want = min(floor5(want), floor5(left))
            if want >= LONG_MIN:
                long_minutes = want
                progressive = (spec.block == "peak" and spec.kind == "load" and self.goal is not None
                               and (self.goal.distance_km or 0) >= PROGRESSIVE_FROM_KM
                               and want >= library.get("long_progressive").min_minutes
                               and self.request.level != "beginner")
                workouts.append(self._workout(day[self.long_slot.weekday], "long_progressive" if progressive else "long",
                                              self.long_slot, minutes=want))
                left -= want

        # ---- the second long workout, in the supporting sport the day after the long run (PlanRequest.second_long)
        after = long_minutes if self.pair_wd else st.sunday_long
        if pair and after and self.pair_wd in day:
            d = day[self.pair_wd]
            slot = next(s for s in chosen if s.weekday == self.pair_wd)
            if (d not in {w.date for w in workouts} and self.support in slot.sports and d not in self.blocked
                    and d not in self.capped):
                part = shares(self.roles, spec.block)[self.support] * ceiling
                want = floor5(min(slot.max_minutes, level.easy_max, SUPPORT_LONG_OF_MAIN * after, part, left))
                if spec.kind != "load" and st.last_pair:      # an easy or a test week: three quarters, as the long run
                    want = min(want, round5(0.75 * st.last_pair))
                if want >= SUPPORT_LONG_MIN:
                    w = self._workout(d, "long", slot, minutes=want)
                    workouts.append(w)
                    pinned[w.key] = self.support
                    left -= want

        # ---- a smaller long workout in the supporting sport, away from the long run (the rule in the docstring)
        if support_long and long_minutes:
            near = {(self.long_slot.weekday + k) % 7 for k in (-1, 0, 1)}
            busy = {w.date for w in workouts}
            hard = {w.date for w in workouts if w.quality} | ({spec.monday - timedelta(days=1)} if st.sunday_hard else set())
            free = [s for s in chosen if s.weekday not in near and day[s.weekday] not in busy
                    and day[s.weekday] - timedelta(days=1) not in hard and support_long in s.sports
                    and day[s.weekday] not in self.blocked and day[s.weekday] not in self.capped]
            if free:
                slot = max(free, key=lambda s: (s.max_minutes, s.weekday))
                part = shares(self.roles, spec.block)[support_long] * ceiling
                want = floor5(min(slot.max_minutes, self.long_cap, SUPPORT_LONG_OF_MAIN * long_minutes,
                                  part, left))
                if want >= SUPPORT_LONG_MIN:
                    w = self._workout(day[slot.weekday], "long", slot, minutes=want)
                    workouts.append(w)
                    pinned[w.key] = support_long
                    left -= want

        # ---- the rest of the week is easy running, shared over the remaining slots
        taken = {w.date for w in workouts}
        heavy = {w.date for w in workouts} | {g.date for g in spec.goals}   # quality, long run and races
        if st.sunday_hard or self.long_slot.weekday == 6 or st.sunday_pair:
            heavy.add(spec.monday - timedelta(days=1))
        easy_slots = [s for s in chosen if day[s.weekday] not in taken]
        items = []
        kinds: dict[int, str] = {}
        for s in easy_slots:
            d = day[s.weekday]
            fixed = self.easy.get(s.weekday)      # the coach's easy day: never a recovery workout, its own length
            recovery = fixed is None and len(chosen) >= 4 and len(easy_slots) >= 2 and d - timedelta(days=1) in heavy
            tpl = library.get("recovery" if recovery else "easy")
            top = level.recovery_max if recovery else fixed.minutes if fixed and fixed.minutes > 0 else level.easy_max
            hi = min(s.max_minutes, top, self.capped.get(d, 10_000), RACE_WEEK_MAX if spec.kind == "race" else 10_000)
            if hi >= tpl.min_minutes:
                weight = 0.7 if recovery else FIXED_WEIGHT if fixed and fixed.minutes > 0 else 1.0
                items.append((s.weekday, weight, tpl.min_minutes, hi))
                kinds[s.weekday] = tpl.id
        # The fill of a group session (keyed weekday + FILL): easy time around the quality session of a fixed hard
        # day with a length, as far as the week's time allows. Not a test, not in the race week.
        if spec.kind != "race":
            for slot, w in zip(q_slots, workouts):
                room = floor5(min(self.hard_minutes.get(slot.weekday, 0), slot.max_minutes)
                              - library.minutes_of(w.template_id, used[w.family]))
                if room >= 5 and w.family != "test":
                    items.append((slot.weekday + FILL, 1.0, 0, room))
        # A day's second workout (keyed weekday + 7): easy, a recovery workout on a hard day, on the days without a
        # hard session or a long run first, never on the long run's day or around a race.
        # Only on the days the week uses (a week with fewer sessions than days has none: ``doubles`` is 0).
        extra = sorted((s for s in seconds if s.weekday in day and day[s.weekday] not in self.blocked
                        and day[s.weekday] not in self.capped
                        and not (long_minutes and s.weekday == self.long_slot.weekday)),
                       key=lambda s: (day[s.weekday] in taken, -s.max_minutes, s.weekday))[:max(0, doubles)]
        for s in extra:
            tpl = library.get("recovery" if day[s.weekday] in taken else "easy")
            hi = min(s.max_minutes, level.recovery_max if tpl.id == "recovery" else level.easy_max)
            if hi >= tpl.min_minutes:
                items.append((s.weekday + 7, SECOND_WEIGHT, tpl.min_minutes, hi))
                kinds[s.weekday + 7] = tpl.id
        by_wd = {s.weekday: s for s in chosen}
        second_of = {s.weekday: s for s in extra}
        split = _share(max(0, left), items)
        fills: dict[str, int] = {}        # the quality session's key -> the minutes of its fill
        for k, minutes in split.items():
            if k >= FILL:
                fills[self._key(day[k - FILL])] = minutes
            elif k < 7:
                w = self._workout(day[k], kinds[k], by_wd[k], minutes=minutes)
                workouts.append(w)
                fixed = self.easy.get(k)
                if fixed and fixed.sport and fixed.sport in by_wd[k].sports and fixed.sport in self.request.plan_sports:
                    pinned[w.key] = fixed.sport     # the coach's sport on a fixed easy day
            else:
                workouts.append(self._workout(day[k - 7], kinds[k], second_of[k - 7], minutes=minutes, suffix=SECOND))
        # With few slots the easy runs fill up: what is left goes to the long run, within its own limits.
        spare = floor5(left - sum(split.values()))
        if long_minutes and spec.kind == "load" and st.last_long and spare >= 5:
            room = min(self.long_slot.max_minutes, self.long_cap, st.last_long + LONG_STEP) - long_minutes
            if room >= 5:
                long_minutes += min(spare, floor5(room))
                i = next(i for i, w in enumerate(workouts) if w.family == "long")
                workouts[i] = self._workout(workouts[i].date, workouts[i].template_id, self.long_slot, minutes=long_minutes)
        # Every workout in its sport, before the checks: another sport's template may hold more hard minutes.
        self._sports(spec, workouts, by_wd, second_of, pinned)
        for w in workouts:                # the fills, once every quality session has its sport's template
            if fills.get(w.key):
                w.parts = library.fill_out(w.parts, fills[w.key])
        if sum(w.minutes for w in workouts) > ceiling or _hard_share(workouts) > MAX_HARD_SHARE:
            return None
        return workouts, used, long_minutes


def week_of(start: date, day: date) -> int:
    """The plan week (1-based) a day falls in; 0 or less before the plan's first Monday."""
    return (day - start).days // 7 + 1


def horizon_for(start: date, n_weeks: int, today: date) -> int:
    """The last detailed week on ``today``: the week of the day and the three after it, within the plan.
    Before the plan begins, its first four weeks."""
    return max(1, min(n_weeks, max(1, week_of(start, today)) + DETAIL_WEEKS - 1))


def sketch_of(week: Week) -> Week:
    """The week without its workouts: the sums stay (Week.sketch), and so do its races, which the
    athlete gave and which mark the week in the overview."""
    if week.sketch is not None:
        return week
    zm = week.zone_minutes()
    sketch = WeekSketch(week.minutes, week.sessions, week.quality_sessions, week.longest_minutes,
                        tuple((z, round(m, 2)) for z, m in sorted(zm.items())), week.strength_sessions)
    return replace(week, workouts=[w for w in week.workouts if w.goal is not None], sketch=sketch)


def fill_weeks(request: PlanRequest, skeleton: Skeleton, plan_id: str) -> list[Week]:
    """Every week of the skeleton with its workouts, in order: each week's filling depends on the ones before."""
    filler = _Filler(request, skeleton, plan_id)
    return [filler.fill(spec) for spec in skeleton.weeks]


def build_plan(request: PlanRequest, *, plan_id: str = "plan", created: datetime | None = None,
               today: date | None = None) -> Plan:
    """The plan for a request. Raises ValueError with a user-facing text when no plan can be made.

    ``created`` defaults to midnight (UTC) of the request's start date, so that the same request
    gives the same plan and the same calendar file.
    ``today``: the day the plan is built. The weeks up to ``horizon_for`` that day keep their
    workouts and the rest become sketches. None: every week is detailed.
    """
    skeleton = periodize(request)
    weeks = fill_weeks(request, skeleton, plan_id)
    horizon = None
    if today is not None:
        horizon = horizon_for(weeks[0].monday, len(weeks), today)
        weeks = [wk if wk.index <= horizon else sketch_of(wk) for wk in weeks]
    notes = list(skeleton.notes)
    cap = request.capacity_for(request.main_sport)
    if cap.anchors is None and cap.css_s100 is not None:
        pass                          # swimming as the main sport with a CSS test: paces per 100 m
    elif cap.anchors is None:
        notes.append(Note.of("plan.note.no_test"))
    elif cap.speed_from_hr:
        notes.append(Note.of("plan.note.speed_from_hr"))
    elif cap.estimated:
        notes.append(Note.of("plan.note.estimated"))
    for i, r in enumerate(request.plan_roles()):
        if request.own_capacity(r.sport) is not None:
            continue
        if r.sport in BORROWS and request.own_capacity(BORROWS[r.sport]) is not None:
            notes.append(Note.of(f"plan.note.borrowed.{r.sport}"))
        elif i > 0:
            notes.append(Note.of(f"plan.note.no_test_sport.{r.sport}"))
    created = created or datetime.combine(request.start, time(0), tzinfo=timezone.utc)
    return Plan(plan_id, request, created, ENGINE_VERSION, skeleton.blocks, weeks, notes,
                based_on={c.sport: c.tested for c in request.capacity}, horizon=horizon)
