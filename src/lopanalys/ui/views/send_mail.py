"""Skicka till idrottaren: the question before a mail is sent, and the sending itself.

The report (PDF) and the tests' FIT files, or the training plan (PDF, calendar file and the app's file), go to the
athlete's own address, from the SMTP account under Inställningar → E-post. Nothing is sent without
the consent box "contact", and never before the coach has seen the address and the attachments and
pressed *Skicka*. What is noted afterwards is the test's id (or the plan's log), the time and what
was attached: never the address.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit,
                               QVBoxLayout, QWidget)

from lopanalys.core.i18n import t
from lopanalys.core.models import Athlete, Session
from lopanalys.ui import services, theme
from lopanalys.ui.bridge import run_async
from lopanalys.ui.context import AppContext
from lopanalys.ui.widgets.common import button, field, label

log = logging.getLogger(__name__)

FIT_MIME = "application/vnd.ant.fit"
ICS_MIME = "text/calendar"
TROSKEL_MIME = "application/json"   # the .troskel file is the plan's JSON (plan/link.py)


class SendDialog(QDialog):
    """Shows the address and the attachments, lets the coach edit the greeting, and asks: send?"""

    def __init__(self, address: str, attachments: list[tuple[str, str]], subject: str, body: str,
                 parent: QWidget | None = None, warning: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(t("ui.mail.title"))
        self.setMinimumWidth(620)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(14)
        v.addWidget(label(t("ui.mail.title"), "SectionTitle"))
        self.address = label(address)
        self.address.setStyleSheet("font-weight: 600; font-size: 16px;")
        self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(field(t("ui.mail.to"), self.address))
        self.attachments = label("\n".join(f"•  {what}: {name}" for what, name in attachments))
        v.addWidget(field(t("ui.mail.attachments"), self.attachments))
        self.subject = QLineEdit(subject)
        v.addWidget(field(t("ui.mail.subject"), self.subject))
        self.body = QPlainTextEdit(body)
        self.body.setMinimumHeight(220)
        v.addWidget(field(t("ui.mail.message"), self.body))
        warn = label(warning or t("ui.mail.warn"), wrap=True)
        warn.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        v.addWidget(warn)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(t("ui.common.cancel"), kind="ghost")
        self.send_btn = button(t("ui.mail.send"), "mail", kind="primary")
        cancel.clicked.connect(self.reject)
        self.send_btn.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.send_btn)
        v.addLayout(row)
        cancel.setFocus()            # Enter must not send by accident


def send_to_athlete(parent: QWidget, ctx: AppContext, athlete: Athlete | None, sessions: list[Session],
                    make_pdf: Callable[[Path], Any], on_sent: Callable[[dict[str, Any]], None] | None = None,
                    on_end: Callable[[], None] | None = None, after: Callable[[], None] | None = None) -> Any:
    """Mail the report and the tests' FIT files to the athlete, after the question.

    ``make_pdf(path)`` writes the report there (on the GUI thread). The mail itself goes from a worker;
    ``on_sent(entry)`` gets what was noted on the tests, ``on_end()`` is called however it went, and
    ``after()`` when the coach has read that the mail was sent.
    Returns the worker, or None when nothing was sent (not allowed, cancelled, or the report failed)."""
    end = on_end or (lambda: None)
    athlete = _allowed(parent, ctx, athlete)
    sessions = [s for s in sessions if s.id is not None]
    if athlete is None or not sessions:
        if athlete is not None:
            QMessageBox.information(parent, t("ui.mail.title"), t("ui.mail.block.no_athlete"))
        end()
        return None
    fits: list[tuple[str, bytes]] = []
    for s in sessions:
        try:
            fits.append(services.fit_file(ctx.store, s))
        except Exception:      # a test without a time series has no FIT file: the report goes alone
            log.info("test %s has no FIT file to attach", s.id)
    day = min(s.started for s in sessions).astimezone().strftime("%Y-%m-%d")
    pdf_name = f"{t('ui.mail.report_file')}-{day}.pdf"
    subject, body = services.mail_greeting(athlete, day, ctx.branding(), bool(fits), len(sessions))
    ids = [int(s.id) for s in sessions if s.id is not None]
    what = ["report"] + (["fit"] if fits else [])

    def files() -> list[tuple[str, bytes, str]]:
        return [(pdf_name, _pdf_bytes(make_pdf), "application/pdf")] + [(n, b, FIT_MIME) for n, b in fits]

    def note() -> dict[str, Any]:
        entry = ctx.store.db.log_mailed(ids, what)
        log.info("tests %s mailed to the athlete (%s)", ids, ", ".join(what))
        return entry

    return _ask_and_send(parent, ctx, athlete, [(t("ui.mail.att.report"), pdf_name)] +
                         [(t("ui.mail.att.fit"), n) for n, _ in fits], subject, body, files, note,
                         on_sent, end, after)


def send_plan_to_athlete(parent: QWidget, ctx: AppContext, athlete: Athlete | None, plan: Any, plan_row: int | None,
                         make_pdf: Callable[[Path], Any], on_end: Callable[[], None] | None = None) -> Any:
    """Mail the training plan to the athlete: the printout as PDF, the calendar file and the plan for the app
    (the ``.troskel`` file, and the link in the text), after the question.

    ``plan`` is the saved plan and ``plan_row`` its row: the mailing is noted in the plan's log
    (``plan_events``, kind "mailed"). ``make_pdf(path)`` writes the printout there (on the GUI thread).
    Returns the worker, or None when nothing was sent."""
    end = on_end or (lambda: None)
    athlete = _allowed(parent, ctx, athlete)
    if athlete is None:
        end()
        return None
    start = plan.start.isoformat()
    pdf_name, ics_name = (f"{t('ui.mail.plan_file')}-{start}.{ext}" for ext in ("pdf", "ics"))
    app_name = services.plan_app_file_name(plan)
    app_link, app_file = services.plan_app(plan)
    subject, body = services.plan_mail_greeting(athlete, plan, ctx.branding(), app_link)
    what = ["plan", "ics", "app"]

    def files() -> list[tuple[str, bytes, str]]:
        return [(pdf_name, _pdf_bytes(make_pdf), "application/pdf"), (ics_name, services.plan_ics(plan), ICS_MIME),
                (app_name, app_file, TROSKEL_MIME)]

    def note() -> dict[str, Any]:
        entry = ctx.store.db.log_plan_event(plan_row, "mailed", {"what": what}) if plan_row is not None else {}
        log.info("training plan mailed to the athlete (%s)", ", ".join(what))
        return entry

    return _ask_and_send(parent, ctx, athlete, [(t("ui.mail.att.plan"), pdf_name), (t("ui.mail.att.ics"), ics_name),
                                                (t("ui.mail.att.app"), app_name)],
                         subject, body, files, note, None, end, None, t("ui.mail.warn_plan"))


def _allowed(parent: QWidget, ctx: AppContext, athlete: Athlete | None) -> Athlete | None:
    """The athlete as saved now when mail may go to them; otherwise None, after saying why."""
    reason = services.mail_block_reason(ctx.store, athlete)
    if athlete is not None and athlete.id is not None:
        athlete = ctx.store.athlete(athlete.id)          # the address as it is saved now
    if reason or athlete is None:
        QMessageBox.information(parent, t("ui.mail.title"), reason or t("ui.mail.block.no_athlete"))
        return None
    return athlete


def _pdf_bytes(make_pdf: Callable[[Path], Any]) -> bytes:
    # A temporary folder: the PDF that is mailed is not kept on the disk.
    with tempfile.TemporaryDirectory(prefix="lopanalys-mail-") as d:
        make_pdf(Path(d) / "report.pdf")
        return (Path(d) / "report.pdf").read_bytes()


def _ask_and_send(parent: QWidget, ctx: AppContext, athlete: Athlete, listing: list[tuple[str, str]], subject: str,
                  body: str, files: Callable[[], list[tuple[str, bytes, str]]], note: Callable[[], dict[str, Any]],
                  on_sent: Callable[[dict[str, Any]], None] | None, end: Callable[[], None],
                  after: Callable[[], None] | None, warning: str = "") -> Any:
    """The question, then the attachments (on the GUI thread), then the mail from a worker."""
    dlg = SendDialog(athlete.email, listing, subject, body, parent, warning)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        end()
        return None
    try:
        attachments = files()
    except Exception as e:
        log.exception("the attachments for the mail failed")
        QMessageBox.warning(parent, t("ui.app.name"), t("ui.err.pdf", detail=str(e)))
        end()
        return None
    cfg = services.mail_config(ctx.store)
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

    def done(_: Any) -> None:
        QApplication.restoreOverrideCursor()
        try:
            entry = note()
        except Exception:          # sent all the same: say so
            log.exception("noting the mail failed")
            entry = {}
        if on_sent is not None and entry:
            on_sent(entry)
        end()
        QMessageBox.information(parent, t("ui.mail.title"), t("ui.mail.sent"))
        if after is not None:
            after()

    def fail(msg: str) -> None:
        QApplication.restoreOverrideCursor()
        end()
        QMessageBox.warning(parent, t("ui.mail.title"), t("ui.err.mail", detail=msg))

    return run_async(services.send_mail, cfg, athlete.email, dlg.subject.text(), dlg.body.toPlainText(), attachments,
                     on_done=done, on_error=fail)
