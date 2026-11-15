"""Mail against a pretend SMTP server on 127.0.0.1. Nothing leaves the machine."""
import logging

import pytest

from lopanalys.share import mail

from .fake_smtp import FakeSMTP

TO = "anna.lind@example.test"


def _cfg(srv, **kw):
    base = dict(host="127.0.0.1", port=srv.port, security="ssl" if srv.implicit_tls else "starttls", user=srv.user,
                password=srv.password, sender_name="Coach Demo")
    return mail.MailConfig(**{**base, **kw})


def _message(cfg):
    return mail.build_message(cfg, TO, "Din testrapport 2026-11-14", "Hej Anna!\n\nHär kommer rapporten.\n",
                              [mail.Attachment("testrapport-2026-11-14.pdf", b"%PDF-1.7 demo", "application/pdf"),
                               mail.Attachment("test-7-2026-11-14.fit", bytes(range(256)), "application/vnd.ant.fit")])


@pytest.mark.parametrize("implicit", [False, True])
def test_sends_with_attachments_over_tls(tmp_path, implicit):
    with FakeSMTP(tmp_path, implicit_tls=implicit) as srv:
        cfg = _cfg(srv)
        mail.send(cfg, _message(cfg), context=srv.client_context())
    (msg,) = srv.messages
    assert srv.envelopes == [(srv.user, [TO])] and srv.logins == [(srv.user, srv.password)]
    assert msg["To"] == TO and msg["Subject"] == "Din testrapport 2026-11-14"
    assert msg["From"] == f"Coach Demo <{srv.user}>" and msg["Message-ID"].endswith("@example.test>")
    assert "Hej Anna!" in msg.get_body().get_content()
    files = {p.get_filename(): (p.get_content_type(), p.get_content()) for p in msg.iter_attachments()}
    assert files == {"testrapport-2026-11-14.pdf": ("application/pdf", b"%PDF-1.7 demo"),
                     "test-7-2026-11-14.fit": ("application/vnd.ant.fit", bytes(range(256)))}
    # nothing but the greeting and STARTTLS went over the wire before it was encrypted
    assert not [ln for ln in srv.plain_lines if not ln.upper().startswith((b"EHLO", b"STARTTLS"))]


def test_never_logs_in_without_tls(tmp_path):
    with FakeSMTP(tmp_path, starttls=False) as srv:
        cfg = _cfg(srv)
        with pytest.raises(mail.MailError, match="krypterad anslutning"):
            mail.send(cfg, _message(cfg), context=srv.client_context())
    assert srv.logins == [] and srv.messages == []
    assert not [ln for ln in srv.plain_lines if b"AUTH" in ln.upper()]


def test_untrusted_certificate_is_refused(tmp_path):
    with FakeSMTP(tmp_path) as srv:
        cfg = _cfg(srv)
        with pytest.raises(mail.MailError, match="krypterad anslutning"):
            mail.send(cfg, _message(cfg))          # the system's certificates do not know this server
    assert srv.logins == [] and srv.messages == []


def test_errors_are_messages_for_the_coach_and_the_log_stays_clean(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    with FakeSMTP(tmp_path) as srv:
        bad = _cfg(srv, password="fel-losenord-xyz")
        with pytest.raises(mail.MailError, match="applösenord"):
            mail.send(bad, _message(bad), context=srv.client_context())
    with FakeSMTP(tmp_path, refuse_rcpt=True) as srv2:
        cfg = _cfg(srv2)
        with pytest.raises(mail.MailError, match="mottagarens adress") as e:
            mail.send(cfg, _message(cfg), context=srv2.client_context())
        assert TO not in str(e.value)
    closed = mail.MailConfig(host="127.0.0.1", port=1, user="u@example.test", password="p")
    with pytest.raises(mail.MailError, match="nå 127.0.0.1:1"):
        mail.send(closed, _message(closed), timeout=3)
    with pytest.raises(mail.MailError, match="inte inställd"):
        mail.send(mail.MailConfig(), _message(closed))
    assert caplog.records                           # something was logged, but no address and no password
    for word in (TO, "anna.lind", "fel-losenord-xyz", "app-pass-123"):
        assert word not in caplog.text


def test_only_one_plain_address_is_accepted():
    cfg = mail.MailConfig(user="coach@example.test", password="x")
    for bad in ("", "anna", "anna@", "a@b", "a b@example.test", "anna@example.test, eve@example.test",
                "anna@example.test\nBcc: eve@example.test", "Anna <anna@example.test>"):
        with pytest.raises(mail.MailError):
            mail.build_message(cfg, bad, "x", "y")
    msg = mail.build_message(cfg, " anna@example.test ", "Rad ett\r\nBcc: eve@example.test", "y")
    assert msg["To"] == "anna@example.test" and msg["Bcc"] is None and "\n" not in msg["Subject"]
    with pytest.raises(mail.MailError, match="för stora"):
        mail.build_message(cfg, TO, "x", "y", [mail.Attachment("a.pdf", b"0" * (mail.MAX_BYTES + 1))])
    assert mail.MailConfig(user="coach", password="x").missing()                 # no address to send from
    assert mail.MailConfig(user="coach", password="x", sender_address="c@example.test").missing() is None


def test_greeting_from_the_template():
    subject, body = mail.greeting("Anna", "2026-11-14", "Kim Coach", "Tröskel Test AB", fit=True)
    assert subject == "Din testrapport från Tröskel Test AB, 2026-11-14"     # who it is from: less like spam
    assert mail.greeting("Anna", "2026-11-14", "Kim Coach")[0] == "Din testrapport från Kim Coach, 2026-11-14"
    assert body.startswith("Hej Anna!\n\nHär kommer rapporten från ditt test 2026-11-14.\n")
    assert "Strava" in body and body.rstrip().endswith("Hälsningar\nKim Coach\nTröskel Test AB")
    subject, body = mail.greeting("", "2026-11-14", fit=False, tests=3)
    assert subject == "Din testrapport 2026-11-14" and body.startswith("Hej!") and "3 tester" in body
    assert "FIT" not in body and body.rstrip().endswith("Hälsningar")


def test_gmail_app_password_is_used_without_the_spaces_google_shows():
    """2026-11-14: the app password was saved as Google shows it, in four groups with spaces."""
    from lopanalys.share.mail import login_password
    assert login_password("smtp.gmail.com", "abcd efgh ijkl mnop") == "abcdefghijklmnop"
    assert login_password("smtp.example.se", "with space") == "with space"


def test_what_keeps_the_mail_out_of_the_spam_folder():
    """A new account's reports landed in the spam folder: a plain text part next to the HTML,
    Reply-To to the coach's own address, and no links (so no link shorteners)."""
    cfg = mail.MailConfig(user="troskel@example.test", password="x", sender_name="Kim Coach",
                          reply_to=mail.first_address("Kim Coach\n070-000 00 00 · kim@example.se, www.example.se"))
    msg = mail.build_message(cfg, TO, "Din testrapport från Kim Coach, 2026-11-14",
                             "Hej Anna!\n\nHär kommer <rapporten>.\n\nHälsningar\nKim Coach\n",
                             [mail.Attachment("r.pdf", b"%PDF", "application/pdf")])
    assert msg["Reply-To"] == "kim@example.se"
    plain = msg.get_body(preferencelist=("plain",))
    rich = msg.get_body(preferencelist=("html",))
    assert plain is not None and rich is not None and plain is not rich
    assert plain.get_content().startswith("Hej Anna!")
    h = rich.get_content()
    assert "<p>Hej Anna!</p>" in h and "&lt;rapporten&gt;" in h and "Hälsningar<br>" in h
    assert "<a " not in h and "http" not in h and "bit.ly" not in plain.get_content()
    assert [p.get_filename() for p in msg.iter_attachments()] == ["r.pdf"]
    same = mail.MailConfig(user="kim@example.se", password="x", reply_to="kim@example.se")
    assert mail.build_message(same, TO, "x", "y")["Reply-To"] is None          # not the sender again
    assert mail.first_address("Telefon 070-000 00 00") == ""
