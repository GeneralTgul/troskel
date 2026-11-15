"""Data protection in the program: neutral file names, erasure, the retention of films,
consent, the extract, the lock and the customer mode."""
from __future__ import annotations

import re
from pathlib import Path

from lopanalys.assessments import library
from lopanalys.core import paths
from lopanalys.core.models import Athlete
from lopanalys.ui import demo, services
from lopanalys.ui.context import AppContext
from lopanalys.ui.context import TestPlan as Plan
from lopanalys.ui.live_controller import LiveController

SCALE = 40.0
CONSENT = {"date": "2026-11-14", "test": True, "film": True, "store": True, "contact": False}


def _ctx() -> AppContext:
    ctx = AppContext(demo=True, time_scale=SCALE)
    ctx.devices.connect_treadmill("sim")
    ctx.devices.connect_heart_rate("sim")
    return ctx


def _gait_plan(a: Athlete) -> Plan:
    return Plan(a, "gait", library.gait_check([10.0, 12.0], stage_s=60), demo.demo_source_info("Demo"),
                treadmill_mode="sim")


def test_film_is_named_after_the_test_not_the_person(qapp, qtbot):
    ctx = _ctx()
    a = ctx.store.save_athlete(Athlete(None, "Anna", "Svensson", height_cm=170, max_hr=190, consent=dict(CONSENT)))
    ctl = LiveController(ctx, _gait_plan(a))
    ctl.open_camera()
    assert ctl.start()
    film = Path(ctl.session.video_path)
    assert re.fullmatch(rf"session-{ctl.session.id}-\d{{8}}-\d{{6}}\.mp4", film.name)
    assert film.parent == paths.recordings_dir()
    qtbot.waitUntil(lambda: film.is_file(), timeout=10000)
    ctl.abort()
    assert "Svensson" not in " ".join(p.name for p in paths.data_dir().rglob("*"))
    assert ctx.store.db._q("SELECT video_path FROM sessions")[0][0] == f"recordings/{film.name}"
    ctx.shutdown()


def test_report_name_is_neutral_inside_the_data_folder(qapp, tmp_path):
    ctx = _ctx()
    a = ctx.store.save_athlete(Athlete(None, "Anna", "Svensson"))
    p = services.report_target(ctx.store, a, "test3_Laktattrappa 8–14 km/h_2026-11-14")
    assert p == paths.reports_dir() / f"rapport-{a.id}-test3_Laktattrappa_8–14_km-h_2026-11-14.pdf"
    services.register_report(ctx.store, p, a, None)
    assert services.report_target(ctx.store, a, "x").parent == paths.reports_dir()     # still inside: still neutral
    usb = tmp_path / "usb"
    usb.mkdir()
    services.register_report(ctx.store, usb / "r.pdf", a, None)                         # the coach saved elsewhere
    assert services.report_target(ctx.store, a, "x") == usb / "Anna_Svensson_x.pdf"
    assert [Path(f["path"]).name for f in ctx.store.db.saved_files(a.id)] == [p.name, "r.pdf"]
    ctx.shutdown()


# ------------------------------------------------------------------ erasure
def test_erase_from_the_athlete_page(demo_window, qtbot, tmp_path):
    win = demo_window
    st = win.ctx.store
    erik = next(a for a in st.athletes() if a.first_name == "Erik")
    sid = st.history(erik.id)[0].id
    report = tmp_path / "Erik_Sandberg.pdf"
    report.write_bytes(b"%PDF")
    services.register_report(st, report, erik, sid)
    win.open_session(sid)                               # the results page shows him, with a change waiting
    win.results.comment.setPlainText("sparas inte")
    assert win.results._autosave.isActive()
    win.go("athletes")
    assert win.athletes.erase(erik.id)
    assert st.athlete(erik.id) is None and st.session(sid) is None and not report.exists()
    assert win.results.session is None and not win.results._autosave.isActive()
    assert "Erik" not in [a.first_name for a in st.athletes()]
    text = win.athletes.last_erasure
    assert "Erik Sandberg är raderad: 2 tester och 1 filer togs bort." in text
    assert "Det här når programmet inte" in text and "minneskort" in text


def test_no_erasure_while_the_persons_test_runs(demo_window):
    win = demo_window
    anna = next(a for a in win.ctx.store.athletes() if a.first_name == "Anna")
    ctl = LiveController(win.ctx, Plan(anna, "lactate", library.lactate_step(10.0, 1.0, 2, stage_s=40), None,
                                       treadmill_mode="sim"))
    win.ctx.live = ctl
    assert not win.athletes.erase(anna.id)
    assert win.ctx.store.athlete(anna.id) is not None
    win.ctx.live = None


# ------------------------------------------------------------------ retention of films
def test_film_setting_default_and_start_purge(qapp, qtbot):
    from datetime import datetime, timedelta, timezone

    from lopanalys.core.models import Session
    from lopanalys.core.models import TestKind as Kind
    from lopanalys.ui.app import build
    ctx = _ctx()
    assert ctx.setting("privacy.video_retention") == "30d"
    a = ctx.store.save_athlete(Athlete(None, "Anna", "Svensson"))
    film = paths.recordings_dir() / "session-1-20260901-100000.mp4"
    film.write_bytes(b"v")
    old = datetime.now(timezone.utc) - timedelta(days=31)
    s = ctx.store.save_session(Session(None, a.id, Kind.GAIT, old, old, video_path=str(film)))
    ctx.shutdown()
    _, win = build([], qapp)                                           # the program starts: the film is past 30 days
    assert not film.exists() and win.ctx.store.session(s.id).video_path is None
    win.settings.video_keep.setCurrentIndex(win.settings.video_keep.findData("keep"))
    assert win.ctx.setting("privacy.video_retention") == "keep"
    win.close()


def test_film_removed_right_after_the_analysis_when_chosen(qapp, qtbot, monkeypatch):
    from lopanalys.ui.views.results import ResultsView
    ctx = _ctx()
    ctx.set_setting("privacy.video_retention", "after_analysis")
    a = ctx.store.save_athlete(Athlete(None, "Anna", "Svensson", height_cm=170, max_hr=190, consent=dict(CONSENT)))
    ctl = LiveController(ctx, _gait_plan(a))
    ctl.open_camera()
    got = []
    ctl.finished.connect(got.append)
    assert ctl.start()
    film = Path(ctl.session.video_path)
    qtbot.waitUntil(lambda: ctl.state is not None and ctl.state.stage_elapsed_s > 20, timeout=20000)
    ctl.stop()
    qtbot.waitUntil(lambda: bool(got), timeout=20000)
    assert film.is_file() and film.with_name(film.name + ".json").is_file()
    monkeypatch.setattr(services, "analyze_video", lambda *a, **k: {"findings": [], "keyframes": [], "view": "side_left"})
    rv = ResultsView(ctx)
    rv.load_outcome(got[0])
    qtbot.waitUntil(lambda: rv.gait is not None and rv._analysis is None, timeout=20000)
    assert not film.exists() and not film.with_name(film.name + ".json").exists()
    assert rv.session.video_path is None and ctx.store.session(rv.session.id).video_path is None
    rv.flush()
    assert ctx.store.session(rv.session.id).result.get("video_removed")
    ctx.shutdown()


# ------------------------------------------------------------------ demo data apart
def test_demo_has_its_own_data_folder(qapp):
    from lopanalys.ui.app import build
    real = _ctx()
    real.store.save_athlete(Athlete(None, "Riktig", "Kund"))
    real.shutdown()
    home = paths.data_dir()
    _, win = build(["--demo"], qapp)
    assert paths.data_dir() == home / "demo" and (home / "demo" / "lopanalys.sqlite").is_file()
    names = [a.name for a in win.ctx.store.athletes()]
    assert "Riktig Kund" not in names and "Erik Sandberg" in names     # only the demo people
    win.close()
    _, win = build([], qapp)                                            # the real program again: no demo people
    assert paths.data_dir() == home
    assert [a.name for a in win.ctx.store.athletes()] == ["Riktig Kund"]
    win.close()


# ------------------------------------------------------------------ the extract
def test_export_everything_button(demo_window, tmp_path, monkeypatch):
    import zipfile

    from PySide6.QtWidgets import QFileDialog
    win = demo_window
    erik = next(a for a in win.ctx.store.athletes() if a.first_name == "Erik")
    dest = tmp_path / "utdrag.zip"
    asked = {}

    def pick(parent, title, start, flt):
        asked["start"] = start
        return str(dest), flt

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(pick))
    win.athletes.form.load(erik)
    assert win.athletes.form.extract_btn.isVisibleTo(win.athletes.form)
    win.athletes.form.extract_btn.click()
    assert Path(asked["start"]).name.startswith("Utdrag_Erik_Sandberg_")
    with zipfile.ZipFile(dest) as z:
        assert {"sammanstallning.html", "data.json"} <= set(z.namelist())
        assert "Erik Sandberg" in z.read("sammanstallning.html").decode("utf-8")
    win.athletes.form.load(None)
    assert not win.athletes.form.extract_btn.isVisibleTo(win.athletes.form)       # nothing to export for a new athlete


# ------------------------------------------------------------------ consent
def test_consent_form_saves_date_and_boxes(demo_window):
    from datetime import date
    win = demo_window
    form = win.athletes.form
    win.athletes.form_new()
    form.first.setText("Nils")
    assert not form.withdraw_btn.isVisibleTo(form.actions)
    form.consent_boxes["test"].setChecked(True)
    assert form.consent_date.text() == date.today().isoformat()          # ticking a box dates the consent
    form.consent_date.edit.setText("2026-11-02")
    form.save_btn.click()
    nils = next(a for a in win.ctx.store.athletes() if a.first_name == "Nils")
    assert nils.consent == {"test": True, "film": False, "store": False, "contact": False, "date": "2026-11-02"}
    assert nils.consents("test") and not nils.consents("film") and nils.consent_registered
    assert form.withdraw_btn.isVisibleTo(form.actions) and not form.dirty()
    form.consent_date.edit.setText("i går")
    form.save_btn.click()
    assert "datum" in form.error.text()
    form.revert()
    form.withdraw_btn.click()                                             # the question is answered Yes: erased
    assert "Nils" not in [a.first_name for a in win.ctx.store.athletes()]
    assert "Nils är raderad" in win.athletes.last_erasure


def test_without_film_consent_nothing_is_recorded(qapp, qtbot):
    ctx = _ctx()
    a = ctx.store.save_athlete(Athlete(None, "Anna", "Svensson", height_cm=170, max_hr=190,
                                       consent=dict(CONSENT, film=False)))
    ctl = LiveController(ctx, _gait_plan(a))
    ctl.open_camera()
    assert ctl.start()
    qtbot.waitUntil(lambda: ctl.state is not None and ctl.state.stage_elapsed_s > 10, timeout=20000)
    assert ctl.session.video_path is None and ctl._recorder is None
    assert ctl.latest() is not None                                       # the live image is still analysed
    ctl.abort()
    s = ctx.store.session(ctl.session.id)
    assert s.video_path is None and s.result["no_film_consent"] is True
    assert list(paths.recordings_dir().iterdir()) == []
    ctx.shutdown()


def test_no_test_consent_asks_before_the_test(demo_window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    win = demo_window
    kund = win.ctx.store.save_athlete(Athlete(None, "Ny", "Kund", max_hr=185))
    win.ctx.athletes_changed.emit()
    plan = Plan(kund, "lactate", library.lactate_step(10.0, 1.0, 2, stage_s=40), None, treadmill_mode="sim")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No))
    win.launch_test(LiveController(win.ctx, plan))
    assert "inget registrerat samtycke" in asked[0]
    assert win.operator is None and win.ctx.live is None and win.stack.currentWidget() is win.athletes
    assert win.athletes.form.athlete.id == kund.id                        # the form to fill the consent in
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win.launch_test(LiveController(win.ctx, plan))                        # the coach chose to go on
    assert win.operator is not None and win.ctx.live is not None


def test_without_store_consent_everything_goes_after_the_report(demo_window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    win = demo_window
    st = win.ctx.store
    erik = next(a for a in st.athletes() if a.first_name == "Erik")
    sid = st.history(erik.id)[0].id
    pdf = tmp_path / "usb" / "Erik.pdf"
    pdf.parent.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(pdf), "")))
    monkeypatch.setattr(services, "html_to_pdf", lambda html, path: Path(path).write_bytes(b"%PDF"))
    win.open_session(sid)
    win.results.save_pdf()                                                # consent to keep: nothing happens
    assert st.athlete(erik.id) is not None
    erik.consent = dict(erik.consent, store=False)
    st.save_athlete(erik)
    win.open_session(sid)
    win.results.save_pdf()                                                # the question is answered Yes
    assert st.athlete(erik.id) is None and st.session(sid) is None
    assert pdf.is_file()                                                  # the report just handed over stays
    assert "Erik.pdf" in win.athletes.last_erasure and win.stack.currentWidget() is win.athletes
    # an athlete with no consent registered at all is never erased this way
    anna = next(a for a in st.athletes() if a.first_name == "Anna")
    anna.consent = {}
    st.save_athlete(anna)
    win.results.athlete = st.athlete(anna.id)
    win.results._after_report([])
    assert st.athlete(anna.id) is not None


# ------------------------------------------------------------------ backup, restore, export, move
def test_backup_restore_and_export_from_settings(demo_window, tmp_path):
    from lopanalys.storage import privacy
    win = demo_window
    st = win.ctx.store
    assert win.settings.backup(str(tmp_path / "kopia"), "ett långt lösenord")
    dest = tmp_path / "kopia.lopbak"
    assert privacy.is_encrypted(dest) and b"Sandberg" not in dest.read_bytes()
    erik = next(a for a in st.athletes() if a.first_name == "Erik")
    assert win.athletes.erase(erik.id)                                  # erased after the backup was made
    assert "kopia.lopbak" in win.athletes.last_erasure                  # ... and the backup is listed as out of reach
    st.save_athlete(Athlete(None, "Efter", "Kopian"))
    assert win.settings.restore(str(dest), "ett långt lösenord")
    names = [a.name for a in win.ctx.store.athletes()]
    assert "Efter Kopian" not in names and "Anna Lindqvist" in names    # the backup's state ...
    assert "Erik Sandberg" not in names                                 # ... without the person erased since
    assert not win.settings.restore(str(dest), "fel lösenord här")
    assert "Anna Lindqvist" in [a.name for a in win.ctx.store.athletes()]   # a failed restore changes nothing

    anna = next(a for a in win.ctx.store.athletes() if a.first_name == "Anna")
    csv_all, csv_one = tmp_path / "alla.csv", tmp_path / "anna.csv"
    assert win.settings.export_csv(str(csv_all)) and win.settings.export_csv(str(csv_one), anna.id)
    assert csv_one.read_text(encoding="utf-8-sig").count("\n") == 1     # the header only: Anna has no tests
    assert [f["kind"] for f in win.ctx.store.db.saved_files(anna.id)] == ["export"]
    assert [f["kind"] for f in win.ctx.store.db.saved_files(None)].count("export") == 1


def test_backup_hint_does_not_suggest_the_cloud():
    from lopanalys.core.i18n import t
    hint = t("ui.set.backup_hint")
    assert "moln" not in hint and "krypterad" in hint


def test_move_folder_from_settings(qapp, demo_window, tmp_path, monkeypatch):
    from lopanalys.storage import privacy
    from lopanalys.ui.app import build
    from lopanalys.ui.views import settings
    assert not demo_window.settings._move_folder(str(tmp_path / "demo-flytt"))   # not from the demo
    demo_window.close()
    _, win = build([], qapp)
    win.ctx.store.save_athlete(Athlete(None, "Riktig", "Kund"))
    marked = []
    monkeypatch.setattr(settings, "mark_moved", lambda old, new: marked.append((old, new)))
    old = paths.data_dir()
    dest = tmp_path / "flyttad"
    win.ctx.live = object()
    assert not win.settings._move_folder(str(dest)) and not dest.exists()     # never while a test runs
    win.ctx.live = None
    assert win.settings._move_folder(str(dest))
    assert marked == [(str(paths.real_data_dir()), str(dest))]
    assert (dest / "lopanalys.sqlite").is_file() and (dest / "demo" / "lopanalys.sqlite").is_file()
    win.close()
    assert privacy.finish_move(old, dest) > 0 and not (old / "lopanalys.sqlite").exists()


# ------------------------------------------------------------------ lock, customer mode, the TV
def test_pin_locks_at_start_and_after_idle(qapp, qtbot):
    from lopanalys.ui.app import build
    from lopanalys.ui.lock import check_pin
    _, win = build([], qapp)
    win.show()
    assert not win.locked() and not win.settings.lock_now.isVisibleTo(win.settings)
    win.lock()
    assert not win.locked()                                     # no PIN: nothing to open a lock with
    assert not win.settings.set_pin("12")                       # too short
    assert win.settings.set_pin("2468")
    stored = win.ctx.setting("lock.pin")
    assert "2468" not in str(stored) and check_pin("2468", stored) and not check_pin("1357", stored)
    win.settings.lock_now.click()
    assert win.locked() and not win.stack.isVisibleTo(win)      # nothing of the program shows
    win.lock_screen.pin.setText("0000")
    win.lock_screen.try_unlock()
    assert win.locked() and win.lock_screen.error.text() == "Fel PIN-kod."
    win.lock_screen.pin.setText("2468")
    win.lock_screen.try_unlock()
    assert not win.locked()
    win.idle.set_minutes(0.002)                                 # "lock when idle", shortened
    qtbot.waitUntil(win.locked, timeout=3000)
    win.unlock()
    win.ctx.live = object()                                     # a test is running: never locked by the timer
    win._idle()
    assert not win.locked()
    win.ctx.live = None
    win.close()
    _, win2 = build([], qapp)                                   # the program opens locked
    assert win2.locked()
    win2.close()
    _, win3 = build(["--reset-pin"], qapp)                      # the way out of a forgotten PIN
    assert not win3.locked() and win3.ctx.setting("lock.pin") is None
    win3.close()


def test_customer_mode_hides_lists_during_a_test_and_tv_shows_first_name(demo_window, qtbot):
    win = demo_window
    anna = next(a for a in win.ctx.store.athletes() if a.first_name == "Anna")
    plan = Plan(anna, "lactate", library.lactate_step(10.0, 1.0, 2, stage_s=40), None, treadmill_mode="sim")
    win.launch_test(LiveController(win.ctx, plan))
    side = win.nav_buttons["settings"].parentWidget()
    for key in ("athletes", "history", "new_test"):
        assert not win.nav_buttons[key].isVisibleTo(side), key
    assert win.nav_buttons["settings"].isVisibleTo(side) and win.live_btn.isVisibleTo(side)
    assert win.operator.tv.board.header.athlete == "Anna"       # the TV: first name only
    win.test_finished(None)
    for key in ("athletes", "history", "new_test"):
        assert win.nav_buttons[key].isVisibleTo(side), key
    win.settings.customer_mode.setChecked(False)                # the coach turned customer mode off
    win.launch_test(LiveController(win.ctx, plan))
    assert win.nav_buttons["athletes"].isVisibleTo(side)
    win.test_finished(None)


# ------------------------------------------------------------------ the 24-month reminder
def test_reminder_on_the_athlete_page(demo_window):
    from datetime import datetime, timedelta, timezone

    from lopanalys.core.models import Session
    from lopanalys.core.models import TestKind as Kind
    win = demo_window
    assert not win.athletes.remind.isVisibleTo(win.athletes) and win.nav_buttons["athletes"].text() == "Idrottare"
    old = win.ctx.store.save_athlete(Athlete(None, "Gunnar", "Gammal", consent=dict(CONSENT)))
    when = datetime.now(timezone.utc) - timedelta(days=800)
    win.ctx.store.save_session(Session(None, old.id, Kind.LACTATE_STEP, when, when))
    win.go("athletes")
    win.ctx.athletes_changed.emit()
    assert win.athletes.remind.isVisibleTo(win.athletes)
    assert win.athletes.remind_text.text().startswith("1 idrottare behöver gås igenom")
    assert win.nav_buttons["athletes"].text() == "Idrottare (1)"
    win.athletes.remind_btn.click()                                           # Visa: opens the person
    form = win.athletes.form
    assert form.athlete.id == old.id and form.reminder.isVisibleTo(form)
    assert "mer än 24 månader sedan" in form.reminder.text() and when.astimezone().strftime("%Y-%m-%d") in form.reminder.text()
    assert win.ctx.store.athlete(old.id) is not None                          # nothing is erased by itself
    assert win.athletes.erase(old.id)
    assert not win.athletes.remind.isVisibleTo(win.athletes) and win.nav_buttons["athletes"].text() == "Idrottare"
