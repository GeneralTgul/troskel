"""Recovery after a race (#666, docs/TRANINGSPLAN.md, *Återhämtning efter ett lopp*): after an A race that is not the
plan's goal and after a long B race, a day per 3 km in running is easy: no hard session, no long run, short workouts."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.plan import Goal, build_plan
from lopanalys.plan.engine import RECOVERY_MAX
from lopanalys.plan.periodize import recovery_days

from . import cases as c


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield


def test_the_days_follow_the_distance():
    run = lambda km, prio="A": recovery_days(Goal("x", date(2027, 1, 2), distance_km=km, priority=prio))
    assert run(42.2) == 15 and run(21.1) == 8 and run(10) == 4
    assert run(5) == 0                                   # the two easy days every A race has already hold
    assert run(21.1, "B") == 8 and run(10, "B") == 0     # a B race only when it is long
    assert run(42.2, "C") == 0
    assert run(200) == 21                                # never more than three weeks
    assert recovery_days(Goal("x", date(2027, 1, 2))) == 7                       # an A race without a distance
    assert recovery_days(Goal("x", date(2027, 1, 2), priority="B")) == 0
    assert recovery_days(Goal("x", date(2027, 1, 2), sport="bike", distance_km=90)) == 6
    assert recovery_days(Goal("x", date(2027, 1, 2), sport="ski", distance_km=90)) == 9


def _window(plan, race: date, days: int):
    return [w for w in plan.workouts if race < w.date <= race + timedelta(days=days)]


def test_after_a_long_milestone_the_days_are_easy_and_short():
    plan = build_plan(c.CASES["half_b_recovery"], plan_id="t")
    race = date(2027, 1, 2)
    days = 8
    window = _window(plan, race, days)
    assert window
    for w in window:
        assert not w.quality and w.family != "long" and not w.key.endswith("-2"), w.key
        first_half = (w.date - race).days <= (days + 1) // 2
        if not w.strength:
            assert w.minutes <= RECOVERY_MAX[0 if first_half else 1], w.key
        else:
            assert not first_half, w.key                # no strength in the first half
    # the week after the window is a normal week again
    after = [w for w in plan.workouts if race + timedelta(days=days) < w.date <= race + timedelta(days=days + 7)]
    assert any(w.quality for w in after) and any(w.family == "long" for w in after)


def test_the_weeks_of_the_recovery_say_so():
    plan = build_plan(c.CASES["half_b_recovery"], plan_id="t")
    texts = {wk.index: [n.text for n in wk.notes] for wk in plan.weeks}
    note = "Återhämtning efter Halvmaran i vinter: lugna pass, inga hårda pass och inget långpass till och med 2027-01-10."
    assert [i for i, ts in texts.items() if note in ts] == [7, 8]


def test_an_earlier_a_race_gets_recovery_and_the_goal_does_not():
    first = Goal("Vintermaran", date(2027, 1, 2), distance_km=42.2)
    plan = build_plan(c.request(goals=(c.HALF, first)), plan_id="t")
    assert plan.goal == c.HALF
    window = _window(plan, first.date, 15)
    assert window and not any(w.quality or w.family == "long" for w in window)
    assert not any("Återhämtning efter Halvmaran" in n.text for wk in plan.weeks for n in wk.notes)


def test_a_short_milestone_changes_nothing():
    with_ten = build_plan(c.CASES["half_12"], plan_id="t")          # the seeding race is 10 km
    assert not any(n.key == "plan.week.recovery" for wk in with_ten.weeks for n in wk.notes)


def test_a_race_just_before_the_plan_opens_it_with_recovery():
    before = Goal("Förra maran", date(2026, 11, 14), distance_km=42.2, priority="B")     # two days before the start
    plan = build_plan(c.request(weeks=12, goals=(before,)), plan_id="t")
    assert [n.text for n in plan.notes] == [
        "Förra maran var före schemat. Schemat börjar med återhämtning: lugna pass till och med 2026-11-29."]
    window = _window(plan, before.date, 15)
    assert window and not any(w.quality or w.family == "long" for w in window)
    assert all(w.minutes <= RECOVERY_MAX[1] for w in window)
    long_ago = Goal("I somras", date(2026, 8, 1), distance_km=42.2, priority="B")
    plan = build_plan(c.request(weeks=12, goals=(long_ago,)), plan_id="t")
    assert [n.key for n in plan.notes] == ["plan.note.goal_outside"]
