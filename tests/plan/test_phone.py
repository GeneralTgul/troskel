"""The athlete's plan from the phone, read in Tröskel (#781): the app's file and link (plan.phone).

The app's fixture ``app/test/fixtures/athlete.troskel`` is what *Skicka schemat till din coach* gives for the demo
plan after the athlete took Mondays away (the app's *Spara*), ticked off three workouts and added a session outside the
plan. ``athlete.back.link.txt`` is the way back after a new test, which app/test/share.test.tsx reads with the app."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.plan import demo, link, phone, resolve, rolling

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "app" / "test" / "fixtures"


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.fixture
def athlete() -> dict:
    return json.loads((FIXTURES / "athlete.troskel").read_text(encoding="utf-8"))


def _workouts(plan):
    return {w.key: w for w in plan.workouts}


def test_the_file_and_the_link_give_the_same_plan(athlete):
    from_file = phone.read((FIXTURES / "athlete.troskel").read_text(encoding="utf-8"))
    from_link = phone.read((FIXTURES / "athlete.link.txt").read_text(encoding="ascii"))
    assert from_file == from_link
    assert from_file.plan_id == "demo" and from_file.exported == date(2026, 11, 25)
    assert from_file.athlete == "Exempel Löpare"
    assert from_file.counts() == {"done": 2, "partial": 1, "missed": 0, "extras": 1}
    # a link pasted inside a message, and a file with a byte order mark
    assert phone.read("Här är mitt schema:\n" + (FIXTURES / "athlete.link.txt").read_text() + "Hälsningar") == from_file
    assert phone.read("﻿" + json.dumps(athlete)) == from_file


def test_what_is_not_a_plan_from_the_phone_says_why(athlete):
    coach = link.stored_plan(demo.demo_plan(12))
    cases = {
        link.plan_link(link.link_payload(coach)): "coach_plan",
        link.plan_file(coach).decode("utf-8"): "coach_plan",
        "": "none",
        "hej": "none",
        link.APP_URL + "#a=inte-data": "broken",
        json.dumps({**athlete, "format": phone.FORMAT + 1}): "newer",
        json.dumps({**athlete, "plan": {"kind": "troskel.plan"}}): "broken",
        "{trasig": "broken",
    }
    for text, code in cases.items():
        with pytest.raises(phone.NotFromPhone) as e:
            phone.read(text)
        assert e.value.code == code, text[:60]


def test_marks_outside_the_known_statuses_and_broken_extras_are_left_out(athlete):
    athlete["marks"]["x"] = {"status": "planned"}
    athlete["marks"]["y"] = "done"
    athlete["extras"].append({"id": "z"})
    got = phone.read(json.dumps(athlete))
    assert "x" not in got.marks and "y" not in got.marks and len(got.extras) == 1


def test_the_plan_as_troskel_saves_it(athlete):
    got = phone.read(json.dumps(athlete))
    plan = phone.to_plan(got, "Erik Exempel", date(2026, 11, 26))
    assert plan.id == "demo" and plan.request.athlete_name == "Erik Exempel"
    # the athlete's times: no Mondays after the 26th, as the app rebuilt it
    assert all(s.weekday != 0 for s in plan.request.availability)
    assert not [w for w in plan.workouts if w.date >= date(2026, 11, 26) and w.date.weekday() == 0]
    # done stays done, partly done stays planned (its minutes are in the log), the rest is as before
    ws = list(plan.workouts)
    assert [w.status for w in ws[:3]] == ["done", "planned", "done"]
    assert all(w.status == "planned" for w in ws[3:])


def test_times_waiting_on_the_phone_are_laid_on_from_tomorrow(athlete):
    # the athlete saved new times the app could not build (no engine): Tuesday 90 minutes, and away 3 December
    slots = [dict(s, max_minutes=90) if s["weekday"] == 1 else s for s in athlete["plan"]["request"]["availability"]]
    athlete["times"] = {"slots": slots, "unavailable": ["2026-11-20", "2026-12-03"], "saved": "2026-11-25"}
    got = phone.read(json.dumps(athlete))
    plan = phone.to_plan(got, "", date(2026, 11, 26))
    before = phone.to_plan(phone.read(json.dumps({**athlete, "times": None})), "", date(2026, 11, 26))
    tue = [s for s in plan.request.availability if s.weekday == 1]
    assert tue and tue[0].max_minutes == 90
    assert not [w for w in plan.workouts if w.date == date(2026, 12, 3) and w.goal is None]
    # up to and with today nothing moves; the day that already passed is not taken away
    old, new = _workouts(before), _workouts(plan)
    for k, w in old.items():
        if w.date <= date(2026, 11, 26):
            assert new[k] == w
    # a plan written outside the engine keeps its workouts: the times are not the engine's to lay
    athlete["plan"]["plan"]["source"] = "Eget schema"
    written = phone.to_plan(phone.read(json.dumps(athlete)), "", date(2026, 11, 26))
    assert [s.max_minutes for s in written.request.availability if s.weekday == 1] != [90]


def test_a_new_test_on_the_athletes_plan_keeps_the_athletes_times():
    """The way back (#776): the new test lays its targets on the plan from the phone; no day of the athlete's moves,
    and the link to the app has the athlete's times."""
    got = phone.read((FIXTURES / "athlete.troskel").read_text(encoding="utf-8"))
    day = date(2026, 11, 26)
    mine = phone.to_plan(got, got.athlete, day)
    cap = replace(mine.request.capacity_for("run"), tested=day)
    back = rolling.roll_forward(resolve.retest(mine, cap, from_date=day), day)
    assert back.request.availability == mine.request.availability
    before, after = _workouts(mine), _workouts(back)
    assert set(before) <= set(after) and all(after[k].date == w.date for k, w in before.items())
    sent = link.expand(link.read_link((FIXTURES / "athlete.back.link.txt").read_text()))
    assert sent["plan"]["id"] == got.plan_id
    assert sent["request"]["availability"] == got.stored["request"]["availability"]
