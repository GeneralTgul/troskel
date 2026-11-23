"""Träningsschema: the coach's choices in the form (*Coachens val*, docs/TRANINGSPLAN.md#coachens-val) and the group
session's length on a fixed hard day. Every choice is off by default, each one reaches the request when the coach sets
it, and a saved plan with choices keeps them when it is opened and saved again (before, ``collect`` dropped them)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QMessageBox

from lopanalys.plan import HardDay, PlanRequest, build_plan
from lopanalys.ui import services


@pytest.fixture(autouse=True)
def _throw_away_on_close(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)


@pytest.fixture
def erik(demo_window):
    return next(a for a in demo_window.ctx.store.athletes() if a.first_name == "Erik")


def _fill(v) -> None:
    v.level.setCurrentIndex(v.level.findData("trained"))
    v.recent.setText("300")
    for d, m in ((0, "45"), (1, "75"), (3, "75"), (4, "60"), (5, "90"), (6, "120")):
        v.minutes[d].setText(m)
    v.start.edit.setText(date.today().isoformat())
    v.mode.setCurrentIndex(v.mode.findData("weeks"))
    v.weeks.setText("16")
    v.sport_rows["bike"].check.setChecked(True)
    for h in v.health.values():
        h.setChecked(True)


def _same(req: PlanRequest) -> PlanRequest:
    """The request with its capacities in one order (the stored plan keeps them in its own)."""
    return replace(req, capacity=tuple(sorted(req.capacity, key=lambda c: c.sport)))


def _monday_in(weeks: int) -> date:
    today = date.today()
    return today + timedelta(days=7 * weeks - today.weekday())


def test_the_choices_are_off_and_closed_by_default(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    assert not v.choices_btn.isChecked() and v.choices_box.isHidden() and v.choices_btn.text() == "Coachens val"
    assert [v.quality.itemData(i) for i in range(v.quality.count())] == [0, 1, 2, 3]
    assert v.quality.currentText() == "Motorns antal"
    assert all(d.currentData() is None for d in v.easy_days) and not any(m.isEnabled() for m in v.hard_minutes)
    assert not v.second_long.isChecked() and v.easy_weeks == [] and v.max_week.text() == ""
    _fill(v)
    req = v.collect()
    default = PlanRequest("x", date.today(), ())
    for f in ("quality_sessions", "easy_days", "second_long", "easy_weeks", "max_weekly_minutes", "hard_days"):
        assert getattr(req, f) == getattr(default, f), f
    v.choices_btn.click()
    assert v.choices_box.isVisibleTo(v)


def test_each_choice_reaches_the_request(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    v.choices_btn.click()
    v.quality.setCurrentIndex(v.quality.findData(2))
    assert v.collect().quality_sessions == 2 and v.choices_btn.text() == "Coachens val (1 av 5)"
    v.hard_days[0].setCurrentIndex(v.hard_days[0].findData(3))
    assert v.hard_minutes[0].isEnabled()
    v.hard_minutes[0].setText("90")
    assert v.collect().hard_days == (HardDay(3, "", 90),)
    v.easy_days[0].setCurrentIndex(v.easy_days[0].findData(1))
    v.easy_sports[0].setCurrentIndex(v.easy_sports[0].findData("bike"))
    v.easy_minutes[0].setText("60")
    v.easy_days[1].setCurrentIndex(v.easy_days[1].findData(4))
    assert v.collect().easy_days == (HardDay(1, "bike", 60), HardDay(4, "", 0))
    v.second_long.setChecked(True)
    assert v.collect().second_long
    wk = _monday_in(6)
    v.add_easy_week().date.edit.setText((wk + timedelta(days=2)).isoformat())
    v.add_easy_week()                                              # an empty row is no week
    assert v.collect().easy_weeks == (wk + timedelta(days=2),)
    v.max_week.setText("1200")                                     # four digits: 20 hours
    assert v.collect().max_weekly_minutes == 1200
    assert v.choices_btn.text() == "Coachens val (5 av 5)"
    assert v.preview()
    plan = v.plan
    assert max(w.minutes for w in plan.weeks) <= 1200
    assert all(not w.quality for w in plan.workouts if w.date.weekday() == 1)        # the fixed easy day
    assert next(w for w in plan.weeks if w.monday <= wk + timedelta(days=2) <= w.sunday).kind != "load"


def test_choices_out_of_range_are_refused(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    v.hard_days[0].setCurrentIndex(v.hard_days[0].findData(3))
    v.hard_minutes[0].setText("10")
    assert v.collect() is None and v.error.text().startswith("Torsdag: ange gruppassets längd")
    v.hard_minutes[0].clear()
    v.max_week.setText("30")
    assert v.collect() is None and v.error.text().startswith("Mest tid i veckan")
    v.max_week.clear()
    v.add_easy_week().date.edit.setText("2026-13")
    assert v.collect() is None and v.error.text().startswith("Lätt vecka")


def test_a_saved_plan_with_choices_is_read_back_and_saved_again_with_them(demo_window, erik):
    """The round trip: a plan saved with every choice (as from the app or an older form) opens with each field set,
    the form gives the same request, and a change elsewhere keeps the choices in the new plan."""
    win = demo_window
    store = win.ctx.store
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    base = v.collect()
    wk = _monday_in(7)
    req = replace(base, quality_sessions=3, hard_days=(HardDay(3, "bike", 75), HardDay(1, "", 0)),
                  easy_days=(HardDay(5, "run", 50),), second_long=True, easy_weeks=(wk + timedelta(days=3),),
                  max_weekly_minutes=540, strength_sessions=1)
    plan = build_plan(req, plan_id=f"p{erik.id}", today=date.today())
    health = {"confirmed": list(services.HEALTH_ITEMS), "date": date.today().isoformat()}
    services.save_plan(store, erik, plan, v.test.currentData(), health, "saved")
    v.revert()
    win._plan_back()
    win.open_plan(erik)
    assert _same(v.stored.plan.request) == _same(req)
    assert v.choices_btn.isChecked() and v.choices_box.isVisibleTo(v)
    assert v.quality.currentData() == 3 and v.second_long.isChecked() and v.max_week.text() == "540"
    assert [(d.currentData(), s.currentData(), m.text()) for d, s, m in
            zip(v.hard_days, v.hard_sports, v.hard_minutes)] == [(3, "bike", "75"), (1, "", "")]
    assert [(d.currentData(), s.currentData(), m.text()) for d, s, m in
            zip(v.easy_days, v.easy_sports, v.easy_minutes)] == [(5, "run", "50"), (None, "", "")]
    assert [r.date.date() for r in v.easy_weeks] == [wk + timedelta(days=3)]
    assert not v.dirty()
    assert _same(v.collect()) == _same(req)                        # the form gives back what was saved
    assert v.save() and store.db.get_plan(erik.id) is not None
    assert _same(services.load_plan(store, erik).plan.request) == _same(req)
    v.recent.setText("320")                                        # a change elsewhere: the plan is rebuilt
    assert v.save()
    again = services.load_plan(store, erik).plan.request
    assert _same(again) == _same(replace(req, recent_weekly_minutes=320))


def test_the_form_with_the_choices_open_fits_a_laptop(demo_window, erik, qapp):
    win = demo_window
    win.resize(1280, 720)
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    v.choices_btn.click()
    for n in range(2):
        v.hard_days[n].setCurrentIndex(v.hard_days[n].findData(n + 1))
        v.easy_days[n].setCurrentIndex(v.easy_days[n].findData(n + 4))
    v.add_easy_week()
    qapp.processEvents()
    qapp.processEvents()
    assert win.page_scroll.horizontalScrollBar().maximum() == 0
    assert v.form.minimumSizeHint().width() <= v.form_scroll.width()
    assert v.choices_box.width() <= v.form.width()
