"""Engine 9: the high level, the long run after the goal's length and fixed hard days (docs/TRANINGSPLAN.md)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.plan import ENGINE_VERSION, Goal, HardDay, build_plan, codec, link
from lopanalys.plan.models import LEVELS
from lopanalys.plan.periodize import LEVEL, LONG_CAP_MAX, long_cap, week_capacity

from . import cases as c


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _quality_days(week):
    return sorted({w.date.weekday() for w in week.workouts if w.quality and w.goal is None and not w.strength})


def test_engine_version_9():
    assert ENGINE_VERSION == "9"
    assert "high" in LEVELS and "high" in LEVEL


# ---------------------------------------------------------------- 1. the high level


def test_the_high_level_holds_eleven_to_twelve_and_a_half_hours():
    req = c.CASES["high_ski"]
    assert week_capacity(req) >= 12 * 60
    plan = build_plan(req)
    load = [wk.minutes for wk in plan.weeks if wk.kind == "load" and wk.block in ("build", "peak")]
    assert max(load) >= 11 * 60, load
    assert max(wk.sessions for wk in plan.weeks) >= 9


def test_the_high_level_has_longer_easy_runs_within_its_limits():
    plan = build_plan(c.CASES["high_ski"])
    easy = [w.minutes for w in plan.workouts if w.family == "easy" and not w.key.endswith("-2")]
    recovery = [w.minutes for w in plan.workouts if w.family == "recovery"]
    assert max(easy) > 75 and max(easy) <= LEVEL["high"].easy_max
    assert max(recovery) <= LEVEL["high"].recovery_max
    trained = build_plan(replace(c.CASES["high_ski"], level="trained"))
    assert max(w.minutes for w in trained.workouts if w.family == "easy") <= 75
    assert max(w.minutes for w in trained.workouts if w.family == "recovery") <= 45
    assert max(wk.sessions for wk in trained.weeks) <= LEVEL["trained"].max_sessions


def test_the_lower_levels_are_as_before():
    assert [(lv.easy_max, lv.recovery_max) for k, lv in LEVEL.items() if k != "high"] == [(75, 45)] * 3


# ---------------------------------------------------------------- 2. the long run after the goal's length


@pytest.mark.parametrize("level, goal, cap", [
    ("trained", Goal("Vasaloppet", date(2027, 3, 7), "ski", 90), 270),
    ("high", Goal("Vasaloppet", date(2027, 3, 7), "ski", 90), 270),
    ("recreational", Goal("Vasaloppet", date(2027, 3, 7), "ski", 90), 240),
    ("trained", Goal("Milen", date(2027, 3, 7), "run", 10), 180),
    ("recreational", Goal("Maraton", date(2027, 3, 7), "run", 42.2), 150),
    ("trained", Goal("Skidlopp", date(2027, 3, 7), "ski", 45), 180),
    ("trained", Goal("Ultra", date(2027, 3, 7), "run", 100), LONG_CAP_MAX),
    ("trained", Goal("Utan längd", date(2027, 3, 7), "ski", None), 180),
])
def test_long_cap_follows_the_goal(level, goal, cap):
    assert long_cap(replace(c.request(level=level), goals=(goal,))) == cap


def test_no_goal_keeps_the_levels_cap():
    assert long_cap(c.request(weeks=12, level="trained")) == 180


def test_a_ninety_km_ski_race_gets_long_runs_up_to_four_and_a_half_hours():
    plan = build_plan(c.CASES["long_ski_trained"])
    longs = [wk.longest_minutes for wk in plan.weeks]
    assert max(longs) == 270
    assert all(m <= 270 for m in longs)
    # a 10 km race with the same slots keeps today's ceiling
    ten = build_plan(replace(c.CASES["long_ski_trained"], goals=(Goal("Milen", c.LONG_SKI.date, "ski", 10),)))
    assert max(wk.longest_minutes for wk in ten.weeks) <= 180


# ---------------------------------------------------------------- 3. fixed hard days


def test_the_quality_session_goes_to_the_fixed_day():
    plain = build_plan(replace(c.CASES["half_hard_tuesday"], hard_days=()))
    fixed = build_plan(c.CASES["half_hard_tuesday"])
    assert any(1 not in _quality_days(wk) for wk in plain.weeks)      # Tuesday is not the engine's own choice
    for wk in fixed.weeks:
        q = _quality_days(wk)
        if q and wk.kind != "race":
            assert 1 in q, (wk.index, q)


def test_the_first_fixed_day_wins_when_the_week_has_one_quality_session():
    plan = build_plan(c.CASES["high_ski"])
    for wk in plan.weeks:
        q = _quality_days(wk)
        if len(q) == 1 and wk.kind in ("load", "easy"):
            assert q == [3], (wk.index, q)
        if len(q) == 2:
            assert q == [0, 3], (wk.index, q)


def test_the_fixed_days_sport():
    plan = build_plan(c.CASES["high_ski"])
    thursday = [w for w in plan.workouts if w.quality and w.date.weekday() == 3 and w.family != "test"]
    monday = [w for w in plan.workouts if w.quality and w.date.weekday() == 0]
    assert thursday and all(w.sport == "ski" for w in thursday)
    assert monday and all(w.sport == "run" for w in monday)
    bike = build_plan(c.CASES["run_bike_hard_bike"])
    assert all(w.sport == "bike" for w in bike.workouts if w.quality and w.date.weekday() == 3 and w.family != "test")


def test_a_fixed_day_before_the_long_run_stays_easy():
    plan = build_plan(c.CASES["run_bike_hard_bike"])        # Saturday is fixed, the long run is on Sunday
    assert not any(w.quality for w in plan.workouts if w.date.weekday() == 5 and w.goal is None)


def test_never_two_hard_days_in_a_row_with_adjacent_fixed_days():
    req = replace(c.request(c.SEVEN, weeks=12, level="trained", recent=320), hard_days=(HardDay(2), HardDay(3)))
    plan = build_plan(req)
    hard = sorted({w.date for w in plan.workouts if w.quality})
    assert all(b - a > timedelta(days=1) for a, b in zip(hard, hard[1:]))
    assert any(2 in _quality_days(wk) for wk in plan.weeks)


def test_a_fixed_day_is_always_among_the_weeks_slots():
    # four sessions from seven days: without the fixed Wednesday the engine would spread them elsewhere
    req = replace(c.request(c.SEVEN, weeks=8, level="recreational", recent=150), hard_days=(HardDay(2),))
    plan = build_plan(req)
    for wk in plan.weeks:
        assert any(w.date.weekday() == 2 for w in wk.workouts), wk.index


def test_no_hard_days_is_the_plan_before():
    for name in ("half_12", "two_a_day", "ski_main"):
        req = c.CASES[name]
        assert build_plan(replace(req, hard_days=())) == build_plan(req)


# ---------------------------------------------------------------- storing and the link


def test_hard_days_are_stored_and_read_back():
    req = c.CASES["high_ski"]
    d = codec.request_to_dict(req)
    assert d["hard_days"] == [{"weekday": 3, "sport": "ski"}, {"weekday": 0, "sport": "run"}]
    back = codec.request_from_dict(d, req.athlete_name)
    assert back.hard_days == req.hard_days
    no_sport = codec.request_to_dict(c.CASES["half_hard_tuesday"])
    assert no_sport["hard_days"] == [{"weekday": 1}]
    assert "hard_days" not in codec.request_to_dict(c.CASES["half_12"])


def test_the_short_link_carries_the_hard_days_and_the_level():
    plan = build_plan(c.CASES["high_ski"], plan_id="p")
    stored = link.stored_plan(plan)
    short = link.short_plan(stored)
    assert short is not None and short["v"] == "9"
    assert short["r"]["hd"] == [[3, "ski"], [0, "run"]] and short["r"]["l"] == "high"
    assert link.expand(short) == stored
    old = link.pack_request(codec.request_to_dict(c.CASES["half_12"]))
    assert "hd" not in old


def test_second_slots_in_a_week_with_fewer_sessions_than_days():
    # The engine read the day of a second slot the week did not use (KeyError) when a week had fewer sessions than days.
    req = replace(c.CASES["high_ski"], recent_weekly_minutes=None, level="recreational", hard_days=())
    plan = build_plan(req, today=date(2026, 12, 23))
    assert plan.weeks
