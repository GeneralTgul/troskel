"""Mail to the athlete over the coach's own SMTP account. No Qt.

The program has no mail service of its own: it logs in to the account the coach set up under
Inställningar → E-post (default Gmail: smtp.gmail.com, port 587, STARTTLS, an app password) and
sends from there. The connection to the server is always encrypted (STARTTLS or TLS from the start;
there is no plain mode), but ordinary e-mail is not encrypted on its way to the reader, so it is only
sent to a person who ticked the consent box "contact".

Spam filters: a new account without history is easily filed as spam. What the program can do is
done here: a subject that says who it is from, a plain text part next to the HTML, Reply-To to the
coach's own address (from the contact details) and no links at all, so no link shorteners. The real
fix is the coach's own domain with SPF and DKIM (docs: Inställningar → E-post).

Privacy: nothing here logs an address, a subject or a password. Errors are turned into a message
for the coach (``MailError``), and the log gets the kind of error only.
"""
from __future__ import annotations

import html
import logging
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any, Iterable

from lopanalys.core.i18n import t
from lopanalys.core.models import valid_email

log = logging.getLogger(__name__)

SECURITY = ("starttls", "ssl")
DEFAULT_PORT = {"starttls": 587, "ssl": 465}
# The settings (table ``settings``) and their defaults. ``mail.password`` is share.secret.protect().
DEFAULTS: dict[str, Any] = {"mail.host": "smtp.gmail.com", "mail.port": 587, "mail.security": "starttls",
                            "mail.user": "", "mail.password": None, "mail.sender_name": "",
                            "mail.sender_address": ""}
TIMEOUT_S = 30.0
MAX_BYTES = 20 * 1024 * 1024      # Gmail takes 25 MB, and base64 adds a third


class MailError(Exception):
    """The mail was not sent. The message is for the coach (localised) and names no address."""


@dataclass
class MailConfig:
    host: str = "smtp.gmail.com"
    port: int = 587
    security: str = "starttls"
    user: str = ""
    password: str = ""
    sender_name: str = ""
    sender_address: str = ""          # empty: the user name, when that is an address
    reply_to: str = ""                # the coach's own address for answers, or empty

    @property
    def sender(self) -> str:
        return (self.sender_address or self.user).strip()

    def missing(self) -> str | None:
        """What is not filled in yet, as a message for the coach, or None when the account can be used."""
        if not self.host.strip() or not self.user.strip() or not self.password:
            return t("share.mail.err.not_set_up")
        if self.security not in SECURITY or not 0 < int(self.port) < 65536:
            return t("share.mail.err.not_set_up")
        if not valid_email(self.sender):
            return t("share.mail.err.sender")
        return None


@dataclass
class Attachment:
    name: str
    data: bytes
    mime: str = "application/octet-stream"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def first_address(text: str) -> str:
    """The first plain e-mail address in free text (the contact details in the branding), or ""."""
    for word in re.split(r"[\s,;<>()]+", text or ""):
        word = word.strip(".:")
        if valid_email(word):
            return word
    return ""


def html_body(body: str) -> str:
    """The plain text as a small HTML part: paragraphs at blank lines, line breaks kept, no links."""
    paras = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    inner = "\n".join("<p>" + "<br>\n".join(html.escape(ln) for ln in p.splitlines()) + "</p>" for p in paras)
    return ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"></head>\n"
            "<body style=\"font-family: Arial, Helvetica, sans-serif; font-size: 15px; line-height: 1.5; color: #222;\">\n"
            f"{inner}\n</body></html>\n")


def build_message(cfg: MailConfig, to: str, subject: str, body: str,
                  attachments: Iterable[Attachment] = ()) -> EmailMessage:
    """The message with its attachments. Raises MailError for an address that is not one plain address."""
    to = to.strip()
    if not valid_email(to):
        raise MailError(t("share.mail.err.address"))
    if not valid_email(cfg.sender):
        raise MailError(t("share.mail.err.sender"))
    msg = EmailMessage()
    msg["From"] = formataddr((_one_line(cfg.sender_name), cfg.sender))
    msg["To"] = to
    msg["Subject"] = _one_line(subject)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=cfg.sender.rsplit("@", 1)[1])   # not the PC's own name
    reply = cfg.reply_to.strip()
    if reply and valid_email(reply) and reply.lower() != cfg.sender.lower():
        msg["Reply-To"] = reply
    msg.set_content(body)                          # the plain text part, and the same text as HTML
    msg.add_alternative(html_body(body), subtype="html")
    size = 0
    for a in attachments:
        size += len(a.data)
        main, _, sub = a.mime.partition("/")
        msg.add_attachment(a.data, maintype=main, subtype=sub or "octet-stream", filename=a.name)
    if size > MAX_BYTES:
        raise MailError(t("share.mail.err.too_big", mb=round(size / 1e6)))
    return msg


def login_password(host: str, password: str) -> str:
    """Google shows an app password as four groups ("abcd efgh ijkl mnop") and coaches paste it like that;
    Gmail's app passwords never contain spaces, so they are removed there. Other servers get it as typed."""
    return "".join(password.split()) if host.strip().lower().endswith("gmail.com") else password


def send(cfg: MailConfig, msg: EmailMessage, *, context: ssl.SSLContext | None = None,
         timeout: float = TIMEOUT_S) -> None:
    """Log in and send. Raises MailError with what the coach can do about it."""
    problem = cfg.missing()
    if problem:
        raise MailError(problem)
    context = context or ssl.create_default_context()
    host, port = cfg.host.strip(), int(cfg.port)
    try:
        if cfg.security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, local_hostname="localhost", timeout=timeout,
                                                  context=context)
        else:
            smtp = smtplib.SMTP(host, port, local_hostname="localhost", timeout=timeout)
        with smtp:
            if cfg.security == "starttls":
                smtp.starttls(context=context)       # raises when the server has no STARTTLS: never plain
            smtp.login(cfg.user.strip(), login_password(host, cfg.password))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        log.warning("mail: the server refused the login")
        raise MailError(t("share.mail.err.auth")) from None
    except smtplib.SMTPRecipientsRefused:
        log.warning("mail: the server refused the recipient")
        raise MailError(t("share.mail.err.recipient")) from None
    except smtplib.SMTPNotSupportedError:
        log.warning("mail: the server lacks STARTTLS or login")
        raise MailError(t("share.mail.err.tls", host=host, port=port)) from None
    except smtplib.SMTPResponseException as e:
        log.warning("mail: the server answered %s", e.smtp_code)
        raise MailError(t("share.mail.err.smtp", code=e.smtp_code)) from None
    except smtplib.SMTPException as e:
        log.warning("mail: %s", type(e).__name__)
        raise MailError(t("share.mail.err.smtp", code=type(e).__name__)) from None
    except ssl.SSLError as e:
        log.warning("mail: TLS failed (%s)", type(e).__name__)
        raise MailError(t("share.mail.err.tls", host=host, port=port)) from None
    except (socket.gaierror, TimeoutError, OSError) as e:
        log.warning("mail: no connection (%s)", type(e).__name__)
        raise MailError(t("share.mail.err.connect", host=host, port=port)) from None


def greeting(first_name: str, day: str, coach: str = "", business: str = "", *, fit: bool = False,
             tests: int = 1) -> tuple[str, str]:
    """(subject, body) from the template: a short greeting signed with the coach's name and business."""
    who = business.strip() or coach.strip()
    subject = t("share.mail.subject_from", who=who, date=day) if who else t("share.mail.subject", date=day)
    lines = [t("share.mail.hello", name=first_name.strip()) if first_name.strip() else t("share.mail.hello_plain"), "",
             t("share.mail.body_many", date=day, n=tests) if tests > 1 else t("share.mail.body", date=day)]
    if fit:
        lines += ["", t("share.mail.body_fit")]
    lines += ["", t("share.mail.questions"), "", t("share.mail.regards")]
    lines += [x.strip() for x in (coach, business) if x and x.strip()]
    return subject, "\n".join(lines) + "\n"


def plan_greeting(first_name: str, start: str, weeks: int, coach: str = "", business: str = "") -> tuple[str, str]:
    """(subject, body) of the mail with the training plan: the PDF to read and the calendar file, with
    how the calendar file is opened in a phone. Signed as the report's mail (``greeting``)."""
    who = business.strip() or coach.strip()
    subject = t("share.mail.plan_subject_from", who=who) if who else t("share.mail.plan_subject")
    lines = [t("share.mail.hello", name=first_name.strip()) if first_name.strip() else t("share.mail.hello_plain"), "",
             t("share.mail.plan_body", weeks=weeks, start=start), "", t("share.mail.plan_ics"),
             t("share.mail.plan_ics_iphone"), t("share.mail.plan_ics_android"), "", t("share.mail.plan_ics_update"),
             "", t("share.mail.questions"), "", t("share.mail.regards")]
    lines += [x.strip() for x in (coach, business) if x and x.strip()]
    return subject, "\n".join(lines) + "\n"


def probe_message(cfg: MailConfig, to: str | None = None) -> EmailMessage:
    """The mail "Skicka ett provmejl" sends, to ``to`` (default the account itself). Gmail shows a
    mail to the account itself under Skickat only, so the coach gives another inbox."""
    return build_message(cfg, to or cfg.sender, t("share.mail.test_subject"), t("share.mail.test_body") + "\n")
