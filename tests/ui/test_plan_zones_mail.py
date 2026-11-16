"""Träningsschema on the test Puls- och fartzoner (max HR from the ramp, pace from heart rate, and a
test stopped before the ramp that gives no max HR), and *Skicka till idrottaren* on the plan page:
the printout and the calendar file by mail, after the health declaration and with the plan saved.

The mail goes to a pretend SMTP server on 127.0.0.1 (tests/share/fake_smtp.py): nothing leaves the machine."""
from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from lopanalys.assessments import library
from lopanalys.core.models import Athlete, Session, TestKind
from lopanalys.plan import RUNNING, link
from lopanalys.plan.resolve import targets
from lopanalys.share import mail
from lopanalys.ui import services
from lopanalys.ui.views import send_mail


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HERE = Path(__file__).parent
fake_smtp = _load("fake_smtp", HERE.parent / "share" / "fake_smtp.py")
zones_ui = _load("_zones_ui", HERE / "test_hr_zones_ui.py")       # the simulated zones test
plan_ui = _load("_plan_ui", HERE / "test_plan_view.py")           # the filled-in plan form

ADDRESS = "olle.lopare@example.test"
PASSWORD = "abcd efgh ijkl mnop"


# ------------------------------------------------------------------ the plan on a zones test
def _stopped_test(ctx, a, *, exhaustion: bool) -> Session:
    """A zones test stopped after four steady steps, before the ramp: with the stop pressed or without."""
    p = library.hr_zones(8.0)
    full = zones_ui._run(p, ramp_stages=1)
    end = 10 + 600 + 4 * 240.0
    res = {"plan_kind": "hr_zones"}
    if exhaustion:
        res["exhaustion_t_s"] = end
    s = Session(None, a.id, TestKind.HR_ZONES, zones_ui.T0, zones_ui.T0 + timedelta(seconds=end + 65),
                protocol_name=p.name, protocol=p.to_dict(), result=res)
    ctx.store.db.save_test(s, [r for r in full.records if r.index <= 4], [x for x in full.samples if x.t_s <= end],
                           warmup_records=full.warmup_records)
    return s


def test_a_plan_is_built_on_a_zones_test(demo_window):
    win = demo_window
    store = win.ctx.store
    a, s = zones_ui._saved_test(win.ctx)
    assert a.max_hr is None and services.plan_test_ok(s)
    assert [x.id for x in services.plan_tests(store, a.id)] == [s.id]
    cap = services.plan_capacity(store, a, s)
    # the max HR is the ramp's peak (the highest 5 s mean), as on the result page
    peak = services.analyze_max_hr(store.samples(s.id), store.records(s.id), s.result["exhaustion_t_s"]).peak
    assert peak and cap.anchors.max_hr == peak and cap.source == s.protocol_name
    assert cap.speed_line is not None and cap.speed_from_hr
    zt = targets(cap)
    assert all(tg.speed_from_hr for tg in zt.values() if tg.pace_text) and "min/km" in zt["Z3"].pace_text
    # the paces are the result page's: the zones from max HR, each border through the line
    warm = {i for i, st in enumerate(s.protocol["stages"]) if st.get("is_warmup")}
    rel = services.analyze_hr_speed([r for r in store.records(s.id) if r.index not in warm], store.samples(s.id),
                                    s.protocol, s.result["exhaustion_t_s"])
    page = {z["code"]: z for z in services.with_zone_speeds(services.zones(None, peak, a.resting_hr), rel)}
    assert (zt["Z3"].speed_low_kmh, zt["Z3"].speed_high_kmh) == (page["Z3"]["speed_low"], page["Z3"]["speed_high"])
    # on the page: preselected, and the zones show the pace
    win.open_plan(a)
    v = win.plan
    assert v.test.currentData() == s.id
    plan_ui._fill(v, goal=False)
    assert v.preview() and v.plan.request.capacity_for(RUNNING).speed_line == cap.speed_line
    assert "plan.note.speed_from_hr" in [n.key for n in v.plan.notes] and "zontestet" in v.notes.text()
    assert v.save()
    stored = services.load_plan(store, a)
    assert stored.plan.request.capacity_for(RUNNING) == cap               # read back with its line
    assert "fart ur puls" in services.render_plan(stored.plan, win.ctx.branding())


@pytest.mark.parametrize("exhaustion", [True, False], ids=["stopped_in_the_steps", "no_exhaustion"])
def test_a_zones_test_stopped_before_the_ramp_gives_no_max_hr(demo_window, exhaustion):
    store = demo_window.ctx.store
    a = store.save_athlete(Athlete(None, "Li", "Al", height_cm=170))
    s = _stopped_test(demo_window.ctx, a, exhaustion=exhaustion)
    cap = services.plan_capacity(store, a, s)
    assert cap.anchors is None and targets(cap) == {}        # the highest heart rate of the steps is not a max
    # with a saved max HR the zones come from it, and the steps still give the pace
    a.max_hr = 186
    cap = services.plan_capacity(store, store.save_athlete(a), s)
    assert cap.anchors.max_hr == 186 and cap.speed_line is not None
    assert any(tg.pace_text for tg in targets(cap).values())


# ------------------------------------------------------------------ the plan by mail
@pytest.fixture
def smtp(tmp_path, monkeypatch):
    with fake_smtp.FakeSMTP(tmp_path, password=PASSWORD) as srv:
        ctx = srv.client_context()
        monkeypatch.setattr(mail.ssl, "create_default_context", lambda *a, **k: ctx)   # trust the pretend server
        yield srv


@pytest.fixture
def no_pdf(monkeypatch):
    """The printout as a small stand-in file: Chromium is not what these tests are about."""
    made = []
    monkeypatch.setattr(services, "html_to_pdf", lambda html, path: (Path(path).write_bytes(b"%PDF-1.7 schema"),
                                                                    made.append(Path(path)))[0])
    return made


def _athlete(win, contact=True):
    st = win.ctx.store
    erik = next(a for a in st.athletes() if a.first_name == "Erik")
    erik.email = ADDRESS
    erik.consent = dict(erik.consent, contact=contact)
    return st.save_athlete(erik)


def _account(win, srv):
    for key, value in (("host", "127.0.0.1"), ("port", srv.port), ("security", "starttls"), ("user", srv.user)):
        win.ctx.set_setting(f"mail.{key}", value)
    services.set_mail_password(win.ctx.store, PASSWORD)
    win.ctx.set_setting("branding.coach", "Kim Coach")
    win.ctx.set_setting("branding.business", "Tröskel Test")


def _answers(monkeypatch, accept=True):
    shown, notices = [], []

    def run(self):
        shown.append(self)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(send_mail.SendDialog, "exec", run)
    for kind in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(
            lambda *a, _k=kind, **k: notices.append((_k, str(a[2]))) or QMessageBox.StandardButton.Ok))
    return shown, notices


def test_the_mail_button_is_grey_without_consent(demo_window, smtp):
    win = demo_window
    erik = _athlete(win, contact=False)
    _account(win, smtp)
    win.open_plan(erik)
    v = win.plan
    assert not v.mail_btn.isEnabled() and "Kontakt om uppföljning" in v.mail_note.text()
    _athlete(win)
    v._refresh_mail()
    assert v.mail_btn.isEnabled() and v.mail_note.text() == ""


def test_send_the_plan_after_the_health_declaration(demo_window, smtp, no_pdf, monkeypatch, qtbot):
    win = demo_window
    erik = _athlete(win)
    _account(win, smtp)
    shown, notices = _answers(monkeypatch)
    win.open_plan(erik)
    v = win.plan
    start = date.today() + timedelta(days=7 - date.today().weekday())       # next Monday
    plan_ui._fill(v, start=start, goal=False)
    # the health declaration first: nothing is asked, saved or sent without it
    assert not v.send_to_athlete()
    assert shown == [] and smtp.messages == [] and win.ctx.store.db.get_plan(erik.id) is None
    assert "hälsodeklaration" in v.error.text().lower()
    for cb in v.health.values():
        cb.setChecked(True)
    assert v.send_to_athlete()
    # saved first: what is mailed is what is in the program
    stored = services.load_plan(win.ctx.store, erik)
    assert stored is not None and stored.plan == v.plan and services.health_valid(stored.health)
    (dlg,) = shown
    day = v.plan.start.isoformat()
    assert dlg.address.text() == ADDRESS
    assert all(f"traningsschema-{day}.{ext}" in dlg.attachments.text() for ext in ("pdf", "ics", "troskel"))
    assert "Fil till appen" in dlg.attachments.text()
    assert "schemat är inte lösenordsskyddat" in " ".join(w.text() for w in dlg.findChildren(type(dlg.address)))
    qtbot.waitUntil(lambda: v._mailing is None, timeout=10000)
    (msg,) = smtp.messages
    assert msg["To"] == ADDRESS and msg["Subject"] == "Ditt träningsschema från Tröskel Test"
    body = msg.get_body(preferencelist=("plain",)).get_content().replace("\r\n", "\n")
    assert body.startswith("Hej Erik!") and f"{len(v.plan.weeks)} veckor från {day}" in body
    assert "iPhone" in body and "Android" in body and "Kim Coach\nTröskel Test" in body
    # the app's link on a line of its own, and the .troskel file beside: both the plan that was saved
    (line,) = [x for x in body.splitlines() if x.startswith("Öppna schemat i appen: ")]
    app_link = line.removeprefix("Öppna schemat i appen: ")
    assert app_link.startswith(link.APP_URL + "#p=") and link.read_link(app_link) == link.stored_plan(stored.plan)
    files = {p.get_filename(): p for p in msg.iter_attachments()}
    assert set(files) == {f"traningsschema-{day}.pdf", f"traningsschema-{day}.ics", f"traningsschema-{day}.troskel"}
    assert json.loads(files[f"traningsschema-{day}.troskel"].get_content()) == link.read_link(app_link)
    assert files[f"traningsschema-{day}.pdf"].get_content() == b"%PDF-1.7 schema"
    ics = files[f"traningsschema-{day}.ics"]
    assert ics.get_content_type() == "text/calendar"
    assert ics.get_content().replace("\r\n", "\n").startswith("BEGIN:VCALENDAR")
    assert ("information", "Mejlet är skickat.") in notices
    # the mailed PDF was temporary, and the plan's log says what went, never the address
    assert not no_pdf[0].exists() and win.ctx.store.db.saved_files(erik.id) == []
    events = win.ctx.store.db.plan_events(stored.row_id)
    assert events[-1]["kind"] == "mailed" and events[-1]["detail"] == {"what": ["plan", "ics", "app"]}
    assert ADDRESS not in str(events)
    assert v.mail_note.text().startswith("Skickat till idrottaren") and "schemat + kalenderfilen + appfilen" in v.mail_note.text()


def test_nothing_is_sent_when_the_coach_cancels(demo_window, smtp, no_pdf, monkeypatch):
    win = demo_window
    erik = _athlete(win)
    _account(win, smtp)
    shown, _ = _answers(monkeypatch, accept=False)
    win.open_plan(erik)
    v = win.plan
    plan_ui._fill(v, goal=False)
    for cb in v.health.values():
        cb.setChecked(True)
    assert not v.send_to_athlete()
    assert len(shown) == 1 and smtp.messages == [] and no_pdf == [] and v._mailing is None
    stored = services.load_plan(win.ctx.store, erik)
    assert [e["kind"] for e in win.ctx.store.db.plan_events(stored.row_id)] == ["saved"]


def test_plan_greeting_without_a_name_or_business():
    subject, body = mail.plan_greeting("", "2026-11-16", 12)
    assert subject == "Ditt träningsschema" and body.startswith("Hej!") and "12 veckor från 2026-11-16" in body
    assert "http" not in body and "www" not in body                       # no links: less like spam
    _, body = mail.plan_greeting("", "2026-11-16", 12, app_link="https://example.se/app/#p=abc")
    assert "\n\nÖppna schemat i appen: https://example.se/app/#p=abc\n\n" in body                # only the app's
