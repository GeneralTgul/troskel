"""A smaller long workout in a supporting sport (#666, docs/TRANINGSPLAN.md, *Ett mindre långpass i en stödjande
idrott*): every other load week in base and build, away from the main sport's long run, within the sport's share."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from lopanalys.plan import RUNNING, SportRole, build_plan
from lopanalys.plan.engine import SUPPORT_LONG_MIN, SUPPORT_LONG_OF_MAIN, shares

from . import cases as c


def _supporting_longs(plan):
    main = plan.request.main_sport
    return [(wk, w) for wk in plan.weeks for w in wk.workouts if w.family == "long" and w.sport != main]


def test_a_longer_bike_ride_every_other_week_for_a_runner():
    plan = build_plan(c.CASES["four_sports_marathon"], plan_id="t")
    found = _supporting_longs(plan)
    assert len(found) >= 4
    assert {w.sport for _, w in found} == {"bike"}                  # the first supporting sport
    assert all(w.template_id == "bike_long" and not w.quality for _, w in found)
    weeks = [wk.index for wk, _ in found]
    assert all(b - a >= 2 for a, b in zip(weeks, weeks[1:])), weeks    # never two weeks in a row


def test_only_in_the_load_weeks_of_base_and_build_without_a_race():
    for name in ("four_sports_marathon", "triathlon_swim", "run_bike_12"):
        for wk, _ in _supporting_longs(build_plan(c.CASES[name], plan_id="t")):
            assert wk.kind == "load" and wk.block in ("base", "build"), (name, wk.index)
            assert not any(w.goal for w in wk.workouts), (name, wk.index)


def test_never_beside_the_long_run_nor_after_a_hard_day():
    for name in ("four_sports_marathon", "triathlon_swim"):
        plan = build_plan(c.CASES[name], plan_id="t")
        main = plan.request.main_sport
        long_days = {w.date for w in plan.workouts if w.family == "long" and w.sport == main}
        hard = {w.date for w in plan.workouts if w.quality}
        for _, w in _supporting_longs(plan):
            assert not any(abs((w.date - d).days) <= 1 for d in long_days), (name, w.key)
            assert w.date - timedelta(days=1) not in hard, (name, w.key)


def test_smaller_than_the_long_run_and_within_the_sport_s_share():
    plan = build_plan(c.CASES["four_sports_marathon"], plan_id="t")
    roles = plan.request.plan_roles()
    for wk, w in _supporting_longs(plan):
        main_long = max(x.minutes for x in wk.workouts if x.family == "long" and x.sport == RUNNING)
        assert SUPPORT_LONG_MIN <= w.minutes <= SUPPORT_LONG_OF_MAIN * main_long
        assert w.minutes <= shares(roles, wk.block)["bike"] * wk.target_minutes
        assert wk.minutes <= wk.target_minutes


def test_no_supporting_long_workout_without_a_supporting_sport():
    assert _supporting_longs(build_plan(c.CASES["half_12"], plan_id="t")) == []
    maintenance = replace(c.CASES["four_sports_marathon"], roles=(SportRole(RUNNING, "main"),
                                                                  SportRole("bike", "maintenance")))
    assert _supporting_longs(build_plan(maintenance, plan_id="t")) == []
