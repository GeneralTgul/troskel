"""The coach's choices in the request (docs/TRANINGSPLAN.md, *Coachens val*): the number of quality sessions, a
group session's length, fixed easy days, two long workouts in a row and the coach's easy weeks. All are off by
default: a request without them gives the plan it gave before (the app's fixtures hold those plans)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from lopanalys.plan import RUNNING, HardDay, build_plan, codec, library
from lopanalys.plan.edits import shorten
from lopanalys.plan.engine import SUPPORT_LONG_MIN, SUPPORT_LONG_OF_MAIN
from lopanalys.plan.periodize import LEVEL, coach_easy, periodize, week_capacity
from lopanalys.plan.rolling import roll_forward

from . import cases as c

CHOICES = ("high_ski_choices", "high_ski_choices_low", "run_bike_choices", "three_quality_cycle")


def _plan(name: str):
    return build_plan(c.CASES[name], plan_id="t")


def _on(wk, weekday: int):
    return [w for w in wk.workouts if w.date.weekday() == weekday and not w.strength and w.goal is None]


# ---- off by default

def test_a_request_without_choices_is_stored_as_before():
    d = codec.request_to_dict(c.CASES["high_ski"])
    assert not {"quality_sessions", "easy_days", "second_long", "easy_weeks", "max_weekly_minutes"} & set(d)
    assert all("minutes" not in h for h in d["hard_days"])


@pytest.mark.parametrize("name", CHOICES)
def test_the_choices_are_stored_and_read_back(name):
    r = c.CASES[name]
    d = codec.request_to_dict(r)
    assert codec.request_from_dict(d, r.athlete_name) == r
    assert d.get("quality_sessions") == r.quality_sessions
    assert [date.fromisoformat(x) for x in d["easy_weeks"]] == list(r.easy_weeks)


# ---- the number of quality sessions

def test_two_quality_sessions_in_the_base_block_when_the_coach_asks():
    asked, plain = _plan("high_ski_choices"), _plan("high_ski")
    base = lambda p: [wk.quality_sessions for wk in p.weeks if wk.block == "base" and wk.kind == "load"]
    assert set(base(plain)) == {1} and set(base(asked)) == {2}
    for wk in asked.weeks:                        # the fixed hard days first: Thursday on skis, Monday running
        if wk.block == "base" and wk.kind == "load":
            assert [(w.date.weekday(), w.sport) for w in wk.workouts if w.quality] == [(0, RUNNING), (3, "ski")]
        if wk.kind in ("easy", "test", "taper", "race"):
            assert len([w for w in wk.workouts if w.quality and w.goal is None]) <= 1


def test_three_quality_sessions_have_three_families_and_a_day_between():
    plan = _plan("three_quality_cycle")
    threes = [wk for wk in plan.weeks if wk.quality_sessions == 3]
    assert threes and all(wk.kind == "load" for wk in threes)
    for wk in threes:
        hard = [w for w in wk.workouts if w.quality]
        assert len({w.family for w in hard}) == 3
        days = sorted(w.date for w in hard)
        assert all((b - a).days >= 2 for a, b in zip(days, days[1:]))
        assert wk.easy_share >= 0.75


def test_the_number_is_at_most_half_the_weeks_sessions():
    r = c.request(c.THREE, weeks=12, quality=3)
    for wk in build_plan(r, plan_id="t").weeks:
        assert wk.quality_sessions <= 1


# ---- a group session's length

def test_the_group_session_is_filled_out_with_easy_time():
    plan = _plan("high_ski_choices")
    thursdays = [w for wk in plan.weeks if wk.kind != "race" for w in _on(wk, 3) if w.quality and w.family != "test"]
    assert thursdays and max(w.minutes for w in thursdays) >= 100
    for w in thursdays:
        assert w.minutes <= 105
        step = next(st for st in library.get(w.template_id).steps
                    if [p for p in st if p.kind == "work"] == [p for p in w.parts if p.kind == "work"])
        extra = w.seconds - sum(p.total_s for p in step)
        assert extra >= 0 and extra % 300 == 0                       # whole five minutes, all of them easy
        warm = w.parts[0].duration_s - step[0].duration_s
        cool = w.parts[-1].duration_s - step[-1].duration_s
        assert warm + cool == extra and 0 <= cool - warm <= 60       # half before, half after
    mondays = [w for wk in plan.weeks for w in _on(wk, 0) if w.quality]
    assert mondays and all(tuple(w.parts) in library.get(w.template_id).steps for w in mondays)   # no length given


def test_the_fill_gives_way_when_the_week_is_small():
    full, low = _plan("high_ski_choices"), _plan("high_ski_choices_low")
    first = lambda p: next(w for w in _on(p.weeks[0], 3) if w.quality)
    assert first(low).minutes < first(full).minutes
    for a, b in zip(full.weeks, low.weeks):
        assert b.minutes <= b.target_minutes and a.minutes <= a.target_minutes


def test_a_test_keeps_its_length_and_the_race_week_has_no_fill():
    plan = _plan("high_ski_choices")
    tests = [w for w in plan.workouts if w.family == "test"]
    assert tests and all(w.minutes == 60 for w in tests)
    openers = [w for w in plan.weeks[-1].workouts if w.family == "opener"]
    assert openers and all(tuple(w.parts) in library.get(w.template_id).steps for w in openers)


def test_a_filled_session_can_be_shortened():
    plan = _plan("high_ski_choices")
    w = max((w for w in plan.workouts if w.quality and w.date.weekday() == 3 and w.family != "test"),
            key=lambda w: w.minutes)
    cut = shorten(w.parts, 60)
    assert sum(p.total_s for p in cut) <= 60 * 60
    assert [p for p in cut if p.kind == "work"] == [p for p in w.parts if p.kind == "work"]   # the easy time first


def test_the_week_holds_the_group_sessions_length():
    r = c.request(c.SEVEN, weeks=12, level="trained")
    assert week_capacity(replace(r, easy_days=(HardDay(1, minutes=90),))) == week_capacity(r) + 15   # 90 for 75


# ---- fixed easy days

@pytest.mark.parametrize("name,weekday,sport,minutes", [("high_ski_choices", 1, "ski", 105),
                                                         ("high_ski_choices_low", 1, "ski", 105),
                                                         ("run_bike_choices", 3, "bike", 60)])
def test_the_fixed_easy_day_is_easy_in_its_sport(name, weekday, sport, minutes):
    plan = _plan(name)
    seen = 0
    for wk in plan.weeks:
        for w in _on(wk, weekday)[:1]:            # the day's first workout (a second one is another sport)
            seen += 1
            assert (w.family, w.quality, w.sport) == ("easy", False, sport), (wk.index, w.template_id)
            assert w.minutes <= minutes
    assert seen >= len(plan.weeks) - 2            # every week it can: not on a race day
    assert max(w.minutes for wk in plan.weeks for w in _on(wk, weekday)[:1]) >= minutes - 15   # in the full weeks


def test_the_day_after_a_hard_day_stays_an_easy_workout_not_a_recovery_one():
    plain = _plan("high_ski")
    assert any(w.family == "recovery" for wk in plain.weeks for w in _on(wk, 1)[:1])     # Tuesday after Monday's
    asked = _plan("high_ski_choices")
    assert not any(w.family == "recovery" for wk in asked.weeks for w in _on(wk, 1)[:1])


def test_a_fixed_hard_day_wins_over_an_easy_day_on_the_same_weekday():
    r = replace(c.CASES["half_hard_tuesday"], easy_days=(HardDay(1, minutes=75),))
    assert build_plan(r, plan_id="t").weeks == build_plan(c.CASES["half_hard_tuesday"], plan_id="t").weeks


# ---- two long workouts in a row

def test_a_long_run_the_day_after_the_long_ski():
    plan = _plan("high_ski_choices")
    level = LEVEL[plan.request.level]
    pairs = 0
    for wk in plan.weeks:
        main = [w for w in wk.workouts if w.family == "long" and w.sport == "ski"]
        second = [w for w in wk.workouts if w.family == "long" and w.sport != "ski"]
        if wk.kind in ("taper", "race"):
            assert not second
            continue
        assert len(main) == 1 and len(second) == 1, wk.index
        pairs += 1
        a, b = main[0], second[0]
        assert b.date == a.date + timedelta(days=1) and b.sport == RUNNING and not b.quality
        assert SUPPORT_LONG_MIN <= b.minutes <= min(SUPPORT_LONG_OF_MAIN * a.minutes, level.easy_max)
    assert pairs >= 12


def test_the_second_long_follows_last_weeks_long_run_on_a_sunday():
    plan = _plan("run_bike_choices")
    found = 0
    for prev, wk in zip(plan.weeks, plan.weeks[1:]):
        monday = [w for w in _on(wk, 0) if w.family == "long"]
        had_long = any(w.family == "long" and w.sport == RUNNING and w.date.weekday() == 6 for w in prev.workouts)
        if monday:
            found += 1
            assert had_long and monday[0].sport == "bike" and wk.kind in ("load", "easy", "test")
    assert found >= 4
    assert not [w for w in _on(plan.weeks[0], 0) if w.family == "long"]       # no long run before the first week


def test_a_fixed_hard_day_after_the_long_run_wins_over_the_second_long():
    r = replace(c.CASES["run_bike_choices"], hard_days=(HardDay(0, RUNNING),))       # the long run is on the Sunday
    plan = build_plan(r, plan_id="t")
    assert not [w for wk in plan.weeks for w in _on(wk, 0) if w.family == "long"]
    assert sum(1 for wk in plan.weeks for w in _on(wk, 0) if w.quality) >= 4
    easy = replace(c.CASES["run_bike_choices"], easy_days=(HardDay(0, "bike", 45),))    # an easy day gives way
    assert [w for wk in build_plan(easy, plan_id="t").weeks for w in _on(wk, 0) if w.family == "long"]


def test_no_second_long_without_the_choice_or_a_supporting_sport():
    r = replace(c.CASES["high_ski_choices"], second_long=False)
    for wk in build_plan(r, plan_id="t").weeks:
        for w in wk.workouts:
            if w.family == "long" and w.sport != "ski":      # the smaller one every other week: never beside the long
                assert w.date.weekday() not in (4, 5, 6)
    alone = c.request(c.SEVEN, weeks=12, level="trained", second_long=True)
    assert build_plan(alone, plan_id="t").weeks == build_plan(replace(alone, second_long=False), plan_id="t").weeks


# ---- the coach's easy weeks

BASE = ["load", "easy", "load", "load", "load", "easy", "load", "load", "load", "easy", "load", "load", "load", "taper", "race"]


@pytest.mark.parametrize("marked,want", [
    ([5], "lellellllellltr"),        # the week after moves: four load weeks, then the next easy week
    ([4], "lllelelllellltr"),        # the week before moves: the plan opens with three load weeks
    ([3], "llellelllellltr"),
    ([8], "lelllelelellltr"),        # none can move without five load weeks in a row: both stay easy
    ([1], "lelllelllellltr"),        # never the first week
    ([6], "lelllelllellltr"),        # an easy week already
    ([14], "lelllelllellltr"),       # never the taper
    ([5, 9], "lellelllelllltr"),
])
def test_an_easy_week_of_the_coach_moves_the_nearest_counted_one(marked, want):
    kinds = list(BASE)
    coach_easy(kinds, marked, 3)
    assert "".join(k[0] for k in kinds) == want


def test_christmas_as_an_easy_week():
    r = c.CASES["high_ski_choices"]
    plain = [w.kind for w in periodize(replace(r, easy_weeks=())).weeks]
    asked = periodize(r).weeks
    i = (date(2026, 12, 21) - c.START).days // 7
    assert plain[i] == "load" and asked[i].kind == "easy" and plain[i + 1] == "easy" and asked[i + 1].kind == "load"
    assert asked[i].target_minutes < asked[i - 1].target_minutes
    assert [w.kind for w in asked].count("test") == plain.count("test")


def test_easy_weeks_in_a_plan_without_a_goal_keep_the_tests():
    plan = _plan("three_quality_cycle")
    kinds = [wk.kind for wk in plan.weeks]
    assert kinds[-1] == "test" and kinds[(date(2026, 12, 21) - c.START).days // 7] == "easy"
    assert kinds[(date(2027, 2, 15) - c.START).days // 7] in ("easy", "test")


# ---- the most minutes a week

def test_the_week_grows_to_the_coachs_ceiling_and_stops():
    r = c.CASES["marathon_trained"]
    plain = build_plan(r, plan_id="t")
    asked = build_plan(replace(r, max_weekly_minutes=420), plan_id="t")
    assert max(wk.minutes for wk in plain.weeks) > 480
    assert max(wk.target_minutes for wk in asked.weeks) == 420 and max(wk.minutes for wk in asked.weeks) <= 420
    assert asked.weeks[0].minutes == plain.weeks[0].minutes            # the same start: what the athlete trains
    assert week_capacity(replace(r, max_weekly_minutes=420)) == 420
    assert week_capacity(replace(r, max_weekly_minutes=5000)) == week_capacity(r)


def test_a_ceiling_below_what_the_athlete_trains_caps_the_start():
    r = replace(c.CASES["half_12"], max_weekly_minutes=150)
    plan = build_plan(r, plan_id="t")
    assert all(wk.minutes <= 150 for wk in plan.weeks)
    assert any(n.key == "plan.note.capped" for n in plan.notes)
    assert week_capacity(replace(r, max_weekly_minutes=10)) == 60       # never below an hour


# ---- the rolling plan and the same plan twice

@pytest.mark.parametrize("name", CHOICES)
def test_rolled_weeks_are_the_weeks_of_the_whole_plan(name):
    whole = build_plan(c.CASES[name], plan_id="t")
    rolling = build_plan(c.CASES[name], plan_id="t", today=c.START)
    assert rolling.horizon == 4
    rolled = roll_forward(rolling, c.START + timedelta(weeks=7))
    assert rolled.weeks[:rolled.horizon] == whole.weeks[:rolled.horizon]
    assert build_plan(c.CASES[name], plan_id="t") == whole


def test_the_apps_fixtures_for_the_choices_are_up_to_date():
    """app/test/fixtures/choices.json.gz, which the app's engine is checked against: run
    ``python -m tests.plan.choices_fixtures``."""
    from . import choices_fixtures
    assert not choices_fixtures.stale()
