"""Träningsschema with several sports (#666, step 4): the sports with role and share in the form, the sports
each weekday allows, the capacity per sport from the sport's own test, and all of it saved and read back."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QMessageBox

from lopanalys.plan import RUNNING, SportRole
from lopanalys.ui import services


@pytest.fixture(autouse=True)
def _throw_away_on_close(monkeypatch):
    """Closing the window with an unsaved form asks first: the tests throw the changes away."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)


@pytest.fixture
def erik(demo_window):
    return next(a for a in demo_window.ctx.store.athletes() if a.first_name == "Erik")


def _fill(v) -> None:
    v.level.setCurrentIndex(v.level.findData("recreational"))
    v.recent.setText("240")
    for d, m in ((0, "45"), (1, "75"), (3, "75"), (5, "60"), (6, "120")):
        v.minutes[d].setText(m)
    v.start.edit.setText(date.today().isoformat())
    v.mode.setCurrentIndex(v.mode.findData("weeks"))
    v.weeks.setText("12")


def _tick(v, sport: str, on: bool = True) -> None:
    v.sport_rows[sport].check.setChecked(on)


def test_running_alone_is_the_default_and_the_days_ask_nothing(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    rows = v.sport_rows
    assert list(rows) == ["run", "bike", "skierg", "ski", "swim"]
    assert rows["run"].on and rows["run"].snapshot() == (True, "main", 60)
    assert not any(rows[sp].on for sp in ("bike", "skierg", "ski", "swim"))
    assert not rows["bike"].role.isEnabled() and rows["bike"].snapshot() == (False, "support", 30)
    assert all(d.isHidden() for d in v.day_sports) and v.test_box.isVisibleTo(v)
    _fill(v)
    req = v.collect()
    assert req.roles == () and req.sports == (RUNNING,)          # the same request as before several sports
    assert all(s.sports == (RUNNING,) for s in req.availability)


def test_two_sports_with_roles_shares_and_one_main_sport(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    _tick(v, "bike")
    assert v.sport_rows["bike"].snapshot() == (True, "support", 30)
    assert all(not d.isHidden() for d in v.day_sports) and v.day_sports[0].text() == "Alla"
    req = v.collect()
    assert req.roles == (SportRole(RUNNING, "main", 60), SportRole("bike", "support", 30))
    assert all(s.sports == (RUNNING, "bike") for s in req.availability)
    assert {c.sport for c in req.capacity} == {RUNNING, "bike"}
    # the bike as the main sport: running becomes a supporting sport with its share
    bike = v.sport_rows["bike"]
    bike.role.setCurrentIndex(bike.role.findData("main"))
    assert bike.snapshot() == (True, "main", 60) and v.sport_rows["run"].snapshot() == (True, "support", 30)
    assert v.chosen_sports() == ("bike", RUNNING)
    run = v.sport_rows["run"]
    run.share.setCurrentIndex(run.share.findData(40))
    req = v.collect()
    assert req.main_sport == "bike" and req.roles[1] == SportRole(RUNNING, "support", 40)
    assert v.preview() and {w.sport for w in v.plan.workouts} == {RUNNING, "bike"}


def test_a_day_with_some_sports_only_is_saved_and_read_back(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    for h in v.health.values():
        h.setChecked(True)
    _tick(v, "bike")
    _tick(v, "swim")
    swim = v.sport_rows["swim"]
    swim.role.setCurrentIndex(swim.role.findData("maintenance"))
    tue = v.day_sports[1]
    tue.actions_["bike"].setChecked(True)
    assert tue.text() == "Cykel" and tue.chosen() == ("bike",)
    assert not v.day_sports[1].actions_["skierg"].isVisible()      # only the plan's sports can be ticked
    req = v.collect()
    assert req.availability[1].sports == ("bike",) and req.availability[0].sports == (RUNNING, "bike", "swim")
    assert v.save() and not v.dirty()
    win._plan_back()
    win.open_plan(erik)
    assert v.sport_rows["swim"].snapshot() == (True, "maintenance", 10)
    assert v.sport_rows["bike"].snapshot() == (True, "support", 30) and not v.sport_rows["ski"].on
    assert v.day_sports[1].chosen() == ("bike",) and v.day_sports[0].chosen() == ()
    assert v.strength.currentData() == 0
    assert not v.dirty() and v.plan.request.roles == req.roles
    # a change in the sports is a change of the form
    _tick(v, "swim", False)
    assert v.dirty() and v.stale.isVisible()
    v.revert()
    assert v.sport_rows["swim"].on and not v.dirty()


def test_a_plan_without_running_needs_no_running_test(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    _tick(v, "bike")
    _tick(v, "run", False)
    assert v.test_box.isHidden() and v._session_id() is None
    assert all(d.isHidden() for d in v.day_sports)                 # one sport: nothing to ask per day
    req = v.collect()
    assert req.main_sport == "bike" and req.roles == (SportRole("bike", "support", 30),)
    assert [c.sport for c in req.capacity] == ["bike"]
    assert v.preview() and {w.sport for w in v.plan.workouts} == {"bike"}
    _tick(v, "bike", False)
    assert v.collect() is None and v.error.text() == "Välj minst en idrott."


def test_goal_is_in_the_main_sport(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    v.mode.setCurrentIndex(v.mode.findData("goal"))
    v.goal_name.setText("Skidloppet")
    start = date.today()
    v.goal_date.edit.setText((start + timedelta(weeks=14, days=5 - start.weekday())).isoformat())
    _tick(v, "ski")
    ski = v.sport_rows["ski"]
    ski.role.setCurrentIndex(ski.role.findData("main"))
    req = v.collect()
    assert req.goals[0].sport == "ski" and req.main_sport == "ski"


def test_the_basis_of_each_sport_is_shown_under_it(demo_window, erik):
    win = demo_window
    erik.sport_max_hr = {"bike": 184, "skierg": 186}
    win.open_plan(erik)
    v = win.plan
    rows = v.sport_rows
    for sp in ("bike", "skierg", "ski", "swim"):
        _tick(v, sp)
    assert rows["run"].basis.isHidden()
    assert rows["bike"].basis.text() == "Inget test: zonerna uppskattas ur maxpulsen"
    assert rows["skierg"].basis.text() == "Inget test: zonerna uppskattas ur maxpulsen"
    assert rows["ski"].basis.text() == "Lånar zonerna från SkiErg"
    erik.sport_max_hr = {}                                   # nothing to borrow
    v._sport_basis()
    assert rows["ski"].basis.text() == "Inget test och ingen maxpuls: zon och ansträngning"
    assert rows["skierg"].basis.text() == rows["ski"].basis.text()
    assert rows["swim"].basis.text() == "Inget test i Tröskel: upplevd ansträngning"
    assert all(rows[sp].basis.isVisibleTo(v) for sp in ("bike", "skierg", "ski", "swim"))


def test_sport_capacity_takes_the_sport_s_own_test_or_max_hr(demo_window, erik):
    store = demo_window.ctx.store
    erik.sport_max_hr = {"bike": 184}
    bike = services.sport_capacity(store, erik, "bike")
    assert bike.sport == "bike" and bike.anchors.max_hr == 184 and bike.anchors.sport == "bike" and bike.estimated
    erg = services.sport_capacity(store, erik, "skierg")
    assert erg.sport == "skierg" and erg.anchors is None
    assert services.sport_capacity(store, erik, "ski") is None and services.sport_capacity(store, erik, "swim") is None
    assert services.sport_tests(store, erik.id, "bike") == []
    run = services.sport_capacity(store, erik, RUNNING)
    newest = services.plan_tests(store, erik.id)[0]
    assert run == services.plan_capacity(store, erik, newest)


def test_strength_sessions_are_asked_saved_and_read_back(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    for h in v.health.values():
        h.setChecked(True)
    assert [v.strength.itemData(i) for i in range(v.strength.count())] == [0, 1, 2, 3]
    assert v.strength.currentData() == 0 and v.strength.currentText() == "Inga"
    v.strength.setCurrentIndex(v.strength.findData(2))
    req = v.collect()
    assert req.strength_sessions == 2 and req.roles == () and req.multi_sport
    assert v.preview() and sum(wk.strength_sessions for wk in v.plan.detailed_weeks) > 0
    assert v.save() and not v.dirty()
    win._plan_back()
    win.open_plan(erik)
    assert v.strength.currentData() == 2 and not v.dirty()
    v.strength.setCurrentIndex(v.strength.findData(0))
    assert v.dirty()


def test_a_second_time_on_a_weekday_is_asked_saved_and_read_back(demo_window, erik):
    """Two workouts a day (#666): with more than one sport the + beside a day opens a second time."""
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    for h in v.health.values():
        h.setChecked(True)
    assert all(b.isHidden() for b in v.second_btns)               # one sport: no second time
    _tick(v, "bike")
    assert all(b.isVisibleTo(v) for b in v.second_btns) and v.minutes2[1].isHidden()
    v.level.setCurrentIndex(v.level.findData("trained"))
    v.clocks[1].setText("18:00")
    v.second_btns[1].click()
    assert v.minutes2[1].isVisibleTo(v) and v.second_btns[1].text() == "−"
    v.minutes2[1].setText("45")
    assert v.collect() is None and v.error.text() == "Tisdag: två pass samma dag behöver två olika klockslag."
    v.clocks2[1].setText("7")
    v.day_sports2[1].actions_["bike"].setChecked(True)
    req = v.collect()
    tue = [s for s in req.availability if s.weekday == 1]
    assert [(s.max_minutes, f"{s.start:%H:%M}", s.sports) for s in tue] == [(75, "18:00", (RUNNING, "bike")),
                                                                         (45, "07:00", ("bike",))]
    assert v.preview() and any(w.key.endswith("-2") for w in v.plan.workouts)
    assert v.save() and not v.dirty()
    win._plan_back()
    win.open_plan(erik)
    assert v.second_btns[1].isChecked() and v.minutes2[1].text() == "45" and v.clocks2[1].text() == "07:00"
    assert v.day_sports2[1].chosen() == ("bike",) and not v.second_btns[2].isChecked() and not v.dirty()
    v.second_btns[1].click()                                       # closing it removes the second time
    assert v.minutes2[1].text() == "" and v.dirty()
    assert [s.weekday for s in v.collect().availability].count(1) == 1
    v.revert()


def test_fixed_hard_days_and_the_high_level_are_asked_saved_and_read_back(demo_window, erik):
    """Engine 9: up to two fixed hard days, each with a sport or any, and the level for high volume."""
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    _tick(v, "bike")
    for h in v.health.values():
        h.setChecked(True)
    assert [v.level.itemData(i) for i in range(v.level.count())][-1] == "high"
    assert len(v.hard_days) == 2 and v.hard_days[0].currentData() is None and not v.hard_sports[0].isEnabled()
    assert v.collect().hard_days == ()
    v.level.setCurrentIndex(v.level.findData("high"))
    v.hard_days[0].setCurrentIndex(v.hard_days[0].findData(3))
    assert v.hard_sports[0].isEnabled()
    v.hard_sports[0].setCurrentIndex(v.hard_sports[0].findData("bike"))
    v.hard_days[1].setCurrentIndex(v.hard_days[1].findData(1))
    v.hard_sports[1].setCurrentIndex(v.hard_sports[1].findData("swim"))      # not in the plan: any sport
    req = v.collect()
    assert req.level == "high"
    assert [(h.weekday, h.sport) for h in req.hard_days] == [(3, "bike"), (1, "")]
    assert v.preview()
    assert all(w.sport == "bike" for w in v.plan.workouts if w.quality and w.date.weekday() == 3)
    assert v.save() and not v.dirty()
    win._plan_back()
    win.open_plan(erik)
    assert v.level.currentData() == "high"
    assert [(d.currentData(), s.currentData()) for d, s in zip(v.hard_days, v.hard_sports)] == [(3, "bike"), (1, "")]
    assert not v.dirty()
    v.hard_days[1].setCurrentIndex(0)
    assert v.dirty()
