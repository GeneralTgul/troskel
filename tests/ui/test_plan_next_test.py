"""*Nästa test* on the plan page (#778, step 4): the coach chooses the day of the athlete's next test, the overview
says it and what the targets build on, and the day stays through *Spara*, a plan read in from the phone and a new
test (which moves it as far after the new test)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtWidgets import QMessageBox

from lopanalys.core.models import TestKind
from lopanalys.plan import link, next_test, phone
from lopanalys.ui import services


@pytest.fixture
def quiet(monkeypatch):
    for name in ("question", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


@pytest.fixture
def erik(demo_window):
    return next(a for a in demo_window.ctx.store.athletes() if a.first_name == "Erik")


def _plan(win, erik):
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


def test_the_coach_chooses_the_next_test(demo_window, erik, quiet):
    win, store = demo_window, demo_window.ctx.store
    stored = _plan(win, erik)
    v = win.plan
    auto = next_test.default_next_test(stored.plan)
    assert stored.plan.next_test is None and v.next_test.text() == ""
    assert v.next_test.edit.placeholderText() == f"Tomt: {auto.isoformat()}"
    tested = next_test.basis(stored.plan)[1]
    assert v.nextline.text() == f"Nästa test {auto.isoformat()}. Passens mål bygger på testet från {tested.isoformat()}."
    day = auto + timedelta(weeks=2)
    v.next_test.set_date(day)
    assert v.dirty() and not v.stale.isVisible()          # the day never rebuilds the plan
    assert day.isoformat() in v.nextline.text()
    assert v.save()
    saved = services.load_plan(store, erik)
    assert saved.plan.next_test == day and not v.dirty()
    assert [w.key for w in saved.plan.workouts] == [w.key for w in stored.plan.workouts]
    url, _ = services.plan_app(saved.plan)
    assert link.expand(link.read_link(url))["plan"]["next_test"] == day.isoformat()
    win.open_plan(erik)
    assert v.next_test.text() == day.isoformat()
    v.next_test.edit.setText("not a day")
    assert not v.save() and "inget datum" in v.error.text()
    v.next_test.edit.setText("")
    assert v.save() and services.load_plan(store, erik).plan.next_test is None


def test_the_day_stays_through_the_phone_and_moves_with_a_new_test(demo_window, erik, quiet):
    win, store = demo_window, demo_window.ctx.store
    stored = _plan(win, erik)
    v = win.plan
    tested = next_test.basis(stored.plan)[1]
    day = next_test.anchor(stored.plan) + timedelta(weeks=9)
    v.next_test.set_date(day)
    assert v.save()
    sent = link.expand(link.read_link(link.plan_link(link.link_payload(
        link.stored_plan(services.load_plan(store, erik).plan, erik.name)))))
    assert sent["plan"]["next_test"] == day.isoformat()
    sent["plan"]["next_test"] = (day + timedelta(days=3)).isoformat()   # an older day on the phone: Tröskel's wins
    athlete = {"kind": phone.KIND, "format": phone.FORMAT, "exported": date.today().isoformat(), "plan": sent,
               "marks": {}, "extras": []}
    assert v.read_from_phone(phone.athlete_link(athlete))
    assert services.load_plan(store, erik).plan.next_test == day
    src = next(s for s in services.plan_tests(store, erik.id) if s.kind == TestKind.LACTATE_STEP)
    when = datetime.now().astimezone() - timedelta(hours=2)
    newer = store.save_session(replace(src, id=None, started=when, ended=when + timedelta(minutes=50)))
    store.save_records(newer.id, store.records(src.id))
    after = services.apply_retest(store, erik, newer).plan
    new_tested = next_test.basis(after)[1]
    assert new_tested != tested and after.next_test == next_test.anchor(after) + timedelta(weeks=9)
