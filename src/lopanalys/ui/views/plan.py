"""Träningsschema: the athlete's training plan, built on a test.

The form on the left states what the plan engine needs (the test, the level, the time per weekday,
the goal and milestones or a length); the overview on the right shows the plan it gives. The plan
is saved per athlete, printed, saved as PDF and as a calendar file, handed to the athlete's app (*Till appen*:
a QR code, a link and a .troskel file, views/plan_app.py) and mailed to the athlete (the files and the app's
link, views/send_mail.py). Nothing leaves the program before every box of the health declaration
is ticked.

The plan rolls (plan.rolling): only the next four weeks have workouts, the rest is a sketch. A row
under the overview says how far the workouts run, with *Förnya*, which details the next weeks on
what is saved without filling in anything; the banner offers it when they run out within a week.

Several sports (#666, step 4): the coach ticks the plan's sports, each with a role (main, supporting,
maintenance) and a share of the weekly time in tens of percent, 60/30/10 by default. Running's zones come
from the chosen test (*Bygger på testet*); every other sport with tests has a list of its own under its row, the
newest test chosen, and *Inget test* for the max HR alone (services.sport_capacity_of). Skis choose among the SkiErg's
tests, unless the SkiErg is in the plan too. A weekday can
allow some of the sports only: the button beside the day says which, *Alla* by default. Strength is a number of
sessions a week (0-3) on top of the endurance time, placed by the engine. With more than one sport a weekday can have
a second time (the *+* beside the day): a second slot with its own minutes, clock time and sports, which the engine
uses for a second, easy workout in another sport (plan.periodize.second_slots). Under the days, *Fasta hårda pass* gives
up to two weekdays (each with a sport, or any) where the week's quality sessions go first (PlanRequest.hard_days).

The form follows the athlete form: fields start empty, *Spara* is always visible below the
scroll area, and unsaved changes are asked about before they are thrown away.
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace as dataclass_replace
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QMenu,
                               QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget)

from lopanalys.core.i18n import t
from lopanalys.core.models import Athlete
from lopanalys.plan import RUNNING, Capacity, Goal, HardDay, Plan, PlanRequest, Slot, SportRole, build_plan
from lopanalys.plan import rolling
from lopanalys.plan.engine import horizon_for
from lopanalys.plan.models import BORROWS, LEVELS, MAX_STRENGTH, PLAN_SPORTS, ROLE_SHARE, ROLES, SWIM, WEEKDAY_KEYS, ZONES
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
HARD_DAYS = 2                     # fixed hard days in the form (PlanRequest.hard_days)
SHARES = tuple(range(10, 101, 10))   # a sport's share of the weekly time, in tens of percent
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


def _basis_text(cap: Capacity | None, sport: str) -> str:
    """What a sport's zones come from, under its row in the form."""
    if cap is None:
        return t("ui.plan.sport_basis.effort")
    if cap.own_values and cap.known:                       # typed in by the athlete in the app (#777)
        return t("plan.capacity.self")
    if cap.anchors is None and cap.css_s100:                # swimming's CSS test: a pace, no heart rate
        from lopanalys.assessments.css import pace_str
        return t("ui.plan.sport_basis.css", pace=pace_str(cap.css_s100),
                 date=cap.tested.isoformat() if cap.tested else "")
    if cap.anchors is None:
        return t("ui.plan.sport_basis.none")
    if cap.tested:
        return t("ui.plan.sport_basis.test", test=cap.source, date=cap.tested.isoformat())
    return t("ui.plan.sport_basis.max_hr")


class SportRow(QWidget):
    """One sport of the plan: ticked or not, its role and its share of the weekly time."""

    changed = Signal()
    made_main = Signal(object)

    def __init__(self, sport: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.sport = sport
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(2)
        self.check = QCheckBox(t(f"plan.sport.{sport}"))
        self.check.setObjectName(f"sport_{sport}")
        self.role = QComboBox()
        for r in ROLES:
            self.role.addItem(t(f"ui.plan.role.{r}"), r)
        self.share = QComboBox()
        for n in SHARES:
            self.share.addItem(t("ui.plan.share", n=n), n)
        self.basis = label("", "Hint", wrap=True)      # what the sport's zones come from
        self.basis.hide()
        self.test = QComboBox()                          # the sport's test (not running: *Bygger på testet*)
        self.test.setObjectName(f"sport_test_{sport}")
        self.test.hide()
        self.test_shown = False                          # the sport has tests, and chooses its own
        g.addWidget(self.check, 0, 0)
        g.addWidget(self.role, 0, 1)
        g.addWidget(self.share, 0, 2)
        g.addWidget(self.basis, 1, 0, 1, 3)
        g.addWidget(self.test, 2, 0, 1, 3)
        g.setColumnStretch(0, 3)
        g.setColumnStretch(1, 3)
        g.setColumnStretch(2, 2)
        self.set("support", 0, False)
        self.check.toggled.connect(self._toggled)
        self.role.currentIndexChanged.connect(self._role_changed)
        self.share.currentIndexChanged.connect(self.changed)
        self.test.currentIndexChanged.connect(self.changed)

    def set(self, role: str, share: int, on: bool) -> None:
        """Without signals: the role, the share (0: the role's default) and whether the sport is in the plan."""
        for w in (self.check, self.role, self.share):
            w.blockSignals(True)
        self.check.setChecked(on)
        self.role.setCurrentIndex(max(0, self.role.findData(role)))
        n = share if share > 0 else ROLE_SHARE.get(role, ROLE_SHARE["support"])
        self.share.setCurrentIndex(max(0, self.share.findData(min(SHARES, key=lambda x: abs(x - n)))))
        for w in (self.check, self.role, self.share):
            w.blockSignals(False)
        self._enable()

    def _enable(self) -> None:
        on = self.check.isChecked()
        self.role.setEnabled(on)
        self.share.setEnabled(on)
        self.basis.setVisible(on and bool(self.basis.text()))
        self.test.setVisible(on and self.test_shown)

    def _toggled(self, *_: Any) -> None:
        self._enable()
        self.changed.emit()

    def _role_changed(self, *_: Any) -> None:
        """A new role takes the role's share (60/30/10); a new main sport makes the old one supporting."""
        r = self.role.currentData()
        self.share.blockSignals(True)
        self.share.setCurrentIndex(max(0, self.share.findData(ROLE_SHARE[r])))
        self.share.blockSignals(False)
        if r == "main":
            self.made_main.emit(self)
        self.changed.emit()

    @property
    def on(self) -> bool:
        return self.check.isChecked()

    def value(self) -> SportRole:
        return SportRole(self.sport, self.role.currentData(), self.share.currentData())

    def snapshot(self) -> tuple:
        return (self.on, self.role.currentData(), self.share.currentData())

    def test_id(self) -> int | None:
        """The chosen test's session id; None for *Inget test*, or when the sport has no tests to choose."""
        return self.test.currentData() if self.test_shown else None


class DaySports(QPushButton):
    """The sports one weekday allows: *Alla* (every sport of the plan) or the ticked ones, from a menu."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(t("ui.plan.day_sports_tip"))
        self.menu_ = QMenu(self)
        self.actions_: dict[str, Any] = {}
        for sp in PLAN_SPORTS:
            a = self.menu_.addAction(t(f"plan.sport.{sp}"))
            a.setCheckable(True)
            a.toggled.connect(self._toggled)
            self.actions_[sp] = a
        self.setMenu(self.menu_)
        self._offered: tuple[str, ...] = PLAN_SPORTS
        self._text()

    def offer(self, sports: tuple[str, ...]) -> None:
        """Only the plan's sports can be ticked."""
        self._offered = sports
        for sp, a in self.actions_.items():
            a.setVisible(sp in sports)
        self._text()

    def chosen(self) -> tuple[str, ...]:
        """The ticked sports among the offered ones; () is all of them."""
        out = tuple(sp for sp in self._offered if self.actions_[sp].isChecked())
        return () if len(out) == len(self._offered) else out

    def set_chosen(self, sports: tuple[str, ...]) -> None:
        for sp, a in self.actions_.items():
            a.blockSignals(True)
            a.setChecked(sp in sports)
            a.blockSignals(False)
        self._text()

    def _toggled(self, *_: Any) -> None:
        self._text()
        self.changed.emit()

    def _text(self) -> None:
        c = self.chosen()
        self.setText(", ".join(t(f"plan.sport.{sp}") for sp in c) if c else t("ui.plan.day_sports_all"))


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
        # *Läs in från appen* (#781): the athlete's plan from the phone, as a file or a pasted link
        self.phone_btn = button(t("ui.plan.phone.button"), "upload", kind="ghost")
        self.phone_btn.setToolTip(t("ui.plan.phone.tip"))
        phone_menu = QMenu(self.phone_btn)
        phone_menu.addAction(t("ui.plan.phone.from_file"), lambda: self.read_phone_file())
        phone_menu.addAction(t("ui.plan.phone.from_clipboard"), lambda: self.read_phone_clipboard())
        self.phone_btn.setMenu(phone_menu)
        row.addWidget(self.phone_btn)
        self.mail_note = label("", "Hint", wrap=True)     # when it was mailed, or why it cannot be
        row.addWidget(self.mail_note, 1)
        self.cancel_btn = button(t("ui.common.cancel"), "x", kind="ghost")
        self.cancel_btn.clicked.connect(self.revert)
        self.preview_btn = button(t("ui.plan.preview"), "eye")
        self.preview_btn.clicked.connect(lambda: self.preview())
        self.ics_btn = button(t("ui.plan.ics"), "calendar")
        self.ics_btn.clicked.connect(lambda: self.save_ics())
        self.app_btn = button(t("ui.plan.app"), "phone")
        self.app_btn.clicked.connect(lambda: self.to_app())
        self.print_btn = button(t("ui.res.print"), "printer")
        self.print_btn.clicked.connect(lambda: self.print_plan())
        self.pdf_btn = button(t("ui.res.pdf"), "pdf")
        self.pdf_btn.clicked.connect(lambda: self.save_pdf())
        self.mail_btn = button(t("ui.res.mail"), "mail")
        self.mail_btn.clicked.connect(lambda: self.send_to_athlete())
        self.save_btn = button(t("ui.common.save"), "save", kind="primary")
        self.save_btn.clicked.connect(lambda: self.save())
        # two rows, so the page fits a laptop: sharing above, delete / cancel / save below (2026-11-16)
        share = QHBoxLayout()
        share.setSpacing(8)
        share.addStretch(1)
        for w in (self.preview_btn, self.ics_btn, self.app_btn, self.print_btn, self.pdf_btn, self.mail_btn):
            share.addWidget(w)
        outer.addLayout(share)
        for w in (self.cancel_btn, self.save_btn):
            row.addWidget(w)
        outer.addLayout(row)

    # ------------------------------------------------------------------ building the page
    def _build_form(self, v: QVBoxLayout) -> None:
        v.setSpacing(14)
        v.addWidget(label(t("ui.plan.sec.basis"), "SectionTitle"))
        self.test = QComboBox()
        self.test_box = field(t("ui.plan.test"), self.test, t("ui.plan.test_hint"))
        v.addWidget(self.test_box)
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
        v.addWidget(label(t("ui.plan.sec.sports"), "SectionTitle"))
        v.addWidget(label(t("ui.plan.sports_hint"), "Hint", wrap=True))
        self.sport_rows: dict[str, SportRow] = {}
        for sp in PLAN_SPORTS:
            r = SportRow(sp)
            r.changed.connect(self._sports_changed)
            r.made_main.connect(self._new_main)
            r.test.currentIndexChanged.connect(lambda *_, r=r: self._test_changed(r))
            self.sport_rows[sp] = r
            v.addWidget(r)
        self.strength = QComboBox()
        self.strength.setObjectName("strength_sessions")
        for n in range(MAX_STRENGTH + 1):
            self.strength.addItem(t("ui.plan.strength_none") if n == 0 else t("ui.plan.strength_n", n=n), n)
        v.addWidget(field(t("ui.plan.strength"), self.strength, t("ui.plan.strength_hint")))

        v.addSpacing(6)
        v.addWidget(label(t("ui.plan.sec.time"), "SectionTitle"))
        v.addWidget(label(t("ui.plan.time_hint"), "Hint", wrap=True))
        dg = QGridLayout()
        dg.setHorizontalSpacing(12)
        dg.setVerticalSpacing(8)
        self.minutes: list[NumberEdit] = []
        self.clocks: list[QLineEdit] = []
        self.day_sports: list[DaySports] = []
        # The second time of a day (two workouts a day): a row under the day, opened with its + button.
        self.minutes2: list[NumberEdit] = []
        self.clocks2: list[QLineEdit] = []
        self.day_sports2: list[DaySports] = []
        self.second_btns: list[QToolButton] = []
        self.second_rows: list[list[QWidget]] = []
        for i, key in enumerate(WEEKDAY_KEYS):
            dg.addWidget(label(t(f"ui.plan.day.{key}")), 2 * i, 0)
            row: list[QWidget] = [label(t("ui.plan.second_label"), "Hint")]
            dg.addWidget(row[0], 2 * i + 1, 0)
            for n, (mins, clocks, sports) in enumerate(((self.minutes, self.clocks, self.day_sports),
                                                        (self.minutes2, self.clocks2, self.day_sports2))):
                suffix = "" if n == 0 else "_2"
                m = NumberEdit("min")
                m.setObjectName(f"minutes_{key}{suffix}")
                c = SelectAllEdit()
                c.setPlaceholderText(t("ui.plan.clock_placeholder"))
                c.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9:.]{0,5}"), c))
                ds = DaySports()
                ds.setObjectName(f"sports_{key}{suffix}")
                ds.changed.connect(self._changed)
                dg.addWidget(m, 2 * i + n, 1)
                dg.addWidget(c, 2 * i + n, 2)
                dg.addWidget(ds, 2 * i + n, 3)
                mins.append(m)
                clocks.append(c)
                sports.append(ds)
                if n:
                    row += [m, c, ds]
            b = QToolButton()
            b.setObjectName(f"second_{key}")
            b.setText("+")
            b.setCheckable(True)
            b.setToolTip(t("ui.plan.second_add"))
            b.toggled.connect(lambda on, i=i: self._second_toggled(i, on))
            dg.addWidget(b, 2 * i, 4)
            self.second_btns.append(b)
            self.second_rows.append(row)
            for w in row:
                w.setVisible(False)
        dg.setColumnStretch(0, 2)
        dg.setColumnStretch(1, 3)
        dg.setColumnStretch(2, 3)
        dg.setColumnStretch(3, 3)
        v.addLayout(dg)
        # Fixed hard days (a group session): a weekday and the sport of its quality session.
        hw = QWidget()
        hg = QGridLayout(hw)
        hg.setContentsMargins(0, 0, 0, 0)
        hg.setHorizontalSpacing(12)
        hg.setVerticalSpacing(8)
        self.hard_days: list[QComboBox] = []
        self.hard_sports: list[QComboBox] = []
        for n in range(HARD_DAYS):
            d = QComboBox()
            d.setObjectName(f"hard_day_{n + 1}")
            d.addItem(t("ui.plan.hard_day_none"), None)
            for i, key in enumerate(WEEKDAY_KEYS):
                d.addItem(t(f"ui.plan.day.{key}"), i)
            sp = QComboBox()
            sp.setObjectName(f"hard_sport_{n + 1}")
            sp.addItem(t("ui.plan.hard_sport_any"), "")
            for x in PLAN_SPORTS:
                sp.addItem(t(f"plan.sport.{x}"), x)
            sp.setEnabled(False)
            d.currentIndexChanged.connect(lambda *_, d=d, sp=sp: sp.setEnabled(d.currentData() is not None))
            hg.addWidget(d, n, 0)
            hg.addWidget(sp, n, 1)
            self.hard_days.append(d)
            self.hard_sports.append(sp)
        v.addWidget(field(t("ui.plan.hard_days"), hw, t("ui.plan.hard_days_hint")))

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
        # The day of the athlete's next test (#778, plan.next_test): empty is the default, 8-12 weeks after the test.
        self.next_test = DateField(plan=True, placeholder=t("ui.plan.next_test_auto"))
        v.addWidget(field(t("ui.plan.next_test"), self.next_test, t("ui.plan.next_test_hint")))
        self.next_test.edit.textChanged.connect(self._next_test_changed)

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
                  self.start.edit, *self.minutes, *self.clocks, *self.minutes2, *self.clocks2):
            w.textChanged.connect(self._changed)
        for cb in (self.test, self.level, self.strength, *self.hard_days, *self.hard_sports):
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
        # The next test and what the targets build on (#778, plan.next_test).
        self.nextline = label("", "Hint", wrap=True)
        col.addWidget(self.headline)
        col.addWidget(self.subline)
        col.addWidget(self.startline)
        col.addWidget(self.nextline)
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
        self.phone_note = label("", "Hint", wrap=True)     # the latest plan read in from the athlete's phone
        s.addWidget(self.phone_note)
        v.addWidget(self.shown, 1)
        self.shown.hide()

    # ------------------------------------------------------------------ the form's state
    def _inputs(self) -> tuple:
        """Everything the plan is built from. Two equal tuples give the same plan."""
        return (self.test.currentData(), self.level.currentData(), self.recent.value(),
                tuple(r.snapshot() for r in self.sport_rows.values()),
                tuple(r.test_id() for r in self.sport_rows.values()), self.strength.currentData(),
                tuple((d.currentData(), s.currentData()) for d, s in zip(self.hard_days, self.hard_sports)),
                tuple((m.value(), c.text().strip(), d.chosen()) for m, c, d in zip(self.minutes, self.clocks,
                                                                                  self.day_sports)),
                tuple((m.value(), c.text().strip(), d.chosen()) if self._second_on(i) else None
                      for i, (m, c, d) in enumerate(zip(self.minutes2, self.clocks2, self.day_sports2))),
                self.mode.currentData(), self.goal_name.text().strip(), self.goal_date.text().strip(),
                self.goal_km.value(), self.goal_target.text().strip(), tuple(r.snapshot() for r in self.milestones),
                self.weeks.value(), self.start.text().strip())

    def _health(self) -> tuple:
        return tuple(cb.isChecked() for cb in self.health.values())

    def _state(self) -> tuple:
        """What *Spara* saves: the plan's inputs, the health declaration and the day of the next test (which never
        rebuilds the plan)."""
        return self._inputs(), self._health(), self.next_test.text().strip()

    def dirty(self) -> bool:
        return self.athlete is not None and self._state() != self._loaded

    def _chosen_next_test(self) -> date | None:
        """The day typed under *Nästa test*; None when empty (the default) or not a date."""
        try:
            return self.next_test.date()
        except ValueError:
            return None

    def _next_test_changed(self, *_: Any) -> None:
        if self._filling:
            return
        self._show_next_test()
        self._changed()

    def _show_next_test(self) -> None:
        """The default day as the field's placeholder, and the day in the overview's subline (``_show``)."""
        from lopanalys.plan import next_test
        plan = self.plan
        if plan is None:
            self.next_test.edit.setPlaceholderText(t("ui.plan.next_test_auto"))
            self.nextline.setText("")
            return
        auto = next_test.default_next_test(plan)
        self.next_test.edit.setPlaceholderText(t("ui.plan.next_test_default", date=auto.isoformat()))
        day = self._chosen_next_test() or auto
        kind, tested = next_test.basis(plan)
        self.nextline.setText(t(f"ui.plan.next_test_line.{kind}", date=day.isoformat(),
                                tested=tested.isoformat() if tested else ""))

    def _changed(self, *_: Any) -> None:
        if self._filling:
            return
        self.cancel_btn.setEnabled(self.dirty())
        self.stale.setVisible(self.plan is not None and self._inputs() != self._plan_snap)
        self._health_text()

    def chosen_sports(self) -> tuple[str, ...]:
        """The ticked sports, the main sport first."""
        on = [r for r in self.sport_rows.values() if r.on]
        on.sort(key=lambda r: r.role.currentData() != "main")
        return tuple(r.sport for r in on)

    def _new_main(self, row: SportRow) -> None:
        """One main sport: the one that was main becomes a supporting sport."""
        for r in self.sport_rows.values():
            if r is not row and r.role.currentData() == "main":
                r.set("support", 0, r.on)

    def _sports_changed(self, *_: Any) -> None:
        """The days offer the plan's sports, and are asked only when there is more than one;
        the running test is chosen only when running is in the plan."""
        sports = self.chosen_sports()
        for d in self.day_sports + self.day_sports2:
            d.offer(sports)
        for d in self.day_sports:
            d.setVisible(len(sports) > 1)
        ski = self.sport_rows.get("ski")
        if ski is not None:                             # skis with the SkiErg in the plan: the SkiErg's test
            ski.test_shown = ski.test.count() > 1 and "skierg" not in sports
            ski._enable()
        for i, b in enumerate(self.second_btns):        # two workouts a day are two sports
            b.setVisible(len(sports) > 1)
            for w in self.second_rows[i]:
                w.setVisible(self._second_on(i))
        self.test_box.setVisible(RUNNING in sports or not sports)
        self._changed()

    def _second_on(self, i: int) -> bool:
        """The weekday's second time is open and the plan has more than one sport."""
        return self.second_btns[i].isChecked() and len(self.chosen_sports()) > 1

    def _second_toggled(self, i: int, on: bool) -> None:
        self.second_btns[i].setText("−" if on else "+")
        if not on:
            self.minutes2[i].clear()
            self.clocks2[i].clear()
            self.day_sports2[i].set_chosen(())
        for w in self.second_rows[i]:
            w.setVisible(self._second_on(i))
        self._changed()

    def _sport_basis(self, req: PlanRequest | None = None, session_id: int | None = None) -> None:
        """Under each sport but running: its tests to choose from, or what its zones come from without one.

        The list has the sport's tests (services.sport_tests, a CSS test for swimming), newest first, and *Inget
        test*. A saved plan chooses the test its targets came from (the day of ``PlanRequest.own_capacity``), and
        *Inget test* when it had none; a new form chooses ``session_id`` when it is one of them, else the newest."""
        a = self.athlete
        for sp, r in self.sport_rows.items():
            text = ""
            r.test.blockSignals(True)
            r.test.clear()
            r.test_shown = False
            if sp != RUNNING and a is not None and a.id is not None:
                src = BORROWS.get(sp, sp)
                try:
                    tests = services.sport_test_list(self.ctx.store, a.id, src)
                    cap = services.sport_capacity(self.ctx.store, a, src)
                except Exception:
                    log.exception("reading the sport's test failed")
                    tests, cap = [], None
                for s in tests:
                    r.test.addItem(f"{s.protocol_name}  ·  {s.started.astimezone():%Y-%m-%d}", s.id)
                if tests:
                    r.test.addItem(t("ui.plan.no_test_swim" if src == SWIM else "ui.plan.no_test"), None)
                    r.test_shown = sp not in BORROWS
                    pick = 0
                    if req is not None:
                        own = req.own_capacity(src)
                        day = own.tested if own is not None else None
                        pick = next((i for i, s in enumerate(tests) if s.started.astimezone().date() == day
                                     and (not own.source or s.protocol_name == own.source)), len(tests))
                    elif session_id is not None and r.test.findData(session_id) >= 0:
                        pick = r.test.findData(session_id)
                    r.test.setCurrentIndex(pick)
                if sp in BORROWS and cap is not None and (cap.anchors is not None or tests):
                    text = t("ui.plan.sport_basis.borrow", sport=t(f"plan.sport.{BORROWS[sp]}"))
                elif not tests:
                    text = _basis_text(cap, sp)
                else:
                    text = self._chosen_basis(r)
            r.basis.setText(text)
            r.test.blockSignals(False)
            r._enable()

    def _chosen_basis(self, r: SportRow) -> str:
        """Under a sport's list of tests: a CSS test's pace, or what *Inget test* gives (the max HR or nothing).
        A test with thresholds says nothing more than the list."""
        a = self.athlete
        if a is None or r.sport in BORROWS:
            return r.basis.text()
        try:
            cap = services.sport_capacity_of(self.ctx.store, a, r.sport, r.test.currentData())
        except Exception:
            log.exception("reading the sport's test failed")
            return ""
        return "" if cap is not None and cap.anchors is not None and cap.tested else _basis_text(cap, r.sport)

    def _test_changed(self, r: SportRow) -> None:
        r.basis.setText(self._chosen_basis(r))
        r._enable()

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
                  self.start.edit, self.next_test.edit, *self.minutes, *self.clocks, *self.minutes2, *self.clocks2):
            w.clear()
        for b in self.second_btns:
            b.setChecked(False)
        req = st.plan.request if st else None
        roles = {r.sport: r for r in req.plan_roles()} if req else {RUNNING: SportRole(RUNNING, "main")}
        for sp, row in self.sport_rows.items():
            r = roles.get(sp)
            row.set(r.role if r else "support", r.share if r else 0, r is not None)
        for d in self.day_sports + self.day_sports2:
            d.set_chosen(())
        self.strength.setCurrentIndex(max(0, self.strength.findData(req.strength_count if req else 0)))
        given = list(req.hard_days[:HARD_DAYS]) if req else []
        for n, (d, sp) in enumerate(zip(self.hard_days, self.hard_sports)):
            h = given[n] if n < len(given) else None
            d.setCurrentIndex(max(0, d.findData(h.weekday)) if h else 0)
            sp.setCurrentIndex(max(0, sp.findData(h.sport)) if h else 0)
        self._sport_basis(req, session_id)
        if req is None:
            self.test.setCurrentIndex(max(0, self.test.findData(session_id)) if session_id else 0)
            self.level.setCurrentIndex(0)
            self.mode.setCurrentIndex(0)
        else:
            self.test.setCurrentIndex(self.test.findData(st.session_id))
            self.level.setCurrentIndex(max(0, self.level.findData(req.level)))
            self.recent.setValue(req.recent_weekly_minutes or 0)
            seen: set[int] = set()
            for s in req.availability:
                if s.role != "core" or not 0 <= s.weekday <= 6 or (s.weekday in seen and self.second_btns[s.weekday].isChecked()):
                    continue
                second = s.weekday in seen           # the day's second time
                if second:
                    self.second_btns[s.weekday].setChecked(True)
                seen.add(s.weekday)
                mins, clocks, sports = ((self.minutes2, self.clocks2, self.day_sports2) if second
                                        else (self.minutes, self.clocks, self.day_sports))
                mins[s.weekday].setValue(s.max_minutes)
                clocks[s.weekday].setText(f"{s.start:%H:%M}" if s.start else "")
                if set(roles) - set(s.sports):           # not every sport of the plan: the day's own
                    sports[s.weekday].set_chosen(tuple(s.sports))
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
            self.next_test.set_date(st.plan.next_test if st else None)
        ok = bool(st and services.health_valid(st.health))
        for cb in self.health.values():
            cb.setChecked(ok)
        self._filling = False
        self._sports_changed()
        self._mode_changed()
        self._loaded = self._state()
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
            self.banner_text.setText(services.retest_offer_text(self._retest))
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
        main = req.main_sport
        cap = req.capacity_for(main)
        goal = plan.goal
        self.headline.setText(goal.name if goal else t("plan.pdf.headline_weeks", weeks=len(plan.weeks)))
        if cap.anchors is None:
            basis = t("plan.pdf.fact.no_test")
        elif cap.own_values:
            basis = t("plan.capacity.self")
        else:
            basis = " · ".join(x for x in (cap.source, cap.tested.isoformat() if cap.tested else "") if x) \
                or t("ui.plan.basis_max_hr")
        self.subline.setText(f"{_day(plan.start)} – {_day(plan.end)} {plan.end.year}   ·   "
                             + t("ui.plan.based_on", basis=basis))
        self.startline.setText(start_line(plan))
        self._show_next_test()
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
        zt = targets(cap, plan.zone_system, main)
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
            self.zone_lay.addWidget(label(" · ".join(x for x in (zt[z].power_text, zt[z].pace_text) if x)
                                          if z in zt else "", "Hint"), i, 3)
        self.zone_lay.setColumnStretch(3, 1)
        notes = [n.text for n in plan.notes]
        if plan.capacity_log:
            until, old = max(plan.capacity_log, key=lambda x: x[0])
            notes.append(t("ui.plan.retested_note", date=until.isoformat(),
                           before=old.tested.isoformat() if old.tested else "–"))
        self.notes.setText("\n".join(notes))
        self.notes.setVisible(bool(notes))
        sent = services.phone_logs(self.ctx.store, self.stored) if plan is (self.stored.plan if self.stored else None) else []
        if sent:
            last = sent[-1]
            done = sum(1 for m in last.get("marks", {}).values() if m.get("status") in ("done", "partial"))
            self.phone_note.setText(t("ui.plan.phone.note", date=last.get("exported", ""), done=done,
                                      extras=len(last.get("extras", []))))
        self.phone_note.setVisible(bool(sent))

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
        sports = self.chosen_sports()
        if not sports:
            return self._fail(t("ui.plan.err_no_sport"))
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
            slots.append(Slot(i, int(v), self.day_sports[i].chosen() or sports, start=start))
            if self._second_on(i):
                m2, c2 = self.minutes2[i].value(), self.clocks2[i].text().strip()
                if m2 is None and not c2:
                    continue
                if m2 is None or not MIN_SLOT <= m2 <= MAX_SLOT:
                    return self._fail(t("ui.plan.err_minutes", day=day, lo=MIN_SLOT, hi=MAX_SLOT))
                try:
                    start2 = parse_clock(c2)
                except ValueError:
                    return self._fail(t("ui.plan.err_clock", day=day))
                if start is None or start2 is None or start == start2:
                    return self._fail(t("ui.plan.err_second_clock", day=day))
                slots.append(Slot(i, int(m2), self.day_sports2[i].chosen() or sports, start=start2))
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
                goals.append(Goal(name, d, sports[0], distance_km=km, priority=prio, target=target))
        else:
            w = self.weeks.value()
            if w is None or w > MAX_WEEKS:
                return self._fail(t("ui.plan.err_weeks", hi=MAX_WEEKS))
            weeks = int(w)
        sid = self._session_id()
        session = self.ctx.store.session(sid) if sid else None
        caps: list[Capacity] = []
        try:
            for sp in sports:
                if sp == RUNNING:
                    cap = services.plan_capacity(self.ctx.store, a, session)
                elif sp in BORROWS:          # skis: the SkiErg's test, from the SkiErg's row when it is in the plan
                    src = BORROWS[sp]
                    if src in sports:
                        continue
                    row = self.sport_rows[sp]
                    cap = (services.sport_capacity_of(self.ctx.store, a, src, row.test_id()) if row.test_shown
                           else services.sport_capacity(self.ctx.store, a, src))
                    if cap is not None and cap.anchors is None:
                        cap = None           # nothing to borrow: zone and effort
                else:
                    row = self.sport_rows[sp]
                    cap = (services.sport_capacity_of(self.ctx.store, a, sp, row.test_id()) if row.test_shown
                           else services.sport_capacity(self.ctx.store, a, sp))
                if cap is not None:
                    caps.append(cap)
        except Exception as e:
            log.exception("reading the test failed")
            return self._fail(t("ui.err.db", detail=str(e)))
        recent = self.recent.value()
        roles = tuple(self.sport_rows[sp].value() for sp in sports)
        hard: list[HardDay] = []
        for d, sp in zip(self.hard_days, self.hard_sports):
            wd = d.currentData()
            if wd is not None and wd not in [h.weekday for h in hard]:
                hard.append(HardDay(wd, sp.currentData() if sp.currentData() in sports else ""))
        return PlanRequest(a.name, start_day, tuple(slots), tuple(caps), sports, goals=tuple(goals),
                           duration_weeks=weeks, level=level, recent_weekly_minutes=int(recent) if recent else None,
                           roles=roles if len(roles) > 1 or roles[0].sport != RUNNING else (),
                           strength_sessions=self.strength.currentData() or 0, hard_days=tuple(hard))

    def _session_id(self) -> int | None:
        """The running test the plan is built on; none when running is not in the plan."""
        return self.test.currentData() if RUNNING in self.chosen_sports() else None

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
        sid = self._session_id()
        try:
            self.next_test.date()
        except ValueError:
            self._fail(t("ui.plan.next_test_bad"))
            return False
        plan = services.with_saved_edits(self.stored.plan if self.stored else None, plan)
        plan = self.plan = dataclass_replace(plan, next_test=self._chosen_next_test())
        try:
            row = services.save_plan(self.ctx.store, a, plan, sid, health, "saved" if self.stored is None else "changed")
        except Exception as e:
            log.exception("saving the plan failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.save_failed", detail=str(e)))
            return False
        self.stored = services.StoredPlan(plan, sid, health, row)
        self._loaded = self._state()
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
        QMessageBox.information(self, t("ui.plan.title"), services.retest_done_text(s, date.today()))

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

    def read_phone_file(self) -> bool:
        """*Läs in från appen → Från en fil*: the ``.troskel`` file the athlete saved in the app."""
        if self.athlete is None:
            return False
        fn, _ = QFileDialog.getOpenFileName(self, t("ui.plan.phone.button"), str(Path.home()), t("ui.plan.phone.filter"))
        if not fn:
            return False
        try:
            text = Path(fn).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.plan.phone.err.file", detail=str(e)))
            return False
        return self.read_from_phone(text)

    def read_phone_clipboard(self) -> bool:
        """*Läs in från appen → Klistra in länken*: the link the athlete copied in the app and sent in a message."""
        from PySide6.QtGui import QGuiApplication
        return self.read_from_phone(QGuiApplication.clipboard().text())

    def read_from_phone(self, text: str, today: date | None = None) -> bool:
        """Read the athlete's plan from the phone in (services.import_phone_plan): the plan as the athlete has it,
        so that a new test lays its targets on it. Another plan, or a plan with another name, is asked about first."""
        from lopanalys.plan import phone
        a = self.athlete
        if a is None or not self.confirm_discard():
            return False
        try:
            got = services.read_phone_plan(text)
        except phone.NotFromPhone as e:
            QMessageBox.warning(self, t("ui.plan.phone.button"), t(f"ui.plan.phone.err.{e.code}"))
            return False
        question = services.phone_plan_question(self.stored, a, got)
        if question is not None:
            r = QMessageBox.question(self, t("ui.plan.phone.button"), question,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Yes:
                return False
        try:
            services.import_phone_plan(self.ctx.store, a, got, today)
        except Exception as e:
            log.exception("reading the plan from the phone failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.save_failed", detail=str(e)))
            return False
        self.stored = services.load_plan(self.ctx.store, a)
        self._fill(None)
        assert self.stored is not None
        QMessageBox.information(self, t("ui.plan.title"), services.phone_read_text(got, self.stored.plan, today))
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

    def to_app(self) -> bool:
        """*Till appen*: the saved plan as a QR code, a link and a ``.troskel`` file for the athlete's app. The same
        rules as the other ways out; after *Förnya* it gives the renewed plan, which the app takes in its place."""
        from lopanalys.ui.views.plan_app import AppDialog
        plan = self._for_output()
        if plan is None:
            return False
        try:
            link, data = services.plan_app(plan)
        except Exception as e:
            log.exception("the plan for the app failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.plan.err_app", detail=str(e)))
            return False
        target = self._target(".troskel")
        dlg = AppDialog(link, data, target, self,
                        on_saved=lambda p: services.register_report(self.ctx.store, p, self.athlete))
        self._app_dialog = dlg
        dlg.exec()
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
