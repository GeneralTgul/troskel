"""The plan's next test (#778, step 4): the day the coach plans the athlete's next test, after which the targets of
the workouts fit worse and worse.

A plan can run for half a year, but its targets come from one test (or the athlete's own values, #777). The athlete
gets better or worse, so the paces and watts drift away from what the athlete can do; a new test gives new targets
(services.apply_retest). The app shows the day under *Schemat*, and after it says that the targets build on the
test from that day and fit worse and worse (app/src/model/nexttest.ts, the same rules).

The rules:
  - ``Plan.next_test`` is the coach's day, chosen in Tröskel; None (the default) gives ``default_next_test``.
  - The default: the test day of the plan's first test week in the 8th to 12th week after the test of the main
    sport (a week that begins 7 to 11 weeks after it), or after the plan's start when there is no test or the test
    is older (the workout of the test family, the main sport first; the Monday of a sketched week). The engine puts
    a retest in the week after 7 full weeks of training (periodize.py). Without such a test week: 10 weeks after.
  - A new test in the main sport moves a chosen day as far after the new test as it was after the old one
    (``after_retest``); without a chosen day the default follows the new test by itself.
  - Nothing here changes the workouts: the engine places its test weeks as before (periodize.py).

``basis`` says what the targets build on, for the app's line: ``("test", day)``, ``("own", None)`` for own values,
``("none", None)`` for a plan without a test (max HR only).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from .models import Plan

WINDOW_WEEKS = (8, 12)        # the default's window after the test
FALLBACK_WEEKS = 10           # the default without a test day in the window


def basis(plan: Plan) -> tuple[str, date | None]:
    """What the targets of the main sport build on: ("test", the test's day), ("own", None) or ("none", None)."""
    cap = plan.request.capacity_for(plan.request.main_sport)
    if cap.own_values:
        return "own", None
    if cap.anchors is not None and cap.tested is not None:
        return "test", cap.tested
    return "none", None


def anchor(plan: Plan) -> date:
    """The day the default counts from: the test of the main sport, or the plan's start when there is no test or
    the test is older than the plan (a test from the summer must not give a next test before the plan begins)."""
    tested = basis(plan)[1]
    return max(tested, plan.start) if tested else plan.start


def default_next_test(plan: Plan) -> date:
    """The test day of the first test week in the 8th to 12th week after ``anchor``, else 10 weeks after it."""
    start = anchor(plan)
    lo, hi = start + timedelta(weeks=WINDOW_WEEKS[0] - 1), start + timedelta(weeks=WINDOW_WEEKS[1] - 1)
    main = plan.request.main_sport
    for wk in plan.weeks:
        if wk.kind != "test" or not lo <= wk.monday <= hi:
            continue
        tests = sorted((w for w in wk.workouts if w.family == "test"), key=lambda w: (w.sport != main, w.date))
        return tests[0].date if tests else wk.monday
    return start + timedelta(weeks=FALLBACK_WEEKS)


def next_test(plan: Plan) -> date:
    """The day of the next test: the coach's (``Plan.next_test``) or the default."""
    return plan.next_test or default_next_test(plan)


def after_retest(before: Plan, after: Plan) -> Plan:
    """``after`` (the plan laid on a new test) with the coach's day moved as far after the new test as it was after
    the old one (after ``anchor``: the test, or the plan's start when the test was older). Only a new test in the main sport moves it; without a chosen day nothing is stored."""
    if before.next_test is None:
        return after
    old, new = basis(before), basis(after)
    if new[0] != "test" or new[1] == old[1]:
        return after
    return replace(after, next_test=anchor(after) + (before.next_test - anchor(before)))


__all__ = ["FALLBACK_WEEKS", "WINDOW_WEEKS", "after_retest", "anchor", "basis", "default_next_test", "next_test"]
