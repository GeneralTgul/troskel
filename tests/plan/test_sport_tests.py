"""Each sport's own test and the sweet spot as a band of the threshold power (#666, the rest of step 2), one test
per rule (docs/TRANINGSPLAN.md)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import has, set_language
from lopanalys.plan import Capacity, Part, SportRole, build_plan, codec, ics, library, link, resolve, text
from lopanalys.plan.models import RUNNING

from . import cases as c

TESTS = {"bike": "bike_test", "skierg": "skierg_test", "ski": "ski_test", "swim": "swim_test"}


@pytest.fixture(autouse=True)
def _sv():
    set_language("sv")
    yield
    set_language("sv")


def _alone(sport: str, capacity: tuple = ()) -> "object":
    """Twelve weeks without a goal in one sport: the plan ends with a test."""
    return build_plan(c.request(c.for_sports(c.FIVE, sport), weeks=12, capacity=capacity, roles=(SportRole(sport),)))


def _test_of(plan):
    return next(w for w in plan.workouts if w.family == "test")


# ---------------------------------------------------------------- the test of each sport


@pytest.mark.parametrize("sport,tid", TESTS.items())
def test_every_sport_has_its_own_test_as_long_as_runnings(sport, tid):
    assert library.for_sport("test", sport) == tid
    tpl, run = library.get(tid), library.get("test")
    assert (tpl.family, tpl.quality, tpl.top_zone, tpl.base, tpl.sport) == ("test", True, None, "test", sport)
    assert library.minutes_of(tid) == library.minutes_of("test") == 60
    assert [p.kind for p in library.build(tid)] == ["warmup", "test", "cooldown"]
    assert library.build(tid)[1].zone is None and library.build(tid)[1].rpe == 10


def test_the_tests_follow_the_programmes_protocols():
    """The bike: 6-8 steps of 5 minutes (30-40 min). The SkiErg: 6-8 steps of 4 minutes with half a minute's pause
    (27-36 min). Swimming: 400 m and 200 m with a full rest between."""
    length = lambda tid: [p.duration_s // 60 for p in library.build(tid)]
    assert length("bike_test") == [10, 40, 10]
    assert length("skierg_test") == length("ski_test") == [10, 35, 15]
    assert length("swim_test") == [20, 25, 15]
    assert length("test") == [15, 35, 10]                          # running's is as before


@pytest.mark.parametrize("sport,tid", TESTS.items())
@pytest.mark.parametrize("lang", ["sv", "en"])
def test_every_test_has_its_texts(sport, tid, lang):
    set_language(lang)
    assert all(has(f"plan.workout.{tid}.{f}") for f in ("title", "purpose", "how"))


@pytest.mark.parametrize("sport,title", [
    ("bike", "Cykel: laktattest hos din coach"), ("skierg", "SkiErg: laktattest hos din coach"),
    ("ski", "Skidor: laktattest på SkiErg hos din coach"), ("swim", "Simning: simtest, 400 m och 200 m")])
def test_a_plan_in_another_sport_ends_with_the_sports_test(sport, title):
    plan = _alone(sport)
    w = _test_of(plan)
    assert plan.weeks[-1].kind == "test" and w in plan.weeks[-1].workouts
    assert (w.sport, w.template_id, w.title, w.minutes) == (sport, TESTS[sport], title, 60)
    assert w.how and w.purpose
    assert w.load == pytest.approx(_test_of(_alone(RUNNING, (c.TESTED,))).load
                                   * {"bike": 0.7, "swim": 0.7}.get(sport, 1.0), rel=0.2)   # still counted as hard


def test_the_test_says_how_it_is_done():
    assert "5 minuter" in _test_of(_alone("bike")).how and "25–30 W" in _test_of(_alone("bike")).how
    assert "4 minuter" in _test_of(_alone("skierg")).how
    assert "görs på SkiErgen" in _test_of(_alone("ski")).how
    swim = _test_of(_alone("swim"))
    assert "400 m" in swim.how and "200 m" in swim.how and "CSS" in swim.purpose


def test_running_keeps_its_test_and_every_test_is_in_the_main_sport():
    assert _test_of(_alone(RUNNING, (c.TESTED,))).template_id == "test"
    multi = build_plan(c.request(c.for_sports(c.FIVE, RUNNING, "bike"), weeks=12, capacity=(c.TESTED, c.BIKE_TEST),
                                 roles=(SportRole("bike", "main"), SportRole(RUNNING, "support"))))
    assert {(w.sport, w.template_id) for w in multi.workouts if w.family == "test"} == {("bike", "bike_test")}


def test_the_week_of_the_test_keeps_its_minutes():
    run, bike = _alone(RUNNING, (c.TESTED,)), _alone("bike", (c.TESTED,))
    assert [wk.minutes for wk in run.weeks] == [wk.minutes for wk in bike.weeks]


def test_an_older_plan_with_runnings_test_in_another_sport_still_reads():
    plan = _alone("bike", (c.BIKE_TEST,))
    d = codec.plan_to_dict(plan)
    for wk in d["weeks"]:
        for w in wk["workouts"]:
            if w["template_id"] == "bike_test":
                w["template_id"] = "test"
    d["engine_version"] = "6"
    old = codec.plan_from_dict(d, plan.request)
    assert _test_of(old).title == "Cykel: test hos din coach" and old.engine_version == "6"


# ---------------------------------------------------------------- the sweet spot


def test_the_sweet_spot_is_88_to_94_percent_of_the_threshold_power():
    band = resolve.band_targets(c.BIKE_TEST)["sweetspot"]                   # LT2 at 240 W
    assert (band.power_low_w, band.power_high_w) == (211, 226)
    assert band.power_text == "211–226 W"
    z4 = resolve.targets(c.BIKE_TEST)["Z4"]
    assert (band.zone, band.hr_low, band.hr_high) == ("Z4", z4.hr_low, z4.hr_high)   # the zone's heart rates
    assert (z4.power_low_w, z4.power_high_w) == (210, 240)                  # the zones themselves are as before
    assert set(resolve.targets(c.BIKE_TEST)) == {"Z1", "Z2", "Z3", "Z4", "Z5"}


def test_the_sweet_spot_follows_the_threshold_not_lt1():
    high_lt1 = replace(c.BIKE_TEST, anchors=replace(c.BIKE_TEST.anchors, lt1_speed_kmh=216.0))     # 90 % of LT2
    low_lt1 = replace(c.BIKE_TEST, anchors=replace(c.BIKE_TEST.anchors, lt1_speed_kmh=160.0))
    for cap in (high_lt1, low_lt1):
        band = resolve.band_targets(cap)["sweetspot"]
        assert (band.power_low_w, band.power_high_w) == (211, 226)


def test_the_sweet_spot_blocks_carry_the_band_and_count_as_z4():
    for step in range(library.last_step("bike_sweetspot") + 1):
        parts = library.build("bike_sweetspot", step=step)
        assert [(p.kind, p.band, p.zone) for p in parts] == [("warmup", "", "Z2"), ("work", "sweetspot", "Z4"),
                                                             ("cooldown", "", "Z2")]
    assert library.get("bike_sweetspot").top_zone == "Z4"
    others = [p for tid, tpl in library.TEMPLATES.items() if tid != "bike_sweetspot" for st in tpl.steps for p in st]
    assert not any(p.band for p in others)


def test_the_sweet_spot_workout_shows_the_band_and_the_other_parts_their_zone():
    p = build_plan(c.CASES["bike_skierg_tested"])
    w = next(w for w in p.workouts if w.template_id == "bike_sweetspot")
    lines = [text.plain(x) for x in text.workout_lines(w, p.capacity_on("bike", w.date), p.zone_system)]
    assert lines[1] == "Intervaller: 2 × 12 min i Z4 (150–160 slag/min · 211–226 W), lugnt 3 min emellan"
    assert lines[0].endswith("under 180 W)")
    thr = next(w for w in p.workouts if w.template_id == "bike_threshold")
    assert "210–240 W" in text.plain(text.workout_lines(thr, p.capacity_on("bike", thr.date), p.zone_system)[1])


def test_without_a_measured_threshold_power_the_sweet_spot_falls_back_to_its_zone():
    assert resolve.band_targets(None) == {}
    assert resolve.band_targets(Capacity("bike")) == {}
    max_hr = Capacity("bike", Anchors(max_hr=185, sport="bike"), date(2026, 11, 1))
    assert resolve.band_targets(max_hr) == {}
    part = next(p for p in library.build("bike_sweetspot") if p.band)
    line = text.part_line(part, resolve.targets(max_hr), "bike", resolve.band_targets(max_hr))
    assert line["zone"] == "Z4" and " W" not in line["target"] and "slag/min" in line["target"]


def test_the_threshold_power_alone_gives_the_sweet_spot():
    """An FTP test gives the threshold power and no LT1: the band has its watt, and the zone's estimated heart rates."""
    ftp = Capacity("bike", Anchors(max_hr=185, lt2_speed_kmh=250.0, sport="bike"), date(2026, 11, 1))
    band = resolve.band_targets(ftp)["sweetspot"]
    assert (band.power_low_w, band.power_high_w, band.estimated) == (220, 235, True)


def test_the_band_is_the_bikes_only():
    assert resolve.band_targets(c.TESTED) == {} and resolve.band_targets(c.SKIERG_TEST) == {}
    assert resolve.band_targets(c.SKIERG_TEST, sport="ski") == {}
    assert resolve.band_targets(c.BIKE_TEST, sport=RUNNING) == {}


def test_a_retest_moves_the_sweet_spot():
    p = build_plan(c.CASES["bike_skierg_tested"])
    stronger = replace(c.BIKE_TEST, anchors=replace(c.BIKE_TEST.anchors, lt1_speed_kmh=200.0, lt2_speed_kmh=260.0),
                       tested=date(2027, 1, 10))
    q = resolve.retest(p, stronger)
    assert resolve.band_targets(q.request.capacity_for("bike"))["sweetspot"].power_text == "229–244 W"
    assert q.weeks == p.weeks                                               # no workout moved


def test_the_band_is_stored_only_where_it_is_and_an_older_plan_reads_without_it():
    p = build_plan(c.CASES["bike_skierg_tested"])
    d = codec.plan_to_dict(p)
    parts = [x for wk in d["weeks"] for w in wk["workouts"] for x in w["parts"]]
    assert {x["band"] for x in parts if "band" in x} == {"sweetspot"}
    assert all(x["kind"] == "work" for x in parts if "band" in x)
    assert codec.plan_from_dict(d, p.request) == p
    for x in parts:                                                         # as engine 6 stored the workout
        if x.pop("band", None):
            x["zone"] = "Z3"
    old = codec.plan_from_dict(d, p.request)
    w = next(w for w in old.workouts if w.template_id == "bike_sweetspot")
    assert all(isinstance(x, Part) and x.band == "" for x in w.parts)
    line = text.plain(text.workout_lines(w, old.capacity_on("bike", w.date), old.zone_system)[1])
    assert "i Z3 (140–150 slag/min · 180–210 W)" in line                    # shown as it was handed out


def test_the_app_link_and_the_calendar_carry_the_sweet_spot():
    p = build_plan(c.CASES["bike_skierg_tested"])
    stored = link.stored_plan(p)
    band = stored["targets"]["bike"]["sweetspot"]
    assert (band["power_low_w"], band["power_high_w"], band["hr_low"], band["hr_high"]) == (211, 226, 150, 160)
    assert "sweetspot" not in stored["targets"][RUNNING] and "sweetspot" not in stored["targets"]["skierg"]
    assert "211–226 W" in ics.to_ics(p).replace("\r\n ", "")
