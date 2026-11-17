"""*Läs in från appen* on the plan page (#781): the athlete's plan from the phone, read in before a new test.

The athlete's phone is played with plan.phone and the plan's own link: the plan Tröskel sent, with the times the
athlete changed in the app (phone.rebuild, the rule of the app's *Spara*), what was ticked off and a session outside
the plan. Then a new test lays its targets on that plan, and the link back to the app has the athlete's times."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QMessageBox

from lopanalys.core.models import TestKind
from lopanalys.plan import link, phone
from lopanalys.ui import services


@pytest.fixture
def boxes(monkeypatch):
    """The message boxes the page shows, answered with ``boxes["answer"]`` (Yes unless a test says otherwise)."""
    out: dict = {"asked": [], "warned": [], "told": [], "answer": QMessageBox.StandardButton.Yes}
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda _p, _t, text, *a, **k: out["asked"].append(text) or out["answer"]))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda _p, _t, text, *a, **k: out["warned"].append(text) or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda _p, _t, text, *a, **k: out["told"].append(text) or QMessageBox.StandardButton.Ok))
    return out


@pytest.fixture
def erik(demo_window):
    return next(a for a in demo_window.ctx.store.athletes() if a.first_name == "Erik")


def _plan(win, erik):
    """A saved plan for Erik that began three weeks ago, five days a week."""
    win.open_plan(erik)
    v = win.plan
    v.level.setCurrentIndex(v.level.findData("recreational"))
    v.recent.setText("240")
    for d, m in ((0, "45"), (1, "75"), (3, "75"), (5, "60"), (6, "120")):
        v.minutes[d].setText(m)
    v.start.edit.setText((date.today() - timedelta(weeks=3)).isoformat())
    v.mode.setCurrentIndex(v.mode.findData("weeks"))
    v.weeks.setText("16")
    for h in v.health.values():
        h.setChecked(True)
    assert v.save(), v.error.text()
    return services.load_plan(win.ctx.store, erik)


def _from_phone(stored, *, name: str, plan_id: str | None = None, times: dict | None = None) -> dict:
    """What the app's *Skicka schemat till din coach* gives: the plan the coach sent, without Mondays from tomorrow
    (the athlete's *Spara*), the first workout done, the second partly done, the third missed, and a yoga session."""
    sent = link.expand(link.read_link(link.plan_link(link.link_payload(link.stored_plan(stored.plan, name)))))
    today = date.today()
    slots = [s for s in sent["request"]["availability"] if s["weekday"] != 0]
    mine = phone.rebuild(sent, slots, [], (today + timedelta(days=1)).isoformat())
    if plan_id:
        mine["plan"]["id"] = plan_id
    ws = [w for wk in mine["plan"]["weeks"] for w in wk["workouts"] if w["date"] < today.isoformat()]
    out = {"kind": phone.KIND, "format": phone.FORMAT, "exported": today.isoformat(), "plan": mine,
           "marks": {ws[0]["key"]: {"status": "done"}, ws[1]["key"]: {"status": "partial", "minutes": 20},
                     ws[2]["key"]: {"status": "missed"}, ws[3]["key"]: {"status": "done", "auto": True}},
           "extras": [{"id": f"{ws[0]['date']}-1", "date": ws[0]["date"], "sport": "other", "minutes": 30,
                       "name": "Yoga"}]}
    if times:
        out["times"] = times
    return out


def _newer_test(store, erik):
    src = next(s for s in services.plan_tests(store, erik.id) if s.kind == TestKind.LACTATE_STEP)
    when = datetime.now().astimezone() - timedelta(hours=2)
    s = store.save_session(replace(src, id=None, started=when, ended=when + timedelta(minutes=50)))
    store.save_records(s.id, store.records(src.id))
    return s


def test_the_plan_from_the_phone_then_a_new_test(demo_window, erik, boxes):
    win, store = demo_window, demo_window.ctx.store
    before = _plan(win, erik)
    v = win.plan
    assert v.phone_btn.text() == "Läs in från appen" and v.phone_btn.isVisible() and not v.phone_note.isVisible()
    sent = _from_phone(before, name=erik.name)
    assert v.read_from_phone(phone.athlete_link(sent))
    assert boxes["asked"] == [] and boxes["warned"] == []                 # the same plan: read in without a question
    stored = services.load_plan(store, erik)
    assert stored.plan.id == before.plan.id and stored.session_id == before.session_id
    assert stored.health == before.health and not v.dirty()
    # the athlete's times, and what was ticked off as the workouts' status
    assert all(s.weekday != 0 for s in stored.plan.request.availability)
    marks = sent["marks"]
    status = {w.key: w.status for w in stored.plan.workouts}
    assert [status[k] for k in marks] == ["done", "planned", "missed", "done"]
    # the whole log in the plan's event log, and on the page
    events = store.db.plan_events(stored.row_id)
    assert events[-1]["kind"] == "phone" and events[-1]["detail"]["marks"] == marks
    assert events[-1]["detail"]["extras"][0]["name"] == "Yoga" and events[-1]["detail"]["replaced"] is None
    assert services.phone_logs(store, stored) == [events[-1]["detail"]]
    assert v.phone_note.isVisible() and "3 pass bockade och 1 utanför schemat" in v.phone_note.text()
    (told,) = boxes["told"]
    assert "2 pass gjorda, 1 delvis gjorda, 1 missade och 1 pass utanför schemat" in told
    assert "nytt test" in told and "väntade" not in told
    # a new test lays its targets on the athlete's plan: the athlete's days stay, and the link has the athlete's times
    newer = _newer_test(store, erik)
    win._plan_back()
    win.open_plan(erik)
    assert v.retest_btn.isVisible()
    v.apply_retest()
    after = services.load_plan(store, erik)
    assert after.session_id == newer.id and after.plan.request.availability == stored.plan.request.availability
    assert {(w.key, w.date) for w in stored.plan.workouts} <= {(w.key, w.date) for w in after.plan.workouts}
    url, _ = services.plan_app(after.plan)
    back = link.expand(link.read_link(url))
    assert back["plan"]["id"] == sent["plan"]["plan"]["id"]
    assert back["request"]["availability"] == sent["plan"]["request"]["availability"]
    # #778: the new test's report has *Mot schemat*, with what was ticked off on the phone
    f = services.plan_followup(store, erik, newer, store.previous_session(newer))
    assert f is not None and f["expected"]["sport"] == "run" and f["expected"]["gain_pct"] > 0
    assert f["done"]["logged_until"] == date.today().isoformat() and f["done"]["share"] is not None
    assert f["done"]["extras"] == 1 and f["done"]["done_min"] > 0
    assert services.plan_followup(store, erik, newer, None) is None


def test_not_a_plan_from_the_phone(demo_window, erik, boxes):
    win, store = demo_window, demo_window.ctx.store
    before = _plan(win, erik)
    v = win.plan
    n = len(store.db.plan_events(before.row_id))
    url, _ = services.plan_app(before.plan)
    assert not v.read_from_phone(url)
    assert not v.read_from_phone("hej")
    assert "schema från Tröskel" in boxes["warned"][0] and "Skicka schemat till din coach" in boxes["warned"][0]
    assert "inget schema från appen" in boxes["warned"][1]
    assert len(store.db.plan_events(before.row_id)) == n and services.load_plan(store, erik).plan == before.plan


def test_another_plan_or_another_name_is_asked_about_first(demo_window, erik, boxes):
    win, store = demo_window, demo_window.ctx.store
    before = _plan(win, erik)
    v = win.plan
    other = _from_phone(before, name=erik.name, plan_id="ett-annat")
    boxes["answer"] = QMessageBox.StandardButton.Cancel
    assert not v.read_from_phone(phone.athlete_link(other))
    assert "ett annat schema" in boxes["asked"][-1] and before.plan.start.isoformat() in boxes["asked"][-1]
    assert services.load_plan(store, erik).plan == before.plan
    boxes["answer"] = QMessageBox.StandardButton.Yes
    assert v.read_from_phone(phone.athlete_link(other))
    stored = services.load_plan(store, erik)
    assert stored.plan.id == "ett-annat" and stored.session_id is None
    assert store.db.plan_events(stored.row_id)[-1]["detail"]["replaced"] == before.plan.id
    # another name: the same plan, but maybe another athlete's phone
    boxes["answer"] = QMessageBox.StandardButton.Cancel
    named = _from_phone(stored, name="Anna Annan")
    assert not v.read_from_phone(json.dumps(named))
    assert "Anna Annan" in boxes["asked"][-1] and erik.name in boxes["asked"][-1]
    assert "ett annat schema" not in boxes["asked"][-1]


def test_from_a_file_and_from_the_clipboard(demo_window, erik, boxes, tmp_path, monkeypatch):
    win, store = demo_window, demo_window.ctx.store
    before = _plan(win, erik)
    v = win.plan
    sent = _from_phone(before, name=erik.name)
    f = tmp_path / "troskel-erik.troskel"
    f.write_text("﻿" + json.dumps(sent, indent=1), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(f), "")))
    assert v.read_phone_file()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    assert not v.read_phone_file()                                       # the coach closed the dialog
    QGuiApplication.clipboard().setText("Hej! Här är mitt schema:\n" + phone.athlete_link(sent) + "\n/Erik")
    assert v.read_phone_clipboard()
    kinds = [e["kind"] for e in store.db.plan_events(services.load_plan(store, erik).row_id)]
    assert kinds[-2:] == ["phone", "phone"] and boxes["warned"] == []


def test_times_waiting_on_the_phone_are_laid_on_from_tomorrow(demo_window, erik, boxes):
    win, store = demo_window, demo_window.ctx.store
    before = _plan(win, erik)
    v = win.plan
    tomorrow = date.today() + timedelta(days=1)
    sent = _from_phone(before, name=erik.name)
    slots = [dict(s, max_minutes=90) if s["weekday"] == 1 else s for s in sent["plan"]["request"]["availability"]]
    sent["times"] = {"slots": slots, "unavailable": [], "saved": date.today().isoformat()}
    assert v.read_from_phone(phone.athlete_link(sent))
    stored = services.load_plan(store, erik)
    assert [s.max_minutes for s in stored.plan.request.availability if s.weekday == 1] == [90]
    assert f"från och med {tomorrow.isoformat()}" in boxes["told"][-1]
    assert store.db.plan_events(stored.row_id)[-1]["detail"]["times"]["slots"] == slots
