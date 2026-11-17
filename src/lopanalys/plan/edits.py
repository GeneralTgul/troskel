"""The athlete's changes of single workouts (#777, step 3): move a workout to another day, swap two, shorten one.

The changes are kept as a list on the plan (``Plan.edits``), each with the workout's key, the new day and the new
length, and laid on the weeks again after everything that makes workouts anew: the app's new times
(``phone.rebuild``, app/src/engine/index.ts), *Förnya* and a new test (``rolling.roll_forward``,
``services.apply_retest``), a new plan from the coach with the same id (the app's ``keptEdits``, Tröskel's form). So a
change does not disappear when the athlete saves new times or a new test comes (#776). The app has the same rules
(app/src/engine/edits.ts), checked with the fixtures of app/scripts/make_fixtures.py.

The rules, a pure function of the plan (no clock):
  - An edit is laid on the workout with its key in a detailed week; a key that is no longer there (the new times put
    no workout on that day), and a race or a test, drop the edit from the list.
  - ``date`` moves the workout within its week (a day outside the week is left alone). The workout gets the clock
    time of the new day's slot (the first slot that day that holds its sport, else the day's first), or none. A swap
    is two moves. The key stays, so the athlete's marks follow the workout.
  - ``minutes`` shortens the workout (``shorten``); a workout as short or shorter is left as it is.
  - Laying the edits again on a plan that has them changes nothing.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, time

from .models import Edit, Part, Plan, PlanRequest, Workout

MIN_PART_S = 5 * 60          # a part is never cut below five minutes
KEEP_WARM_S = 10 * 60        # the warm-up and the cool-down keep ten minutes until the repeats are gone
_FIXED = ("warmup", "cooldown", "work")


def editable(w: Workout) -> bool:
    """A workout the athlete may move and shorten: not a race, not the retest."""
    return w.goal is None and not any(p.kind in ("test", "race") for p in w.parts)


def shorten(parts: list[Part], minutes: int) -> list[Part]:
    """The parts cut to at most ``minutes``, in this order: the steady parts (the longest first) down to five minutes,
    the cool-down and the warm-up down to ten, whole repeats of an interval set (the last set first, down to one), the
    cool-down and the warm-up down to five. When a repeat took more than was needed, the warm-up and then the cool-down
    get the rest back (up to their own length). The result is never longer than ``minutes`` unless it cannot be
    shorter."""
    out = list(parts)
    over = sum(p.total_s for p in out) - minutes * 60
    if over <= 0:
        return out

    def cut(i: int, floor: int) -> None:
        nonlocal over
        room = out[i].duration_s - floor
        if over > 0 and room > 0:
            c = min(room, over)
            out[i] = replace(out[i], duration_s=out[i].duration_s - c)
            over -= c

    steady = sorted((i for i, p in enumerate(out) if p.kind not in _FIXED and p.repeat == 1),
                    key=lambda i: (-out[i].duration_s, i))
    for i in steady:
        cut(i, MIN_PART_S)
    ends = [i for kind in ("cooldown", "warmup") for i in reversed(range(len(out))) if out[i].kind == kind]
    for i in ends:
        cut(i, KEEP_WARM_S)
    for i in reversed(range(len(out))):
        while over > 0 and out[i].repeat > 1:
            p = out[i]
            out[i] = replace(p, repeat=p.repeat - 1)
            over -= p.duration_s + p.rest_s
    for i in ends:
        cut(i, MIN_PART_S)
    for i in reversed(ends):          # a repeat took too much: the warm-up, then the cool-down, get it back
        if over < 0:
            back = min(-over, parts[i].duration_s - out[i].duration_s)
            out[i] = replace(out[i], duration_s=out[i].duration_s + back)
            over += back
    return out


def shortest(w: Workout) -> int:
    """The fewest minutes ``shorten`` can cut the workout to."""
    return round(sum(p.total_s for p in shorten(w.parts, 0)) / 60)


def start_on(request: PlanRequest, day: date, sport: str) -> time | None:
    """The clock time of a workout moved to ``day``: the first slot that day that holds its sport, else the day's
    first slot; None when the day has no slot or the slot no time."""
    slots = [s for s in request.availability if s.weekday == day.weekday()]
    fit = [s for s in slots if not s.sports or sport in s.sports]
    pick = (fit or slots or [None])[0]
    return pick.start if pick else None


def apply_edits(plan: Plan) -> Plan:
    """The plan with its edits laid on the weeks, and the edits whose workout is gone dropped. The same plan when it
    has none."""
    if not plan.edits:
        return plan
    weeks = list(plan.weeks)
    kept: list[Edit] = []
    for e in plan.edits:
        at = next(((i, j) for i, wk in enumerate(weeks) if wk.detailed
                   for j, w in enumerate(wk.workouts) if w.key == e.key), None)
        if at is None or not editable(weeks[at[0]].workouts[at[1]]):
            continue
        kept.append(e)
        wk = weeks[at[0]]
        w = wk.workouts[at[1]]
        if e.minutes is not None:
            w = replace(w, parts=shorten(w.parts, e.minutes))
        if e.date is not None and e.date != w.date and wk.monday <= e.date <= wk.sunday:
            w = replace(w, date=e.date, start=start_on(plan.request, e.date, w.sport))
        workouts = list(wk.workouts)
        workouts[at[1]] = w
        weeks[at[0]] = replace(wk, workouts=sorted(workouts, key=lambda x: (x.date, x.key)))
    return replace(plan, weeks=weeks, edits=tuple(kept))


def merged(new: tuple[Edit, ...], old: tuple[Edit, ...]) -> tuple[Edit, ...]:
    """The edits of a new plan with the same id, and the old plan's for the workouts the new one has none for."""
    have = {e.key for e in new}
    return new + tuple(e for e in old if e.key not in have)


__all__ = ["KEEP_WARM_S", "MIN_PART_S", "apply_edits", "editable", "merged", "shorten", "shortest", "start_on"]
