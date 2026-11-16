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
  - Several sports (#666, Tom 2026-11-15): the week is filled as above over every sport at once, so the
    hard days, the long run and the ceilings count over all sports. Then every workout gets its sport
    (``_Filler._sports``): the long run and the test in the main sport, the rest to the sport furthest
    behind its share of the week among the sports its slot allows. The shares shift towards the main sport
    in the peak (half of the rest) and the taper (three quarters). A maintenance sport gets at most one
    workout a week and never a hard one, and running grows by at most 10 % a week of its own minutes.
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
from .models import (BORROWS, EASY_ZONES, RUNNING, WEEKDAY_KEYS, Goal, Note, Plan, PlanRequest, Slot, SportRole, Week,
                     WeekSketch, Workout)
from .periodize import LEVEL, MAX_WEEKLY_INCREASE, Level, Skeleton, WeekSpec, core_slots, long_slot, periodize, round5

ENGINE_VERSION = "4"           # 2: the volume grows from what the athlete trains; 3: the rolling plan; 4: several sports
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


def _gap(days: list[int]) -> int:
    """Smallest distance in days between any two of the weekdays, around the week."""
    if len(days) < 2:
        return 7
    d = sorted(days)
    return min([b - a for a, b in zip(d, d[1:])] + [7 - d[-1] + d[0]])


def _spread(slots: list[Slot], n: int, seed: Slot | None) -> list[Slot]:
    """n of the slots, as evenly spread over the week as possible (ties: the earlier weekday)."""
    chosen = [seed] if seed is not None else slots[:1]
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


def _quality_count(spec: WeekSpec, n: int, final: Goal | None) -> int:
    if n < 2:
        return 0
    if spec.kind == "easy":
        q = 1 if n >= 3 else 0
    elif spec.kind in ("test", "taper", "race"):
        q = 1
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
    if spec.block == "base":
        fams = [("fartlek", "hills", "threshold")[state.base_i % 3]]
    elif spec.block == "build":
        fams = ["vo2max", "threshold"] if q >= 2 else [("threshold", "vo2max")[state.build_i % 2]]
    elif goal is not None and (goal.distance_km or 0) >= PROGRESSIVE_FROM_KM:
        fams = ["threshold", "fartlek"][:q]
    else:
        fams = ["vo2max", "threshold"][:q]
    if early_beginner:   # nothing above LT2 yet
        fams = ["fartlek" if library.get(f).top_zone == "Z5" else f for f in fams]
        if len(fams) == 2 and fams[0] == fams[1]:
            fams[1] = "threshold" if fams[0] == "fartlek" else "fartlek"
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
        # The long run has one home all through the plan: the longest slot of the main sport, the later day on a tie.
        self.long_slot = long_slot(self.slots, self.main)
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

    def _key(self, day: date, suffix: str = "") -> str:
        iso = day.isocalendar()
        return f"{self.plan_id}-{iso.year % 100:02d}{iso.week:02d}-{WEEKDAY_KEYS[day.weekday()]}{suffix}"

    def _workout(self, day: date, template_id: str, slot: Slot | None, *, minutes: int | None = None,
                 step: int = 0, goal: Goal | None = None) -> Workout:
        tpl = library.get(template_id)
        sport = goal.sport if goal else self.main     # _sports sets the sport of the others
        return Workout(self._key(day, "-race" if goal else ""), day, sport, template_id, tpl.family, tpl.quality,
                       library.build(template_id, minutes=minutes, step=step), slot.start if slot else None, goal)

    def _place(self, cands: list[Slot], q: int, fixed_hard: list[int]) -> list[Slot] | None:
        """The q quality days with the most room between hard days; None when no legal choice exists."""
        best, best_score = None, -1
        for combo in combinations(cands, q):
            days = [s.weekday for s in combo]
            if _gap(days) < 2:
                continue
            score = _gap(days + fixed_hard)
            if score > best_score:
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
        n = min(len(avail), level.max_sessions, max(1, ceiling // MIN_SESSION))
        major_race = any(g.priority in ("A", "B") for g in spec.goals)
        long_day = spec.monday + timedelta(days=self.long_slot.weekday)
        want_long = (n >= 2 and self.long_slot in avail and not major_race
                     and long_day - timedelta(days=1) not in self.race_dates)   # no long run the day after a race
        chosen = _spread(avail, n, self.long_slot if want_long else None) if avail else []
        day = {s.weekday: spec.monday + timedelta(days=s.weekday) for s in chosen}
        long_wd = self.long_slot.weekday
        early = self.request.level == "beginner" and spec.index <= BEGINNER_EARLY_WEEKS
        cands = [s for s in chosen
                 if not (want_long and s is self.long_slot) and day[s.weekday] not in self.blocked
                 and s.max_minutes >= QUALITY_MIN_SLOT and (long_wd - s.weekday) % 7 != 1
                 and not (s.weekday == 0 and st.sunday_hard)]
        fixed_hard = sorted(race_days | ({long_wd} if want_long else set()))

        result: tuple[list[Workout], dict[str, int], int | None] | None = None
        for q in range(min(_quality_count(spec, n, final), len(cands)), -1, -1):
            q_slots = self._place(cands, q, fixed_hard)
            if q_slots is None:
                continue
            fams = _families(spec, q, st, self.goal, early)
            for down in range(max(len(library.get(f).steps) for f in fams) if fams else 1):
                result = self._assemble(spec, chosen, day, q_slots, fams, down, ceiling, want_long)
                if result is not None:
                    break
            if result is not None:
                break
        workouts, used_steps, long_minutes = result if result else ([], {}, None)
        self._sports(spec, workouts, {s.weekday: s for s in chosen})
        for g in spec.goals:
            workouts.append(self._workout(g.date, "race", None, goal=g))
        workouts.sort(key=lambda w: (w.date, w.key))

        week = Week(spec.index, spec.monday, spec.block, spec.kind, spec.target_minutes, workouts,
                    [Note.of(f"plan.week.focus.{spec.block if spec.kind == 'load' else spec.kind}")])
        for g in spec.goals:
            if g is not final:
                week.notes.append(Note.of(f"plan.week.race_{g.priority.lower()}", name=g.name))
        # ---- what the next week needs to know
        if spec.kind == "load":
            for fam, step in used_steps.items():
                st.steps[fam] = min(step + 1, library.last_step(fam))
            if long_minutes:
                st.last_long = long_minutes
            st.load_target, st.load_actual = spec.target_minutes, week.minutes
            st.base_i += spec.block == "base"
            st.build_i += spec.block == "build"
        st.max_minutes = max(st.max_minutes or 0, week.minutes)
        st.max_run = max(st.max_run, week.sport_minutes().get(RUNNING, 0))
        st.sunday_hard = any(w.quality and w.date.weekday() == 6 for w in workouts)
        return week

    def _sports(self, spec: WeekSpec, workouts: list[Workout], slot_of: dict[int, Slot]) -> None:
        """Gives every workout of the week (races not among them) its sport, in place.

        The long run and the test first, in the main sport; then the hard sessions by date and the easy ones,
        longest first: each to the sport furthest behind its share of the week's minutes, among the sports its
        slot allows (ties: the order of the roles). A maintenance sport is never hard and gets one workout a
        week, and running is left out once it would pass 10 % above its biggest week, as long as the slot
        allows another sport."""
        if len(self.roles) < 2:
            return
        share = shares(self.roles, spec.block)
        total = sum(w.minutes for w in workouts)
        done = {r.sport: 0 for r in self.roles}
        count = {r.sport: 0 for r in self.roles}
        run_cap = floor5((1 + MAX_WEEKLY_INCREASE) * self.state.max_run) if self.state.max_run else None
        main_first = lambda w: w.family == "long" or w.template_id == "test"
        order = sorted(workouts, key=lambda w: (0 if main_first(w) else 1 if w.quality else 2,
                                                0 if main_first(w) or w.quality else -w.minutes, w.date))
        for w in order:
            allowed = [r for r in self.roles if r.sport in slot_of[w.date.weekday()].sports]
            cands = ([r for r in allowed if r.role != "maintenance"] if w.quality else allowed) or allowed
            cands = [r for r in cands if r.role != "maintenance" or count[r.sport] == 0] or cands
            cands = [r for r in cands if r.sport != RUNNING or run_cap is None
                     or done[RUNNING] + w.minutes <= run_cap] or cands
            if main_first(w) and any(r.sport == self.main for r in cands):
                pick = self.main
            else:
                pick = max(cands, key=lambda r: (share[r.sport] * total - done[r.sport], -self.roles.index(r))).sport
            w.sport = pick
            done[pick] += w.minutes
            count[pick] += 1

    def _assemble(self, spec: WeekSpec, chosen: list[Slot], day: dict[int, date], q_slots: list[Slot],
                  fams: list[str], down: int, ceiling: int, want_long: bool,
                  ) -> tuple[list[Workout], dict[str, int], int | None] | None:
        st, level = self.state, self.level
        workouts: list[Workout] = []
        used: dict[str, int] = {}
        back = STEP_BACK if spec.kind in ("easy", "taper") else 0
        for slot, fam in zip(q_slots, fams):
            step = max(0, min(st.steps.get(fam, level.first_step), library.last_step(fam)) - back - down)
            while step > 0 and library.minutes_of(fam, step) > slot.max_minutes:
                step -= 1
            if library.minutes_of(fam, step) > slot.max_minutes:
                return None
            used[fam] = step
            workouts.append(self._workout(day[slot.weekday], fam, slot, step=step))
        left = ceiling - sum(library.minutes_of(w.template_id, used[w.family]) for w in workouts)
        if left < 0:
            return None

        long_minutes = None
        if want_long:
            want = min(self.long_slot.max_minutes, level.long_cap, max(45, round5(LONG_SHARE * ceiling)))
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

        # ---- the rest of the week is easy running, shared over the remaining slots
        taken = {w.date for w in workouts}
        heavy = {w.date for w in workouts} | {g.date for g in spec.goals}   # quality, long run and races
        if st.sunday_hard or self.long_slot.weekday == 6:
            heavy.add(spec.monday - timedelta(days=1))
        easy_slots = [s for s in chosen if day[s.weekday] not in taken]
        items = []
        for s in easy_slots:
            d = day[s.weekday]
            recovery = len(chosen) >= 4 and len(easy_slots) >= 2 and d - timedelta(days=1) in heavy
            tpl = library.get("recovery" if recovery else "easy")
            hi = min(s.max_minutes, tpl.max_minutes, self.capped.get(d, 10_000),
                     RACE_WEEK_MAX if spec.kind == "race" else 10_000)
            if hi >= tpl.min_minutes:
                items.append((s.weekday, 0.7 if recovery else 1.0, tpl.min_minutes, hi))
        kinds = {wd: ("recovery" if w < 1.0 else "easy") for wd, w, _, _ in items}
        by_wd = {s.weekday: s for s in chosen}
        shares = _share(max(0, left), items)
        for wd, minutes in shares.items():
            workouts.append(self._workout(day[wd], kinds[wd], by_wd[wd], minutes=minutes))
        # With few slots the easy runs fill up: what is left goes to the long run, within its own limits.
        spare = floor5(left - sum(shares.values()))
        if long_minutes and spec.kind == "load" and st.last_long and spare >= 5:
            room = min(self.long_slot.max_minutes, level.long_cap, st.last_long + LONG_STEP) - long_minutes
            if room >= 5:
                long_minutes += min(spare, floor5(room))
                i = next(i for i, w in enumerate(workouts) if w.family == "long")
                workouts[i] = self._workout(workouts[i].date, workouts[i].template_id, self.long_slot, minutes=long_minutes)
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
                        tuple((z, round(m, 2)) for z, m in sorted(zm.items())))
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
    if cap.anchors is None:
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
