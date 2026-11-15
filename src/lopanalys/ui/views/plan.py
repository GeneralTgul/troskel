"""Träningsschema: the athlete's training plan, built on a test.

The form on the left states what the plan engine needs (the test, the level, the time per weekday,
the goal and milestones or a length); the overview on the right shows the plan it gives. The plan
is saved per athlete, printed, saved as PDF and as a calendar file, and mailed to the athlete (both
files, views/send_mail.py). Nothing leaves the program before every box of the health declaration
is ticked.

The plan rolls (plan.rolling): only the next four weeks have workouts, the rest is a sketch. A row
under the overview says how far the workouts run, with *Förnya*, which details the next weeks on
what is saved without filling in anything; the banner offers it when they run out within a week.

The form follows the athlete form: fields start empty, *Spara* is always visible below the
scroll area, and unsaved changes are asked about before they are thrown away.
"""
from __future__ import annotations

import logging
import re
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit,
                               QMessageBox, QToolButton, QVBoxLayout, QWidget)

from lopanalys.core.i18n import t
from lopanalys.core.models import Athlete
from lopanalys.plan import RUNNING, Goal, Plan, PlanRequest, Slot, build_plan
from lopanalys.plan import rolling
from lopanalys.plan.engine import horizon_for
from lopanalys.plan.models import LEVELS, WEEKDAY_KEYS, ZONES
from lopanalys.plan.resolve import targets
from lopanalys.plan.text import hours, start_line
from lopanalys.ui import services, theme
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.athletes import DateField, NumberEdit, SelectAllEdit
from lopanalys.ui.widgets.common import Card, PageHeader, button, clear_layout, field, label, scrolled
from lopanalys.ui.widgets.plan_overview import PlanChart
from lopanalys.ui.widgets.tiles import MetricTile

log = logging.getLogger(__name__)

MIN_SLOT, MAX_SLOT = 20, 480      # minutes in one day
MAX_WEEKS = 104
_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.]?(\d{2}))?$")


def parse_clock(text: str) -> time | None:
    """A typed clock time: ``18``, ``18:30``, ``18.30`` or ``1830``. Empty is no time; ValueError otherwise."""
    s = text.strip()
    if not s:
        return None
    m = _TIME_RE.match(s)
    if not m:
        raise ValueError(text)
    return time(int(m.group(1)), int(m.group(2) or 0))     # ValueError for 25:00


def _day(d: date) -> str:
    return t("plan.fmt.day", day=d.day, month=t(f"plan.month.{d.month}"))


class MilestoneRow(QWidget):
    """One milestone or training race: name, date, distance and which of the two it is."""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.name = QLineEdit()
        self.name.setPlaceholderText(t("ui.plan.goal_name"))
        self.date = DateField(plan=True)
        self.km = NumberEdit("km", decimals=1)
        self.kind = QComboBox()
        self.kind.addItem(t("ui.plan.milestone_b"), "B")
        self.kind.addItem(t("ui.plan.milestone_c"), "C")
        rm = QToolButton()
        rm.setIcon(theme.icon("x", "muted"))
        rm.setToolTip(t("ui.plan.milestone_remove"))
        rm.setCursor(Qt.CursorShape.PointingHandCursor)
        rm.clicked.connect(lambda: self.removed.emit(self))
        g.addWidget(self.name, 0, 0, 1, 2)
        g.addWidget(self.kind, 0, 2)
        g.addWidget(rm, 0, 3)
        g.addWidget(self.date, 1, 0, 1, 2)
        g.addWidget(self.km, 1, 2)
        for w in (self.name, self.date.edit, self.km):
            w.textChanged.connect(self.changed)
        self.kind.currentIndexChanged.connect(self.changed)

    def snapshot(self) -> tuple:
        return (self.name.text().strip(), self.date.text().strip(), self.km.value(), self.kind.currentData())


class PlanPrintDialog(QDialog):
    """The printout as it will look, to read only: printing and saving are done from the page."""

    def __init__(self, html: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("ui.plan.print_preview"))
        self.resize(1000, 900)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        from lopanalys.ui.widgets.report_view import ReportView
        self.web = ReportView(self)
        v.addWidget(self.web, 1)
        self.web.show_html(html)
        row = QHBoxLayout()
        row.addStretch(1)
        close = button(t("ui.common.close"), kind="primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)


class PlanView(QWidget):
    back = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.athlete: Athlete | None = None
        self.stored: services.StoredPlan | None = None
        self.plan: Plan | None = None          # the plan the overview shows
        self._plan_snap: tuple = ()            # the inputs that plan was built from
        self._loaded: tuple = ()
        self._retest: Any = None               # the newer test the plan can be laid on
        self._filling = False
        self.milestones: list[MilestoneRow] = []
        self._mailing: Any = None              # the mail on its way
        ctx.athlete_erased.connect(self._forget)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 26, 32, 28)
        outer.setSpacing(14)
        self.header = PageHeader(t("ui.plan.title"), "")
        b = button(t("ui.common.back"), "chevron-left", kind="ghost")
        b.clicked.connect(self.back)
        self.header.add_action(b)
        outer.addWidget(self.header)

        self.banner = Card(flat=True, pad=14)
        brow = QHBoxLayout()
        self.banner_text = label("", wrap=True)
        self.banner_text.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        brow.addWidget(self.banner_text, 1)
        self.retest_btn = button(t("ui.plan.retest_apply"), "refresh", kind="primary")
        self.retest_btn.clicked.connect(self.apply_retest)
        brow.addWidget(self.retest_btn)
        self.banner_renew = button(t("ui.plan.renew"), "refresh", kind="primary")
        self.banner_renew.clicked.connect(lambda: self.renew())
        brow.addWidget(self.banner_renew)
        self.banner.body.addLayout(brow)
        self.banner.hide()
        outer.addWidget(self.banner)

        body = QHBoxLayout()
        body.setSpacing(20)
        outer.addLayout(body, 1)
        self.form = Card(pad=24)
        self._build_form(self.form.body)
        self.form_scroll = scrolled(self.form)
        self.form_scroll.setFixedWidth(450)
        body.addWidget(self.form_scroll)
        self.overview = Card(pad=24)
        self._build_overview(self.overview.body)
        body.addWidget(scrolled(self.overview), 1)

        self.error = label("", wrap=True)
        self.error.setStyleSheet(f"color: {theme.TOKENS['fix']}; font-weight: 600;")
        self.error.hide()
        outer.addWidget(self.error)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.del_btn = button(t("ui.plan.delete"), "trash", kind="ghost")
        self.del_btn.clicked.connect(self.delete)
        row.addWidget(self.del_btn)
        self.mail_note = label("", "Hint", wrap=True)     # when it was mailed, or why it cannot be
        row.addWidget(self.mail_note, 1)
        self.cancel_btn = button(t("ui.common.cancel"), "x", kind="ghost")
        self.cancel_btn.clicked.connect(self.revert)
        self.preview_btn = button(t("ui.plan.preview"), "eye")
        self.preview_btn.clicked.connect(lambda: self.preview())
        self.ics_btn = button(t("ui.plan.ics"), "calendar")
        self.ics_btn.clicked.connect(lambda: self.save_ics())
        self.print_btn = button(t("ui.res.print"), "printer")
        self.print_btn.clicked.connect(lambda: self.print_plan())
        self.pdf_btn = button(t("ui.res.pdf"), "pdf")
        self.pdf_btn.clicked.connect(lambda: self.save_pdf())
        self.mail_btn = button(t("ui.res.mail"), "mail")
        self.mail_btn.clicked.connect(lambda: self.send_to_athlete())
        self.save_btn = button(t("ui.common.save"), "save", kind="primary")
        self.save_btn.clicked.connect(lambda: self.save())
        for w in (self.cancel_btn, self.preview_btn, self.ics_btn, self.print_btn, self.pdf_btn, self.mail_btn,
                  self.save_btn):
            row.addWidget(w)
        outer.addLayout(row)

    # ------------------------------------------------------------------ building the page
    def _build_form(self, v: QVBoxLayout) -> None:
        v.setSpacing(14)
        v.addWidget(label(t("ui.plan.sec.basis"), "SectionTitle"))
        self.test = QComboBox()
        v.addWidget(field(t("ui.plan.test"), self.test, t("ui.plan.test_hint")))
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        self.level = QComboBox()
        self.level.addItem(t("ui.plan.level_choose"), "")
        for lv in LEVELS:
            self.level.addItem(t(f"plan.level.{lv}"), lv)
        self.recent = NumberEdit(t("ui.plan.unit.min_week"))
        g.addWidget(field(t("ui.plan.level"), self.level), 0, 0)
        g.addWidget(field(t("ui.plan.recent"), self.recent), 0, 1)
        v.addLayout(g)
        v.addWidget(label(t("ui.plan.recent_hint"), "Hint", wrap=True))

        v.addSpacing(6)
        v.addWidget(label(t("ui.plan.sec.time"), "SectionTitle"))
        v.addWidget(label(t("ui.plan.time_hint"), "Hint", wrap=True))
        dg = QGridLayout()
        dg.setHorizontalSpacing(12)
        dg.setVerticalSpacing(8)
        self.minutes: list[NumberEdit] = []
        self.clocks: list[QLineEdit] = []
        for i, key in enumerate(WEEKDAY_KEYS):
            dg.addWidget(label(t(f"ui.plan.day.{key}")), i, 0)
            m = NumberEdit("min")
            m.setObjectName(f"minutes_{key}")
            c = SelectAllEdit()
            c.setPlaceholderText(t("ui.plan.clock_placeholder"))
            c.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9:.]{0,5}"), c))
            dg.addWidget(m, i, 1)
            dg.addWidget(c, i, 2)
            self.minutes.append(m)
            self.clocks.append(c)
        dg.setColumnStretch(0, 2)
        dg.setColumnStretch(1, 3)
        dg.setColumnStretch(2, 3)
        v.addLayout(dg)

        v.addSpacing(6)
        v.addWidget(label(t("ui.plan.sec.goal"), "SectionTitle"))
        self.mode = QComboBox()
        self.mode.addItem(t("ui.plan.mode_goal"), "goal")
        self.mode.addItem(t("ui.plan.mode_weeks"), "weeks")
        v.addWidget(self.mode)
        self.goal_box = QWidget()
        gv = QVBoxLayout(self.goal_box)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.setSpacing(12)
        gg = QGridLayout()
        gg.setHorizontalSpacing(14)
        gg.setVerticalSpacing(12)
        self.goal_name = QLineEdit()
        self.goal_date = DateField(plan=True)
        self.goal_km = NumberEdit("km", decimals=1)
        self.goal_target = QLineEdit()
        self.goal_target.setPlaceholderText(t("ui.plan.goal_target_placeholder"))
        gg.addWidget(field(t("ui.plan.goal_name"), self.goal_name), 0, 0, 1, 2)
        gg.addWidget(field(t("ui.plan.goal_date"), self.goal_date), 1, 0, 1, 2)
        gg.addWidget(field(t("ui.plan.goal_km"), self.goal_km), 2, 0)
        gg.addWidget(field(t("ui.plan.goal_target"), self.goal_target), 2, 1)
        gv.addLayout(gg)
        gv.addWidget(label(t("ui.plan.milestones"), "Hint", wrap=True))
        self.ms_lay = QVBoxLayout()
        self.ms_lay.setSpacing(12)
        gv.addLayout(self.ms_lay)
        self.add_ms = button(t("ui.plan.milestone_add"), "plus", kind="ghost")
        self.add_ms.clicked.connect(lambda: self.add_milestone())
        gv.addWidget(self.add_ms, 0, Qt.AlignmentFlag.AlignLeft)
        v.addWidget(self.goal_box)
        self.weeks = NumberEdit(t("plan.pdf.unit.weeks"))
        self.weeks_box = field(t("ui.plan.weeks"), self.weeks, t("ui.plan.weeks_hint"))
        v.addWidget(self.weeks_box)
        self.start = DateField(plan=True)
        v.addWidget(field(t("ui.plan.start"), self.start, t("ui.plan.start_hint")))

        v.addSpacing(6)
        v.addWidget(label(t("ui.plan.sec.health"), "SectionTitle"))
        v.addWidget(label(t("ui.plan.health_hint"), "Hint", wrap=True))
        self.health: dict[str, QCheckBox] = {}
        for item in services.HEALTH_ITEMS:
            cb = QCheckBox()
            cb.setObjectName(f"health_{item}")
            row = QHBoxLayout()      # a check box does not wrap its text: the text is a label beside it
            row.setSpacing(10)
            row.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(label(t(f"ui.plan.health.{item}"), wrap=True), 1)
            v.addLayout(row)
            cb.toggled.connect(self._changed)
            self.health[item] = cb
        self.health_note = label("", "Hint", wrap=True)
        v.addWidget(self.health_note)
        v.addStretch(1)

        for w in (self.recent, self.goal_name, self.goal_date.edit, self.goal_km, self.goal_target, self.weeks,
                  self.start.edit, *self.minutes, *self.clocks):
            w.textChanged.connect(self._changed)
        for cb in (self.test, self.level):
            cb.currentIndexChanged.connect(self._changed)
        self.mode.currentIndexChanged.connect(self._mode_changed)

    def _build_overview(self, v: QVBoxLayout) -> None:
        v.setSpacing(14)
        self.empty = label(t("ui.plan.empty"), "PageSubtitle", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.empty, 1)
        self.shown = QWidget()
        s = QVBoxLayout(self.shown)
        s.setContentsMargins(0, 0, 0, 0)
        s.setSpacing(14)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.headline = label("", "PageTitle")
        self.headline.setStyleSheet("font-size: 22px;")
        self.subline = label("", "Hint", wrap=True)
        # Where the weekly time starts and where it grows to (plan.text.start_line).
        self.startline = label("", "Hint", wrap=True)
        col.addWidget(self.headline)
        col.addWidget(self.subline)
        col.addWidget(self.startline)
        hrow = QHBoxLayout()               # how far the workouts run, and *Förnya*
        hrow.setSpacing(8)
        self.horizon = label("", "Hint", wrap=True)
        hrow.addWidget(self.horizon, 1)
        self.renew_btn = button(t("ui.plan.renew"), "refresh", kind="ghost")
        self.renew_btn.clicked.connect(lambda: self.renew())
        hrow.addWidget(self.renew_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(hrow)
        top.addLayout(col, 1)
        self.full_btn = button(t("ui.plan.print_preview"), "eye", kind="ghost")
        self.full_btn.clicked.connect(lambda: self.show_printout())
        top.addWidget(self.full_btn, 0, Qt.AlignmentFlag.AlignTop)
        s.addLayout(top)
        self.stale = label(t("ui.plan.stale"), wrap=True)
        self.stale.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        self.stale.hide()
        s.addWidget(self.stale)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.t_weeks = MetricTile(t("plan.pdf.tile.weeks"), t("plan.pdf.unit.weeks"), "calendar")
        self.t_sessions = MetricTile(t("plan.pdf.tile.sessions"), t("plan.pdf.unit.sessions"), "run")
        self.t_volume = MetricTile(t("ui.plan.tile.volume"), t("plan.pdf.unit.hours"), "timer")
        self.t_easy = MetricTile(t("plan.pdf.tile.easy"), "%", "heart")
        for w in (self.t_weeks, self.t_sessions, self.t_volume, self.t_easy):
            w.setFixedHeight(112)
            w.setMinimumWidth(118)
            tiles.addWidget(w)
        s.addLayout(tiles)
        self.chart = PlanChart()
        s.addWidget(self.chart, 1)
        self.zone_lay = QGridLayout()
        self.zone_lay.setHorizontalSpacing(16)
        self.zone_lay.setVerticalSpacing(6)
        s.addLayout(self.zone_lay)
        self.notes = label("", "Hint", wrap=True)
        s.addWidget(self.notes)
        v.addWidget(self.shown, 1)
        self.shown.hide()

    # ------------------------------------------------------------------ the form's state
    def _inputs(self) -> tuple:
        """Everything the plan is built from. Two equal tuples give the same plan."""
        return (self.test.currentData(), self.level.currentData(), self.recent.value(),
                tuple((m.value(), c.text().strip()) for m, c in zip(self.minutes, self.clocks)),
                self.mode.currentData(), self.goal_name.text().strip(), self.goal_date.text().strip(),
                self.goal_km.value(), self.goal_target.text().strip(), tuple(r.snapshot() for r in self.milestones),
                self.weeks.value(), self.start.text().strip())

    def _health(self) -> tuple:
        return tuple(cb.isChecked() for cb in self.health.values())

    def dirty(self) -> bool:
        return self.athlete is not None and (self._inputs(), self._health()) != self._loaded

    def _changed(self, *_: Any) -> None:
        if self._filling:
            return
        self.cancel_btn.setEnabled(self.dirty())
        self.stale.setVisible(self.plan is not None and self._inputs() != self._plan_snap)
        self._health_text()

    def _mode_changed(self, *_: Any) -> None:
        goal = self.mode.currentData() == "goal"
        self.goal_box.setVisible(goal)
        self.weeks_box.setVisible(not goal)
        self._changed()

    def _health_text(self) -> None:
        h = self.stored.health if self.stored else {}
        if all(self._health()) and services.health_valid(h):
            until = date.fromisoformat(h["date"]) + timedelta(days=services.HEALTH_VALID_DAYS)
            self.health_note.setText(t("ui.plan.health_valid", date=h["date"], until=until.isoformat()))
        elif h.get("date") and not services.health_valid(h) and set(services.HEALTH_ITEMS) <= set(h.get("confirmed", [])):
            self.health_note.setText(t("ui.plan.health_expired", date=h["date"]))
        else:
            self.health_note.setText("")

    def add_milestone(self, goal: Goal | None = None) -> MilestoneRow:
        r = MilestoneRow()
        if goal is not None:
            r.name.setText(goal.name)
            r.date.set_date(goal.date)
            r.km.setValue(goal.distance_km or 0)
            r.kind.setCurrentIndex(max(0, r.kind.findData(goal.priority)))
        r.changed.connect(self._changed)
        r.removed.connect(self._remove_milestone)
        self.milestones.append(r)
        self.ms_lay.addWidget(r)
        theme.polish_inputs(r)
        self._changed()
        return r

    def _remove_milestone(self, r: MilestoneRow) -> None:
        self.milestones.remove(r)
        self.ms_lay.removeWidget(r)
        r.deleteLater()
        self._changed()

    def _forget(self, athlete_id: int) -> None:
        """The person shown here is being erased: the page lets go of them."""
        if self.athlete is not None and self.athlete.id == athlete_id:
            self.athlete, self.stored, self.plan = None, None, None
            self._loaded = ()

    # ------------------------------------------------------------------ open and fill
    def open(self, athlete: Athlete, session_id: int | None = None) -> None:
        """Show the athlete's saved plan, or an empty form with ``session_id`` (or the newest test) chosen."""
        self.athlete = athlete
        self.header.subtitle.setText(athlete.name)
        self.header.subtitle.show()
        try:
            self.stored = services.load_plan(self.ctx.store, athlete)
        except Exception as e:
            log.exception("reading the plan failed")
            self.stored = None
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.db", detail=str(e)))
        self._fill(session_id)

    def revert(self) -> None:
        """*Avbryt*: back to the saved plan (an empty form when none is saved)."""
        if self.athlete is not None:
            self._fill(None)

    def _fill(self, session_id: int | None) -> None:
        a, st = self.athlete, self.stored
        assert a is not None and a.id is not None
        self._filling = True
        self.error.hide()
        self.test.clear()
        for s in services.plan_tests(self.ctx.store, a.id):
            self.test.addItem(f"{s.protocol_name}  ·  {s.started.astimezone():%Y-%m-%d}", s.id)
        self.test.addItem(t("ui.plan.no_test"), None)
        for r in list(self.milestones):
            self._remove_milestone(r)
        for w in (self.recent, self.goal_name, self.goal_date.edit, self.goal_km, self.goal_target, self.weeks,
                  self.start.edit, *self.minutes, *self.clocks):
            w.clear()
        req = st.plan.request if st else None
        if req is None:
            self.test.setCurrentIndex(max(0, self.test.findData(session_id)) if session_id else 0)
            self.level.setCurrentIndex(0)
            self.mode.setCurrentIndex(0)
        else:
            self.test.setCurrentIndex(self.test.findData(st.session_id))
            self.level.setCurrentIndex(max(0, self.level.findData(req.level)))
            self.recent.setValue(req.recent_weekly_minutes or 0)
            for s in req.availability:
                self.minutes[s.weekday].setValue(s.max_minutes)
                self.clocks[s.weekday].setText(f"{s.start:%H:%M}" if s.start else "")
            main = st.plan.goal
            self.mode.setCurrentIndex(0 if main else 1)
            if main:
                self.goal_name.setText(main.name)
                self.goal_date.set_date(main.date)
                self.goal_km.setValue(main.distance_km or 0)
                self.goal_target.setText(main.target)
            for g in sorted(req.goals, key=lambda g: g.date):
                if g is not main:
                    self.add_milestone(g)
            self.weeks.setValue(req.duration_weeks or 0)
            self.start.set_date(req.start)
        ok = bool(st and services.health_valid(st.health))
        for cb in self.health.values():
            cb.setChecked(ok)
        self._filling = False
        self._mode_changed()
        self._loaded = (self._inputs(), self._health())
        self.plan = st.plan if st else None
        self._plan_snap = self._inputs() if st else ()
        self.del_btn.setVisible(st is not None)
        self._show()
        self._fill_banner()
        self._refresh_mail()
        self._changed()

    def _refresh_mail(self) -> None:
        """*Skicka till idrottaren*: grey with the reason when nothing can be mailed, and when it was last sent."""
        a = self.athlete
        try:
            reason = services.mail_block_reason(self.ctx.store, a)
            sent = services.plan_mailed_text(self.ctx.store, self.stored)
        except Exception:
            log.exception("mail check failed")
            reason, sent = t("ui.mail.block.not_set_up"), ""
        self.mail_btn.setEnabled(reason is None and self._mailing is None)
        self.mail_btn.setToolTip(reason or "")
        self.mail_note.setText(sent or (t("ui.mail.blocked", reason=reason) if reason else ""))

    def showEvent(self, e: Any) -> None:   # back from Idrottare or Inställningar: the button follows
        super().showEvent(e)
        if self.athlete is not None:
            self._refresh_mail()

    def _fill_banner(self) -> None:
        self._retest = None
        a = self.athlete
        if a is not None and self.stored is not None:
            try:
                self._retest = services.retest_candidate(self.ctx.store, a, self.stored)
            except Exception:
                log.exception("looking for a newer test failed")
        renew = self.stored is not None and rolling.renew_soon(self.stored.plan, date.today())
        self.retest_btn.setVisible(self._retest is not None)
        self.banner_renew.setVisible(self._retest is None and renew)
        if self._retest is not None:          # a new test renews too (services.apply_retest)
            s = self._retest
            self.banner_text.setText(t("ui.plan.retest_offer", test=s.protocol_name,
                                       date=f"{s.started.astimezone():%Y-%m-%d}"))
        elif renew:
            assert self.stored is not None
            plan = self.stored.plan
            last = plan.detailed_until
            to = horizon_for(plan.start, len(plan.weeks), date.today())
            self.banner_text.setText(t("ui.plan.renew_offer", date=last.sunday.isoformat(), week=to))
        else:
            self.banner.hide()
            return
        self.banner.show()

    def _show_horizon(self) -> None:
        """*Detaljerat till och med vecka N (datum). [Förnya]*: shown while weeks after it are a sketch."""
        plan = self.plan
        sketch = plan is not None and bool(plan.sketch_weeks)
        self.horizon.setVisible(sketch)
        self.renew_btn.setVisible(sketch and self.stored is not None and plan is self.stored.plan)
        if sketch:
            assert plan is not None
            last = plan.detailed_until
            self.horizon.setText(t("ui.plan.horizon", week=last.index, date=last.sunday.isoformat()))

    # ------------------------------------------------------------------ the overview
    def _show(self) -> None:
        plan = self.plan
        self.empty.setVisible(plan is None)
        self.shown.setVisible(plan is not None)
        self.stale.hide()
        if plan is None:
            return
        req = plan.request
        cap = req.capacity_for(RUNNING)
        goal = plan.goal
        self.headline.setText(goal.name if goal else t("plan.pdf.headline_weeks", weeks=len(plan.weeks)))
        if cap.anchors is None:
            basis = t("plan.pdf.fact.no_test")
        else:
            basis = " · ".join(x for x in (cap.source, cap.tested.isoformat() if cap.tested else "") if x) \
                or t("ui.plan.basis_max_hr")
        self.subline.setText(f"{_day(plan.start)} – {_day(plan.end)} {plan.end.year}   ·   "
                             + t("ui.plan.based_on", basis=basis))
        self.startline.setText(start_line(plan))
        self._show_horizon()
        load = [wk.minutes for wk in plan.weeks if wk.kind == "load"] or [wk.minutes for wk in plan.weeks]
        sessions = [wk.sessions for wk in plan.weeks if wk.kind == "load"]
        self.t_weeks.set_value(str(len(plan.weeks)), f"{_day(plan.start)} – {_day(plan.end)}")
        self.t_sessions.set_value(str(max(sessions)) if sessions else "–",
                                  t("plan.pdf.tile.sessions_sub", n=sum(wk.sessions for wk in plan.weeks)))
        self.t_volume.set_value(f"{hours(min(load))}–{hours(max(load))}", t("ui.plan.tile.volume_sub"))
        self.t_easy.set_value(str(round(100 * plan.easy_share)), t("ui.plan.tile.easy_sub"))
        self.chart.set_plan(plan)
        clear_layout(self.zone_lay)
        zt = targets(cap, plan.zone_system)
        for i, z in enumerate(ZONES):
            sw = label("  ")
            sw.setFixedSize(14, 14)
            sw.setStyleSheet(f"background: {theme.zone_color(i)}; border-radius: 4px;")
            name = zt[z].name if z in zt else t(f"assessments.zones.garmin.{z}.name")
            nm = label(f"{z}  {name}")
            nm.setStyleSheet("font-weight: 600;")
            self.zone_lay.addWidget(sw, i, 0)
            self.zone_lay.addWidget(nm, i, 1)
            self.zone_lay.addWidget(label(zt[z].hr_text if z in zt else "–"), i, 2)
            self.zone_lay.addWidget(label(zt[z].pace_text if z in zt else "", "Hint"), i, 3)
        self.zone_lay.setColumnStretch(3, 1)
        notes = [n.text for n in plan.notes]
        if plan.capacity_log:
            until, old = max(plan.capacity_log, key=lambda x: x[0])
            notes.append(t("ui.plan.retested_note", date=until.isoformat(),
                           before=old.tested.isoformat() if old.tested else "–"))
        self.notes.setText("\n".join(notes))
        self.notes.setVisible(bool(notes))

    # ------------------------------------------------------------------ form -> plan
    def _fail(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def collect(self) -> PlanRequest | None:
        """The form as a request, or None (with a message) when something is wrong or missing."""
        a = self.athlete
        if a is None:
            return None
        level = self.level.currentData()
        if not level:
            return self._fail(t("ui.plan.err_level"))
        slots = []
        for i, (m, c) in enumerate(zip(self.minutes, self.clocks)):
            day = t(f"ui.plan.day.{WEEKDAY_KEYS[i]}")
            v = m.value()
            if v is None:
                if c.text().strip():
                    return self._fail(t("ui.plan.err_clock_without_minutes", day=day))
                continue
            if not MIN_SLOT <= v <= MAX_SLOT:
                return self._fail(t("ui.plan.err_minutes", day=day, lo=MIN_SLOT, hi=MAX_SLOT))
            try:
                start = parse_clock(c.text())
            except ValueError:
                return self._fail(t("ui.plan.err_clock", day=day))
            slots.append(Slot(i, int(v), start=start))
        if not slots:
            return self._fail(t("plan.error.no_slots"))
        try:
            start_day = self.start.date() or date.today()
        except ValueError:
            return self._fail(t("ui.plan.err_start"))
        goals: list[Goal] = []
        weeks = None
        if self.mode.currentData() == "goal":
            rows = [(self.goal_name.text().strip(), self.goal_date, self.goal_km.value(), "A",
                     self.goal_target.text().strip())]
            rows += [(r.name.text().strip(), r.date, r.km.value(), r.kind.currentData(), "") for r in self.milestones]
            for name, df, km, prio, target in rows:
                if not name:
                    return self._fail(t("ui.plan.err_goal_name"))
                try:
                    d = df.date()
                except ValueError:
                    d = None
                if d is None:
                    return self._fail(t("ui.plan.err_goal_date", name=name))
                if prio != "A" and d >= goals[0].date:
                    return self._fail(t("ui.plan.err_milestone_late", name=name))
                goals.append(Goal(name, d, distance_km=km, priority=prio, target=target))
        else:
            w = self.weeks.value()
            if w is None or w > MAX_WEEKS:
                return self._fail(t("ui.plan.err_weeks", hi=MAX_WEEKS))
            weeks = int(w)
        sid = self.test.currentData()
        session = self.ctx.store.session(sid) if sid else None
        try:
            cap = services.plan_capacity(self.ctx.store, a, session)
        except Exception as e:
            log.exception("reading the test failed")
            return self._fail(t("ui.err.db", detail=str(e)))
        recent = self.recent.value()
        return PlanRequest(a.name, start_day, tuple(slots), (cap,), goals=tuple(goals), duration_weeks=weeks,
                           level=level, recent_weekly_minutes=int(recent) if recent else None)

    def _current_plan(self) -> Plan | None:
        """The plan of the form as it stands: the shown one while its inputs are unchanged (a saved plan
        is never rebuilt by opening it), otherwise a new one from the engine."""
        self.error.hide()
        if self.plan is not None and self._inputs() == self._plan_snap:
            return self.plan
        req = self.collect()
        if req is None:
            return None
        assert self.athlete is not None
        try:
            plan = build_plan(req, plan_id=f"p{self.athlete.id}", today=date.today())
        except ValueError as e:      # the engine's own message, e.g. the goal is too close
            return self._fail(str(e))
        if not self.start.text().strip():        # an empty start is today: the plan keeps that day
            self._filling = True
            self.start.set_date(req.start)
            self._filling = False
        self.plan, self._plan_snap = plan, self._inputs()
        self._show()
        self._changed()
        return plan

    def preview(self) -> bool:
        return self._current_plan() is not None

    def show_printout(self) -> None:
        if self.plan is None:
            return
        try:
            html = services.render_plan(self.plan, self.ctx.branding())
        except Exception as e:
            log.exception("plan printout failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.report", detail=str(e)))
            return
        PlanPrintDialog(html, self).exec()

    # ------------------------------------------------------------------ save, delete, retest
    def _health_dict(self) -> dict[str, Any]:
        old = self.stored.health if self.stored else {}
        confirmed = [k for k, cb in self.health.items() if cb.isChecked()]
        keep = services.health_valid(old) and set(confirmed) == set(old.get("confirmed", []))
        return {"confirmed": confirmed, "date": old["date"] if keep else (date.today().isoformat() if confirmed else None)}

    def save(self) -> bool:
        a = self.athlete
        plan = self._current_plan()
        if a is None or plan is None:
            return False
        health = self._health_dict()
        sid = self.test.currentData()
        try:
            row = services.save_plan(self.ctx.store, a, plan, sid, health, "saved" if self.stored is None else "changed")
        except Exception as e:
            log.exception("saving the plan failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.save_failed", detail=str(e)))
            return False
        self.stored = services.StoredPlan(plan, sid, health, row)
        self._loaded = (self._inputs(), self._health())
        self.del_btn.show()
        self._show_horizon()
        self._fill_banner()
        self._changed()
        return True

    def delete(self) -> None:
        a = self.athlete
        if a is None or a.id is None or self.stored is None:
            return
        r = QMessageBox.question(self, t("ui.plan.delete"), t("ui.plan.delete_confirm", name=a.name))
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            services.delete_plan(self.ctx.store, a.id)
        except Exception as e:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.db", detail=str(e)))
            return
        self.stored = None
        self._fill(None)

    def apply_retest(self) -> None:
        """Lay the rest of the plan on the newer test: new targets from today, every workout on its day."""
        a, s = self.athlete, self._retest
        if a is None or s is None or not self.confirm_discard():
            return
        try:
            self.stored = services.apply_retest(self.ctx.store, a, s)
        except Exception as e:
            log.exception("retest failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.save_failed", detail=str(e)))
            return
        self.stored = services.load_plan(self.ctx.store, a)
        self._fill(None)
        QMessageBox.information(self, t("ui.plan.title"), t("ui.plan.retest_done", date=date.today().isoformat()))

    def renew(self, today: date | None = None) -> bool:
        """*Förnya*: the saved plan gets its workouts up to four weeks from today; the form, the test and
        the health declaration stay as saved, and weeks that already had workouts keep them."""
        a = self.athlete
        if a is None or self.stored is None or not self.confirm_discard():
            return False
        before = self.stored.plan.horizon
        try:
            self.stored = services.renew_plan(self.ctx.store, a, today)
        except Exception as e:
            log.exception("renewing the plan failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.save_failed", detail=str(e)))
            return False
        self.stored = services.load_plan(self.ctx.store, a)
        self._fill(None)
        assert self.stored is not None
        plan = self.stored.plan
        QMessageBox.information(self, t("ui.plan.title"),
                                t("ui.plan.renew_done", week=plan.detailed_until.index) if plan.horizon != before
                                else t("ui.plan.renew_nothing"))
        return True

    def confirm_discard(self) -> bool:
        """True when nothing is unsaved, or the coach chose to throw the changes away (as the athlete form)."""
        if not self.dirty():
            return True
        r = QMessageBox.question(self, t("ui.app.name"), t("ui.athletes.unsaved"),
                                 QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                                 QMessageBox.StandardButton.Cancel)
        if r != QMessageBox.StandardButton.Ok:
            return False
        self.revert()
        return True

    def leave_ok(self) -> bool:
        return self.confirm_discard()

    # ------------------------------------------------------------------ hand the plan out
    def _for_output(self) -> Plan | None:
        """The saved plan to hand out, or None with a message: the health declaration comes first, and
        what is printed is always what is saved."""
        if not all(self._health()):
            return self._fail(t("ui.plan.err_health"))
        return self.plan if self.save() else None

    def _target(self, suffix: str) -> Path:
        assert self.plan is not None
        p = services.report_target(self.ctx.store, self.athlete, f"traningsschema_{self.plan.start.isoformat()}")
        return p.with_suffix(suffix)

    def save_pdf(self) -> bool:
        plan = self._for_output()
        if plan is None:
            return False
        fn, _ = QFileDialog.getSaveFileName(self, t("ui.res.pdf"), str(self._target(".pdf")), "PDF (*.pdf)")
        if not fn:
            return False
        try:
            services.html_to_pdf(services.render_plan(plan, self.ctx.branding()), Path(fn))
        except Exception as e:
            log.exception("plan pdf failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.pdf", detail=str(e)))
            return False
        services.register_report(self.ctx.store, Path(fn), self.athlete)
        QMessageBox.information(self, t("ui.res.pdf"), t("ui.plan.file_saved", path=fn))
        return True

    def save_ics(self) -> bool:
        plan = self._for_output()
        if plan is None:
            return False
        fn, _ = QFileDialog.getSaveFileName(self, t("ui.plan.ics"), str(self._target(".ics")), t("ui.plan.ics_filter"))
        if not fn:
            return False
        try:
            Path(fn).write_bytes(services.plan_ics(plan))
        except Exception as e:
            log.exception("plan calendar file failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.plan.err_ics", detail=str(e)))
            return False
        services.register_report(self.ctx.store, Path(fn), self.athlete)   # so erasing the person finds it
        QMessageBox.information(self, t("ui.plan.ics"), t("ui.plan.file_saved", path=fn))
        return True

    def send_to_athlete(self) -> bool:
        """*Skicka till idrottaren*: the printout as PDF and the calendar file by mail, after the question.
        The same rules as the other ways out: the health declaration first, and the plan is saved."""
        from lopanalys.ui.views.send_mail import send_plan_to_athlete
        if self._mailing is not None:
            return False
        plan = self._for_output()
        if plan is None or self.stored is None:
            return False
        branding = self.ctx.branding()

        def end() -> None:
            self._mailing = None
            if self.athlete is not None:
                self._refresh_mail()

        self._mailing = True
        self.mail_btn.setEnabled(False)
        w = send_plan_to_athlete(self, self.ctx, self.athlete, plan, self.stored.row_id,
                                 lambda p: services.html_to_pdf(services.render_plan(plan, branding), p), on_end=end)
        if w is not None:
            self._mailing = w
        return w is not None

    def print_plan(self) -> bool:
        plan = self._for_output()
        if plan is None:
            return False
        from lopanalys.ui.views.settings import pick_printer
        name = pick_printer(self, str(self.ctx.setting("report.printer") or ""))
        if name is None:
            return False
        try:
            services.print_report(services.render_plan(plan, self.ctx.branding()), name or None)
        except Exception as e:
            log.exception("plan print failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.print", detail=str(e)))
            return False
        return True
