"""The form for the swimming test (#666): the coach writes in the 400 m and the 200 m time, the date and the pool.

The CSS test (assessments.css) has no measurements: it is saved as a manual session (services.save_css_test)
and shown in the history, where it opens in this form again. The training plan takes swimming's pace per
100 m from the athlete's newest CSS test (services.swim_capacity). When the athlete's saved plan took swimming
from an older test, or from none, saving offers to lay the rest of the plan on this one (services.apply_retest).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QComboBox, QDateEdit, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from lopanalys.assessments import css
from lopanalys.core.i18n import t
from lopanalys.core.models import Athlete, Session
from lopanalys.ui import services, theme
from lopanalys.ui.context import AppContext
from lopanalys.ui.widgets.common import button, field, label

log = logging.getLogger(__name__)
POOLS = (25, 50)


def time_text(seconds: float) -> str:
    """Seconds as the coach writes a time: '6:40' or '6:40.5'."""
    m, s = divmod(round(float(seconds), 1), 60)
    return f"{int(m)}:{int(s):02d}" if s == int(s) else f"{int(m)}:{s:04.1f}"


class CssTestDialog(QDialog):
    """Write in, or correct, one CSS test. ``saved`` is the stored session after Spara."""

    def __init__(self, ctx: AppContext, athlete: Athlete, session: Session | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx, self.athlete, self.session = ctx, athlete, session
        self.saved: Session | None = None
        self.setWindowTitle(t("ui.css.title"))
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)
        v.addWidget(label(t("ui.css.title_for", name=athlete.name), "SectionTitle"))
        v.addWidget(label(t("ui.css.intro"), "Hint", wrap=True))
        self.t400 = QLineEdit()
        self.t200 = QLineEdit()
        for e in (self.t400, self.t200):
            e.setPlaceholderText(t("ui.css.time_placeholder"))
            e.textChanged.connect(self._changed)
        times = QHBoxLayout()
        times.addWidget(field(t("ui.css.t400"), self.t400), 1)
        times.addWidget(field(t("ui.css.t200"), self.t200), 1)
        v.addLayout(times)
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy-MM-dd")
        self.day.setMaximumDate(QDate.currentDate())
        self.pool = QComboBox()
        for m in POOLS:
            self.pool.addItem(t("ui.css.pool_m", m=m), m)
        row = QHBoxLayout()
        row.addWidget(field(t("ui.css.date"), self.day), 1)
        row.addWidget(field(t("ui.css.pool"), self.pool), 1)
        v.addLayout(row)
        self.notes = QLineEdit()
        v.addWidget(field(t("ui.css.notes"), self.notes))
        self.pace = label("", "SectionTitle")
        v.addWidget(self.pace)
        self.error = label("", wrap=True)
        self.error.setStyleSheet(f"color: {theme.TOKENS['fix']}; font-weight: 600;")
        v.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(t("ui.common.cancel"))
        cancel.clicked.connect(self.reject)
        self.save_btn = button(t("ui.common.save"), "check", kind="primary")
        self.save_btn.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_btn)
        v.addLayout(buttons)
        old = services.css_test_of(session)
        if old is not None:
            self.t400.setText(time_text(old.t400_s))
            self.t200.setText(time_text(old.t200_s))
            if old.tested:
                self.day.setDate(QDate(old.tested.year, old.tested.month, old.tested.day))
            self.pool.setCurrentIndex(max(0, self.pool.findData(old.pool_m)))
            self.notes.setText(session.notes if session is not None else "")
        self._changed()

    def _times(self) -> tuple[float | None, float | None]:
        return css.parse_time(self.t400.text()), css.parse_time(self.t200.text())

    def _changed(self, *_: Any) -> None:
        """The pace as soon as the two times give one; the error first when Spara is pressed."""
        t400, t200 = self._times()
        self.error.setText("")
        if css.check(t400, t200) is None:
            test = css.make(t400, t200)
            self.pace.setText(t("ui.css.result", pace=css.pace_str(test.css_s100)))
        else:
            self.pace.setText("")

    def test(self) -> css.CssTest | None:
        """The test as the form stands; None, with the error shown, when the times give no CSS."""
        t400, t200 = self._times()
        err = css.check(t400, t200)
        if err:
            self.error.setText(t(err))
            return None
        d = self.day.date()
        return css.make(t400, t200, date(d.year(), d.month(), d.day()), int(self.pool.currentData()))

    def save(self) -> None:
        test = self.test()
        if test is None:
            return
        try:
            self.saved = services.save_css_test(self.ctx.store, self.athlete, test, self.session,
                                                self.notes.text().strip())
        except Exception as e:
            log.exception("saving the CSS test failed")
            self.error.setText(t("ui.err.db", detail=str(e)))
            return
        self.ctx.sessions_changed.emit()
        self._offer_retest()
        self.accept()

    def _offer_retest(self) -> None:
        """The athlete's plan takes swimming from an older test (or none): offer to lay the rest of it on this one."""
        s = self.saved
        try:
            if s is None or not any(c.id == s.id for c in services.retest_candidates(self.ctx.store, self.athlete)):
                return
        except Exception:
            log.exception("looking for the athlete's plan failed")
            return
        ans = QMessageBox.question(self, t("ui.plan.title"), services.retest_offer_text(s))
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            services.apply_retest(self.ctx.store, self.athlete, s)
        except Exception as e:
            log.exception("retest failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.save_failed", detail=str(e)))
            return
        QMessageBox.information(self, t("ui.plan.title"), services.retest_done_text(s, date.today()))
