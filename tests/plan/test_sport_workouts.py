"""The bike's and the SkiErg's own workouts and targets (#666, step 2), one test per rule (docs/TRANINGSPLAN.md)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language
from lopanalys.plan import Capacity, SportRole, build_plan, codec, ics, library, link, resolve, text
from lopanalys.plan.engine import MAX_HARD_SHARE
from lopanalys.plan.models import RUNNING

from . import cases as c

RUN_TEMPLATES = [tid for tid, tpl in library.TEMPLATES.items()
                 if not tpl.base and tpl.sport == RUNNING and tid not in ("test", "race")]


@pytest.fixture(autouse=True)
def _sv():
    set_language("sv")
    yield
    set_language("sv")


def _plan(name: str):
    return build_plan(c.CASES[name])


# ---------------------------------------------------------------- the library


@pytest.mark.parametrize("sport", ["bike", "skierg", "ski", "swim"])
def test_every_running_template_has_one_in_the_bike_and_the_skierg(sport):
    assert sorted(library.SPORT_TEMPLATES[sport]) == sorted(RUN_TEMPLATES)
    assert library.for_sport("test", sport) == "test" and library.for_sport("race", sport) == "race"


@pytest.mark.parametrize("tid", [t for t, tpl in library.TEMPLATES.items() if tpl.base])
def test_a_sport_template_takes_the_place_of_its_running_template(tid):
    """The same family, effort and limits, and every step exactly as long as the running step: the week keeps its
    minutes when a workout changes sport."""
    tpl, run = library.get(tid), library.get(library.get(tid).base)
    assert (tpl.family, tpl.quality, tpl.min_minutes, tpl.max_minutes) == (run.family, run.quality, run.min_minutes,
                                                                            run.max_minutes)
    assert tpl.sport == tid.split("_")[0] and tpl.sport in ("bike", "skierg", "swim")
    assert len(tpl.steps) == len(run.steps)
    for own, theirs in zip(tpl.steps, run.steps):
        assert sum(p.total_s for p in own) == sum(p.total_s for p in theirs)
        assert own[-1].kind != "cooldown" or own[-1].duration_s >= 5 * 60
    if not tpl.steps and tid != "swim_technique":      # drills: one part of its own, just as long
        assert library.build(tid, minutes=90) == library.build(run.id, minutes=90)


def test_the_bike_has_z2_sweet_spot_threshold_and_vo2_and_the_skierg_easy_and_intervals():
    bike = library.SPORT_TEMPLATES["bike"]
    zone = lambda tid, step=0: next(p.zone for p in library.build(tid, step=step) if p.kind == "work")
    assert library.get(bike["easy"]).top_zone == "Z2" and library.build(bike["long"], minutes=120)[0].zone == "Z2"
    assert zone(bike["fartlek"]) == "Z3"                         # sweet spot: below the threshold zone
    assert zone(bike["threshold"]) == "Z4" and zone(bike["vo2max"]) == "Z5" and zone(bike["hills"]) == "Z5"
    # The bike's threshold intervals are longer than running's: no impact on the legs.
    work = lambda tid, step: sum(p.work_s for p in library.build(tid, step=step) if p.kind == "work")
    assert all(work(bike["threshold"], s) > work("threshold", s) for s in range(library.last_step("threshold") + 1))
    erg = library.SPORT_TEMPLATES["skierg"]
    assert library.get(erg["easy"]).top_zone == "Z2"
    assert zone(erg["threshold"]) == "Z4" and zone(erg["vo2max"]) == "Z5"


@pytest.mark.parametrize("tid", RUN_TEMPLATES)
def test_in_sport_keeps_the_step_and_the_minutes(tid):
    for step in range(library.last_step(tid) + 1):
        parts = library.build(tid, step=step) if library.get(tid).steps else library.build(tid, minutes=95)
        new, moved = library.in_sport(parts, tid, "bike")
        assert new == library.SPORT_TEMPLATES["bike"][tid]
        assert sum(p.total_s for p in moved) == sum(p.total_s for p in parts)
        if library.get(tid).steps:
            assert moved == library.build(new, step=step)
    assert library.in_sport(library.build("easy", minutes=40), "easy", RUNNING) == ("easy", library.build("easy", minutes=40))


# ---------------------------------------------------------------- the engine


@pytest.mark.parametrize("name", ["bike_skierg_tested", "bike_alone_26", "ski_main", "four_sports_marathon"])
def test_every_workout_has_its_sports_template(name):
    for w in _plan(name).workouts:
        tpl = library.get(w.template_id)
        if w.goal is not None or w.template_id == "test":
            continue
        if w.sport in library.SPORT_TEMPLATES:
            assert tpl.base and library.SPORT_TEMPLATES[w.sport][tpl.base] == w.template_id, (w.sport, w.template_id)
        else:
            assert not tpl.base, (w.sport, w.template_id)      # running and swimming: the running templates


def test_a_plan_on_the_bike_alone_is_bike_workouts_all_through():
    p = _plan("bike_alone_26")
    assert {w.sport for w in p.workouts} == {"bike"}
    assert all(w.template_id.startswith("bike_") for w in p.workouts if w.template_id not in ("test", "race"))


@pytest.mark.parametrize("name", ["bike_skierg_tested", "bike_alone_26", "ski_main"])
def test_the_hard_share_holds_with_the_sports_templates(name):
    for wk in _plan(name).weeks:
        total = sum(wk.zone_minutes().values())
        assert not total or wk.hard_minutes / total <= MAX_HARD_SHARE + 1e-9, wk.index


def test_the_week_keeps_its_minutes_when_the_workouts_change_sport():
    """The same week in running and in another sport: the sport changes the templates, not the time, as long as
    the hard share holds (it does in this plan's base weeks)."""
    run = _plan("no_goal_12")
    bike = build_plan(replace(c.CASES["no_goal_12"], availability=c.for_sports(c.FIVE, "bike"),
                              roles=(SportRole("bike"),), capacity=(c.BIKE_TEST,)))
    assert [wk.minutes for wk in bike.weeks[:3]] == [wk.minutes for wk in run.weeks[:3]]


# ---------------------------------------------------------------- the targets


def test_the_bike_gets_watt_from_its_test():
    tg = resolve.targets(c.BIKE_TEST)
    assert (tg["Z3"].power_low_w, tg["Z3"].power_high_w) == (180, 210)       # LT1 to halfway
    assert (tg["Z4"].power_low_w, tg["Z4"].power_high_w) == (210, 240)       # halfway to LT2
    assert tg["Z2"].power_low_w is None and tg["Z2"].power_high_w == 180
    assert tg["Z4"].text.endswith("slag/min · 210–240 W")
    assert tg["Z2"].power_text == "under 180 W"
    assert tg["Z4"].pace_text == "" and tg["Z4"].speed_low_kmh is None
    set_language("en")
    assert tg["Z2"].power_text == "below 180 W"


def test_the_skierg_gets_watt_and_time_per_500_m():
    tg = resolve.targets(c.SKIERG_TEST)
    assert (tg["Z4"].power_low_w, tg["Z4"].power_high_w) == (185, 210)
    # Concept2: 500 * (2.80 / W) ** (1/3) seconds; the harder end is the faster split.
    assert tg["Z4"].pace_text == f"{resolve.split_str(185)}–{resolve.split_str(210)} /500 m"
    assert resolve.split_str(210) < resolve.split_str(185)
    assert tg["Z4"].text.endswith(" W · " + tg["Z4"].pace_text)
    assert tg["Z2"].pace_text.endswith("/500 m eller lugnare")


def test_skis_borrow_the_skierg_heart_rates_but_not_its_watt():
    erg = resolve.targets(c.SKIERG_TEST)
    ski = resolve.targets(c.SKIERG_TEST, sport="ski")
    assert {z: (x.hr_low, x.hr_high) for z, x in ski.items()} == {z: (x.hr_low, x.hr_high) for z, x in erg.items()}
    assert all(x.power_low_w is None and x.power_high_w is None and "W" not in x.text for x in ski.values())
    p = _plan("ski_main")
    assert "plan.note.borrowed.ski" in [n.key for n in p.notes]
    w = next(w for w in p.workouts if w.sport == "ski" and w.quality and w.goal is None and w.template_id != "test")
    lines = text.workout_lines(w, p.capacity_on("ski", w.date), p.zone_system)
    assert all("W" not in x["target"] and "/500" not in x["target"] for x in lines)
    assert any("slag/min" in x["target"] for x in lines)


def test_without_a_test_the_sport_gets_zone_and_effort_only():
    p = build_plan(replace(c.CASES["bike_skierg_tested"], capacity=(c.TESTED,)))
    w = next(w for w in p.workouts if w.sport == "bike" and w.quality)
    lines = text.workout_lines(w, p.capacity_on("bike", w.date), p.zone_system)
    assert all(x["target"] == "" and x["zone"] for x in lines if x["kind"] != "Test")
    assert any(x["rpe"] for x in lines)
    assert w.template_id.startswith("bike_")                      # the bike's own workout all the same


def test_a_part_in_another_sport_is_not_called_running():
    p = _plan("bike_skierg_tested")
    w = next(w for w in p.workouts if w.template_id == "bike_sweetspot")
    lines = [text.plain(x) for x in text.workout_lines(w, p.capacity_on("bike", w.date), p.zone_system)]
    assert lines[0].startswith("Uppvärmning: 10 min i Z2 (")
    assert lines[1].startswith("Intervaller: 2 × 12 min i Z3 (") and lines[1].endswith("W), lugnt 3 min emellan")
    assert lines[2].startswith("Nedvarvning: ")
    easy = next(w for w in p.workouts if w.template_id == "bike_easy")
    assert text.plain(text.workout_lines(easy, c.BIKE_TEST, p.zone_system)[0]).startswith("Jämnt: ")
    assert w.title == "Cykel: sweet spot"


def test_a_retest_moves_the_watt():
    p = _plan("bike_skierg_tested")
    stronger = replace(c.BIKE_TEST, anchors=replace(c.BIKE_TEST.anchors, lt1_speed_kmh=200.0, lt2_speed_kmh=260.0),
                       tested=date(2027, 1, 10))
    q = resolve.retest(p, stronger, from_date=date(2027, 1, 11))
    w_before = next(w for w in q.workouts if w.sport == "bike" and w.date < date(2027, 1, 11))
    w_after = next(w for w in q.workouts if w.sport == "bike" and w.date >= date(2027, 1, 11))
    assert resolve.targets(q.capacity_on("bike", w_before.date))["Z4"].power_high_w == 240
    assert resolve.targets(q.capacity_on("bike", w_after.date))["Z4"].power_high_w == 260


def test_the_calendar_and_the_app_link_carry_the_watt():
    p = _plan("bike_skierg_tested")
    cal = ics.to_ics(p).replace("\r\n ", "")
    assert "Cykel: tröskelintervaller" in cal and " W" in cal and "/500 m" in cal
    stored = link.stored_plan(p)
    assert stored["targets"]["bike"]["Z4"]["power_high_w"] == 240
    assert "power_high_w" not in stored["targets"][RUNNING]["Z4"]      # a running payload is as before


def test_an_older_plan_with_running_templates_in_the_bike_still_reads_and_shows_watt():
    """A plan built by engine 4 (step 1) has the running templates in the bike: it reads back and shows them with
    the sport's name and the bike's watt."""
    p = _plan("bike_skierg_tested")
    d = codec.plan_to_dict(p)
    for wk in d["weeks"]:
        for w in wk["workouts"]:
            base = library.get(w["template_id"]).base
            if base:
                w["template_id"] = base
    d["engine_version"] = "4"
    old = codec.plan_from_dict(d, codec.request_from_dict(codec.request_to_dict(p.request), "Exempel"))
    w = next(w for w in old.workouts if w.sport == "bike" and w.family == "threshold")
    assert w.template_id == "threshold" and w.title == "Cykel: tröskelintervaller"
    assert any(x["target"].endswith(" W") for x in text.workout_lines(w, old.capacity_on("bike", w.date), old.zone_system))


def test_anchors_in_watt_without_a_bike_sport_give_no_watt_to_running():
    run = Capacity(RUNNING, Anchors(192, 46, 165, 174, 13.2, 14.4), date(2026, 11, 13))
    assert all(x.power_low_w is None and x.power_high_w is None for x in resolve.targets(run).values())
