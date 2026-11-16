"""The swimming test in the program (#666): the form for the 400 m and 200 m times, the test saved as a manual
session, the history, and the training plan taking swimming's pace per 100 m from it."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog, QMessageBox

from lopanalys.assessments import css
from lopanalys.core.i18n import set_language, t
from lopanalys.core.models import TestKind
from lopanalys.core.sport import Sport, profile, selectable_sports
from lopanalys.plan import RUNNING, resolve
from lopanalys.plan.models import SWIM
from lopanalys.ui import services
from lopanalys.ui.views.css_test import CssTestDialog, time_text


@pytest.fixture(autouse=True)
def _throw_away_on_close(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Ok)


@pytest.fixture
def erik(demo_window):
    return next(a for a in demo_window.ctx.store.athletes() if a.first_name == "Erik")


def _dialog(win, athlete, session=None, t400="6:40", t200="3:10") -> CssTestDialog:
    d = CssTestDialog(win.ctx, athlete, session, win)
    d.t400.setText(t400)
    d.t200.setText(t200)
    return d


def test_swimming_is_a_sport_with_one_manual_test_and_not_in_the_guide():
    assert Sport.SWIM.value == SWIM == "swim" and TestKind.SWIM_CSS.value == css.KIND
    assert Sport.SWIM not in selectable_sports()                  # nothing to run in the program
    p = profile("swim")
    assert p.pace_unit == "/100 m" and p.pace_str(1.0) == "1:40" and p.load_sources == ("manual",)
    assert time_text(400) == "6:40" and time_text(190.5) == "3:10.5"


def test_the_form_shows_the_pace_and_the_errors_of_the_check(demo_window, erik):
    d = _dialog(demo_window, erik)
    assert d.pace.text() == "CSS 1:45 /100 m" and d.error.text() == ""
    assert [d.pool.itemData(i) for i in range(d.pool.count())] == [25, 50]
    for t400, t200, key in (("", "3:10", "missing"), ("abc", "3:10", "missing"), ("6:40", "0:30", "range"),
                            ("5:00", "6:40", "order"), ("6:00", "3:10", "order")):
        d.t400.setText(t400)
        d.t200.setText(t200)
        assert d.pace.text() == "" and d.error.text() == ""     # no error while typing
        d.save()
        assert d.error.text() == t(f"assessments.css.error.{key}"), (t400, t200)
        assert d.saved is None and d.result() != QDialog.DialogCode.Accepted
    assert services.css_tests(demo_window.ctx.store, erik.id) == []


def test_the_test_is_saved_as_a_manual_session_and_read_back(demo_window, erik):
    win, store = demo_window, demo_window.ctx.store
    day = date.today() - timedelta(days=3)
    d = _dialog(win, erik, t400="6:40,5", t200="3:10")
    d.day.setDate(QDate(day.year, day.month, day.day))
    d.pool.setCurrentIndex(d.pool.findData(50))
    d.notes.setText("Utomhusbassäng")
    emitted = []
    win.ctx.sessions_changed.connect(lambda: emitted.append(1))
    d.save()
    assert d.saved is not None and d.saved.id is not None and emitted
    s = store.session(d.saved.id)
    assert s.kind == TestKind.SWIM_CSS and s.sport is Sport.SWIM and s.status == "finished"
    assert s.result == css.to_result(css.make(400.5, 190.0, pool_m=50)) and s.result["manual"] is True
    assert s.started.astimezone().date() == day and s.notes == "Utomhusbassäng" and s.protocol_name == "CSS-test"
    assert store.records(s.id) == [] and store.samples(s.id) == []
    test = services.css_test_of(s)
    assert test == css.CssTest(400.5, 190.0, day, 50) and test.css_s100 == 105.2
    assert services.css_test_of(services.plan_tests(store, erik.id)[0]) is None      # a running test is no CSS test
    assert all(x.id != s.id for x in services.plan_tests(store, erik.id))            # and never the plan's running test

    # opened again from the history: the form holds the stored values, and saving corrects the same session
    again = CssTestDialog(win.ctx, erik, s, win)
    assert (again.t400.text(), again.t200.text(), again.pool.currentData()) == ("6:40.5", "3:10", 50)
    assert again.day.date() == QDate(day.year, day.month, day.day) and again.notes.text() == "Utomhusbassäng"
    again.t400.setText("6:30")
    again.save()
    assert again.saved.id == s.id and len(services.css_tests(store, erik.id)) == 1
    assert store.session(s.id).result["css_s100"] == 100.0


def test_the_history_lists_the_test_and_opens_its_form(demo_window, erik, monkeypatch):
    win = demo_window
    opened = []
    monkeypatch.setattr(CssTestDialog, "exec", lambda self: opened.append(self) or 0)
    win.go("history")
    h = win.history
    h.select_athlete(erik.id)
    assert h.css_btn.isEnabled() and h.css_btn.text() == "Simtest (CSS)"
    h.css_btn.click()
    assert len(opened) == 1 and opened[0].athlete.id == erik.id and opened[0].session is None
    d = opened[0]
    d.t400.setText("6:40")
    d.t200.setText("3:10")
    d.save()                                                      # sessions_changed reloads the table
    row = next(r for r, s in enumerate(h.sessions) if s.kind == TestKind.SWIM_CSS)
    cells = [h.table.item(row, c).text() for c in range(4)]
    assert cells[1:] == ["Simning · CSS-test", "CSS-test", "CSS 1:45 /100 m"]
    assert h.kind.findData("swim_css") > 0
    h.table.selectRow(row)
    assert h.open_btn.isEnabled()
    h.open_btn.click()                                            # no result page: the form, with the session
    assert len(opened) == 2 and opened[1].session.id == d.saved.id and win.stack.currentWidget() is not win.results
    win.open_session(d.saved.id)                                  # the same from the start page
    assert len(opened) == 3 and win.stack.currentWidget() is not win.results
    # a manual test has no curve and no report: not compared and not collected
    h.table.selectAll()
    assert len(h._selected()) >= 2 and not h.cmp_btn.isEnabled() and not h.report_btn.isEnabled()


def test_the_plan_takes_swimmings_pace_from_the_newest_css_test(demo_window, erik):
    win, store = demo_window, demo_window.ctx.store
    assert services.sport_capacity(store, erik, "swim") is None   # no test: swimming on effort
    old = css.make(420.0, 200.0, date.today() - timedelta(days=60))
    new = css.make(400.0, 190.0, date.today() - timedelta(days=2))
    services.save_css_test(store, erik, new)
    services.save_css_test(store, erik, old)                      # written in later, but swum earlier
    cap = services.sport_capacity(store, erik, "swim")
    assert cap == css.capacity(new, "CSS-test") and cap.sport == SWIM and cap.css_s100 == 105.0
    assert cap.anchors is None and cap.tested == new.tested

    win.open_plan(erik)
    v = win.plan
    assert v.sport_rows["swim"].basis.text() == f"Tempot ur CSS-testet {new.tested.isoformat()}: 1:45 /100 m"
    v.level.setCurrentIndex(v.level.findData("recreational"))
    v.recent.setText("240")
    for d, m in ((0, "45"), (1, "75"), (3, "75"), (5, "60"), (6, "120")):
        v.minutes[d].setText(m)
    v.start.edit.setText(date.today().isoformat())
    v.mode.setCurrentIndex(v.mode.findData("weeks"))
    v.weeks.setText("12")
    v.sport_rows["swim"].check.setChecked(True)
    req = v.collect()
    assert req is not None and set(req.sports) == {RUNNING, SWIM}
    assert next(c for c in req.capacity if c.sport == SWIM) == cap
    plan = v._current_plan()
    swims = [w for w in plan.workouts if w.sport == SWIM]
    assert swims and plan.capacity_on(SWIM, swims[0].date) == cap
    tg = resolve.targets(plan.capacity_on(SWIM, swims[0].date), sport=SWIM)
    assert tg and all(z.sport == SWIM and (z.swim_low_s or z.swim_high_s) for z in tg.values())   # a pace per 100 m


@pytest.mark.parametrize("lang", ["sv", "en"])
def test_the_texts_of_the_form_exist_in_both_languages(lang):
    set_language(lang)
    try:
        keys = ["ui.sport.swim", "ui.testkind.swim_css", "ui.hist.css", "ui.hist.css_tip", "ui.css.name",
                "ui.css.title", "ui.css.intro", "ui.css.t400", "ui.css.t200", "ui.css.date", "ui.css.pool",
                "ui.css.notes", "ui.css.time_placeholder"]
        assert all(t(k) and t(k) != k for k in keys)
        assert t("ui.hist.css_pace", pace="1:45") == "CSS 1:45 /100 m" == t("ui.css.result", pace="1:45")
        assert "1:45" in t("ui.plan.sport_basis.css", pace="1:45", date="2026-11-16")
    finally:
        set_language("sv")
