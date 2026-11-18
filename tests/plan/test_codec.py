"""The plan as JSON and back, and a retest that only moves the targets from a given day."""
from __future__ import annotations

import json
from datetime import date

import pytest

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language
from lopanalys.plan import RUNNING, Capacity, build_plan, codec, resolve
from lopanalys.plan.text import workout_lines

from . import cases as c

BETTER = Capacity(RUNNING, Anchors(193, 45, 167, 177, 13.8, 15.0), date(2027, 1, 8), "Laktattrappa")


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _roundtrip(plan):
    req = json.loads(json.dumps(codec.request_to_dict(plan.request)))
    body = json.loads(json.dumps(codec.plan_to_dict(plan)))
    return codec.plan_from_dict(body, codec.request_from_dict(req, plan.request.athlete_name))


def _target(plan, w):
    return workout_lines(w, plan.capacity_on(RUNNING, w.date), plan.zone_system)[1]["target"]


@pytest.mark.parametrize("name", sorted(c.CASES))
def test_every_case_survives_json(name):
    plan = build_plan(c.CASES[name])
    assert _roundtrip(plan) == plan


def test_the_name_is_not_stored():
    plan = build_plan(c.CASES["half_12"])
    assert "Exempel" not in json.dumps(codec.request_to_dict(plan.request))
    assert "Exempel" not in json.dumps(codec.plan_to_dict(plan))
    assert codec.request_from_dict(codec.request_to_dict(plan.request)).athlete_name == ""


def test_retest_from_a_day_keeps_the_earlier_targets():
    plan = build_plan(c.CASES["half_12"])
    switch = date(2027, 1, 11)
    new = resolve.retest(plan, BETTER, from_date=switch)
    assert new.weeks is plan.weeks                                 # no workout is touched or moved
    before = next(w for w in plan.workouts if w.template_id == "threshold" and w.date < switch)
    after = next(w for w in plan.workouts if w.template_id == "threshold" and w.date >= switch)
    assert _target(new, before) == _target(plan, before) == "170–174 slag/min · 4:21–4:10 min/km"
    assert _target(new, after) != _target(plan, after) and "177" in _target(new, after)
    assert _roundtrip(new) == new                                  # the log of earlier tests is stored too


def test_retest_without_a_day_moves_the_whole_plan():
    plan = build_plan(c.CASES["half_12"])
    new = resolve.retest(plan, BETTER)
    assert new.capacity_log == ()
    assert new.capacity_on(RUNNING, plan.workouts[0].date) is new.request.capacity_for(RUNNING)


def test_two_retests_keep_each_period():
    plan = build_plan(c.CASES["three_days_52"])
    second = Capacity(RUNNING, Anchors(194, 44, 169, 179, 14.2, 15.4), date(2027, 4, 2), "Laktattrappa")
    new = resolve.retest(resolve.retest(plan, BETTER, from_date=date(2027, 1, 11)), second, from_date=date(2027, 4, 5))
    assert new.capacity_on(RUNNING, date(2026, 12, 1)) == c.TESTED
    assert new.capacity_on(RUNNING, date(2027, 2, 1)) == BETTER
    assert new.capacity_on(RUNNING, date(2027, 5, 1)) == second
    # a day at or before one already covered does not rewrite what was handed out
    again = resolve.retest(new, BETTER, from_date=date(2027, 2, 1))
    assert again.capacity_log == new.capacity_log


def test_the_sport_key_is_core_sports_and_old_plans_read_running_as_run():
    from lopanalys.core.sport import Sport
    assert RUNNING == Sport.RUN.value == "run"
    plan = build_plan(c.CASES[sorted(c.CASES)[0]])
    old_req = json.loads(json.dumps(codec.request_to_dict(plan.request)).replace('"run"', '"running"'))
    old_body = json.loads(json.dumps(codec.plan_to_dict(plan)).replace('"run"', '"running"'))
    assert "running" in json.dumps(old_body)                     # a plan saved before core.sport
    back = codec.plan_from_dict(old_body, codec.request_from_dict(old_req, plan.request.athlete_name))
    assert back == plan and {w.sport for wk in back.weeks for w in wk.workouts} == {"run"}


def test_a_zones_test_keeps_its_speed_line_in_json():
    from lopanalys.plan.models import SpeedLine
    line = SpeedLine(8.0, 60.0, 8.0, 12.0, 15.0)
    cap = Capacity(RUNNING, Anchors(max_hr=190), date(2026, 11, 13), "Puls- och fartzoner", line)
    plan = build_plan(c.request(weeks=12, capacity=(cap,)))
    back = _roundtrip(plan)
    assert back == plan and back.request.capacity_for(RUNNING).speed_line == line
    assert resolve.targets(back.request.capacity_for(RUNNING))["Z3"].pace_text == "6:36–5:13 min/km"
    # a capacity without a line is stored as before, and an old plan without the key reads back
    d = codec.capacity_to_dict(BETTER)
    assert "speed_line" not in d and codec.capacity_from_dict(d) == BETTER
