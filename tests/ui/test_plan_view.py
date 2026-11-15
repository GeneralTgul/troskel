"""Träningsschema: the form, the health declaration before anything is handed out, saving per
athlete, and the retest that lays the rest of the plan on the new test without moving a workout."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from lopanalys.plan import RUNNING
from lopanalys.ui import services


@pytest.fixture
def erik(demo_window):
    return next(a for a in demo_window.ctx.store.athletes() if a.first_name == "Erik")


def _fill(v, *, start: date | None = None, goal: bool = True) -> None:
    v.level.setCurrentIndex(v.level.findData("recreational"))
    v.recent.setText("200")
    for d, m, c in ((0, "45", "18:00"), (1, "75", "18"), (3, "75", ""), (6, "120", "9.30")):
        v.minutes[d].setText(m)
        v.clocks[d].setText(c)
    start = start or date.today()
    v.start.edit.setText(start.isoformat())
    if goal:
        v.goal_name.setText("Vårloppet")
        v.goal_date.edit.setText((start + timedelta(weeks=14, days=5 - start.weekday())).isoformat())
        v.goal_km.setText("21,1")
        v.goal_target.setText("under 1:45")
    else:
        v.mode.setCurrentIndex(v.mode.findData("weeks"))
        v.weeks.setText("12")


def test_empty_form_with_the_newest_test_preselected(demo_window, erik):
    win = demo_window
    win.go("athletes")
    win.athletes.form.load(erik)
    win.athletes.form.plan_btn.click()
    v = win.plan
    assert win.stack.currentWidget() is v and v.athlete.id == erik.id
    newest = services.plan_tests(win.ctx.store, erik.id)[0]
    assert v.test.currentData() == newest.id and v.test.itemData(v.test.count() - 1) is None
    assert all(m.text() == "" for m in v.minutes) and v.level.currentData() == "" and v.goal_name.text() == ""
    assert not any(cb.isChecked() for cb in v.health.values())
    assert not v.dirty() and v.plan is None and not v.del_btn.isVisible() and v.save_btn.isVisible()
    assert not v.preview() and v.error.text() == "Välj nivå."      # nothing is guessed


def test_preview_save_and_open_again(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    r = v.add_milestone()
    r.name.setText("Premiärmilen")
    r.date.edit.setText((date.today() + timedelta(weeks=8)).isoformat())
    r.km.setText("10")
    assert v.dirty() and v.preview() and v.error.isHidden()
    plan = v.plan
    assert plan.goal.name == "Vårloppet" and [g.priority for g in plan.request.goals] == ["A", "B"]
    assert [(s.weekday, s.max_minutes, s.start and f"{s.start:%H:%M}") for s in plan.request.availability] == \
        [(0, 45, "18:00"), (1, 75, "18:00"), (3, 75, None), (6, 120, "09:30")]
    assert v.headline.text() == "Vårloppet" and v.shown.isVisible() and v.chart.plan is plan
    assert v.t_weeks.value == str(len(plan.weeks))
    v.goal_km.setText("10")                                     # the overview says when it is behind the form
    assert v.stale.isVisible()
    v.goal_km.setText("21,1")
    assert not v.stale.isVisible()
    assert v.save() and not v.dirty() and v.del_btn.isVisible()
    row = win.ctx.store.db.get_plan(erik.id)
    assert "Erik" not in str(row["request"]) and "Sandberg" not in str(row["plan"])   # the name lives in one place
    # open it again: the same plan, read back and not rebuilt
    win._plan_back()
    win.open_plan(erik)
    assert v.plan == plan and v.goal_name.text() == "Vårloppet" and len(v.milestones) == 1 and not v.dirty()
    assert v.minutes[6].text() == "120" and v.clocks[6].text() == "09:30" and v.minutes[2].text() == ""


def test_plan_without_goal_and_form_errors(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v, goal=False)
    v.minutes[1].setText("5")
    assert not v.preview() and v.error.text().startswith("Tisdag:")
    v.minutes[1].setText("75")
    v.clocks[1].setText("25")
    assert not v.preview() and "klockslaget" in v.error.text()
    v.clocks[1].setText("")
    v.weeks.setText("2")
    assert not v.preview() and "minst 4 veckor" in v.error.text()        # the engine's own message
    v.weeks.setText("12")
    assert v.preview() and len(v.plan.weeks) == 12 and v.plan.goal is None
    assert v.plan.weeks[-1].kind == "test"
    v.revert()                                   # leave nothing unsaved behind: the window asks before closing


def test_nothing_is_handed_out_before_the_health_declaration(demo_window, erik, tmp_path, monkeypatch):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    made: list[str] = []
    monkeypatch.setattr(services, "html_to_pdf", lambda html, path: (made.append("pdf"), path.write_bytes(b"%PDF"))[0])
    monkeypatch.setattr(services, "print_report", lambda html, name: made.append("print"))
    from lopanalys.ui.views import settings
    monkeypatch.setattr(settings, "pick_printer", lambda *a, **k: "")
    paths = iter([tmp_path / "schema.pdf", tmp_path / "schema.ics"])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(next(paths)), "")))
    assert not v.save_pdf() and not v.print_plan() and not v.save_ics()
    assert made == [] and "hälsodeklarationen" in v.error.text() and win.ctx.store.db.get_plan(erik.id) is None
    v.health["asked"].setChecked(True)
    v.health["cleared"].setChecked(True)
    assert not v.save_pdf() and made == []                               # two of three is not enough
    v.health["advice"].setChecked(True)
    assert v.save_pdf() and v.save_ics() and v.print_plan() and made == ["pdf", "print"]
    ics = (tmp_path / "schema.ics").read_bytes()
    assert ics.startswith(b"BEGIN:VCALENDAR") and b"UID:p%d-" % erik.id in ics
    # handing out saved the plan with the declaration and today's date, and the files are remembered for erasure
    stored = services.load_plan(win.ctx.store, erik)
    assert stored.plan == v.plan and stored.health["date"] == date.today().isoformat()
    assert services.health_valid(stored.health) and not v.dirty()
    assert {p["path"] for p in win.ctx.store.db.saved_files(erik.id)} >= {str(tmp_path / "schema.pdf"),
                                                                          str(tmp_path / "schema.ics")}
    # a declaration older than 12 months has to be gone through again
    old = dict(stored.health, date=(date.today() - timedelta(days=400)).isoformat())
    services.save_plan(win.ctx.store, erik, stored.plan, stored.session_id, old)
    win._plan_back()
    win.open_plan(erik)
    assert not any(cb.isChecked() for cb in v.health.values()) and "äldre än 12 månader" in v.health_note.text()


def test_unsaved_changes_are_asked_about(demo_window, erik, monkeypatch):
    win = demo_window
    win.go("athletes")
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    asked: list[str] = []
    answer = [QMessageBox.StandardButton.Cancel]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or answer[0]))
    win.nav_buttons["settings"].click()
    assert asked and win.stack.currentWidget() is v and win.nav_buttons["athletes"].isChecked()
    assert v.level.currentData() == "recreational"                       # nothing was thrown away
    v.back.emit()
    assert win.stack.currentWidget() is v
    answer[0] = QMessageBox.StandardButton.Ok
    v.back.emit()
    assert win.stack.currentWidget() is win.athletes and not v.dirty() and v.level.currentData() == ""


def test_delete_plan_and_erasing_the_athlete(demo_window, erik):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    _fill(v)
    assert v.save()
    v.delete()
    assert win.ctx.store.db.get_plan(erik.id) is None and v.plan is None and v.level.currentData() == ""
    _fill(v)
    assert v.save()
    win.athletes.erase(erik.id)
    assert v.athlete is None and win.ctx.store.db._q("SELECT COUNT(*) FROM plans")[0][0] == 0


def test_retest_lays_the_rest_of_the_plan_on_the_new_test(demo_window, erik):
    win = demo_window
    store = win.ctx.store
    newer, older = services.plan_tests(store, erik.id)[:2]
    win.open_plan(erik, older.id)
    v = win.plan
    assert v.test.currentData() == older.id
    start = date.today() - timedelta(weeks=3)
    _fill(v, start=start)
    assert v.save()
    before = services.load_plan(store, erik).plan
    old_cap = before.request.capacity_for(RUNNING)
    assert old_cap.tested == older.started.astimezone().date()
    # the plan page offers it ...
    assert v.banner.isVisible() and newer.protocol_name in v.banner_text.text()
    # ... and so does the result of the newer test, but not the result the plan is built on
    win.open_session(older.id)
    assert win.results.plan_card.isVisible() and not win.results.retest_btn.isVisible()
    win.open_session(newer.id)
    assert win.results.retest_btn.isVisible() and erik.first_name in win.results.plan_text.text()
    win.results.retest_btn.click()
    stored = services.load_plan(store, erik)
    after = stored.plan
    assert stored.session_id == newer.id and not win.results.retest_btn.isVisible()
    # no workout moved, backwards or forwards: the same keys on the same days with the same parts
    assert [(w.key, w.date, w.parts) for w in after.workouts] == [(w.key, w.date, w.parts) for w in before.workouts]
    # the new targets apply from today; earlier workouts keep the test they were handed out with
    new_cap = after.request.capacity_for(RUNNING)
    assert new_cap.tested == newer.started.astimezone().date() and new_cap != old_cap
    assert after.capacity_on(RUNNING, date.today() - timedelta(days=1)) == old_cap
    assert after.capacity_on(RUNNING, date.today()) == new_cap
    events = store.db.plan_events(stored.row_id)
    assert [e["kind"] for e in events] == ["saved", "retest"]
    assert events[-1]["detail"]["to_session"] == newer.id and events[-1]["detail"]["from_date"] == date.today().isoformat()
    # the plan page shows the plan on the new test and offers nothing more
    win.results.plan_btn.click()
    assert win.stack.currentWidget() is v and v.test.currentData() == newer.id and not v.banner.isVisible()
    assert not v.dirty() and v.plan == after and "Lagt om" in v.notes.text()


def test_gait_test_has_no_plan_card(demo_window):
    win = demo_window
    store = win.ctx.store
    for a in store.athletes():
        for s in store.history(a.id):
            if s.kind not in services.PLAN_TEST_KINDS:
                win.open_session(s.id)
                assert not win.results.plan_card.isVisible()
                return
    pytest.skip("the demo data has no gait test")
