"""Several sports in one plan (#666, step 1): roles and shares, the sport of every workout, the ceilings over the sports."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language
from lopanalys.plan import RUNNING, Capacity, Goal, Slot, SportRole, build_plan, codec, link, resolve
from lopanalys.plan.engine import SHARE_SHIFT, floor5, shares
from lopanalys.plan.models import ROLE_SHARE
from lopanalys.plan.periodize import core_slots

from . import cases as c

MULTI = ("run_bike_12", "four_sports_marathon")


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.fixture(scope="module", params=MULTI)
def plan(request):
    return build_plan(c.CASES[request.param], plan_id=request.param)


def _roles(plan):
    return {r.sport: r for r in plan.request.plan_roles()}


# ---------------------------------------------------------------- the roles


def test_one_sport_is_the_main_sport_and_each_sport_comes_once():
    r = c.request(roles=(SportRole("bike", "support"), SportRole(RUNNING, "main"), SportRole("bike", "main"),
                         SportRole("swim", "main")))
    roles = r.plan_roles()
    assert [x.sport for x in roles] == [RUNNING, "bike", "swim"]
    assert [x.role for x in roles] == ["main", "support", "support"]
    assert r.main_sport == RUNNING


def test_without_a_main_sport_the_a_goal_decides_and_else_the_first():
    goal = Goal("Vätternrundan", date(2027, 2, 6), sport="bike")
    r = c.request(goals=(goal,), roles=(SportRole(RUNNING, "support"), SportRole("bike", "support")))
    assert r.main_sport == "bike"
    assert c.request(roles=(SportRole("swim", "support"), SportRole(RUNNING, "support"))).main_sport == "swim"


def test_without_roles_the_sports_are_used_and_one_sport_is_the_main_sport():
    r = c.request()
    assert r.roles == () and r.plan_roles() == (SportRole(RUNNING, "main"),)
    two = replace(r, sports=(RUNNING, "bike"))
    assert two.plan_roles() == (SportRole(RUNNING, "main"), SportRole("bike", "support"))


def test_the_roles_give_60_30_10_when_no_share_is_given():
    assert ROLE_SHARE == {"main": 60, "support": 30, "maintenance": 10}
    roles = c.FOUR      # ski has 20 of its own
    sh = shares(roles, "base")
    assert sh == pytest.approx({RUNNING: 0.5, "bike": 0.25, "ski": 20 / 120, "swim": 10 / 120})
    assert sum(sh.values()) == pytest.approx(1.0)


def test_the_shares_shift_towards_the_main_sport_near_the_goal():
    for block, k in SHARE_SHIFT.items():
        sh = shares(c.RUN_BIKE, block)
        assert sh[RUNNING] == pytest.approx(2 / 3 + k / 3) and sum(sh.values()) == pytest.approx(1.0)
    assert shares(c.RUN_BIKE, "build") == shares(c.RUN_BIKE, "base")


def test_one_sport_given_as_a_role_gives_the_same_plan_as_before():
    before = build_plan(c.CASES["half_12"], plan_id="p")
    roles = build_plan(replace(c.CASES["half_12"], roles=(SportRole(RUNNING, "main", 100),)), plan_id="p")
    assert roles.weeks == before.weeks and roles.notes == before.notes


# ---------------------------------------------------------------- the sport of every workout


def test_every_workout_has_a_sport_its_slot_allows(plan):
    slots = {s.weekday: s for s in core_slots(plan.request)}
    sports = set(plan.request.plan_sports)
    for w in plan.workouts:
        assert w.sport in sports, w
        if w.goal is None:
            assert w.sport in slots[w.date.weekday()].sports, w


def test_the_long_run_and_the_test_are_in_the_main_sport(plan):
    main = plan.request.main_sport
    for w in plan.workouts:
        if w.family == "long" or w.template_id == "test":
            assert w.sport == main, w


def test_a_race_is_in_the_goal_sport():
    goal = Goal("Lidingö", date(2027, 1, 2), sport="bike", priority="B", distance_km=30)
    p = build_plan(replace(c.CASES["run_bike_12"], goals=(c.HALF, goal)))
    races = {w.goal.name: w.sport for w in p.workouts if w.goal}
    assert races == {"Halvmaran": RUNNING, "Lidingö": "bike"}


def test_several_sports_share_the_week(plan):
    """Every sport gets time, and over the load weeks of base and build each sport lies near its share."""
    roles = _roles(plan)
    total = sum(r.weight for r in roles.values())
    minutes: dict[str, int] = {}
    for wk in plan.weeks:
        if wk.kind == "load" and wk.block in ("base", "build"):
            for sp, m in wk.sport_minutes().items():
                minutes[sp] = minutes.get(sp, 0) + m
    all_minutes = sum(minutes.values())
    assert set(minutes) == set(roles)
    for sp, r in roles.items():
        assert abs(minutes[sp] / all_minutes - r.weight / total) < 0.12, (sp, minutes)


def test_the_main_sport_takes_over_in_the_race_week(plan):
    race = plan.weeks[-1].sport_minutes()
    main = plan.request.main_sport
    assert race[main] >= 0.75 * sum(race.values())


def test_a_maintenance_sport_has_one_easy_workout_a_week_at_most():
    p = build_plan(c.CASES["four_sports_marathon"])
    seen = 0
    for wk in p.weeks:
        swim = [w for w in wk.workouts if w.sport == "swim"]
        assert len(swim) <= 1 and not any(w.quality for w in swim), wk.index
        seen += len(swim)
    assert seen > len(p.weeks) / 2


def test_hard_days_count_over_all_sports(plan):
    """Never two hard days in a row, whatever the sports (test_rules checks the same day by day)."""
    hard = sorted(w.date for w in plan.workouts if w.quality and w.goal is None)
    assert all((b - a).days >= 2 for a, b in zip(hard, hard[1:]))
    assert len({w.sport for w in plan.workouts if w.quality and w.goal is None}) >= 2


def test_running_grows_by_at_most_ten_percent_of_its_own_minutes(plan):
    best = 0
    for wk in plan.weeks:
        run = wk.sport_minutes().get(RUNNING, 0)
        if best:
            assert run <= floor5(1.1 * best), (wk.index, run, best)
        best = max(best, run)


def test_a_slot_for_one_sport_only_gets_that_sport():
    slots = c.for_sports(c.FIVE, RUNNING) + (Slot(2, 60, ("bike",)),)
    p = build_plan(replace(c.CASES["run_bike_12"], availability=slots))
    wed = [w for w in p.workouts if w.date.weekday() == 2]
    assert wed and all(w.sport == "bike" for w in wed)
    assert all(w.sport == RUNNING for w in p.workouts if w.date.weekday() != 2)


def test_a_slot_for_a_sport_the_plan_does_not_have_is_not_used():
    slots = c.FIVE + (Slot(2, 60, ("bike",)),)
    p = build_plan(replace(c.CASES["half_12"], availability=slots))
    assert not [w for w in p.workouts if w.date.weekday() == 2]


def test_a_plan_in_one_other_sport_is_that_sport_all_through():
    r = replace(c.CASES["no_goal_12"], availability=c.for_sports(c.FIVE, "bike"), roles=(SportRole("bike"),))
    p = build_plan(r)
    assert p.workouts and {w.sport for w in p.workouts} == {"bike"}


# ---------------------------------------------------------------- names, tests and targets


def test_a_workout_in_another_sport_has_the_sport_in_its_title():
    p = build_plan(c.CASES["four_sports_marathon"])
    titles = {(w.sport, w.template_id): w.title for w in p.workouts if w.goal is None}
    assert titles[(RUNNING, "easy")] == "Lugn löpning"
    assert titles[("bike", "bike_easy")] == "Cykel: lugnt pass i Z2"
    assert titles[("swim", "easy")] == "Simning: lugnt pass"          # swimming keeps the running templates
    set_language("en")
    assert next(w for w in p.workouts if w.template_id == "bike_easy").title == "Cycling: easy Z2 ride"


def test_sports_without_a_test_say_so_and_skis_borrow_the_skierg():
    notes = [n.key for n in build_plan(c.CASES["four_sports_marathon"]).notes]
    assert notes[-3:] == ["plan.note.no_test_sport.bike", "plan.note.no_test_sport.ski", "plan.note.no_test_sport.swim"]
    skierg = Capacity("skierg", Anchors(188, 48, 150, 168, 160.0, 210.0, sport="skierg"), date(2026, 11, 1), "Laktattrappa")
    r = replace(c.CASES["four_sports_marathon"], capacity=(c.TESTED, skierg))
    p = build_plan(r)
    assert "plan.note.borrowed.ski" in [n.key for n in p.notes]
    assert r.capacity_for("ski") is skierg
    stored = link.stored_plan(p)
    assert set(stored["targets"]) == {RUNNING, "skierg", "ski"}
    # Skis get the SkiErg's heart rates, but not its watt: on snow the load is not the ergometer's.
    ski, erg = stored["targets"]["ski"], stored["targets"]["skierg"]
    assert {z: (x["hr_low"], x["hr_high"]) for z, x in ski.items()} == {z: (x["hr_low"], x["hr_high"]) for z, x in erg.items()}
    assert erg["Z4"]["power_low_w"] and not any("power_low_w" in x for x in ski.values())


def test_other_sports_get_heart_rate_and_no_pace_in_km_h():
    bike = Capacity("bike", Anchors(185, 48, 140, 160, 180.0, 240.0, sport="bike"), date(2026, 11, 1), "Laktattrappa")
    tg = resolve.targets(bike)
    assert tg["Z4"].hr_low and tg["Z4"].speed_low_kmh is None and tg["Z4"].speed_high_kmh is None
    assert "min/km" not in tg["Z4"].text and " W" in tg["Z4"].text
    assert resolve.targets(c.TESTED)["Z4"].speed_high_kmh


def test_the_load_weighs_minutes_by_zone_and_sport():
    p = build_plan(c.CASES["run_bike_12"])
    easy = {w.sport: w for w in p.workouts if w.family == "easy"}
    run, bike = easy[RUNNING], easy["bike"]
    assert run.load == pytest.approx(run.minutes * 1.0)            # all in Z2
    assert bike.load == pytest.approx(bike.minutes * 0.7)
    hard = next(w for w in p.workouts if w.family == "threshold")
    assert hard.load > hard.minutes * (0.7 if hard.sport == "bike" else 1.0)


# ---------------------------------------------------------------- storage


def test_the_roles_are_stored_and_old_requests_read_without_them():
    r = c.CASES["four_sports_marathon"]
    d = codec.request_to_dict(r)
    assert d["roles"][2] == {"sport": "ski", "role": "support", "share": 20}
    assert codec.request_from_dict(d, r.athlete_name) == r
    old = {k: v for k, v in d.items() if k != "roles"}
    back = codec.request_from_dict(old, r.athlete_name)
    assert back.roles == () and back.plan_roles()[0] == SportRole(RUNNING, "main")


def test_a_stored_multi_sport_plan_reads_back_the_same(plan):
    req = codec.request_from_dict(codec.request_to_dict(plan.request), plan.request.athlete_name)
    assert codec.plan_from_dict(codec.plan_to_dict(plan), req) == plan


def test_the_printout_names_the_sport_of_each_workout():
    from lopanalys.report.plan_builder import render_plan_html
    html = render_plan_html(build_plan(c.CASES["four_sports_marathon"]), None, "sv")
    assert "Cykel: lugnt pass" in html and "Simning: " in html and "Lugn löpning" in html
