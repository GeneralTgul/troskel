"""The tests of the other sports than running in the plan (#666): the test chosen per sport in the form, and a new
test that lays the rest of the plan on it (a bike or SkiErg test offered on the plan page and on its result, a CSS
test offered when it is saved), moving only that sport's targets."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtWidgets import QMessageBox

from lopanalys.assessments import css
from lopanalys.core.models import TestKind
from lopanalys.core.sport import Sport
from lopanalys.plan import RUNNING
from lopanalys.ui import services
from lopanalys.ui.views.css_test import CssTestDialog


@pytest.fixture(autouse=True)
def _answers(monkeypatch):
    """Every question is answered yes (a closing window throws an unsaved form away); notices are dismissed."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)


@pytest.fixture
def erik(demo_window):
    return next(a for a in demo_window.ctx.store.athletes() if a.first_name == "Erik")


def _plan(win, erik, *sports):
    """A saved plan for Erik that began three weeks ago, with running and the given sports."""
    win.open_plan(erik)
    v = win.plan
    v.level.setCurrentIndex(v.level.findData("recreational"))
    v.recent.setText("240")
    for d, m in ((0, "45"), (1, "75"), (3, "75"), (5, "60"), (6, "120")):
        v.minutes[d].setText(m)
    v.start.edit.setText((date.today() - timedelta(weeks=3)).isoformat())
    v.mode.setCurrentIndex(v.mode.findData("weeks"))
    v.weeks.setText("16")
    for sp in sports:
        v.sport_rows[sp].check.setChecked(True)
    for h in v.health.values():
        h.setChecked(True)
    assert v.save(), v.error.text()
    return services.load_plan(win.ctx.store, erik)


def _lactate_test(store, erik, sport: Sport, days_ago: int):
    """A lactate test in ``sport``: the steps of Erik's running lactate test, a day of its own."""
    src = next(s for s in services.plan_tests(store, erik.id) if s.kind == TestKind.LACTATE_STEP)
    when = datetime.now().astimezone() - timedelta(days=days_ago)
    s = store.save_session(replace(src, id=None, sport=sport, started=when, ended=when + timedelta(minutes=50)))
    store.save_records(s.id, store.records(src.id))
    return s


def test_a_bike_test_moves_only_the_bike(demo_window, erik):
    win, store = demo_window, demo_window.ctx.store
    before = _plan(win, erik, "bike")
    assert services.retest_candidates(store, erik, before) == []
    bike = _lactate_test(store, erik, Sport.BIKE, 1)
    assert [s.id for s in services.retest_candidates(store, erik, before)] == [bike.id]
    v = win.plan
    win._plan_back()
    win.open_plan(erik)
    assert v.banner.isVisible() and "Cykel" in v.banner_text.text() and v.retest_btn.isVisible()
    # the result of the bike test offers it too
    win.open_session(bike.id)
    assert win.results.plan_card.isVisible() and win.results.retest_btn.isVisible()
    assert "Cykel" in win.results.plan_text.text()
    win.results.retest_btn.click()
    stored = services.load_plan(store, erik)
    after = stored.plan
    assert stored.session_id == before.session_id                       # the running test stays the plan's test
    assert after.request.capacity_for(RUNNING) == before.plan.request.capacity_for(RUNNING)
    new = after.request.capacity_for("bike")
    assert new.tested == bike.started.astimezone().date() and new != before.plan.request.capacity_for("bike")
    assert after.capacity_on("bike", date.today() - timedelta(days=1)) == before.plan.request.capacity_for("bike")
    assert after.capacity_on("bike", date.today()) == new
    old_keys = {(w.key, w.date) for w in before.plan.workouts}
    assert old_keys <= {(w.key, w.date) for w in after.workouts}       # no workout moved
    events = store.db.plan_events(stored.row_id)
    assert events[-1]["kind"] == "retest" and events[-1]["detail"]["sport"] == "bike"
    assert services.retest_candidates(store, erik) == [] and not win.results.retest_btn.isVisible()


def test_a_test_in_a_sport_the_plan_does_not_have_is_not_offered(demo_window, erik):
    win, store = demo_window, demo_window.ctx.store
    _plan(win, erik)
    erg = _lactate_test(store, erik, Sport.SKIERG, 1)
    assert services.retest_candidates(store, erik) == []
    win.open_session(erg.id)
    assert win.results.plan_card.isVisible() and not win.results.retest_btn.isVisible()


def test_skis_are_laid_on_a_new_skierg_test(demo_window, erik):
    win, store = demo_window, demo_window.ctx.store
    _plan(win, erik, "ski")
    erg = _lactate_test(store, erik, Sport.SKIERG, 1)
    assert [s.id for s in services.retest_candidates(store, erik)] == [erg.id]
    after = services.apply_retest(store, erik, erg).plan
    assert after.request.own_capacity("skierg").tested == erg.started.astimezone().date()
    assert after.request.capacity_for("ski").tested == erg.started.astimezone().date()   # skis borrow it


def test_a_css_test_is_offered_when_it_is_saved(demo_window, erik):
    win, store = demo_window, demo_window.ctx.store
    before = _plan(win, erik, "swim")
    assert before.plan.request.own_capacity("swim") is None              # swimming on effort
    d = CssTestDialog(win.ctx, erik, None, win)
    d.t400.setText("6:40")
    d.t200.setText("3:10")
    d.save()
    stored = services.load_plan(store, erik)
    swim = stored.plan.request.own_capacity("swim")
    assert swim is not None and swim.css_s100 == 105.0 and stored.session_id == before.session_id
    assert stored.plan.capacity_on("swim", date.today() - timedelta(days=1)).css_s100 is None
    assert services.retest_candidates(store, erik) == []
    assert services.retest_done_text(d.saved, date(2027, 1, 4)).startswith("Schemat är omlagt efter det nya testet, Simning.")


def test_each_sport_chooses_its_test_in_the_form(demo_window, erik):
    win, store = demo_window, demo_window.ctx.store
    old_bike = _lactate_test(store, erik, Sport.BIKE, 40)
    new_bike = _lactate_test(store, erik, Sport.BIKE, 5)
    old_swim = css.make(420.0, 200.0, date.today() - timedelta(days=60))
    new_swim = css.make(400.0, 190.0, date.today() - timedelta(days=2))
    services.save_css_test(store, erik, old_swim)
    services.save_css_test(store, erik, new_swim)
    win.open_plan(erik)
    v = win.plan
    bike, swim, ski, erg = (v.sport_rows[sp] for sp in ("bike", "swim", "ski", "skierg"))
    assert [bike.test.itemData(i) for i in range(bike.test.count())] == [new_bike.id, old_bike.id, None]
    assert bike.test.itemText(2).startswith("Inget test") and bike.test.currentData() == new_bike.id   # the newest
    assert bike.test.isHidden()                                       # only for a sport in the plan
    assert erg.test.count() == 0 and not erg.test_shown               # no SkiErg tests: the text as before
    for sp in ("bike", "swim"):
        v.sport_rows[sp].check.setChecked(True)
    assert bike.test.isVisibleTo(v) and swim.test.isVisibleTo(v)
    assert bike.basis.text() == "" and swim.basis.text().startswith("Tempot ur CSS-testet")
    bike.test.setCurrentIndex(1)
    swim.test.setCurrentIndex(1)
    _ = _plan_form(v)
    req = v.collect()
    cap = {c.sport: c for c in req.capacity}
    assert cap["bike"].tested == old_bike.started.astimezone().date()
    assert cap["swim"].tested == old_swim.tested and cap["swim"].css_s100 == old_swim.css_s100
    assert v.save() and not v.dirty()
    # read back: the chosen tests, not the newest
    win._plan_back()
    win.open_plan(erik)
    assert bike.test.currentData() == old_bike.id and swim.test.currentIndex() == 1 and not v.dirty()
    # the newer tests are offered as a retest, running first
    assert [s.sport for s in services.retest_candidates(store, erik)] == [Sport.BIKE, Sport.SWIM]
    # *Inget test*: the bike on its max HR, swimming on effort
    bike.test.setCurrentIndex(2)
    swim.test.setCurrentIndex(2)
    assert v.dirty() and swim.basis.text() == "Inget test i Tröskel: upplevd ansträngning"
    req = v.collect()
    cap = {c.sport: c for c in req.capacity}
    assert "swim" not in cap and cap["bike"].tested is None
    v.revert()
    assert bike.test.currentData() == old_bike.id


def test_skis_choose_among_the_skierg_s_tests_unless_the_skierg_is_in_the_plan(demo_window, erik):
    win, store = demo_window, demo_window.ctx.store
    erg = _lactate_test(store, erik, Sport.SKIERG, 3)
    win.open_plan(erik)
    v = win.plan
    ski, sk = v.sport_rows["ski"], v.sport_rows["skierg"]
    ski.check.setChecked(True)
    assert ski.test.isVisibleTo(v) and ski.test.currentData() == erg.id
    assert ski.basis.text() == "Lånar zonerna från SkiErg"
    _plan_form(v)
    req = v.collect()
    assert [c.sport for c in req.capacity if c.sport != RUNNING] == ["skierg"]
    assert req.capacity_for("ski").tested == erg.started.astimezone().date()
    sk.check.setChecked(True)                                          # the SkiErg's own row chooses
    assert ski.test.isHidden() and sk.test.isVisibleTo(v)
    req = v.collect()
    assert [c.sport for c in req.capacity if c.sport != RUNNING] == ["skierg"]
    v.revert()


def _plan_form(v):
    v.level.setCurrentIndex(v.level.findData("recreational"))
    v.recent.setText("240")
    for d, m in ((0, "45"), (1, "75"), (3, "75"), (5, "60"), (6, "120")):
        v.minutes[d].setText(m)
    v.start.edit.setText(date.today().isoformat())
    v.mode.setCurrentIndex(v.mode.findData("weeks"))
    v.weeks.setText("12")
    for h in v.health.values():
        h.setChecked(True)
