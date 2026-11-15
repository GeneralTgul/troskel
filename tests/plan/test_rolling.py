"""The rolling plan: four weeks with workouts, the rest a sketch, and Förnya that details the next weeks
on what was saved, without filling in anything (plan.rolling, docs/TRANINGSPLAN.md)."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, timedelta

import pytest

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language
from lopanalys.plan import RUNNING, Capacity, build_plan, codec, ics, resolve
from lopanalys.plan.engine import DETAIL_WEEKS, horizon_for, week_of
from lopanalys.plan.periodize import periodize
from lopanalys.plan.rolling import due, renew_soon, roll_forward, skeleton_of
from lopanalys.report.plan_builder import render_plan_html

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


def _day(week: int, weekday: int = 2) -> date:
    return c.START + timedelta(weeks=week - 1, days=weekday)


# ---------------------------------------------------------------- the horizon
def test_horizon_is_the_week_of_the_day_and_three_more():
    n = 12
    assert horizon_for(c.START, n, c.START - timedelta(days=30)) == DETAIL_WEEKS     # before the plan begins
    assert horizon_for(c.START, n, c.START) == 4
    assert horizon_for(c.START, n, _day(1, 6)) == 4                                # Sunday is still week 1
    assert horizon_for(c.START, n, _day(2, 0)) == 5
    assert horizon_for(c.START, n, _day(11)) == n                                  # never past the plan
    assert horizon_for(c.START, n, _day(40)) == n
    assert week_of(c.START, c.START - timedelta(days=1)) == 0


@pytest.mark.parametrize("name", sorted(c.CASES))
def test_only_the_next_four_weeks_have_workouts_and_the_sketch_keeps_the_sums(name):
    full = build_plan(c.CASES[name])
    plan = build_plan(c.CASES[name], today=c.START)
    n = len(full.weeks)
    assert full.horizon is None and all(wk.detailed for wk in full.weeks)
    assert plan.horizon == min(4, n) and [wk.detailed for wk in plan.weeks] == [i <= plan.horizon for i in range(1, n + 1)]
    assert plan.detailed_until.index == plan.horizon
    for a, b in zip(plan.weeks, full.weeks):
        assert (a.index, a.monday, a.block, a.kind, a.target_minutes, a.notes) == \
            (b.index, b.monday, b.block, b.kind, b.target_minutes, b.notes)
        assert (a.minutes, a.sessions, a.quality_sessions, a.longest_minutes) == \
            (b.minutes, b.sessions, b.quality_sessions, b.longest_minutes)
        assert a.zone_minutes() == pytest.approx(b.zone_minutes(), abs=0.01)
        if not a.detailed:              # no workouts in a sketch, only the races the athlete gave
            assert all(w.goal is not None for w in a.workouts)
    assert plan.workouts == [w for wk in full.weeks[:plan.horizon] for w in wk.workouts]


def test_the_calendar_and_the_printout_have_only_the_detailed_weeks():
    plan = build_plan(c.CASES["half_12"], today=c.START)
    text = ics.to_ics(plan)
    assert text.count("BEGIN:VEVENT") == len(plan.workouts)
    assert "Halvmaran" not in text                       # the goal lies in the sketch: not in the calendar yet
    html = render_plan_html(plan)
    assert len(re.findall(r'<section class="week">', html)) == 4
    assert 'class="section plan-sketch"' in html and "Passen står till och med vecka 4" in html
    assert html.count('class="sketch"') == len(plan.weeks) - 4          # faded bars in the overview
    full = render_plan_html(build_plan(c.CASES["half_12"]))
    assert "plan-sketch" not in full.replace(".plan-sketch", "") and len(re.findall(r'<section class="week">', full)) == 12


# ---------------------------------------------------------------- rolling forward
def test_skeleton_of_the_stored_plan_is_the_engines_skeleton():
    req = c.CASES["half_12"]
    sk = skeleton_of(build_plan(req, today=c.START))
    ref = periodize(req)
    assert [(w.index, w.monday, w.block, w.kind, w.target_minutes, w.goals) for w in sk.weeks] == \
        [(w.index, w.monday, w.block, w.kind, w.target_minutes, w.goals) for w in ref.weeks]
    assert sk.goal == ref.goal


@pytest.mark.parametrize("name", sorted(c.CASES))
def test_rolling_forward_gives_the_same_workouts_as_the_whole_plan(name):
    full = build_plan(c.CASES[name])
    plan = build_plan(c.CASES[name], today=c.START)
    for day in range(0, 7 * len(full.weeks) + 7, 3):
        plan = roll_forward(plan, c.START + timedelta(days=day))
        assert plan.horizon == horizon_for(c.START, len(full.weeks), c.START + timedelta(days=day))
    assert plan.weeks == full.weeks
    # the same plan and the same day always give the same plan
    a = roll_forward(build_plan(c.CASES[name], today=c.START), _day(3))
    b = roll_forward(build_plan(c.CASES[name], today=c.START), _day(3))
    assert a == b


def test_nothing_due_returns_the_plan_as_it_is():
    plan = build_plan(c.CASES["half_12"], today=_day(3))
    assert due(plan, _day(3)) == 0 and roll_forward(plan, _day(3)) is plan
    assert roll_forward(plan, _day(1)) is plan                          # a day back changes nothing either
    full = build_plan(c.CASES["half_12"])
    assert due(full, _day(9)) == 0 and roll_forward(full, _day(9)) is full   # saved before the rolling plan


def test_rolling_never_changes_a_detailed_week():
    plan = build_plan(c.CASES["half_12"], today=c.START)
    # what the athlete did, and a week as an older engine filled it: both stay as saved
    w0 = replace(plan.weeks[0].workouts[0], status="done")
    first = replace(plan.weeks[0], workouts=[w0] + plan.weeks[0].workouts[1:])
    second = replace(plan.weeks[1], workouts=plan.weeks[1].workouts[:-1])
    plan = replace(plan, weeks=[first, second] + plan.weeks[2:], engine_version="2")
    rolled = roll_forward(plan, _day(3))
    assert rolled.horizon == 6 and rolled.engine_version != "2"
    assert rolled.weeks[:4] == plan.weeks[:4]
    assert rolled.weeks[0].workouts[0].status == "done"
    assert rolled.weeks[6:] == plan.weeks[6:]                            # the sketch after the new horizon
    assert rolled.request == plan.request and rolled.blocks == plan.blocks and rolled.notes == plan.notes
    full = build_plan(c.CASES["half_12"])
    assert rolled.weeks[4:6] == full.weeks[4:6]


def test_a_new_test_moves_the_targets_and_rolls_on_the_same_path():
    plan = build_plan(c.CASES["half_12"], today=c.START)
    moved = roll_forward(resolve.retest(plan, BETTER, from_date=_day(4)), _day(4))
    assert moved.request.capacity_for(RUNNING) == BETTER and moved.horizon == 7
    assert moved.weeks == build_plan(c.CASES["half_12"]).weeks[:7] + plan.weeks[7:]   # the test never moves a workout
    assert moved.capacity_on(RUNNING, _day(1)) == c.TESTED


def test_renew_soon_is_the_last_week_of_workouts():
    plan = build_plan(c.CASES["half_12"], today=c.START)        # workouts to the Sunday of week 4
    end = plan.detailed_until.sunday
    assert not renew_soon(plan, end - timedelta(days=7))
    assert renew_soon(plan, end - timedelta(days=6)) and renew_soon(plan, end + timedelta(days=3))
    last = roll_forward(plan, _day(12))
    assert last.horizon == 12 and not renew_soon(last, _day(12))      # nothing left to detail
    assert not renew_soon(build_plan(c.CASES["half_12"]), _day(10))


# ---------------------------------------------------------------- storage
@pytest.mark.parametrize("today", [c.START, _day(6)])
def test_a_sketched_plan_survives_json(today):
    plan = build_plan(c.CASES["marathon_trained"], today=today)
    back = _roundtrip(plan)
    assert back == plan and back.horizon == plan.horizon and back.engine_version == plan.engine_version
    assert _roundtrip(roll_forward(back, _day(9))) == roll_forward(plan, _day(9))


def test_a_plan_saved_before_the_rolling_plan_reads_as_fully_detailed():
    plan = build_plan(c.CASES["half_12"])
    old = json.loads(json.dumps(codec.plan_to_dict(plan)))
    del old["horizon"]
    old["format"] = 1
    old["engine_version"] = "1"
    for wk in old["weeks"]:
        assert "sketch" not in wk
    back = codec.plan_from_dict(old, plan.request)
    assert back.horizon is None and all(wk.detailed for wk in back.weeks) and back.engine_version == "1"
    assert back.workouts == plan.workouts and roll_forward(back, _day(8)) is back
