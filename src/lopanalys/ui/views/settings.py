"""Inställningar: branding, language, TV screen, default devices, printer, data folder, backup and e-mail."""
from __future__ import annotations

import csv
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIntValidator, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QScrollArea,
                               QVBoxLayout, QWidget)

from lopanalys.core import paths
from lopanalys.core.i18n import t
from lopanalys.ui import services, theme
from lopanalys.ui.context import AppContext
from lopanalys.ui.lock import IDLE_CHOICES, MIN_PIN, hash_pin
from lopanalys.ui.widgets.common import Card, ColumnsBox, PageHeader, button, field, label, page
from lopanalys.ui.widgets.inputs import SpecialSpinBox

log = logging.getLogger(__name__)

ORG, APP = "Lopanalys", "Lopanalys"


def data_folder_override() -> str:
    """The data folder the coach chose (stored outside the data folder itself, in QSettings)."""
    return str(QSettings(ORG, APP).value("data_dir", "") or "")


def set_data_folder_override(path: str) -> None:
    QSettings(ORG, APP).setValue("data_dir", path)


def mark_moved(old: str, new: str) -> None:
    """After "Flytta mapp": the new folder is used from the next start, which also empties the old one."""
    set_data_folder_override(new)
    QSettings(ORG, APP).setValue("data_dir_moved_from", old)


def pick_printer(parent: QWidget, default: str) -> str | None:
    """Ask which printer to use. Returns the name, '' for the system default, None if cancelled."""
    printers = services.available_printers()
    dlg = QDialog(parent)
    dlg.setWindowTitle(t("ui.res.print"))
    v = QVBoxLayout(dlg)
    v.setContentsMargins(24, 24, 24, 24)
    v.setSpacing(14)
    v.addWidget(label(t("ui.set.choose_printer"), "SectionTitle"))
    combo = QComboBox()
    combo.addItem(t("ui.set.system_printer"), "")
    for p in printers:
        combo.addItem(p, p)
    i = combo.findData(default)
    combo.setCurrentIndex(max(0, i))
    v.addWidget(combo)
    if not printers:
        v.addWidget(label(t("ui.set.no_printers"), "Hint", wrap=True))
    row = QHBoxLayout()
    row.addStretch(1)
    cancel = button(t("ui.common.cancel"), kind="ghost")
    ok = button(t("ui.res.print"), "printer", kind="primary")
    cancel.clicked.connect(dlg.reject)
    ok.clicked.connect(dlg.accept)
    row.addWidget(cancel)
    row.addWidget(ok)
    v.addLayout(row)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return str(combo.currentData())


class SettingsView(QWidget):
    """Every change is saved immediately."""

    lock_requested = Signal()      # "Lås nu"
    lock_changed = Signal()        # the PIN or the idle time changed

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802 - Qt's name
        if obj is self.scroll.viewport() and e.type() == QEvent.Type.Resize:
            self.cols.fit(self.scroll.viewport().width() - 2 * 32)      # the page's margins
        return super().eventFilter(obj, e)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        outer.addWidget(sa)
        self.scroll = sa
        w, v = page()
        sa.setWidget(w)
        v.addWidget(PageHeader(t("ui.nav.settings"), t("ui.set.subtitle")))
        # two columns on a wide screen, one on a laptop (2026-11-16: the page did not fit a laptop)
        self.cols = ColumnsBox()
        c1, c2 = self.cols.c1, self.cols.c2
        v.addWidget(self.cols)
        sa.viewport().installEventFilter(self)

        # --- branding
        b = Card(t("ui.set.branding"), t("ui.set.branding_sub"))
        g = QGridLayout()
        g.setSpacing(14)
        self.business = self._line("branding.business")
        self.coach = self._line("branding.coach")
        self.contact = QPlainTextEdit(str(ctx.setting("branding.contact") or ""))
        self.contact.setFixedHeight(80)
        self.contact.textChanged.connect(lambda: ctx.set_setting("branding.contact", self.contact.toPlainText()))
        g.addWidget(field(t("ui.set.business"), self.business), 0, 0)
        g.addWidget(field(t("ui.set.coach"), self.coach), 0, 1)
        g.addWidget(field(t("ui.set.contact"), self.contact, t("ui.set.contact_hint")), 1, 0, 1, 2)
        b.body.addLayout(g)
        lr = QHBoxLayout()
        lr.setSpacing(14)
        self.logo = QLabel()
        self.logo.setFixedSize(150, 70)
        self.logo.setObjectName("CardFlat")
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lr.addWidget(self.logo)
        lcol = QVBoxLayout()
        lcol.addWidget(label(t("ui.set.logo"), "SectionTitle"))
        lcol.addWidget(label(t("ui.set.logo_hint"), "Hint", wrap=True))
        lb = QHBoxLayout()
        pick = button(t("ui.set.logo_pick"), "image")
        pick.clicked.connect(self._pick_logo)
        rm = button(t("ui.set.logo_remove"), kind="ghost")
        rm.clicked.connect(lambda: self._set_logo(""))
        lb.addWidget(pick)
        lb.addWidget(rm)
        lb.addStretch(1)
        lcol.addLayout(lb)
        lr.addLayout(lcol, 1)
        b.body.addLayout(lr)
        ar = QHBoxLayout()
        self.accent_btn = button("")
        self.accent_btn.setFixedSize(48, 38)
        self.accent_btn.clicked.connect(self._pick_accent)
        ar.addWidget(self.accent_btn)
        ar.addWidget(label(t("ui.set.accent"), "SectionTitle"))
        ar.addStretch(1)
        reset = button(t("ui.set.accent_reset"), kind="ghost")
        reset.clicked.connect(lambda: self._set_accent("#22A699"))
        ar.addWidget(reset)
        b.body.addLayout(ar)
        c1.addWidget(b)
        self._set_logo(str(ctx.setting("branding.logo") or ""), save=False)
        self._paint_accent()

        # --- language and TV
        lt = Card(t("ui.set.display"))
        self.lang = QComboBox()
        self.lang.addItem("Svenska", "sv")
        self.lang.addItem("English", "en")
        self.lang.setCurrentIndex(max(0, self.lang.findData(ctx.setting("app.language"))))
        self.lang.currentIndexChanged.connect(self._lang_changed)
        lt.body.addWidget(field(t("ui.set.language"), self.lang, t("ui.set.language_hint")))
        self.screen = QComboBox()
        self.screen.addItem(t("ui.set.screen_auto"), -1)
        for i, s in enumerate(QGuiApplication.screens()):
            g2 = s.geometry()
            self.screen.addItem(t("ui.set.screen_n", n=i + 1, name=s.name(), w=g2.width(), h=g2.height()), i)
        scr = ctx.setting("tv.screen")                # 0 is the first screen, not "choose by itself"
        self.screen.setCurrentIndex(max(0, self.screen.findData(-1 if scr in (None, "") else int(scr))))
        self.screen.currentIndexChanged.connect(lambda _: ctx.set_setting("tv.screen", self.screen.currentData()))
        lt.body.addWidget(field(t("ui.set.tv_screen"), self.screen, t("ui.set.tv_hint")))
        self.sound = QCheckBox(t("ui.set.sound"))
        self.sound.setChecked(bool(ctx.setting("tv.sound")))
        self.sound.toggled.connect(lambda v: ctx.set_setting("tv.sound", v))
        lt.body.addWidget(self.sound)
        c1.addWidget(lt)

        # --- lock and customer mode
        lk = Card(t("ui.lock.card"), t("ui.lock.card_sub"))
        self.pin_state = label("", wrap=True)
        lk.body.addWidget(self.pin_state)
        pr = QHBoxLayout()
        self.pin_btn = button("", kind="primary")
        self.pin_btn.clicked.connect(lambda: self.set_pin())
        self.pin_remove = button(t("ui.lock.remove_pin"), kind="ghost")
        self.pin_remove.clicked.connect(lambda: self.set_pin(""))
        self.lock_now = button(t("ui.lock.lock_now"))
        self.lock_now.clicked.connect(self.lock_requested)
        for w in (self.pin_btn, self.pin_remove, self.lock_now):
            pr.addWidget(w)
        pr.addStretch(1)
        lk.body.addLayout(pr)
        self.idle_min = QComboBox()
        for n in IDLE_CHOICES:
            self.idle_min.addItem(t("ui.lock.idle_min", n=n) if n else t("ui.lock.idle_never"), n)
        self.idle_min.setCurrentIndex(max(0, self.idle_min.findData(int(ctx.setting("lock.idle_min") or 0))))
        self.idle_min.currentIndexChanged.connect(self._idle_changed)
        lk.body.addWidget(field(t("ui.lock.idle"), self.idle_min, t("ui.lock.idle_hint")))
        self.customer_mode = QCheckBox(t("ui.lock.customer_mode"))
        self.customer_mode.setChecked(bool(ctx.setting("lock.customer_mode")))
        self.customer_mode.toggled.connect(lambda v: ctx.set_setting("lock.customer_mode", v))
        lk.body.addWidget(self.customer_mode)
        lk.body.addWidget(label(t("ui.lock.pin_forgot"), "Hint", wrap=True))
        c1.addWidget(lk)
        self.lock_card = lk
        self._refresh_lock()
        c1.addStretch(1)

        # --- devices and printer
        d = Card(t("ui.set.devices"))
        self.mode = QComboBox()
        for m in ("ftms", "qz", "manual") + (("sim",) if ctx.demo else ()):   # simulator: demo only
            self.mode.addItem(t(f"ui.mode.{m}"), m)
        self.mode.setCurrentIndex(max(0, self.mode.findData(ctx.setting("devices.treadmill_mode"))))
        self.mode.currentIndexChanged.connect(lambda _: ctx.set_setting("devices.treadmill_mode", self.mode.currentData()))
        d.body.addWidget(field(t("ui.set.default_treadmill"), self.mode))
        # The belt's real range: QZ cannot report it, and a ramp past it does not load the runner.
        self.belt_speed = SpecialSpinBox()   # 0 shows "Som bandet anger" and still takes digits
        self.belt_speed.setRange(0, 30)
        self.belt_speed.setSingleStep(0.5)
        self.belt_speed.setDecimals(1)
        self.belt_speed.setSuffix(" km/h")
        self.belt_speed.setSpecialValueText(t("ui.set.belt_from_device"))
        self.belt_speed.setValue(float(ctx.setting("devices.max_speed_kmh") or 0))
        self.belt_speed.valueChanged.connect(lambda v: ctx.set_setting("devices.max_speed_kmh", float(v)))
        self.belt_incline = SpecialSpinBox()
        self.belt_incline.setRange(0, 40)
        self.belt_incline.setSingleStep(0.5)
        self.belt_incline.setDecimals(1)
        self.belt_incline.setSuffix(" %")
        self.belt_incline.setSpecialValueText(t("ui.set.belt_from_device"))
        self.belt_incline.setValue(float(ctx.setting("devices.max_incline_pct") or 0))
        self.belt_incline.valueChanged.connect(lambda v: ctx.set_setting("devices.max_incline_pct", float(v)))
        d.body.addWidget(field(t("ui.set.belt_max_speed"), self.belt_speed))
        d.body.addWidget(field(t("ui.set.belt_max_incline"), self.belt_incline, t("ui.set.belt_hint")))
        self.printer = QComboBox()
        self.printer.addItem(t("ui.set.system_printer"), "")
        for p in services.available_printers():
            self.printer.addItem(p, p)
        self.printer.setCurrentIndex(max(0, self.printer.findData(ctx.setting("report.printer"))))
        self.printer.currentIndexChanged.connect(lambda _: ctx.set_setting("report.printer", self.printer.currentData()))
        d.body.addWidget(field(t("ui.set.printer"), self.printer))
        c2.addWidget(d)

        # --- data
        dc = Card(t("ui.set.data"), t("ui.set.data_sub"))
        self.folder = QLineEdit(str(paths.data_dir()))
        self.folder.setReadOnly(True)
        self.folder.setCursorPosition(0)
        self.folder.setToolTip(str(paths.data_dir()))
        dc.body.addWidget(field(t("ui.set.data_folder"), self.folder))
        r1 = QHBoxLayout()
        ob = button(t("ui.set.open_folder"), "folder")
        ob.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir()))))
        mv = button(t("ui.set.move_folder"), "folder", kind="ghost")
        mv.clicked.connect(lambda: self._move_folder())
        mv.setVisible(not ctx.demo)
        r1.addWidget(ob)
        r1.addWidget(mv)
        r1.addStretch(1)
        dc.body.addLayout(r1)
        self.video_keep = QComboBox()
        for key in ("after_analysis", "30d", "keep"):
            self.video_keep.addItem(t(f"ui.set.video.{key}"), key)
        self.video_keep.setCurrentIndex(max(0, self.video_keep.findData(ctx.setting("privacy.video_retention"))))
        self.video_keep.currentIndexChanged.connect(
            lambda _: ctx.set_setting("privacy.video_retention", self.video_keep.currentData()))
        dc.body.addWidget(field(t("ui.set.video_retention"), self.video_keep, t("ui.set.video_retention_hint")))
        r2 = QHBoxLayout()
        bk = button(t("ui.set.backup"), "download", kind="primary")
        bk.clicked.connect(lambda: self.backup())
        rs = button(t("ui.set.restore"), "refresh")
        rs.clicked.connect(lambda: self.restore())
        ex = button(t("ui.set.export"), "upload")
        ex.clicked.connect(lambda: self.export_csv())
        r2.addWidget(bk)
        r2.addWidget(rs)
        r2.addWidget(ex)
        r2.addStretch(1)
        dc.body.addLayout(r2)
        dc.body.addWidget(label(t("ui.set.backup_hint"), "Hint", wrap=True))
        c2.addWidget(dc)

        # --- e-mail: the account the reports are mailed from
        mc = Card(t("ui.set.mail"), t("ui.set.mail_sub"))
        mg = QGridLayout()
        mg.setSpacing(14)
        self.mail_host = self._line("mail.host")
        self.mail_port = QLineEdit(str(ctx.setting("mail.port") or ""))
        self.mail_port.setValidator(QIntValidator(1, 65535, self.mail_port))
        self.mail_port.editingFinished.connect(self._mail_port_changed)
        self.mail_security = QComboBox()
        for key in ("starttls", "ssl"):
            self.mail_security.addItem(t(f"ui.set.mail_security.{key}"), key)
        self.mail_security.setCurrentIndex(max(0, self.mail_security.findData(ctx.setting("mail.security"))))
        self.mail_security.currentIndexChanged.connect(self._mail_security_changed)
        self.mail_user = self._line("mail.user")
        self.mail_password = QLineEdit()
        self.mail_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.mail_password.editingFinished.connect(lambda: self.set_mail_password(self.mail_password.text()))
        self.mail_sender_name = self._line("mail.sender_name")
        self.mail_sender_name.setPlaceholderText(str(ctx.setting("branding.coach") or ""))
        self.mail_sender_address = self._line("mail.sender_address")
        mg.addWidget(field(t("ui.set.mail_host"), self.mail_host), 0, 0)
        mg.addWidget(field(t("ui.set.mail_port"), self.mail_port), 0, 1)
        mg.addWidget(field(t("ui.set.mail_security"), self.mail_security), 0, 2)
        mg.addWidget(field(t("ui.set.mail_user"), self.mail_user), 1, 0)
        mg.addWidget(field(t("ui.set.mail_password"), self.mail_password), 1, 1, 1, 2)
        mg.addWidget(field(t("ui.set.mail_sender_name"), self.mail_sender_name), 2, 0)
        mg.addWidget(field(t("ui.set.mail_sender_address"), self.mail_sender_address,
                           t("ui.set.mail_sender_address_hint")), 2, 1, 1, 2)
        mg.setColumnStretch(0, 3)
        mg.setColumnStretch(1, 1)
        mg.setColumnStretch(2, 2)
        for i in range(mg.count()):
            mg.itemAt(i).setAlignment(Qt.AlignmentFlag.AlignTop)
        mc.body.addLayout(mg)
        mr = QHBoxLayout()
        self.mail_test_btn = button(t("ui.set.mail_test"), "mail", kind="primary", tooltip=t("ui.set.mail_test_tip"))
        self.mail_test_btn.clicked.connect(lambda: self.send_test_mail())
        self.mail_remove = button(t("ui.set.mail_password_remove"), kind="ghost")
        self.mail_remove.clicked.connect(lambda: self.set_mail_password("", remove=True))
        mr.addWidget(self.mail_test_btn)
        mr.addWidget(self.mail_remove)
        mr.addStretch(1)
        mc.body.addLayout(mr)
        self.mail_state = label("", wrap=True)
        mc.body.addWidget(self.mail_state)
        self.mail_state.hide()
        mc.body.addWidget(label(t("ui.set.mail_unencrypted"), "Hint", wrap=True))
        c2.addWidget(mc)
        self.mail_card = mc
        self._mail_test: Any = None
        self._refresh_mail()
        about = Card(t("ui.set.about"))
        from lopanalys import __version__
        about.body.addWidget(label(t("ui.set.version", v=__version__), "Hint"))
        if ctx.demo:
            about.body.addWidget(label(t("ui.set.demo_on"), "Hint"))
        c2.addWidget(about)
        c2.addStretch(1)

    # ------------------------------------------------------------------ e-mail
    def _refresh_mail(self) -> None:
        saved = bool(self.ctx.setting("mail.password"))
        readable = saved and bool(services.mail_config(self.ctx.store).password)
        self.mail_password.clear()
        self.mail_password.setPlaceholderText(
            t("ui.set.mail_password_saved") if readable else
            t("ui.set.mail_password_unreadable") if saved else t("ui.set.mail_password_none"))
        self.mail_remove.setVisible(saved)

    def _mail_say(self, text: str) -> None:
        self.mail_state.setText(text)
        self.mail_state.setVisible(bool(text))

    def _mail_port_changed(self) -> None:
        txt = self.mail_port.text().strip()
        if txt.isdigit() and 0 < int(txt) < 65536:
            self.ctx.set_setting("mail.port", int(txt))
        else:
            self.mail_port.setText(str(self.ctx.setting("mail.port") or ""))

    def _mail_security_changed(self, _: int) -> None:
        """The usual port follows the choice when the port was the other choice's usual one."""
        from lopanalys.share.mail import DEFAULT_PORT
        key = self.mail_security.currentData()
        self.ctx.set_setting("mail.security", key)
        if int(self.ctx.setting("mail.port") or 0) in DEFAULT_PORT.values():
            self.ctx.set_setting("mail.port", DEFAULT_PORT[key])
            self.mail_port.setText(str(DEFAULT_PORT[key]))

    def set_mail_password(self, password: str, remove: bool = False) -> bool:
        """Save the app password protected (never as typed), or remove it. An empty field changes nothing."""
        if not password and not remove:
            return False
        try:
            services.set_mail_password(self.ctx.store, password.strip())
        except Exception as e:
            log.exception("protecting the mail password failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.set.mail_password_failed", detail=type(e).__name__))
            return False
        self._refresh_mail()
        return True

    def ask_test_address(self, default: str) -> str | None:
        """The address the test mail goes to (None: cancelled). Gmail files a mail to the account
        itself under Skickat only, so the coach picks an inbox where it can be seen."""
        from lopanalys.core.models import valid_email
        dlg = QDialog(self)
        dlg.setWindowTitle(t("ui.set.mail_test"))
        v = QVBoxLayout(dlg)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)
        to = QLineEdit(default)
        to.setMinimumWidth(320)
        to.selectAll()
        v.addWidget(field(t("ui.set.mail_test_to"), to, t("ui.set.mail_test_to_hint")))
        err = label("", wrap=True)
        err.setStyleSheet(f"color: {theme.TOKENS['fix']}; font-weight: 600;")
        v.addWidget(err)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = button(t("ui.common.cancel"), kind="ghost"), button(t("ui.mail.send"), "mail", kind="primary")
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)

        def accept() -> None:
            if valid_email(to.text().strip()):
                dlg.accept()
            else:
                err.setText(t("share.mail.err.address"))

        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(accept)
        to.returnPressed.connect(accept)
        return to.text().strip() if dlg.exec() == QDialog.DialogCode.Accepted else None

    def send_test_mail(self, to: str | None = None) -> Any:
        """*Skicka ett provmejl*: from the account to an address the coach gives (``to``; None asks,
        with the last one used filled in), so the settings are known to work."""
        if self._mail_test is not None:
            return None
        for e in (self.mail_host, self.mail_user, self.mail_sender_name, self.mail_sender_address):
            e.editingFinished.emit()            # what is typed but not left yet counts
        self._mail_port_changed()
        self.set_mail_password(self.mail_password.text())
        cfg = services.mail_config(self.ctx.store)
        problem = cfg.missing()
        if problem:
            self._mail_say(problem)
            QMessageBox.warning(self, t("ui.set.mail"), problem)
            return None
        if to is None:
            to = self.ask_test_address(str(self.ctx.setting("mail.test_to") or cfg.sender))
            if not to:
                return None
        self.ctx.set_setting("mail.test_to", to)
        self.mail_test_btn.setEnabled(False)
        self._mail_say(t("ui.mail.sending"))

        def done(_: Any) -> None:
            self._mail_test = None
            self.mail_test_btn.setEnabled(True)
            self._mail_say(t("ui.set.mail_test_ok", address=to))
            log.info("test mail sent")

        def fail(msg: str) -> None:
            self._mail_test = None
            self.mail_test_btn.setEnabled(True)
            self._mail_say(t("ui.err.mail", detail=msg))

        from lopanalys.ui.bridge import run_async
        self._mail_test = run_async(services.send_test_mail, cfg, to, on_done=done, on_error=fail)
        return self._mail_test

    def _refresh_lock(self) -> None:
        on = bool(self.ctx.setting("lock.pin"))
        self.pin_state.setText(t("ui.lock.pin_on") if on else t("ui.lock.pin_off"))
        self.pin_btn.setText(t("ui.lock.change_pin") if on else t("ui.lock.set_pin"))
        self.pin_remove.setVisible(on)
        self.lock_now.setVisible(on)

    def _idle_changed(self, _: int) -> None:
        self.ctx.set_setting("lock.idle_min", self.idle_min.currentData())
        self.lock_changed.emit()

    def set_pin(self, pin: str | None = None) -> bool:
        """Set, change or (with "") remove the PIN. None: ask for it in a dialog."""
        if pin is None:
            pin = self.ask_pin()
            if pin is None:
                return False
        if pin and len(pin) < MIN_PIN:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.lock.pin_short", n=MIN_PIN))
            return False
        self.ctx.set_setting("lock.pin", hash_pin(pin) if pin else None)
        self._refresh_lock()
        self.lock_changed.emit()
        return True

    def ask_pin(self) -> str | None:
        dlg = QDialog(self)
        dlg.setWindowTitle(t("ui.lock.set_pin"))
        v = QVBoxLayout(dlg)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)
        first, again = QLineEdit(), QLineEdit()
        for e in (first, again):
            e.setEchoMode(QLineEdit.EchoMode.Password)
            e.setMinimumWidth(280)
        v.addWidget(field(t("ui.lock.new_pin", n=MIN_PIN), first))
        v.addWidget(field(t("ui.lock.new_pin_again"), again))
        v.addWidget(label(t("ui.lock.pin_forgot"), "Hint", wrap=True))
        err = label("", wrap=True)
        err.setStyleSheet(f"color: {theme.TOKENS['fix']}; font-weight: 600;")
        v.addWidget(err)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = button(t("ui.common.cancel"), kind="ghost"), button(t("ui.common.ok"), kind="primary")
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)

        def accept() -> None:
            if len(first.text()) < MIN_PIN:
                err.setText(t("ui.lock.pin_short", n=MIN_PIN))
            elif first.text() != again.text():
                err.setText(t("ui.lock.pin_mismatch"))
            else:
                dlg.accept()

        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(accept)
        again.returnPressed.connect(accept)
        return first.text() if dlg.exec() == QDialog.DialogCode.Accepted else None

    def _line(self, key: str) -> QLineEdit:
        e = QLineEdit(str(self.ctx.setting(key) or ""))
        e.editingFinished.connect(lambda: self.ctx.set_setting(key, e.text().strip()))
        return e

    def _pick_logo(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, t("ui.set.logo_pick"), "", "Bilder (*.png *.jpg *.jpeg *.svg)")
        if fn:
            dest = paths.sub("branding") / ("logo" + Path(fn).suffix.lower())
            try:
                shutil.copyfile(fn, dest)
            except OSError as e:
                QMessageBox.warning(self, t("ui.app.name"), str(e))
                return
            self._set_logo(str(dest))

    def _set_logo(self, path: str, save: bool = True) -> None:
        if save:
            self.ctx.set_setting("branding.logo", path)
            self.ctx.settings_changed.emit()
        pm = QPixmap(path) if path else QPixmap()
        if pm.isNull():
            self.logo.setPixmap(theme.pixmap("image", "muted", 32))
        else:
            self.logo.setPixmap(pm.scaled(140, 60, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))

    def _pick_accent(self) -> None:
        c = QColorDialog.getColor(QColor(str(self.ctx.setting("branding.accent"))), self, t("ui.set.accent"))
        if c.isValid():
            self._set_accent(c.name())

    def _set_accent(self, hex_colour: str) -> None:
        self.ctx.set_setting("branding.accent", hex_colour)
        theme.set_accent(hex_colour)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.qss())
        self._paint_accent()
        self.ctx.settings_changed.emit()

    def _paint_accent(self) -> None:
        c = str(self.ctx.setting("branding.accent") or "#22A699")
        self.accent_btn.setStyleSheet(f"background: {c}; border: 2px solid {theme.TOKENS['line']}; border-radius: 10px;")

    def _lang_changed(self, _: int) -> None:
        self.ctx.set_setting("app.language", self.lang.currentData())
        QMessageBox.information(self, t("ui.set.language"), t("ui.set.restart_needed"))

    def _busy(self) -> bool:
        """True (with a message) while a test runs: the data folder and the database are not touched then."""
        if self.ctx.live is not None:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.set.busy_test"))
            return True
        return False

    def _move_folder(self, dest: str | None = None) -> bool:
        """A real move: everything is copied and checked, the program closes, and the old folder
        is emptied at the next start (``storage.privacy.finish_move``). Before, the old folder was left
        behind as a full copy that nothing ever removed."""
        if self.ctx.demo or self._busy():     # the demo folder lives inside the real one and moves with it
            return False
        d = dest or QFileDialog.getExistingDirectory(self, t("ui.set.move_folder"), str(paths.data_dir()))
        if not d:
            return False
        r = QMessageBox.question(self, t("ui.set.move_folder"), t("ui.set.move_confirm", path=d))
        if r != QMessageBox.StandardButton.Yes:
            return False
        from lopanalys.storage import privacy
        old = paths.real_data_dir()
        try:
            privacy.move_data_folder(self.ctx.store.db, d)
        except Exception as e:
            log.exception("moving the data folder failed")
            QMessageBox.warning(self, t("ui.app.name"), str(e))
            return False
        mark_moved(str(old), d)
        QMessageBox.information(self, t("ui.set.move_folder"), t("ui.set.move_done", path=d))
        if dest is None:
            self.window().close()      # nothing more is written to the old folder
        return True

    def ask_password(self, title: str, new: bool) -> str | None:
        """A password dialog. ``new``: typed twice, with the minimum length and the warning about losing it."""
        from lopanalys.storage import privacy
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)
        v.addWidget(label(title, "SectionTitle"))
        if new:
            v.addWidget(label(t("ui.set.backup_password_hint", n=privacy.MIN_PASSWORD), "Hint", wrap=True))
        first, again = QLineEdit(), QLineEdit()
        for e in (first, again):
            e.setEchoMode(QLineEdit.EchoMode.Password)
            e.setMinimumWidth(320)
        v.addWidget(field(t("ui.set.password"), first))
        if new:
            v.addWidget(field(t("ui.set.password_again"), again))
        err = label("", wrap=True)
        err.setStyleSheet(f"color: {theme.TOKENS['fix']}; font-weight: 600;")
        v.addWidget(err)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = button(t("ui.common.cancel"), kind="ghost"), button(t("ui.common.ok"), kind="primary")
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)

        def accept() -> None:
            if new and len(first.text()) < privacy.MIN_PASSWORD:
                err.setText(t("storage.error.password_short", n=privacy.MIN_PASSWORD))
            elif new and first.text() != again.text():
                err.setText(t("ui.set.password_mismatch"))
            elif first.text():
                dlg.accept()

        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(accept)
        first.returnPressed.connect(accept if not new else again.setFocus)
        again.returnPressed.connect(accept)
        return first.text() if dlg.exec() == QDialog.DialogCode.Accepted else None

    def backup(self, dest: str | None = None, password: str | None = None) -> bool:
        """An encrypted backup of the database and the key frames (not the large recordings)."""
        from lopanalys.storage import privacy
        name = f"lopanalys-backup-{datetime.now():%Y%m%d-%H%M}{privacy.BACKUP_SUFFIX}"
        fn = dest or QFileDialog.getSaveFileName(self, t("ui.set.backup"), str(Path.home() / name),
                                                 f"Tröskel (*{privacy.BACKUP_SUFFIX})")[0]
        if not fn:
            return False
        if not fn.lower().endswith(privacy.BACKUP_SUFFIX):
            fn += privacy.BACKUP_SUFFIX
        password = password or self.ask_password(t("ui.set.backup_password"), new=True)
        if not password:
            return False
        try:
            self.ctx.store.backup(Path(fn), password)
        except Exception as e:
            log.exception("backup failed")
            QMessageBox.warning(self, t("ui.app.name"), str(e))
            return False
        QMessageBox.information(self, t("ui.set.backup"), t("ui.set.backup_done", path=fn))
        return True

    def restore(self, src: str | None = None, password: str | None = None) -> bool:
        """Replace everything with a backup (an encrypted one, or a zip from before the encrypted backup)."""
        from lopanalys.storage import privacy
        if self._busy():
            return False
        fn = src or QFileDialog.getOpenFileName(self, t("ui.set.restore"), str(Path.home()),
                                                f"Tröskel (*{privacy.BACKUP_SUFFIX} *.zip)")[0]
        if not fn:
            return False
        if privacy.is_encrypted(fn) and not password:
            password = self.ask_password(t("ui.set.restore_password"), new=False)
            if not password:
                return False
        r = QMessageBox.question(self, t("ui.set.restore"), t("ui.set.restore_confirm", path=fn))
        if r != QMessageBox.StandardButton.Yes:
            return False
        self.ctx.closing.emit()                 # pending changes on the results page are written first
        try:
            self.ctx.store.restore(Path(fn), password)
        except Exception as e:
            log.exception("restore failed")
            QMessageBox.warning(self, t("ui.app.name"), str(e))
            return False
        finally:
            self.ctx.athletes_changed.emit()
            self.ctx.sessions_changed.emit()
            self.ctx.settings_changed.emit()
        QMessageBox.information(self, t("ui.set.restore"), t("ui.set.restore_done"))
        return True

    def ask_export_athlete(self) -> tuple[bool, int | None]:
        """(go on, athlete id or None for everyone): the warning and the choice before a CSV export."""
        dlg = QDialog(self)
        dlg.setWindowTitle(t("ui.set.export"))
        v = QVBoxLayout(dlg)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)
        v.addWidget(label(t("ui.set.export_who"), "SectionTitle"))
        warn = label(t("ui.set.export_warn"), wrap=True)
        warn.setMinimumWidth(420)
        v.addWidget(warn)
        who = QComboBox()
        who.addItem(t("ui.set.export_all"), None)
        for a in self.ctx.store.athletes():
            who.addItem(a.name, a.id)
        v.addWidget(who)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = button(t("ui.common.cancel"), kind="ghost"), button(t("ui.set.export"), "upload", kind="primary")
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)
        return dlg.exec() == QDialog.DialogCode.Accepted, who.currentData()

    def export_csv(self, dest: str | None = None, athlete_id: int | None = None) -> bool:
        """Tests and stages as one CSV (semicolon, decimal comma: opens directly in Swedish Excel), for
        everyone or one person. The file is not encrypted: the coach is told, and it is registered."""
        if dest is None:
            go, athlete_id = self.ask_export_athlete()
            if not go:
                return False
        fn = dest or QFileDialog.getSaveFileName(self, t("ui.set.export"), str(Path.home() / "lopanalys-tester.csv"),
                                                 "CSV (*.csv)")[0]
        if not fn:
            return False
        st = self.ctx.store
        try:
            with open(fn, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f, delimiter=";").writerows(services.export_rows(st, athlete_id))
            st.db.add_saved_file("export", fn, athlete_id=athlete_id)
        except Exception as e:
            log.exception("export failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.copy", detail=str(e)))
            return False
        QMessageBox.information(self, t("ui.set.export"), t("ui.set.export_done", path=fn) + "\n\n" + t("ui.set.export_warn"))
        return True
