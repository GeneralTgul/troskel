"""FIT export and mail to the athlete in the program: the address on the athlete, the
buttons on Resultat and in Samlad rapport, the consent box "contact", the question before sending,
the note on the test, and the password that is never stored or logged as typed.

The mail goes to a pretend SMTP server on 127.0.0.1 (tests/share/fake_smtp.py): nothing leaves the machine."""
from __future__ import annotations

import importlib.util
import logging
import ssl
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from lopanalys.core import paths
from lopanalys.share import fit, mail
from lopanalys.ui import services
from lopanalys.ui.views import send_mail

_spec = importlib.util.spec_from_file_location("fake_smtp", Path(__file__).parents[1] / "share" / "fake_smtp.py")
fake_smtp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_smtp)

ADDRESS = "erik.unik-adress@example.test"
PASSWORD = "abcd efgh ijkl mnop"


@pytest.fixture
def smtp(tmp_path, monkeypatch):
    with fake_smtp.FakeSMTP(tmp_path, password=PASSWORD) as srv:
        ctx = srv.client_context()
        monkeypatch.setattr(mail.ssl, "create_default_context", lambda *a, **k: ctx)   # trust the pretend server
        yield srv


@pytest.fixture
def no_pdf(monkeypatch):
    """The report as a small stand-in file: Chromium is not what these tests are about."""
    made = []
    monkeypatch.setattr(services, "html_to_pdf", lambda html, path: (Path(path).write_bytes(b"%PDF-1.7 rapport"),
                                                                    made.append(Path(path)))[0])
    return made


def _erik(win, email=ADDRESS, contact=True):
    st = win.ctx.store
    erik = next(a for a in st.athletes() if a.first_name == "Erik")
    erik.email = email
    erik.consent = dict(erik.consent, contact=contact)
    st.save_athlete(erik)
    return erik, sorted(st.history(erik.id), key=lambda s: s.started)


def _account(win, srv):
    for key, value in (("host", "127.0.0.1"), ("port", srv.port), ("security", "starttls"), ("user", srv.user)):
        win.ctx.set_setting(f"mail.{key}", value)
    services.set_mail_password(win.ctx.store, PASSWORD)
    win.ctx.set_setting("branding.coach", "Kim Coach")
    win.ctx.set_setting("branding.business", "Tröskel Test")


def _answers(monkeypatch, accept=True):
    """Stub the question and the notices; returns what was shown: [dialog], [(kind, text)]."""
    shown, notices = [], []

    def run(self):
        shown.append(self)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(send_mail.SendDialog, "exec", run)
    for kind in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(
            lambda *a, _k=kind, **k: notices.append((_k, str(a[2]))) or QMessageBox.StandardButton.Ok))
    return shown, notices


# ------------------------------------------------------------------ the address on the athlete
def test_email_field_is_optional_and_validated(demo_window):
    win = demo_window
    win.go("athletes")
    f = win.athletes.form
    win.athletes.form_new()
    f.first.setText("Ny")
    assert f.collect().email == ""                                  # empty is fine
    for bad in ("ny@", "ny exempel.se", "Ny <ny@exempel.se>", "a@b.se, c@d.se"):
        f.email.setText(bad)
        assert f.collect() is None and "E-postadressen" in f.error.text()
    f.email.setText("  ny.lopare@exempel.se ")
    assert f.dirty()
    f._save()
    saved = next(a for a in win.ctx.store.athletes() if a.first_name == "Ny")
    assert saved.email == "ny.lopare@exempel.se"
    f.load(saved)
    assert f.email.text() == "ny.lopare@exempel.se" and not f.dirty()
    f.email.clear()
    f._save()
    assert win.ctx.store.athlete(saved.id).email == ""


# ------------------------------------------------------------------ the FIT file
def test_save_fit_file_from_the_result_page(demo_window, tmp_path):
    win = demo_window
    erik, tests = _erik(win)
    win.open_session(tests[-1].id)
    res = win.results
    assert res.fit_btn.isEnabled() and "Strava" in res.fit_btn.toolTip()
    target = services.fit_target(win.ctx.store, res.session)
    assert target.parent == paths.reports_dir() and target.name == fit.file_name(res.session)
    assert "Erik" not in target.name and "Sandberg" not in target.name and f"test-{res.session.id}-" in target.name
    dest = tmp_path / "usb" / target.name
    dest.parent.mkdir()
    assert res.save_fit(str(dest))
    m = fit.decode(dest.read_bytes())                                 # both checksums are verified here
    assert m["session"][0]["sport"] == 1 and m["session"][0]["sub_sport"] == 1
    samples = win.ctx.store.samples(res.session.id)
    assert len(m["record"]) > 0.9 * len(samples) and len(m["lap"]) >= len(res.records)
    assert max(r["heart_rate"] or 0 for r in m["record"]) == max(x.hr or 0 for x in samples)
    assert b"Erik" not in dest.read_bytes() and b"Sandberg" not in dest.read_bytes()
    # registered: erasing the person removes the file wherever it was saved
    assert [(f["kind"], f["path"]) for f in win.ctx.store.db.saved_files(erik.id, "fit")] == [("fit", str(dest))]
    win.go("athletes")
    assert win.athletes.erase(erik.id) and not dest.exists()


def test_a_test_without_time_series_has_a_grey_fit_button(demo_window):
    win = demo_window
    _, tests = _erik(win)
    sid = tests[-1].id
    win.ctx.store.save_samples(sid, [])
    win.open_session(sid)
    assert not win.results.fit_btn.isEnabled() and "tidsserie" in win.results.fit_btn.toolTip()
    with pytest.raises(fit.FitError):
        services.fit_file(win.ctx.store, win.results.session)


# ------------------------------------------------------------------ why the mail button is grey
def test_mail_needs_address_consent_and_account(demo_window, smtp):
    win = demo_window
    erik, tests = _erik(win, email="", contact=True)
    win.open_session(tests[-1].id)
    res = win.results
    assert not res.mail_btn.isEnabled() and "ingen e-postadress" in res.mail_btn.toolTip()
    assert "ingen e-postadress" in res.mail_note.text()                     # the reason is on the page too
    _erik(win, contact=False)
    res._refresh_share()
    assert not res.mail_btn.isEnabled() and "Kontakt om uppföljning" in res.mail_note.text()
    _erik(win)
    res._refresh_share()
    assert not res.mail_btn.isEnabled() and "Inställningar" in res.mail_note.text()     # no account yet
    _account(win, smtp)
    res._refresh_share()
    assert res.mail_btn.isEnabled() and res.mail_note.text() == "" and res.mail_btn.toolTip() == ""


def test_nothing_is_sent_without_consent_even_when_called(demo_window, smtp, no_pdf, monkeypatch):
    win = demo_window
    erik, tests = _erik(win, contact=False)
    _account(win, smtp)
    shown, notices = _answers(monkeypatch)
    win.open_session(tests[-1].id)
    win.results.send_to_athlete()
    assert shown == [] and smtp.messages == [] and no_pdf == []
    assert notices and "Kontakt om uppföljning" in notices[0][1]
    assert win.results._mailing is None


# ------------------------------------------------------------------ sending
def test_send_report_and_fit_file_from_the_result_page(demo_window, smtp, no_pdf, monkeypatch, qtbot, caplog):
    caplog.set_level(logging.INFO)
    win = demo_window
    erik, tests = _erik(win)
    _account(win, smtp)
    shown, notices = _answers(monkeypatch)
    sid = tests[-1].id
    win.open_session(sid)
    res = win.results
    res.comment.setPlainText("Fin utveckling")
    res.send_to_athlete()
    # the question showed the address and both attachments before anything was sent
    (dlg,) = shown
    assert dlg.address.text() == ADDRESS
    day = res.session.started.astimezone().strftime("%Y-%m-%d")
    assert f"testrapport-{day}.pdf" in dlg.attachments.text() and f"test-{sid}-{day}.fit" in dlg.attachments.text()
    assert "inte krypterad" in " ".join(w.text() for w in dlg.findChildren(type(dlg.address)))
    qtbot.waitUntil(lambda: res._mailing is None, timeout=10000)
    (msg,) = smtp.messages
    assert smtp.envelopes == [(smtp.user, [ADDRESS])] and msg["To"] == ADDRESS
    assert msg["From"] == f"Kim Coach <{smtp.user}>" and msg["Subject"] == f"Din testrapport {day} · Tröskel Test"
    body = msg.get_body().get_content().replace("\r\n", "\n")
    assert body.startswith("Hej Erik!") and "Kim Coach\nTröskel Test" in body and "Strava" in body
    files = {p.get_filename(): p.get_content() for p in msg.iter_attachments()}
    assert set(files) == {f"testrapport-{day}.pdf", f"test-{sid}-{day}.fit"}
    assert files[f"testrapport-{day}.pdf"] == b"%PDF-1.7 rapport"
    assert fit.decode(files[f"test-{sid}-{day}.fit"])["session"][0]["sub_sport"] == 1
    assert not any("Sandberg" in n for n in files)
    assert ("information", "Mejlet är skickat.") in notices
    # the mailed PDF was a temporary file: nothing of it is left, and nothing new is registered
    assert not no_pdf[0].exists() and win.ctx.store.db.saved_files(erik.id) == []
    # noted on the test: time and content, and it stays when the page saves again
    assert "Skickat till idrottaren" in res.mail_note.text() and "rapporten + FIT-filen" in res.mail_note.text()
    res.save()
    saved = win.ctx.store.session(sid).result
    assert [m["what"] for m in saved["mailed"]] == [["report", "fit"]] and saved["coach_comment"] == "Fin utveckling"
    assert ADDRESS not in str(saved)
    # the log says which test, never to whom, and never the password
    assert f"tests [{sid}] mailed to the athlete (report, fit)" in caplog.text
    for word in (ADDRESS, "unik-adress", PASSWORD, "abcd efgh"):
        assert word not in caplog.text
    logs = "".join(p.read_text(encoding="utf-8", errors="replace") for p in paths.data_dir().rglob("*.log*"))
    assert ADDRESS not in logs and PASSWORD not in logs


def test_cancel_sends_nothing_and_the_message_can_be_edited(demo_window, smtp, no_pdf, monkeypatch, qtbot):
    win = demo_window
    _, tests = _erik(win)
    _account(win, smtp)
    shown, _ = _answers(monkeypatch, accept=False)
    win.open_session(tests[-1].id)
    win.results.send_to_athlete()
    assert len(shown) == 1 and smtp.messages == [] and no_pdf == [] and win.results._mailing is None
    assert "mailed" not in win.ctx.store.session(tests[-1].id).result

    def edited(self):
        self.body.setPlainText("Hej Erik, vi ses i vår.\n")
        self.subject.setText("Rapporten")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(send_mail.SendDialog, "exec", edited)
    win.results.send_to_athlete()
    qtbot.waitUntil(lambda: win.results._mailing is None, timeout=10000)
    assert smtp.messages[0]["Subject"] == "Rapporten"
    assert smtp.messages[0].get_body().get_content().replace("\r\n", "\n") == "Hej Erik, vi ses i vår.\n"


def test_a_failed_mail_says_why_and_notes_nothing(demo_window, smtp, no_pdf, monkeypatch, qtbot):
    win = demo_window
    _, tests = _erik(win)
    _account(win, smtp)
    services.set_mail_password(win.ctx.store, "fel-losenord")
    _, notices = _answers(monkeypatch)
    win.open_session(tests[-1].id)
    win.results.send_to_athlete()
    qtbot.waitUntil(lambda: win.results._mailing is None, timeout=10000)
    assert smtp.messages == [] and "mailed" not in win.ctx.store.session(tests[-1].id).result
    (kind, text), = notices
    assert kind == "warning" and "applösenord" in text and ADDRESS not in text
    assert win.results.mail_btn.isEnabled() and "Skickat" not in win.results.mail_note.text()


def test_collected_report_is_mailed_with_each_tests_fit_file(demo_window, smtp, no_pdf, monkeypatch, qtbot):
    from lopanalys.ui.views.history import CollectedReportDialog
    win = demo_window
    erik, tests = _erik(win)
    _account(win, smtp)
    shown, notices = _answers(monkeypatch)
    dlg = CollectedReportDialog(win.ctx, "<html><body>Samlad</body></html>", "samlad_rapport_x", win, athlete=erik,
                                sessions=tests)
    assert dlg.mail_btn.isEnabled() and dlg.mail_note.text() == ""
    dlg.send_to_athlete()
    qtbot.waitUntil(lambda: dlg._mailing is None, timeout=10000)
    (msg,) = smtp.messages
    names = sorted(p.get_filename() for p in msg.iter_attachments())
    assert len(names) == 1 + len(tests) and sum(n.endswith(".fit") for n in names) == len(tests)
    assert f"{len(tests)} tester" in msg.get_body().get_content()
    for s in tests:                                                   # noted on every test in the report
        assert win.ctx.store.session(s.id).result["mailed"][0]["what"] == ["report", "fit"]
    # without the consent box the button is grey and says why
    _erik(win, contact=False)
    blocked = CollectedReportDialog(win.ctx, "<html></html>", "x", win, athlete=erik, sessions=tests)
    assert not blocked.mail_btn.isEnabled() and "Kontakt om uppföljning" in blocked.mail_note.text()
    dlg.close()
    blocked.close()


# ------------------------------------------------------------------ Inställningar → E-post
def test_settings_defaults_suit_gmail(demo_window):
    v = demo_window.settings
    assert v.mail_host.text() == "smtp.gmail.com" and v.mail_port.text() == "587"
    assert v.mail_security.currentData() == "starttls" and v.mail_user.text() == ""
    assert v.mail_password.echoMode() == v.mail_password.EchoMode.Password and not v.mail_remove.isVisibleTo(v)
    v.mail_security.setCurrentIndex(v.mail_security.findData("ssl"))        # the usual port follows the choice
    assert v.mail_port.text() == "465" and demo_window.ctx.setting("mail.port") == 465
    v.mail_port.setText("2525")
    v.mail_port.editingFinished.emit()
    v.mail_security.setCurrentIndex(v.mail_security.findData("starttls"))   # a port of the coach's own stays
    assert demo_window.ctx.setting("mail.port") == 2525


def test_the_password_is_never_stored_as_typed(demo_window):
    win = demo_window
    v = win.settings
    v.mail_password.setText(PASSWORD)
    v.mail_password.editingFinished.emit()
    assert v.mail_password.text() == "" and "Sparat" in v.mail_password.placeholderText()   # not kept in the field
    stored = win.ctx.setting("mail.password")
    assert stored["scheme"] in ("keyfile", "dpapi") and PASSWORD not in str(stored)
    assert services.mail_config(win.ctx.store).password == PASSWORD
    win.ctx.store.db._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    blob = b"".join(p.read_bytes() for p in paths.data_dir().rglob("*") if p.is_file())
    for needle in (PASSWORD.encode(), b"abcd efgh", PASSWORD.encode("utf-16-le")):
        assert needle not in blob                                           # database, logs, everything
    # a backup carries the protected value, which another PC cannot read: there it is typed again
    # (only the key-file scheme can be made unreadable here; Windows DPAPI is bound to the account)
    if stored["scheme"] == "keyfile":
        (Path(paths.real_data_dir()).parent / "keys" / "secret.key").unlink()
        assert services.mail_config(win.ctx.store).password == ""
        v._refresh_mail()
        assert "Skriv det igen" in v.mail_password.placeholderText()
    v.set_mail_password("", remove=True)
    assert win.ctx.setting("mail.password") is None and not v.mail_remove.isVisibleTo(v)


def test_send_a_test_mail(demo_window, smtp, qtbot, monkeypatch):
    win = demo_window
    v = win.settings
    _, notices = _answers(monkeypatch)
    assert v.send_test_mail() is None and "inte inställd" in v.mail_state.text()      # nothing filled in yet
    v.mail_host.setText("127.0.0.1")
    v.mail_port.setText(str(smtp.port))
    v.mail_user.setText(smtp.user)
    v.mail_password.setText(PASSWORD)                 # typed but the field not left: the button saves it first
    assert v.send_test_mail() is not None
    qtbot.waitUntil(lambda: v._mail_test is None, timeout=10000)
    (msg,) = smtp.messages
    assert msg["To"] == smtp.user and msg["Subject"] == "Provmejl från Tröskel"
    assert smtp.user in v.mail_state.text() and v.mail_test_btn.isEnabled()
    assert win.ctx.setting("mail.port") == smtp.port and v.mail_password.text() == ""
    v.mail_password.setText("fel")
    v.send_test_mail()
    qtbot.waitUntil(lambda: v._mail_test is None, timeout=10000)
    assert "applösenord" in v.mail_state.text() and len(smtp.messages) == 1
