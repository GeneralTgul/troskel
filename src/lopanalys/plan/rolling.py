"""The rolling plan: the weeks ahead get their workouts as the time comes, without filling in anything.

A plan is built whole (blocks, weekly time and the goal lie fixed as a sketch), but only the week of
the day it is built and the three after it have workouts (engine.DETAIL_WEEKS). ``roll_forward``
details the next weeks from the week the athlete stands in, on what was saved: the same request,
the same sketch, the same plan id. It is a pure function of the stored plan and the day: no clock,
no randomness, no Qt, so it can be ported as it stands.

The rules (docs/TRANINGSPLAN.md, "Det rullande schemat"):
  - A week that is already detailed is never rebuilt: past weeks and the weeks handed out stay as
    they were saved, with their status.
  - A new week gets the workouts the engine fills it with when every week before it is filled in
    order from week 1 on the stored sketch. The filling does not look at the test, so the same plan
    and the same day give the same workouts, and a week gets the same workouts whether it was
    detailed when the plan was built or later.
  - A new test moves only the targets (resolve.retest); the workouts follow the same path.
  - The athlete's changes of single workouts (``Plan.edits``, #777) are laid on the weeks again (plan.edits), so a
    workout moved or shortened in a new week keeps the change.
  - The dates that do not work for the athlete (``Plan.unavailable``, #846) get no workouts in a new week (a race
    stays), as the app's renewal of its own plan (app/src/model/request.ts renewOwn).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from .edits import apply_edits
from .engine import ENGINE_VERSION, fill_weeks, horizon_for
from .models import Plan, Week
from .periodize import Skeleton, WeekSpec


def skeleton_of(plan: Plan) -> Skeleton:
    """The stored plan's sketch as the engine's skeleton: every week's block, kind, time and races."""
    goals = sorted(plan.request.goals, key=lambda g: g.date)
    specs = [WeekSpec(wk.index, wk.monday, wk.block, wk.kind, wk.target_minutes,
                      [g for g in goals if wk.monday <= g.date <= wk.monday + timedelta(days=6)])
             for wk in plan.weeks]
    a_goals = [g for g in goals if g.priority == "A"]
    return Skeleton(specs, plan.blocks, [], a_goals[-1] if a_goals else None)


def due(plan: Plan, today: date) -> int:
    """How many weeks ``roll_forward`` would detail on ``today`` (0: nothing to do)."""
    if plan.horizon is None or not plan.weeks:
        return 0
    return max(0, horizon_for(plan.start, len(plan.weeks), today) - plan.horizon)


def renew_soon(plan: Plan, today: date, days: int = 7) -> bool:
    """True when the detailed part ends within ``days`` and there are weeks after it to detail."""
    if plan.horizon is None or plan.horizon >= len(plan.weeks):
        return False
    return (plan.detailed_until.sunday - today).days < days


def without_off_days(week: Week, days: tuple[date, ...] | list[date]) -> Week:
    """``week`` without its workouts on the dates that do not work (``Plan.unavailable``); a race stays, as in the
    app's ``rebuild`` (app/src/engine/index.ts). The same week when none of them falls in it."""
    off = set(days)
    if not off or not any(w.date in off and w.goal is None for w in week.workouts):
        return week
    return replace(week, workouts=[w for w in week.workouts if w.goal is not None or w.date not in off])


def roll_forward(plan: Plan, today: date) -> Plan:
    """The stored plan with the weeks up to four weeks from ``today`` detailed. Everything else is kept:
    the request (form, test), the blocks, the notes, the detailed weeks, the sketch of the rest.
    Returns the plan unchanged when nothing is due (and a plan without a horizon is detailed already)."""
    if due(plan, today) == 0:
        return plan
    assert plan.horizon is not None
    new = horizon_for(plan.start, len(plan.weeks), today)
    filled = fill_weeks(plan.request, skeleton_of(plan), plan.id)
    weeks: list[Week] = []
    for old, fresh in zip(plan.weeks, filled):
        weeks.append(without_off_days(fresh, plan.unavailable) if plan.horizon < old.index <= new else old)
    return apply_edits(replace(plan, weeks=weeks, horizon=new, engine_version=ENGINE_VERSION))


__all__ = ["due", "renew_soon", "roll_forward", "skeleton_of", "without_off_days"]
