"""Zone to heart rate and pace, and the retest that moves every target."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language
from lopanalys.plan import RUNNING, Capacity, build_plan, resolve
from lopanalys.plan.models import SpeedLine
from lopanalys.plan.text import duration, plain, workout_lines

from . import cases as c


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def test_targets_from_a_lactate_test():
    zt = resolve.targets(c.TESTED)
    assert list(zt) == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert (zt["Z2"].hr_low, zt["Z2"].hr_high) == (115, 165)      # zone 2 ends at LT1
    assert (zt["Z4"].hr_low, zt["Z4"].hr_high) == (170, 174)      # zone 4 ends at LT2
    assert zt["Z4"].text == "170–174 slag/min · 4:21–4:10 min/km"
    assert zt["Z2"].pace_text == "4:33 min/km eller lugnare"       # pace only where the test measured it
    assert zt["Z5"].pace_text == "4:10 min/km eller snabbare"
    assert zt["Z1"].pace_text == "" and not zt["Z4"].estimated


def test_max_hr_only_is_marked_estimated_and_has_no_pace():
    cap = Capacity(RUNNING, Anchors(max_hr=190))
    assert cap.estimated
    zt = resolve.targets(cap)
    assert zt["Z4"].estimated and (zt["Z4"].hr_low, zt["Z4"].hr_high) == (152, 171) and zt["Z4"].pace_text == ""
    plan = build_plan(c.request(weeks=12, capacity=(cap,)))
    assert [n.key for n in plan.notes] == ["plan.note.estimated"]


def test_no_test_gives_zone_and_effort_only():
    assert resolve.targets(Capacity(RUNNING)) == {} and resolve.target(None, "Z4") is None
    plan = build_plan(c.request(weeks=12, capacity=()))
    assert [n.key for n in plan.notes] == ["plan.note.no_test"]
    line = workout_lines(next(w for w in plan.workouts if w.template_id == "threshold"), plan.request.capacity_for(RUNNING),
                         plan.zone_system)[1]
    assert line["target"] == "" and line["zone"] == "Z4" and line["zone_name"] == "Tröskel" and "7 av 10" in line["rpe"]


def test_retest_moves_every_target_and_nothing_else():
    plan = build_plan(c.CASES["half_12"])
    better = Capacity(RUNNING, Anchors(193, 45, 167, 177, 13.8, 15.0), date(2027, 1, 8), "Laktattrappa")
    new = resolve.retest(plan, better)
    assert new.weeks is plan.weeks and new.blocks is plan.blocks          # the plan itself is untouched
    assert new.based_on[RUNNING] == date(2027, 1, 8) and plan.based_on[RUNNING] == date(2026, 11, 13)
    w = next(w for w in plan.workouts if w.template_id == "threshold")
    old_line = workout_lines(w, plan.request.capacity_for(RUNNING), plan.zone_system)[1]
    new_line = workout_lines(w, new.request.capacity_for(RUNNING), new.zone_system)[1]
    assert old_line["zone"] == new_line["zone"] == "Z4" and old_line["what"] == new_line["what"]
    assert old_line["target"] == "170–174 slag/min · 4:21–4:10 min/km"
    assert new_line["target"] == "172–177 slag/min · 4:10–4:00 min/km"


def test_resolve_survives_the_renaming_of_the_speed_fields(monkeypatch):
    @dataclass
    class NewZone:                     # Zone after step 0b: load instead of speed_kmh
        code: str
        name: str
        hr_low: int
        hr_high: int
        load_low: float
        load_high: float
        estimated: bool = False

    monkeypatch.setattr(resolve, "convert", lambda anchors, system: [NewZone("Z4", "Tröskel", 170, 174, 13.8, 14.4)])
    assert resolve.targets(c.TESTED)["Z4"].pace_text == "4:21–4:10 min/km"


def test_texts():
    assert [duration(s) for s in (45, 60, 90, 360, 3600, 5400)] == ["45 s", "1 min", "90 s", "6 min", "1 h", "1 h 30 min"]
    plan = build_plan(c.CASES["half_12"])
    w = next(w for w in plan.workouts if w.template_id == "vo2max")
    lines = [plain(x) for x in workout_lines(w, c.TESTED, plan.zone_system)]
    assert lines[1].startswith("Intervaller: ") and "× 3 min i Z5 (174–192 slag/min · 4:10 min/km eller snabbare), vila 2 min" in lines[1]
    set_language("en")
    assert plain(workout_lines(w, c.TESTED, plan.zone_system)[0]) == "Warm-up: 15 min in Z2 (115–165 bpm · 4:33 min/km or slower)"


# ------------------------------------------------------------------ a zones test: pace from heart rate
# HR = 60 + 8 * speed, measured from 8 to 12 km/h, ramp run to 15 km/h; max HR 190 (Garmin's % of max).
LINE = SpeedLine(slope=8.0, intercept=60.0, speed_min=8.0, speed_max=12.0, ramp_top_kmh=15.0)
ZONES_TEST = Capacity(RUNNING, Anchors(max_hr=190), date(2026, 11, 13), "Puls- och fartzoner", LINE)


def test_a_zones_test_gives_the_estimated_zones_a_pace_from_heart_rate():
    assert ZONES_TEST.estimated and ZONES_TEST.speed_from_hr
    zt = resolve.targets(ZONES_TEST)
    z3, z4, z5 = zt["Z3"], zt["Z4"], zt["Z5"]
    # the heart rates are those of max HR alone: the line only adds the pace
    assert [(z.hr_low, z.hr_high) for z in (z3, z4)] == [(133, 152), (152, 171)]
    assert z3.speed_from_hr and z3.estimated
    assert (z3.speed_low_kmh, z3.speed_high_kmh) == (9.1, 11.5)               # inside the steps: no ≈
    assert z3.pace_text == "6:36–5:13 min/km" and "≈" not in z3.pace_text
    assert z4.speed_high_kmh == 13.9 and z4.speed_high_approx and not z4.speed_low_approx
    assert z4.pace_text == "5:13–≈4:19 min/km"                                 # a little above the steps: ≈
    assert z5.speed_high_kmh is None and z5.pace_text == "≈4:19 min/km eller snabbare"   # never past what was run
    plan = build_plan(c.request(weeks=12, capacity=(ZONES_TEST,)))
    assert [n.key for n in plan.notes] == ["plan.note.speed_from_hr"]


def test_the_lactate_thresholds_win_over_the_speed_line():
    cap = Capacity(RUNNING, c.TESTED.anchors, c.TESTED.tested, "Laktattrappa", LINE)
    assert not cap.speed_from_hr
    assert resolve.targets(cap) == resolve.targets(c.TESTED)


def test_speed_line_from_an_unusable_relation_is_none():
    from lopanalys.assessments.hr_zones import HrSpeedResult
    assert resolve.speed_line(None) is None and resolve.speed_line(HrSpeedResult()) is None
    rel = HrSpeedResult(slope=8.0, intercept=60.0, speed_min=8.0, speed_max=12.0, ramp_top_kmh=15.0)
    assert resolve.speed_line(rel) == LINE
