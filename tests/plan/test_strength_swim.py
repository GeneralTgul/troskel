"""Strength and swimming (#666, step 3), one test per rule (docs/TRANINGSPLAN.md, "Styrka" and "Simning")."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from lopanalys.assessments import css
from lopanalys.core.i18n import set_language
from lopanalys.plan import build_plan, codec, ics, library, link, resolve, text
from lopanalys.plan.models import MAX_STRENGTH, RUNNING, STRENGTH, SWIM
from lopanalys.plan.rolling import roll_forward

from . import cases as c

STRENGTH_CASES = ("run_strength_2", "run_strength_slots_3", "triathlon_swim")


@pytest.fixture(autouse=True)
def _sv():
    set_language("sv")
    yield
    set_language("sv")


@pytest.fixture(scope="module", params=STRENGTH_CASES)
def plan(request):
    return build_plan(c.CASES[request.param], plan_id=request.param)


def _strength(wk):
    return [w for w in wk.workouts if w.strength]


# ---------------------------------------------------------------- strength: how many, and where


def test_the_coach_sets_the_number_and_it_holds_every_week(plan):
    n = plan.request.strength_count
    for wk in plan.weeks:
        got = len(_strength(wk))
        assert got == wk.strength_sessions
        if wk.kind == "race":
            assert got <= 1, wk.index
        else:
            assert got == n, (wk.index, got)


def test_no_more_than_three_and_none_without_the_number():
    assert all(not _strength(wk) for wk in build_plan(c.CASES["half_12"]).weeks)
    many = build_plan(replace(c.CASES["run_strength_slots_3"], strength_sessions=7))
    assert MAX_STRENGTH == 3 and max(len(_strength(wk)) for wk in many.weeks) == 3


def test_strength_comes_on_top_and_leaves_the_endurance_week_as_it_was(plan):
    """The endurance week is filled first and never changes for strength: the same workouts as without it."""
    without = build_plan(replace(plan.request, strength_sessions=0), plan_id=plan.id)
    for a, b in zip(plan.weeks, without.weeks):
        assert a.endurance == b.workouts
        assert a.minutes == b.minutes and a.sessions == b.sessions and a.quality_sessions == b.quality_sessions


def test_never_the_day_before_a_hard_day_or_the_long_run(plan):
    """When the week leaves room for it. Three sessions in three strength slots (Monday, Wednesday, Friday) leave
    none: then the legs session is never the one before the hard day."""
    for wk in plan.weeks:
        heavy = {w.date for w in wk.workouts if w.quality or w.family == "long" or w.goal}
        for w in _strength(wk):
            if w.date + timedelta(days=1) in heavy:
                assert plan.id == "run_strength_slots_3" and w.template_id != "strength_legs", w.key


def test_never_on_a_race_day_the_day_before_or_the_three_days_before_an_a_or_b_race(plan):
    for g in plan.request.goals:
        closed = {g.date, g.date - timedelta(days=1)}
        if g.priority in ("A", "B"):
            closed |= {g.date - timedelta(days=k) for k in (1, 2, 3)}
        assert not [w.key for w in plan.workouts if w.strength and w.date in closed]
    final = plan.goal
    if final:
        assert not [w for w in plan.workouts if w.strength and w.date > final.date]


def test_a_day_between_two_strength_sessions():
    p = build_plan(c.CASES["run_strength_slots_3"])
    for wk in p.weeks:
        days = sorted(w.date for w in _strength(wk))
        assert all((b - a).days >= 2 for a, b in zip(days, days[1:])), wk.index


def test_the_lightest_days_first():
    """Strength slots on Monday, Wednesday and Friday. Wednesday is the day before Thursday's hard run, so two
    sessions take Monday and Friday; one takes Friday, the day without running and before an easy run."""
    p = build_plan(replace(c.CASES["run_strength_slots_3"], strength_sessions=2))
    wk = p.weeks[0]
    assert sorted(w.date.weekday() for w in _strength(wk)) == [0, 4]
    one = build_plan(replace(c.CASES["run_strength_slots_3"], strength_sessions=1))
    assert [w.date.weekday() for w in _strength(one.weeks[0])] == [4]
    assert all(w.start is not None and w.start.hour == 7 for w in _strength(wk))   # the strength slot's time


def test_without_strength_slots_on_the_days_of_the_endurance_slots_at_any_time(plan):
    if any("strength" in s.sports for s in plan.request.availability):
        return
    days = {s.weekday for s in plan.request.availability}
    for w in plan.workouts:
        if w.strength:
            assert w.date.weekday() in days and w.start is None


def test_the_legs_take_the_day_with_most_room_and_the_taper_the_maintenance_session(plan):
    for wk in plan.weeks:
        ids = [w.template_id for w in _strength(wk)]
        if wk.kind in ("taper", "race"):
            assert set(ids) <= {"strength_maintenance"}
        elif len(ids) >= 2:
            assert "strength_legs" in ids
        elif ids:
            assert ids == ["strength_full"]


def test_strength_has_no_zones_is_no_hard_day_and_loads_point_eight_a_minute(plan):
    for w in plan.workouts:
        if not w.strength:
            continue
        assert not w.quality and w.family == "strength" and w.sport == STRENGTH
        assert all(p.zone is None for p in w.parts) and w.zone_seconds() == {}
        assert w.load == pytest.approx(w.seconds / 60 * 0.8)
        assert w.key.endswith("-strength")
    wk = next(wk for wk in plan.weeks if _strength(wk))
    assert wk.load == pytest.approx(sum(w.load for w in wk.workouts if w.goal is None))


def test_a_missed_strength_session_is_never_made_up_for():
    """No rule moves a workout: the weeks after a missed session hold the same number as before."""
    r = c.CASES["run_strength_2"]
    p = build_plan(r, plan_id="x", today=r.start)
    for w in p.weeks[0].workouts:
        if w.strength:
            w.status = "missed"
    later = roll_forward(p, r.start + timedelta(days=14))
    assert [len(_strength(wk)) for wk in later.weeks[:6]] == [2] * 6
    assert all(len(_strength(wk)) == wk.strength_sessions for wk in later.weeks if wk.detailed)
    assert sum(w.status == "missed" for w in later.workouts) == 2


def test_a_sketched_week_keeps_its_number_of_strength_sessions():
    r = c.CASES["run_strength_2"]
    p = build_plan(r, today=r.start)
    sk = [wk for wk in p.weeks if not wk.detailed]
    assert sk and all(wk.strength_sessions == (1 if wk.kind == "race" else 2) for wk in sk)
    back = codec.plan_from_dict(codec.plan_to_dict(p), p.request)
    assert [wk.strength_sessions for wk in back.weeks] == [wk.strength_sessions for wk in p.weeks]


# ---------------------------------------------------------------- titles and the calendar file


def test_a_plan_with_several_sports_puts_the_sport_first_also_in_running():
    p = build_plan(c.CASES["run_strength_2"])
    titles = {w.template_id: p.title(w) for w in p.workouts if w.goal is None}
    assert titles["easy"] == "Löpning: lugnt pass" and titles["threshold"] == "Löpning: tröskelintervaller"
    assert titles["strength_legs"] == "Styrka: ben och bål"
    assert {p.title(w) for w in p.workouts if w.goal} == {"Halvmaran", "Seedningslopp", "Parkrun"}
    set_language("en")
    assert p.title(next(w for w in p.workouts if w.template_id == "easy")) == "Running: easy session"
    assert "SUMMARY:Strength: legs and core · 40 min" in ics.to_ics(p)


def test_a_running_plan_looks_as_before():
    p = build_plan(c.CASES["half_12"])
    assert not p.request.multi_sport
    assert all(p.title(w) == w.title for w in p.workouts)
    assert p.title(next(w for w in p.workouts if w.template_id == "easy")) == "Lugn löpning"
    summaries = [x for x in ics.to_ics(p).split("\r\n") if x.startswith("SUMMARY:")]
    assert "SUMMARY:Lugn löpning · 35 min" in summaries and not any("Löpning:" in x for x in summaries)


# ---------------------------------------------------------------- swimming


def test_swimming_has_its_own_templates_easy_technique_and_intervals():
    swim = library.SPORT_TEMPLATES[SWIM]
    assert swim["easy"] == "swim_easy" and swim["recovery"] == "swim_technique"
    assert swim["threshold"] == "swim_intervals" and swim["vo2max"] == "swim_hard"
    assert library.build("swim_technique", minutes=30)[0].kind == "technique"
    p = build_plan(c.CASES["triathlon_swim"])
    used = {w.template_id for w in p.workouts if w.sport == SWIM}
    assert used and all(tid.startswith("swim_") for tid in used)


def test_swimming_without_a_test_is_on_effort_without_numbers():
    p = build_plan(c.CASES["four_sports_marathon"])
    w = next(w for w in p.workouts if w.sport == SWIM)
    lines = text.workout_lines(w, p.capacity_on(SWIM, w.date), p.zone_system)
    assert all(x["target"] == "" and x["rpe"] for x in lines)
    assert "plan.note.no_test_sport.swim" in [n.key for n in p.notes]


def test_a_css_test_gives_the_swimming_a_pace_per_100_m():
    p = build_plan(c.CASES["triathlon_swim"])
    assert "plan.note.no_test_sport.swim" not in [n.key for n in p.notes]
    w = next(w for w in p.workouts if w.template_id == "swim_intervals")
    lines = text.workout_lines(w, p.capacity_on(SWIM, w.date), p.zone_system)
    work = next(x for x in lines if x["zone"] == "Z4" and x["rest"])
    assert work["target"] == "1:58–1:53 /100 m"          # CSS 1:55: +3 s to -2 s
    assert "slag/min" not in " ".join(x["target"] for x in lines)


def test_the_swimming_zones_follow_on_each_other_around_css():
    tg = resolve.swim_targets(100.0)
    assert [tg[z].swim_low_s for z in ("Z1", "Z2", "Z3", "Z4", "Z5")] == [None, 115.0, 107.0, 103.0, 98.0]
    assert [tg[z].swim_high_s for z in ("Z1", "Z2", "Z3", "Z4", "Z5")] == [115.0, 107.0, 103.0, 98.0, None]
    assert tg["Z1"].pace_text == "1:55 /100 m eller lugnare" and tg["Z5"].pace_text == "1:38 /100 m eller snabbare"
    assert tg["Z4"].hr_text == "" and tg["Z4"].text == "1:43–1:38 /100 m"


def test_the_app_gets_the_swimming_targets_only_with_a_test():
    stored = link.stored_plan(build_plan(c.CASES["triathlon_swim"]))
    assert stored["targets"][SWIM]["Z4"]["swim_low_s"] == 118.0
    assert "swim_low_s" not in stored["targets"][RUNNING]["Z4"]
    assert SWIM not in link.stored_plan(build_plan(c.CASES["four_sports_marathon"]))["targets"]


# ---------------------------------------------------------------- the CSS test


def test_css_from_the_two_times():
    test = css.make(440, 210, date(2026, 11, 10))      # 7:20 and 3:30
    assert test.css_s100 == 115.0 and test.css_ms == pytest.approx(200 / 230)
    assert css.pace_str(test.css_s100) == "1:55"
    cap = css.capacity(test)
    assert (cap.sport, cap.anchors, cap.css_s100, cap.tested) == (SWIM, None, 115.0, date(2026, 11, 10))
    assert cap.known and not replace(cap, css_s100=None).known


@pytest.mark.parametrize("t400,t200,key", [(None, 210, "missing"), (440, None, "missing"), (440, 60, "range"),
                                           (2000, 300, "range"), (400, 210, "order"), (210, 440, "order")])
def test_css_refuses_times_that_cannot_be_right(t400, t200, key):
    assert css.check(t400, t200) == f"assessments.css.error.{key}"
    with pytest.raises(ValueError):
        css.make(t400 or 0, t200 or 0)


def test_css_times_as_the_coach_writes_them():
    assert css.parse_time("7:20") == 440 and css.parse_time("3:30,5") == 210.5
    assert css.parse_time("440") == 440 and css.parse_time("") is None and css.parse_time("7:75") is None
    assert css.parse_time("abc") is None


def test_css_is_stored_as_a_manual_test_and_in_the_plan():
    test = css.make(440, 210, date(2026, 11, 10), pool_m=50)
    res = css.to_result(test)
    assert res["kind"] == "swim_css" and res["manual"] is True
    assert css.from_result(res, date(2026, 11, 10)) == test
    assert css.from_result({"kind": "lactate"}) is None and css.from_result({**res, "t400_s": 300}) is None
    r = c.CASES["triathlon_swim"]
    back = codec.request_from_dict(codec.request_to_dict(r), r.athlete_name)
    assert back == r and back.capacity_for(SWIM).css_s100 == 115.0


def test_older_requests_and_capacities_read_without_strength_or_css():
    d = codec.request_to_dict(c.CASES["half_12"])
    assert "strength_sessions" not in d and all("css_s100" not in x for x in d["capacity"])
    back = codec.request_from_dict(d)
    assert back.strength_sessions == 0 and back.capacity[0].css_s100 is None
