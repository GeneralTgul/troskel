"""Resultat: lactate curve with LT1/LT2 (method selectable, recalculated live), zones in a selectable
system with the values for the athlete's watch, estimated VO2max, gait findings with key frames, the
report preview, printing and PDF.

Everything is saved without a button: a finished test's results right when it opens, and
every change (threshold method, zone system, comment, lactate or RPE in the table) ``AUTOSAVE_MS``
after the last keystroke, and at once when the coach leaves the page (``flush()``). A test that was
stopped or cut off says so at the top, with "Fortsätt från steg N" when continuing is reasonable."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QScrollArea, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from lopanalys.assessments import erg_values
from lopanalys.assessments.cadence import cadence_label, with_strap_cadence
from lopanalys.core import paths, simulated
from lopanalys.core.i18n import t
from lopanalys.core.models import MAX_KINDS, Athlete, Session, StepRecord, TestKind, TimeSample
from lopanalys.core.sport import DEFAULT_SPORT, profile, sport_of
from lopanalys.plan.models import RUNNING
from lopanalys.ui import services, theme
from lopanalys.ui.bridge import run_async
from lopanalys.ui.context import GAIT_KINDS, TECHNIQUE_KINDS, AppContext, TestOutcome
from lopanalys.ui.fmt import clock, num, pace
from lopanalys.ui.live_controller import gait_ref, gait_status
from lopanalys.ui.widgets.charts import HrSpeedChart, LactateChart
from lopanalys.ui.widgets.common import Card, PageHeader, StatusPill, button, clear_layout, field_label, label
from lopanalys.ui.widgets.tiles import MetricTile

log = logging.getLogger(__name__)

SEV_COLOUR = {"ok": "good", "watch": "watch", "fix": "fix"}
AUTOSAVE_MS = 800


class ResultsView(QWidget):
    """Shows one session. ``load_outcome()`` right after a test, ``load_session()`` from history."""

    back = Signal()
    resume_requested = Signal(object)   # ui.context.ResumeFrom
    erase_requested = Signal(int, object)   # athlete id, [the report just saved]: no consent to keep the results
    plan_requested = Signal(object, object)   # the athlete and the test's id: open Träningsschema

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.athlete: Athlete | None = None
        self.session: Session | None = None
        self.records: list[StepRecord] = []
        self.samples: list[TimeSample] = []
        self.gait: dict[str, Any] | None = None
        self.stage_gait: list[dict[str, Any]] = []
        self.lactate: services.LactateView | None = None
        self.zone_list: list[dict[str, Any]] = []
        self.zone_compare: list[dict[str, Any]] = []
        self.garmin: dict[str, Any] | None = None
        self.vo2: dict[str, Any] | None = None
        self._zone_basis: tuple[Any, int | None, int | None] = (None, None, None)   # lactate, max HR, resting HR
        self._max_known = False    # max HR is the athlete's known value or was measured in a test to the limit
        self.max_hr: int | None = None
        self.max_view: services.MaxHrView | None = None
        self.hr_speed: Any = None   # assessments.hr_zones.HrSpeedResult of a zones test without lactate
        self.warmup: list[StepRecord] = []
        self._html: str | None = None
        self._web: Any = None
        self._resume: Any = None
        self._plan_retest = False   # the athlete's plan is built on an older test than the one shown
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(AUTOSAVE_MS)
        self._autosave.timeout.connect(self.save)
        # A pending save is written before the database closes, and no timer outlives the context
        # (an active QTimer destroyed later from another thread crashed the process).
        ctx.closing.connect(self.flush)
        ctx.athlete_erased.connect(self._forget)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 26, 32, 28)
        outer.setSpacing(16)
        self.header = PageHeader("", "")
        b = button(t("ui.common.back"), "chevron-left", kind="ghost")
        b.clicked.connect(self.back)
        self.header.add_action(b)
        self.save_btn = button(t("ui.common.save"), "save")
        self.save_btn.clicked.connect(self.save)
        self.preview_btn = button(t("ui.res.preview"), "eye")
        # clicked(bool) would land in ``switch`` as False and the page never changed: no arguments.
        self.preview_btn.clicked.connect(lambda: self.show_report())
        self.print_btn = button(t("ui.res.print"), "printer")
        self.print_btn.clicked.connect(self.print_report)
        self.pdf_btn = button(t("ui.res.pdf"), "pdf", kind="primary")
        self.pdf_btn.clicked.connect(self.save_pdf)
        for w in (self.save_btn, self.preview_btn, self.print_btn, self.pdf_btn):
            self.header.add_action(w)
        outer.addWidget(self.header)
        # --- hand the test over: the FIT file, and the report by mail
        share = QHBoxLayout()
        share.setSpacing(10)
        self.mail_note = label("", "Hint", wrap=True)     # when it was mailed, or why it cannot be
        share.addWidget(self.mail_note, 1)
        self.fit_btn = button(t("ui.res.fit"), "download")
        self.fit_btn.clicked.connect(lambda: self.save_fit())
        self.mail_btn = button(t("ui.res.mail"), "mail")
        self.mail_btn.clicked.connect(lambda: self.send_to_athlete())
        share.addWidget(self.fit_btn)
        share.addWidget(self.mail_btn)
        outer.addLayout(share)
        self._mailing: Any = None
        self.banner = Card(flat=True, pad=14)
        brow = QHBoxLayout()
        self.banner_text = label("", wrap=True)
        self.banner_text.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        brow.addWidget(self.banner_text, 1)
        self.resume_btn = button("", "play", kind="primary")
        self.resume_btn.clicked.connect(lambda: self._resume is not None and self.resume_requested.emit(self._resume))
        brow.addWidget(self.resume_btn)
        self.banner.body.addLayout(brow)
        self.banner.hide()
        outer.addWidget(self.banner)
        # The training plan: build one on this test, open it, or lay it on this newer test.
        self.plan_card = Card(flat=True, pad=12)
        prow = QHBoxLayout()
        self.plan_text = label("", wrap=True)
        prow.addWidget(self.plan_text, 1)
        self.retest_btn = button(t("ui.plan.retest_apply"), "refresh", kind="primary")
        self.retest_btn.clicked.connect(self.apply_retest)
        prow.addWidget(self.retest_btn)
        self.plan_btn = button(t("ui.plan.title"), "calendar")
        self.plan_btn.clicked.connect(
            lambda: self.athlete and self.plan_requested.emit(self.athlete, self.session.id if self.session else None))
        prow.addWidget(self.plan_btn)
        self.plan_card.body.addLayout(prow)
        self.plan_card.hide()
        outer.addWidget(self.plan_card)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        # --- overview tab
        ov = QWidget()
        ovl = QVBoxLayout(ov)
        ovl.setContentsMargins(0, 12, 0, 0)
        ovl.setSpacing(16)
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_lt1 = MetricTile("LT1", "km/h", "activity")
        self.t_lt2 = MetricTile("LT2", "km/h", "activity")
        self.t_max = MetricTile(t("ui.res.max_hr"), t("ui.unit.bpm"), "heart")
        self.t_dur = MetricTile(t("ui.res.duration"), "", "timer")
        for w in (self.t_lt1, self.t_lt2, self.t_max, self.t_dur):
            w.setFixedHeight(128)
            tiles.addWidget(w)
        # Cycling in watt: FTP with its method, the best minute and Coggan's power zones. The card
        # shares the first place of the page with the tiles, so the order of the rest is as before.
        self.power_card = Card(t("ui.res.power"))
        self.power_lay = QVBoxLayout()
        self.power_lay.setSpacing(6)
        self.power_card.body.addLayout(self.power_lay)
        self.power_card.hide()
        head = QVBoxLayout()
        head.setSpacing(16)
        head.addLayout(tiles)
        head.addWidget(self.power_card)
        ovl.addLayout(head)
        self.power: dict[str, Any] | None = None
        mid = QHBoxLayout()
        mid.setSpacing(16)
        lc = self.curve_card = Card(t("ui.res.lactate_curve"))
        self.methods_row = QWidget()
        top = QHBoxLayout(self.methods_row)
        top.setContentsMargins(0, 0, 0, 0)
        lt1m, lt2m = services.lactate_methods()
        top.addWidget(label("LT1", "Hint"))
        self.method1 = QComboBox()
        self.method1.addItem(t("ui.res.method_auto"), None)
        for mid_, name in lt1m:
            self.method1.addItem(name, mid_)
        top.addWidget(self.method1)
        top.addSpacing(10)
        top.addWidget(label("LT2", "Hint"))
        self.method = QComboBox()
        self.method.addItem(t("ui.res.method_auto"), None)
        for mid_, name in lt2m:
            self.method.addItem(name, mid_)
        top.addWidget(self.method)
        for cb in (self.method1, self.method):
            cb.setToolTip(t("ui.res.method_tip"))
            cb.currentIndexChanged.connect(self.recalculate)
            cb.currentIndexChanged.connect(self._changed)
        top.addStretch(1)
        self.compare_note = label("", "Hint")
        top.addWidget(self.compare_note)
        lc.body.addWidget(self.methods_row)
        self.chart = LactateChart()
        self.chart.background = False
        self.chart.setMinimumHeight(320)
        lc.body.addWidget(self.chart, 1)
        # the zones test without lactate: heart rate against speed instead of the lactate curve
        self.hr_hint = label(t("ui.res.hr_speed_hint"), "Hint", wrap=True)
        lc.body.insertWidget(1, self.hr_hint)
        self.hr_chart = HrSpeedChart()
        self.hr_chart.background = False
        self.hr_chart.setMinimumHeight(320)
        self.hr_chart.empty_text = t("ui.chart.no_steps")
        lc.body.addWidget(self.hr_chart, 1)
        self.hr_quality = label("", "Hint", wrap=True)
        lc.body.addWidget(self.hr_quality)
        self.hr_hint.hide()
        self.hr_chart.hide()
        self.hr_quality.hide()
        self.warn = label("", wrap=True)
        self.warn.setStyleSheet(f"color: {theme.TOKENS['watch']};")
        lc.body.addWidget(self.warn)
        mid.addWidget(lc, 6)
        self.lactate_card = lc
        sc = Card(t("ui.res.stages"))
        self.stages_hint = label(t("ui.res.stages_hint"), "Hint", wrap=True)
        sc.body.addWidget(self.stages_hint)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([t("ui.wiz.col.stage"), "km/h", t("ui.wiz.col.pace"), t("ui.res.col.hr"),
                                              t("ui.res.col.lactate"), "RPE", t("ui.res.col.rise")])
        self.table.setColumnHidden(6, True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setShowGrid(False)
        self.table.itemChanged.connect(self._cell_edited)
        sc.body.addWidget(self.table, 1)
        # SkiErg: the damper as the PM5 measured it, or written in from the monitor
        self.drag_row = QWidget()
        dl = QHBoxLayout(self.drag_row)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)
        dl.addWidget(field_label(t("ui.res.drag")))
        self.drag_edit = QLineEdit()
        self.drag_edit.setFixedWidth(70)
        self.drag_edit.setPlaceholderText("–")
        self.drag_edit.editingFinished.connect(self._drag_edited)
        dl.addWidget(self.drag_edit)
        self.drag_note = label("", "Hint", wrap=True)
        dl.addWidget(self.drag_note, 1)
        self.drag_row.hide()
        sc.body.addWidget(self.drag_row)
        from PySide6.QtWidgets import QPlainTextEdit
        self.comment = QPlainTextEdit()
        self.comment.setPlaceholderText(t("ui.res.comment_hint"))
        self.comment.setFixedHeight(90)
        self.comment.textChanged.connect(self._comment_changed)
        sc.body.addWidget(label(t("ui.res.comment"), "SectionTitle"))
        sc.body.addWidget(self.comment)
        mid.addWidget(sc, 4)
        self._mid_lay = mid
        ovl.addLayout(mid, 1)
        zc = Card(t("ui.res.zones"))
        zrow = QHBoxLayout()
        zrow.addWidget(label(t("ui.res.zone_system"), "Hint"))
        self.zone_system = QComboBox()
        for key, name in services.zone_system_choices():
            self.zone_system.addItem(name, key)
        self.zone_system.setToolTip(t("ui.res.zone_system_tip"))
        self.zone_system.currentIndexChanged.connect(self._zone_system_changed)
        zrow.addWidget(self.zone_system)
        zrow.addSpacing(10)
        self.zone_est = label("")
        self.zone_est.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        zrow.addWidget(self.zone_est)
        zrow.addStretch(1)
        zc.body.addLayout(zrow)
        self.zones_table = QTableWidget(0, 4)
        self.zones_table.setHorizontalHeaderLabels([t("ui.res.col.zone"), t("ui.res.col.hr"), t("ui.res.col.speed"),
                                                    t("ui.res.col.purpose")])
        self.zones_table.verticalHeader().hide()
        hh = self.zones_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        self.zones_table.setShowGrid(False)
        self.zones_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.zones_table.setFixedHeight(250)
        zc.body.addWidget(self.zones_table)
        self.zone_note = label("", "Hint", wrap=True)
        zc.body.addWidget(self.zone_note)
        ovl.addWidget(zc)
        self.zone_card, self._overview = zc, ovl
        low = QHBoxLayout()
        low.setSpacing(16)
        self.watch_card = Card(services.garmin_card_title())
        self.watch_lay = QVBoxLayout()
        self.watch_lay.setSpacing(8)
        self.watch_card.body.addLayout(self.watch_lay)
        self.watch_card.body.addStretch(1)
        low.addWidget(self.watch_card, 6)
        self.vo2_card = Card(t("ui.res.vo2max"))
        self.vo2_lay = QVBoxLayout()
        self.vo2_lay.setSpacing(8)
        self.vo2_card.body.addLayout(self.vo2_lay)
        self.vo2_card.body.addStretch(1)
        low.addWidget(self.vo2_card, 4)
        ovl.addLayout(low)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(ov)
        self.tabs.addTab(sa, t("ui.res.tab.overview"))

        # --- gait tab
        self._analysis: Any = None
        self.analysis_box = Card(flat=True, pad=14)
        self.analysis_text = label(t("ui.res.analyzing"))
        from PySide6.QtWidgets import QProgressBar
        self.analysis_bar = QProgressBar()
        self.analysis_bar.setRange(0, 100)
        self.analysis_bar.setTextVisible(False)
        self.analysis_bar.setFixedHeight(10)
        self.analysis_box.body.addWidget(self.analysis_text)
        self.analysis_box.body.addWidget(self.analysis_bar)
        self.analysis_box.hide()
        gt = QWidget()
        self.gait_lay = QVBoxLayout(gt)
        self.gait_lay.setContentsMargins(0, 12, 0, 0)
        self.gait_lay.setSpacing(14)
        gs = QScrollArea()
        gs.setWidgetResizable(True)
        gs.setWidget(gt)
        self.tabs.addTab(gs, t("ui.res.tab.gait"))

        # --- bike fit / SkiErg technique tab (shown only for a test that has such an analysis)
        from lopanalys.ui.views.technique_panel import TechniquePanel
        self.technique: dict[str, Any] | None = None
        self.tech_panel = TechniquePanel()
        tech_wrap = QWidget()
        tw = QVBoxLayout(tech_wrap)
        tw.setContentsMargins(0, 0, 0, 0)
        self.tech_status = QVBoxLayout()      # the analysis in progress, or why there is none
        self.tech_status.setContentsMargins(0, 12, 0, 0)
        tw.addLayout(self.tech_status)
        tw.addWidget(self.tech_panel, 1)
        ts = QScrollArea()
        ts.setWidgetResizable(True)
        ts.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ts.setWidget(tech_wrap)
        self.tech_tab = ts
        self.tabs.addTab(ts, t("technique.kind.bikefit"))
        self.tabs.setTabVisible(self.tabs.indexOf(ts), False)

        # --- report tab (web view created on first open)
        self.report_tab = QWidget()
        self.report_lay = QVBoxLayout(self.report_tab)
        self.report_lay.setContentsMargins(0, 12, 0, 0)
        self.tabs.addTab(self.report_tab, t("ui.res.tab.report"))
        self.tabs.currentChanged.connect(self._tab_changed)

    # ================================================================== loading
    def load_outcome(self, o: TestOutcome) -> None:
        self.flush()
        self._load(o.plan.athlete, o.session, o.records + list(getattr(o, "warmup_records", [])), o.samples, None,
                   o.gait_summaries)
        if o.session.id is not None:
            self.save()            # the computed results (thresholds, zones, VO2max) belong to the test
        if o.video_path and o.plan.kind in GAIT_KINDS:
            self.analyze_video()   # right away after a gait test, in the background
            self._fill_gait()
        if o.video_path and o.plan.kind in TECHNIQUE_KINDS:
            self.analyze_technique()   # bike fit and double poling: pose.bike and pose.ski, in the background

    def load_session(self, session_id: int) -> None:
        self.flush()
        st = self.ctx.store
        s = st.session(session_id)
        if s is None:
            return
        a = st.athlete(s.athlete_id)
        self._load(a, s, st.records(session_id), st.samples(session_id), st.gait(session_id),
                   [r.gait for r in st.records(session_id) if r.gait])

    def _load(self, a: Athlete | None, s: Session, records: list[StepRecord], samples: list[TimeSample],
              gait: dict[str, Any] | None, stage_gait: list[dict[str, Any]]) -> None:
        self.athlete, self.session = a, s
        warm_idx = {i for i, st in enumerate((s.protocol or {}).get("stages", [])) if st.get("is_warmup")}
        self.warmup = [r for r in records if r.index in warm_idx]
        self.records, self.samples = [r for r in records if r.index not in warm_idx], list(samples)
        self.gait, self.stage_gait = gait, stage_gait
        self._html = None
        name = a.name if a else "–"
        when = s.started.astimezone().strftime("%Y-%m-%d %H:%M") if s.started else ""
        self.header.title.setText(name)
        sim = simulated.label(simulated.of_session(s), s.sport)   # a practice run with a simulator
        self.header.subtitle.setText(f"{s.protocol_name}  ·  {when}" + (f"  ·  {sim}" if sim else ""))
        self.header.subtitle.show()
        res = s.result or {}
        for cb, key in ((self.method1, "lt1_method"), (self.method, "lt2_method")):
            cb.blockSignals(True)
            cb.setCurrentIndex(max(0, cb.findData(res.get(key))))
            cb.blockSignals(False)
        self.zone_system.blockSignals(True)
        self.zone_system.setCurrentIndex(max(0, self.zone_system.findData(res.get("zone_system"))))
        self.zone_system.blockSignals(False)
        self.comment.blockSignals(True)
        self.comment.setPlainText(res.get("coach_comment", ""))
        self.comment.blockSignals(False)
        self.save_btn.setText(t("ui.res.saved"))
        self._fill_banner()
        self._set_layout_for_kind()
        self._fill_table()
        self._fill_drag()
        self._arrange_lactate()
        self.recalculate()
        self.refresh_plan_card()
        self._fill_gait()
        self._fill_technique()
        self._refresh_share()
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.tech_tab) if self.technique or self.tech_kind() else
                                  1 if s.kind == TestKind.GAIT else 0)

    def _refresh_share(self) -> None:
        """*Spara FIT-fil* and *Skicka till idrottaren*: grey with the reason when it cannot be done."""
        s, a = self.session, self.athlete
        if a is not None and a.id is not None:      # the address or the consent may have changed meanwhile
            try:
                fresh = self.ctx.store.athlete(a.id)
            except Exception:
                fresh = None
            if fresh is not None:
                a.email, a.consent = fresh.email, fresh.consent
        saved = s is not None and s.id is not None
        no_fit = None if saved and len(self.samples) >= 2 else t("ui.fit.no_samples")
        self.fit_btn.setEnabled(no_fit is None)
        self.fit_btn.setToolTip(no_fit or t("ui.res.fit_tip"))
        try:
            reason = services.mail_block_reason(self.ctx.store, a) if saved else t("ui.mail.block.no_athlete")
        except Exception:
            log.exception("mail check failed")
            reason = t("ui.mail.block.not_set_up")
        self.mail_btn.setEnabled(reason is None and self._mailing is None)
        self.mail_btn.setToolTip(reason or "")
        self.mail_note.setText(services.mailed_text(s) or (t("ui.mail.blocked", reason=reason) if reason else ""))

    def showEvent(self, e: Any) -> None:   # back from Idrottare or Inställningar: the buttons follow
        super().showEvent(e)
        if self.session is not None:
            self._refresh_share()

    def _hr_zones(self) -> bool:
        """True for the zones test without lactate (steady steps, then the ramp to max)."""
        return bool(self.session and self.session.kind == TestKind.HR_ZONES)

    def _set_layout_for_kind(self) -> None:
        """The zones test without lactate shows heart rate against speed where the other tests show
        the lactate curve, and the heart rate of each step instead of the lactate column."""
        hz = self._hr_zones()
        prof = profile(self._sport())
        power = prof.load_quantity == "power"
        unit = prof.load_unit
        self.chart.x_unit = unit
        self.chart.x_decimals = prof.load_decimals
        # in watt the table has one more column (cadence) and longer cells ("78 (mål 80)"): the first two fit their text
        # in watt the table has one more column (cadence) and a longer cell ("78 (mål 80)"): it gets more room
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents if power else QHeaderView.ResizeMode.Stretch)
        self._mid_lay.setStretch(0, 5 if power else 6)
        self._mid_lay.setStretch(1, 5 if power else 4)
        # column 2: the pace (min/km or /500 m), or W/kg on the bike; column 6: the cadence in watt sports
        self.table.setHorizontalHeaderLabels([
            t("ui.wiz.col.stage"), unit, (prof.pace_unit or "W/kg") if power else t("ui.wiz.col.pace"),
            t("ui.res.col.hr"), t("ui.res.col.lactate"), "RPE",
            t(f"ui.live.cadence.{prof.sport.value}") if power else t("ui.res.col.rise")])
        self.zones_table.setHorizontalHeaderLabels([t("ui.res.col.zone"), t("ui.res.col.hr"),
                                                    t("ui.res.col.power" if power else "ui.res.col.speed"),
                                                    t("ui.res.col.purpose")])
        self.vo2_card.setVisible(not power)      # the estimate is built on the running equation
        self.watch_card.setVisible(prof.sport.value != "skierg")    # no watch setting is handed out for double poling
        self.tabs.setTabVisible(1, not power)    # no technique analysis in these tests
        # Bike fit and double poling measure no thresholds and no running: their own tab only
        tech = self.tech_kind() is not None
        self.tabs.setTabVisible(0, not tech)
        if tech:
            self.tabs.setTabVisible(1, False)
        self.curve_card.title.setText(t("ui.res.hr_speed") if hz else t("ui.res.lactate_curve"))
        for w in (self.methods_row, self.chart):
            w.setVisible(not hz)
        for w in (self.hr_hint, self.hr_chart, self.hr_quality):
            w.setVisible(hz)
        self.stages_hint.setText(t("ui.res.stages_hint_hr") if hz else
                                 t("ui.res.stages_hint_power") if power else t("ui.res.stages_hint"))
        self.drag_row.setVisible(prof.sport.value == "skierg" and not tech)
        self.table.setColumnHidden(4, hz)
        self.table.setColumnHidden(5, hz)      # nobody stands by to ask for the effort: the test runs by itself
        self.table.setColumnHidden(6, not (hz or power))
        self.tabs.setTabText(0, t("ui.res.tab.overview_hr") if hz else t("ui.res.tab.overview"))
        titles = ((t("ui.res.easy_speed"), "km/h"), (t("ui.res.relation"), t("ui.res.relation_unit"))) if hz else \
            (("LT1", unit), ("LT2", unit))
        if self._power_tiles():       # a test in watt without lactate: FTP (cycling) and the best minute instead
            best = (t("ui.res.map"), "W")
            titles = (("FTP", "W"), best) if self._ftp_sport() else (best, best)
        for tile, (title, tunit) in zip((self.t_lt1, self.t_lt2), titles):
            tile.title, tile.unit = title, tunit
            tile.update()

    def _power_tiles(self) -> bool:
        """True when the two first tiles show FTP and the best minute: a test in watt without lactate."""
        s = self.session
        return bool(s and profile(s.sport).load_quantity == "power" and s.kind in (TestKind.FTP, TestKind.MAX_HR)
                    and not self.lactate_measured())

    def _ftp_sport(self) -> bool:
        """FTP is a cycling measure: a SkiErg ramp shows the best minute only."""
        return bool(self.session and sport_of(self.session.sport).value == "bike")

    def _stage_stats(self) -> dict[int, dict[str, Any]]:
        """What the ergometer measured per stage (runner.stage_stats, saved with the test)."""
        res = (self.session.result or {}) if self.session else {}
        return {int(x["index"]): x for x in res.get("stage_stats") or [] if x.get("index") is not None}

    def _fill_banner(self) -> None:
        """Stopped or cut-off tests say so, and offer to go on when that gives a usable test."""
        s = self.session
        self._resume = None
        status = getattr(s, "status", "finished") if s else "finished"
        if s is None or status not in ("interrupted", "stopped"):
            self.banner.hide()
            return
        when = (s.ended or s.started).astimezone().strftime("%H:%M") if (s.ended or s.started) else ""
        lines = [t("ui.recover.banner_interrupted" if status == "interrupted" else "ui.recover.banner_stopped",
                   time=when)]
        if (s.result or {}).get("video_unreadable"):
            lines.append(t("ui.recover.video_lost"))
        if status == "interrupted":
            from lopanalys.ui import recovery
            try:
                self._resume = recovery.resume_info(self.ctx.store, s)
            except Exception:
                log.exception("resume check failed")
        if self._resume is not None:
            st = (s.protocol or {}).get("stages", [])
            n = sum(1 for i, x in enumerate(st[: self._resume.from_index + 1]) if not x.get("is_warmup"))
            self.resume_btn.setText(t("ui.recover.resume", n=n))
        elif status == "interrupted":
            lines.append(t("ui.recover.no_resume"))
        self.resume_btn.setVisible(self._resume is not None)
        self.banner_text.setText("\n".join(lines))
        self.banner.show()

    def refresh_plan_card(self) -> None:
        """Lactate and max HR tests carry the way to the training plan. When the athlete's plan is built
        on an older test than this one, the card offers to lay the rest of the plan on this test."""
        a, s = self.athlete, self.session
        self._plan_retest = False
        if a is None or a.id is None or s is None or s.id is None or not services.plan_test_ok(s):
            self.plan_card.hide()
            return
        text = t("ui.plan.res_none")
        try:
            stored = services.load_plan(self.ctx.store, a)
            if stored is not None:
                cand = services.retest_candidate(self.ctx.store, a, stored)
                cap = stored.plan.request.capacity_for(RUNNING)
                if cand is not None and cand.id == s.id:
                    self._plan_retest = True
                    text = t("ui.plan.res_retest", name=a.first_name, date=cap.tested.isoformat() if cap.tested else "–")
                else:
                    text = t("ui.plan.res_same" if stored.session_id == s.id else "ui.plan.res_other")
        except Exception:
            log.exception("reading the training plan failed")
        self.plan_text.setText(text)
        self.plan_text.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;" if self._plan_retest else "")
        self.retest_btn.setVisible(self._plan_retest)
        self.plan_card.show()

    def apply_retest(self) -> None:
        """The rest of the athlete's plan follows this test: new targets from today, no workout moves."""
        a, s = self.athlete, self.session
        if a is None or s is None or not self._plan_retest:
            return
        self.save()         # the thresholds as they stand on the page
        try:
            services.apply_retest(self.ctx.store, a, s)
        except Exception as e:
            log.exception("retest failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.save_failed", detail=str(e)))
            return
        self.refresh_plan_card()
        from datetime import date
        QMessageBox.information(self, t("ui.plan.title"), t("ui.plan.retest_done", date=date.today().isoformat()))

    def _forget(self, athlete_id: int) -> None:
        """The person shown here is being erased: nothing of the page is written back."""
        if self.athlete is not None and self.athlete.id == athlete_id:
            self._autosave.stop()
            self._analysis = None
            self.athlete, self.session = None, None
            self.records, self.samples, self.gait, self.stage_gait, self._html = [], [], None, [], None

    def _changed(self) -> None:
        """Something the coach changed: save shortly (and at once on leaving the page)."""
        self._html = None
        if self.session is not None and self.session.id is not None:
            self.save_btn.setText(t("ui.common.save"))
            self._autosave.start()

    def hideEvent(self, e: Any) -> None:   # another page is shown: what was changed is saved now
        self.flush()
        super().hideEvent(e)

    def flush(self) -> None:
        """Save a pending change now (leaving the page, opening another test, closing the program)."""
        if self._autosave.isActive():
            self._autosave.stop()
            self.save()

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.records))
        labels = [st.get("label", "") for st in ((self.session.protocol or {}).get("stages", []) if self.session else [])]
        sport = self._sport()
        prof = profile(sport)
        power = prof.load_quantity == "power"
        stats = self._stage_stats() if power else {}
        kg = (self.session.result or {}).get("weight_kg") if self.session else None
        for r, rec in enumerate(self.records):
            name = labels[rec.index] if rec.index < len(labels) and labels[rec.index] else str(r + 1)
            if power:
                name = str(r + 1)       # seven columns: the number alone, as the heading says "Steg"
            load_text, second, last, tip, off = num(rec.speed_kmh, prof.load_decimals), pace(rec.speed_kmh, sport), "", "", False
            if power:
                st = stats.get(rec.index) or {}
                manual = st.get("manual") is not None
                if manual:                                          # written in by hand: marked with *
                    load_text = t("ui.res.load_manual", load=load_text)
                if (st.get("self_paced") or manual) and st.get("target"):   # what was held, and what was asked for
                    load_text = t("ui.res.load_of_target", load=load_text, target=num(st["target"], 0))
                    off = st.get("in_band") is False
                    tip = t("ui.res.off_target_tip" if off else "ui.res.self_paced_tip")
                if manual:
                    tip = t("ui.res.manual_tip", auto=num(st.get("auto"), 0) if st.get("auto") is not None else "–")
                if not prof.pace_unit:
                    wkg = services.w_per_kg(rec.speed_kmh, kg)
                    second = num(wkg, 2) if wkg else "–"
                last = num(st["cadence"], 0) if st.get("cadence") else "–"
            vals = [name, load_text, second, str(rec.hr_end or "–"),
                    num(rec.lactate_mmol) if rec.lactate_mmol is not None else "", str(rec.rpe or ""), last]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c not in (4, 5) and not (power and c == 1):   # the power: written in when the monitor dropped
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c == 1 and tip:
                    it.setToolTip(tip)
                    if off:
                        it.setForeground(theme.color("watch"))
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)

    def lactate_measured(self) -> bool:
        """True when the test takes lactate samples or a value was entered."""
        stages = (self.session.protocol or {}).get("stages", []) if self.session else []
        if any(st.get("sample_lactate") for st in stages):
            return True
        if self.session and self.session.kind == TestKind.LACTATE_STEP and not stages:
            return True
        return any(r.lactate_mmol is not None for r in self.records + self.warmup)

    def _arrange_lactate(self) -> None:
        """A test without lactate shows none: no lactate curve, no lactate column, no threshold methods
        or LT tiles, and the max HR and the heart rate zones come first."""
        has = self.lactate_measured()
        # The zones test without lactate uses the same card and tiles for heart rate against speed.
        shown = has or self._hr_zones()
        self.lactate_card.setVisible(shown)
        self.t_lt1.setVisible(shown or self._power_tiles())
        self.t_lt2.setVisible(shown or (self._power_tiles() and self._ftp_sport()))
        self.table.setColumnHidden(4, not has)
        ovl, zc = self._overview, self.zone_card
        ovl.removeWidget(zc)
        ovl.insertWidget(2 if shown else 1, zc)   # tiles, [zones,] curve and stages, [zones,] watch and VO2max

    def _fill_step_hr(self) -> None:
        """The zones test: the settled heart rate of each step and how much it still rose."""
        pts = {p.index: p for p in (self.hr_speed.points if self.hr_speed else [])}
        self.table.blockSignals(True)
        for r, rec in enumerate(self.records):
            p = pts.get(rec.index)
            if p is None or self.table.item(r, 3) is None:
                continue
            self.table.item(r, 3).setText(num(p.hr, 0) if p.hr is not None else "–")
            rise = "–" if p.rise_bpm is None else f"{'+' if p.rise_bpm > 0 else ''}{num(p.rise_bpm)}"
            it = self.table.item(r, 6)
            it.setText(f"{rise}  {t('ui.res.not_settled')}" if p.stable is False else rise)
            it.setForeground(theme.color("watch" if p.stable is False else "text"))
        self.table.blockSignals(False)

    def _fill_hr_speed(self, mx: int | None) -> None:
        """Tiles, chart and warnings of the zones test without lactate."""
        rel = self.hr_speed
        pts = [(p.speed_kmh, p.hr, p.stable is not False) for p in (rel.points if rel else []) if p.hr is not None]
        n = len(self.zone_list)
        bands = [(z["hr_low"], z["hr_high"], theme.zone_color(i, n), z["code"]) for i, z in enumerate(self.zone_list)
                 if z.get("hr_low") and z.get("hr_high")]
        self.hr_chart.set_data(pts, rel.line() if rel else [], bands, mx if self._max_known else None)
        self.hr_quality.setText(services.hr_speed_quality(rel))
        warns = [w["text"] for w in (rel.warnings if rel else [])]
        self.warn.setText("\n".join(warns))
        self.warn.setVisible(bool(warns))
        if rel is not None and rel.ok:
            self.t_lt2.set_value(num(rel.slope), t("ui.res.relation_sub", n=len(rel.used), r2=num(rel.r2, 2),
                                                   see=num(rel.see_bpm)),
                                 {"good": "good", "fair": None, "poor": "watch"}.get(rel.quality or ""))
        else:
            self.t_lt2.set_value("–", t("ui.res.relation_none") if self.records else "")
        easy = services.easy_zone(self.zone_list)
        if easy and (easy.get("speed_low") or easy.get("speed_high")):
            self.t_lt1.set_value(self._speed_range(easy, unit=False),
                                 t("ui.res.easy_sub", zone=easy["code"], hr=f"{easy['hr_low']}–{easy['hr_high']}",
                                   pace=self._pace_range(easy)))
        else:
            self.t_lt1.set_value("–", "")
        self._fill_step_hr()

    @staticmethod
    def _speed_range(z: dict[str, Any], unit: bool = True, sport: Any = None) -> str:
        """"≈7,6–9,0 km/h": the loads of a zone in the sport's unit ("150–175 W", with the pace per
        500 m on a SkiErg), ≈ before a border outside the measured steps."""
        prof = profile(sport)
        dec = prof.load_decimals
        lo, hi = z.get("speed_low"), z.get("speed_high")
        a = ("≈" if z.get("speed_low_extrapolated") else "") + num(lo, dec) if lo else ""
        b = ("≈" if z.get("speed_high_extrapolated") else "") + num(hi, dec) if hi else ""
        if not a and not b:
            return ""
        txt = f"{a}–{b}" if a and b else (f"< {b}" if b else f"> {a}")
        if not unit:
            return txt
        txt = f"{txt} {prof.load_unit}"
        if prof.load_quantity == "power" and prof.pace_unit:
            pa, pb = (pace(lo, sport) if lo else ""), (pace(hi, sport) if hi else "")
            txt += "  ·  " + (f"{pa}–{pb}" if pa and pb else (f"> {pb}" if pb else f"< {pa}")) + f" {prof.pace_unit}"
        return txt

    @staticmethod
    def _pace_range(z: dict[str, Any]) -> str:
        lo, hi = z.get("speed_low"), z.get("speed_high")
        return f"{pace(lo)}–{pace(hi)}" if lo and hi else (f"> {pace(hi)}" if hi else f"< {pace(lo)}")

    def _cell_edited(self, it: QTableWidgetItem) -> None:
        rec = self.records[it.row()]
        txt = it.text().strip().replace(",", ".")
        try:
            if it.column() == 4:
                v = float(txt) if txt else None
                if v is not None and not 0 < v <= 30:
                    raise ValueError
                rec.lactate_mmol = v
            elif it.column() == 5:
                v2 = int(txt) if txt else None
                if v2 is not None and not 6 <= v2 <= 20:
                    raise ValueError
                rec.rpe = v2
            elif it.column() == 1 and self.session is not None:
                # the mean power from the monitor: "182", or the cell as shown ("182* (mål 180)"); empty
                # takes the entry back
                m = re.match(r"\s*(\d+(?:\.\d+)?)", txt)
                if txt and m is None:
                    raise ValueError
                erg_values.set_manual_load(self.session, rec, float(m.group(1)) if m else None)
                self._fill_table()
        except ValueError:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.res.bad_value"))
            self._fill_table()
            return
        self.recalculate()
        self._changed()

    def _fill_drag(self) -> None:
        d = erg_values.drag_factor(self.session)
        self.drag_edit.blockSignals(True)
        self.drag_edit.setText(str(d["value"]) if d else "")
        self.drag_edit.blockSignals(False)
        self.drag_note.setText(t("ui.res.drag_pm5") if d and d.get("source") == "pm5" else
                               t("ui.res.drag_manual") if d else t("ui.res.drag_missing"))

    def _drag_edited(self) -> None:
        s = self.session
        if s is None:
            return
        txt = self.drag_edit.text().strip()
        old = erg_values.drag_factor(s)
        if txt == (str(old["value"]) if old else ""):
            return
        try:
            erg_values.set_drag_factor(s, int(txt) if txt else None)
        except ValueError:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.res.drag_bad", low=erg_values.DRAG_RANGE[0],
                                                          high=erg_values.DRAG_RANGE[1]))
        self._fill_drag()
        self._changed()

    def recalculate(self, *_: Any) -> None:
        """Re-run threshold analysis with the selected method and refresh tiles, chart and zones."""
        a = self.athlete
        try:
            self.max_view = services.analyze_max_hr(self.samples, self.records,
                                                    (self.session.result or {}).get("exhaustion_t_s") if self.session else None,
                                                    self._age(), self._sport())
        except Exception:
            log.exception("max hr analysis failed")
            self.max_view = None
        self.max_hr = (self.max_view.peak if self.max_view else None) or \
            max((r.hr_end or 0 for r in self.records), default=0) or None
        if not services.own_peak_counts(self.session):    # stopped, not a max test: no max HR, no zones from it
            self.max_hr = None
        sport = self._sport()
        known = a.max_hr_for(sport) if a else None      # the max HR of the test's sport
        mx = known or self.max_hr
        try:
            self.lactate = services.analyze_lactate(self.records, self.method1.currentData(),
                                                    self.method.currentData(), mx, sport)
        except Exception:
            log.exception("lactate analysis failed")
            self.lactate = None
        self.warn.setText("\n".join(self.lactate.warnings) if self.lactate else "")
        self.warn.setVisible(bool(self.lactate and self.lactate.warnings))
        self.hr_speed = None
        if self._hr_zones():
            self.hr_speed = services.analyze_hr_speed(self.records, self.samples, self.session.protocol,
                                                      (self.session.result or {}).get("exhaustion_t_s"))
        rest = a.resting_hr if a else None
        self._zone_basis = (self.lactate if self.lactate and self.lactate.lt2 else None, mx, rest)
        # The highest heart rate of a test that stopped short of the limit is not a max HR: it may
        # place the zones, but it is neither extrapolated to (VO2max) nor handed out for the watch.
        self._max_known = bool(known) or self._to_exhaustion()
        self.vo2 = services.estimate_vo2max(self.records, mx if self._max_known else None, rest,
                                            self._to_exhaustion(), self.session.protocol if self.session else None,
                                            self.max_view.likely_max if self.max_view else None)
        lv = self.lactate
        pts = [(sp, la) for sp, la, _ in lv.points] if lv else []
        hrp = [(r.speed_kmh, float(r.hr_end)) for r in self.records if r.hr_end]
        lt1 = (lv.lt1.speed_kmh, lv.lt1.lactate_mmol or 0) if lv and lv.lt1 and lv.lt1.speed_kmh else None
        lt2 = (lv.lt2.speed_kmh, lv.lt2.lactate_mmol or 0) if lv and lv.lt2 and lv.lt2.speed_kmh else None
        self.chart.set_data(pts, lv.curve if lv else [], lt1, lt2, hrp)
        self._compare_previous()
        prof = profile(sport)
        kg = (self.session.result or {}).get("weight_kg") if self.session else None
        for tile, thr in ((self.t_lt1, lv.lt1 if lv else None), (self.t_lt2, lv.lt2 if lv else None)):
            if thr and thr.speed_kmh and prof.load_quantity == "power":
                # watt, with W/kg on the bike and the pace per 500 m on the SkiErg
                wkg = services.w_per_kg(thr.speed_kmh, kg)
                extra = f"{pace(thr.speed_kmh, sport)} {prof.pace_unit}" if prof.pace_unit else (
                    f"{num(wkg, 2)} W/kg" if wkg else t("ui.res.no_weight"))
                tile.set_value(num(thr.speed_kmh, 0), t("ui.res.thr_sub_power", hr=num(thr.hr, 0), extra=extra,
                                                        la=num(thr.lactate_mmol)))
            elif thr and thr.speed_kmh:
                tile.set_value(num(thr.speed_kmh), t("ui.res.thr_sub", hr=num(thr.hr, 0), pace=pace(thr.speed_kmh),
                                                     la=num(thr.lactate_mmol)))
            else:
                tile.set_value("–", t("ui.res.too_few") if self.records else "")
        self._fill_power()
        sub = ""
        if (self.max_view and self.max_view.likely_max is not None and self.session
                and self.session.kind in MAX_KINDS and self._to_exhaustion()):
            sub = t("ui.res.likely_max") if self.max_view.likely_max else t("ui.res.maybe_not_max")
        elif self.max_hr:
            sub = t("ui.res.max_hr_sub")
        # "Maxpuls" only when the criteria were met
        self.t_max.title = t("ui.res.max_likely") if self.max_view and self.max_view.likely_max else (
            t("ui.res.peak_not_max") if self.max_view and self.max_view.likely_max is False else t("ui.res.max_hr"))
        self.t_max.update()
        if known and self.max_hr and self.max_hr < known:
            sub = (sub + " · " if sub else "") + t("ui.res.saved_max", saved=known)
        self.t_max.set_value(str(self.max_hr or "–"), sub, "good" if sub == t("ui.res.likely_max") else None)
        dur = (self.session.ended - self.session.started).total_seconds() if self.session and self.session.ended else \
            sum(r.duration_s for r in self.records)
        self.t_dur.set_value(clock(dur), t("ui.res.n_stages", n=len(self.records)))
        self._fill_vo2()
        self._refresh_zones()
        if self._hr_zones():
            self._fill_hr_speed(mx)

    def _fill_power(self) -> None:
        """FTP, the best minute and the power zones of a cycling test in watt (and the best minute of
        a SkiErg ramp). Every number says how it was computed; FTP is never called a threshold from lactate."""
        self.power = services.analyze_power(self.session, self.samples)
        clear_layout(self.power_lay)
        pw = self.power
        show = bool(pw) and self.session is not None and self.session.kind in (TestKind.FTP, TestKind.MAX_HR)
        self.power_card.setVisible(show)
        if not show or pw is None:
            return
        kg = pw.get("weight_kg")

        def wkg(v: Any) -> str:
            return f" ({num(v, 2)} W/kg)" if v else ""
        if pw.get("ftp_w"):
            self.power_lay.addWidget(self._strong(t("ui.res.ftp_line", w=num(pw["ftp_w"], 0), wkg=wkg(pw.get("ftp_w_per_kg")),
                                                    method=pw.get("method_text", ""))))
        if pw.get("mean_20min_w") and pw.get("method") == "ftp20":
            self.power_lay.addWidget(label(t("ui.res.mean20_line", w=num(pw["mean_20min_w"], 0),
                                             hr=num(pw.get("hr_mean_20min"), 0)), wrap=True))
        if pw.get("map_w"):
            self.power_lay.addWidget(label(t("ui.res.map_line", w=num(pw["map_w"], 0), wkg=wkg(pw.get("map_w_per_kg"))),
                                           wrap=True))
        if pw.get("cadence_mean"):
            self.power_lay.addWidget(label(t(f"ui.res.cadence_line.{sport_of(self._sport()).value}",
                                             n=num(pw["cadence_mean"], 0)), "Hint", wrap=True))
        if pw.get("ftp_w") and not kg:
            self.power_lay.addWidget(label(t("ui.res.no_weight"), "Hint", wrap=True))
        for w in pw.get("warnings") or []:
            lb = label(w.get("text", ""), wrap=True)
            lb.setStyleSheet(f"color: {theme.TOKENS['watch']};")
            self.power_lay.addWidget(lb)
        if pw.get("zones"):
            self.power_lay.addWidget(label(t("ui.res.power_zones") + "  " + "   ".join(
                f"{z['code'].upper()} {z['name']}: " + (f"{z['low_w']}–{z['high_w']}" if z.get("low_w") and z.get("high_w")
                                                       else (f"< {z['high_w']}" if z.get("high_w") else f"> {z['low_w']}"))
                + " W" for z in pw["zones"]), "Hint", wrap=True))
            self.power_lay.addWidget(label(t("ui.res.ftp_note"), "Hint", wrap=True))
        if self._power_tiles():
            best = (num(pw.get("map_w"), 0) if pw.get("map_w") else "–",
                    f"{num(pw['map_w_per_kg'], 2)} W/kg" if pw.get("map_w_per_kg") and self._ftp_sport()
                    else t("ui.res.map_sub"))
            if self._ftp_sport():
                self.t_lt1.set_value(num(pw.get("ftp_w"), 0) if pw.get("ftp_w") else "–",
                                     (pw.get("method_text") or "") if pw.get("ftp_w") else t("ui.res.ftp_none"))
                self.t_lt2.set_value(*best)
            else:
                self.t_lt1.set_value(*best)

    def _to_exhaustion(self) -> bool:
        """True when the athlete ran to the limit (the coach pressed Utmattad, or the belt was stopped
        on the panel): only then does the last stage say anything about the maximum. In the zones
        test without lactate the ramp must have begun: a stop in the steady steps is not a max."""
        if not self.session or (self.session.result or {}).get("exhaustion_t_s") is None:
            return False
        return not self._hr_zones() or bool(self.hr_speed is not None and self.hr_speed.ramp_reached)

    def _zone_system_changed(self, *_: Any) -> None:
        self._refresh_zones()
        self._changed()

    def _refresh_zones(self) -> None:
        """Zones in the chosen system, the comparison between systems and the values for the watch."""
        lv, mx, rest = self._zone_basis
        sport = self._sport()
        self.zone_list = services.zones(lv, mx, rest, self.zone_system.currentData(), sport)
        if self.hr_speed is not None and lv is None:     # speed from heart rate, not from lactate
            self.zone_list = services.with_zone_speeds(self.zone_list, self.hr_speed)
        self.zone_compare = services.zone_comparison(lv, mx, rest, sport)
        self.garmin = services.garmin_setup(lv, mx, rest, self._max_known, sport)
        self._fill_zones()
        self._fill_watch()
        self._html = None

    def _fill_watch(self) -> None:
        """The card "Fyll i din klocka": what the athlete types into Garmin Connect."""
        clear_layout(self.watch_lay)
        card = services.garmin_card(self.garmin)
        if card is None:
            self.watch_lay.addWidget(label(t("ui.res.watch_none"), "Hint", wrap=True))
            return
        self.watch_lay.addWidget(label(card["intro"], wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        for r, f in enumerate(card["fields"]):
            grid.addWidget(label(f["label"], "Hint"), r, 0)
            grid.addWidget(self._strong(f["value"]), r, 1)
        for r, z in enumerate(card["zones"]):
            grid.addWidget(label(z["label"], "Hint"), r, 3)
            grid.addWidget(self._strong(z["bpm"]), r, 4)
            grid.addWidget(label(z["pct"], "Hint"), r, 5)
        grid.setColumnMinimumWidth(2, 28)
        grid.setColumnStretch(6, 1)
        self.watch_lay.addLayout(grid)
        self.watch_lay.addWidget(label(f"{card['why_title']} {card['why']}", "Hint", wrap=True))

    def _fill_vo2(self) -> None:
        """Estimated VO2max: never a lone number, always with interval, method and the disclaimer."""
        clear_layout(self.vo2_lay)
        card = services.vo2max_card(self.vo2, self.athlete)
        if card is None or card.get("none"):
            # rather no value than a half-good one; say why
            self.vo2_lay.addWidget(self._strong(card["text"]) if card else label(t("ui.res.vo2_none"), "Hint", wrap=True))
            for r in (card or {}).get("reasons", []):
                self.vo2_lay.addWidget(label(r, "Hint", wrap=True))
            if card and card.get("watch"):
                self.vo2_lay.addWidget(label(card["watch"]["text"], wrap=True))
            return
        big = label(f"{card['value']} {card['unit']}")
        big.setStyleSheet("font-size: 30px; font-weight: 700;")
        self.vo2_lay.addWidget(big)
        self.vo2_lay.addWidget(self._strong(card["range"]))
        self.vo2_lay.addWidget(label(card["method"], "Hint", wrap=True))
        for o in card["others"]:
            self.vo2_lay.addWidget(label(f"{o['method']}: {o['value']} · {o['range']}", "Hint", wrap=True))
        if card.get("watch"):
            self.vo2_lay.addWidget(label(card["watch"]["text"], wrap=True))
            if card["watch"]["warn"]:
                w = label(card["watch"]["warn"], wrap=True)
                w.setStyleSheet(f"color: {theme.TOKENS['watch']};")
                self.vo2_lay.addWidget(w)
        self.vo2_lay.addWidget(label(card["disclaimer"], wrap=True))

    @staticmethod
    def _strong(text: str) -> QLabel:
        lb = label(text)
        lb.setStyleSheet("font-weight: 600;")
        return lb

    def _compare_previous(self) -> None:
        """Overlay the previous test of the same kind (if any) on the lactate chart."""
        self.chart.others = []
        self.chart.legend = ""
        self.compare_note.setText("")
        prev = self.previous_session()
        if prev is None or prev.id is None:
            return
        try:
            recs = self._work_records(prev, self.ctx.store.records(prev.id))
        except Exception:
            return
        pts = [(r.speed_kmh, r.lactate_mmol) for r in recs if r.lactate_mmol is not None]
        if pts:
            date_s = prev.started.astimezone().strftime("%Y-%m-%d")
            self.chart.others = [(t("ui.res.previous", date=date_s), "muted", pts)]
            self.chart.legend = t("ui.res.this_test")
            self.compare_note.setText(t("ui.res.compared", date=date_s))
            self.chart.update()

    def _sport(self) -> Any:
        """The sport of the test on screen (running for a test from before sport existed)."""
        return self.session.sport if self.session else None

    def previous_session(self) -> Session | None:
        if not self.session or not self.athlete or self.athlete.id is None:
            return None
        try:
            return self.ctx.store.previous_session(self.session)
        except Exception:
            log.exception("previous session lookup failed")
            return None

    def _age(self) -> float | None:
        a = self.athlete
        if not a or not a.birth_date or not self.session:
            return None
        d = self.session.started.date()
        return (d - a.birth_date).days / 365.25

    def _comment_changed(self) -> None:
        self._changed()

    @staticmethod
    def _work_records(s: Session, records: list[StepRecord]) -> list[StepRecord]:
        warm = {i for i, st in enumerate((s.protocol or {}).get("stages", [])) if st.get("is_warmup")}
        return [r for r in records if r.index not in warm]

    def _fill_zones(self) -> None:
        zs = self.zone_list
        estimated = any(z.get("estimated") for z in zs)
        from_hr = any(z.get("speed_basis") == "hr" for z in zs)
        self.zone_est.setText(" · ".join(x for x in (services.zones_estimated_text() if estimated else "",
                                                     services.speed_from_hr_text() if from_hr else "") if x))
        note = services.zone_system_info(self.zone_system.currentData())["note"] if zs else ""
        if from_hr:
            note = f"{note} {services.speed_from_hr_text(long=True)} {services.extrapolated_text(self.hr_speed)}".strip()
        basis = services.zone_basis(self._zone_basis[1], self.athlete, self.max_view, self.session) if self.zone_list else ""
        own = ""
        sport = self._sport()
        if (sport_of(sport) is not DEFAULT_SPORT and self.athlete is not None and not self.athlete.max_hr_for(sport)
                and self._zone_basis[0] is None and not self._to_exhaustion()):
            # no max HR and no thresholds in this sport: say so, and never borrow another sport's
            own = services.no_own_test_text(sport)
        self.zone_note.setText("\n".join(x for x in (own, basis, note) if x))   # which max HR the zones use
        self.zones_table.setRowCount(len(zs))
        self.zones_table.setFixedHeight(250 + 40 * max(0, len(zs) - 5))   # Friel has seven zones
        for r, z in enumerate(zs):
            col = theme.zone_color(r, len(zs))
            hr = f"{num(z['hr_low'], 0)}–{num(z['hr_high'], 0)}" if z.get("hr_low") and z.get("hr_high") else \
                (f"< {num(z['hr_high'], 0)}" if z.get("hr_high") else (f"> {num(z['hr_low'], 0)}" if z.get("hr_low") else "–"))
            sp = self._speed_range(z, sport=sport)
            for c, v in enumerate([z["name"], hr, sp or "–", z.get("description", "")]):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setIcon(self._swatch(col))
                self.zones_table.setItem(r, c, it)

    @staticmethod
    def _swatch(col: str) -> Any:
        from PySide6.QtGui import QIcon, QPainter
        pm = QPixmap(14, 14)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(theme.color(col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 14, 14, 4, 4)
        p.end()
        return QIcon(pm)

    # ================================================================== bike fit and SkiErg technique
    def tech_kind(self) -> str | None:
        """"bikefit" or "ski" for a bike fit or a double poling test, else None."""
        s = self.session
        kind = getattr(getattr(s, "kind", None), "value", None)
        return kind if kind in TECHNIQUE_KINDS else None

    def _fill_technique(self) -> None:
        """The tab for a bike fit or a SkiErg test: shown for those tests, and for an older test that has
        such an analysis saved. While the film is analysed the progress stands at the top."""
        s = self.session
        self.technique = services.technique_record(self.ctx.store, s) if s is not None else None
        kind = (self.technique or {}).get("kind") or self.tech_kind()
        i = self.tabs.indexOf(self.tech_tab)
        self.tabs.setTabVisible(i, bool(kind))
        if kind:
            self.tabs.setTabText(i, t(f"technique.kind.{kind}"))
        self.tech_status.removeWidget(self.analysis_box)    # the shared progress box is moved, never deleted
        if self.tech_kind():
            self.gait_lay.removeWidget(self.analysis_box)
        clear_layout(self.tech_status)
        if self.tech_kind():
            self.tech_status.addWidget(self.analysis_box)
            if not self.technique and self._analysis is None and s is not None:
                res = s.result or {}
                if s.video_path:
                    b = button(t("ui.res.analyze_video"), "activity", kind="primary")
                    b.clicked.connect(self.analyze_technique)
                    row = QHBoxLayout()
                    row.addWidget(b)
                    row.addStretch(1)
                    self.tech_status.addLayout(row)
                elif res.get("no_film_consent"):
                    self.tech_status.addWidget(label(t("ui.res.no_film_consent"), "Hint", wrap=True))
                elif res.get("video_removed"):
                    self.tech_status.addWidget(label(t("ui.res.video_removed", date=res["video_removed"]), "Hint",
                                                     wrap=True))
                else:
                    self.tech_status.addWidget(label(t("ui.res.tech_no_film"), "Hint", wrap=True))
        self.tech_panel.show_record(self.technique, self._save_checklist)

    def analyze_technique(self) -> None:
        """Bike fit or double poling: each film (bike fit: the side and the front) is analysed after
        the other in a worker (services.analyze_technique) and saved as a row of its own."""
        s, a, kind = self.session, self.athlete, self.tech_kind()
        if not s or not kind or not s.video_path or self._analysis is not None:
            return
        kf = paths.keyframes_dir() / f"session-{s.id or 'new'}"
        kf.mkdir(parents=True, exist_ok=True)
        res0 = dict(s.result or {})
        films = [(s.video_path, str(res0.get("view") or "side_left"))]
        if res0.get("second_video"):
            films.append((str(res0["second_video"]), str(res0.get("second_view") or "front")))
        height = a.height_cm if a else None
        # the athlete's last analysis of the same kind and view, read here (the database stays on this thread)
        previous = {v: (services.previous_or_none(self.ctx.store, s, kind, v)) for _, v in films}
        is_demo = bool(res0.get("demo_camera"))
        session_id = s.id
        self.analysis_bar.setValue(0)
        self.analysis_text.setText(t("ui.res.analyzing"))
        self.analysis_box.show()

        def work(progress: Any) -> Any:
            out: list[dict[str, Any]] = []
            errors: list[Exception] = []
            for n, (path, view) in enumerate(films):
                def cb(v: float, *_: Any, n: int = n) -> None:
                    progress((n + max(0.0, min(1.0, v))) / len(films))
                try:
                    out.append(services.analyze_technique(kind, path, height, view, cb, kf, previous[view],
                                                          demo=is_demo))
                except Exception as e:
                    log.exception("analysing the %s film failed", view)
                    errors.append(e)
            if not out:
                raise errors[0]
            return out

        def save(res: Any) -> None:
            if not res or session_id is None:
                return
            try:
                if self.ctx.store.session(session_id) is None:   # erased meanwhile: its new key frames go too
                    import shutil
                    shutil.rmtree(kf, ignore_errors=True)
                    return
                for d in res:
                    self.ctx.store.save_technique(session_id, d)
                if str(self.ctx.setting("privacy.video_retention")) == "after_analysis":
                    self.ctx.store.db.remove_session_video(session_id)   # the analysis is saved: the film goes
            except Exception:
                log.exception("saving the technique analysis failed")

        def done(res: Any) -> None:
            self._analysis = None
            self.analysis_box.hide()
            if self.session is None or self.session.id != session_id:
                return
            fresh = self.ctx.store.session(session_id)
            if fresh is not None:
                self.session.video_path = fresh.video_path
                self.session.result = dict(fresh.result or {})
            self._html = None
            self._fill_technique()

        def fail(msg: str) -> None:
            self._analysis = None
            self.analysis_box.hide()
            self._fill_technique()
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.analysis", detail=msg))
        self._analysis = run_async(work, on_done=lambda r: (save(r), done(r)), on_error=fail,
                                   on_progress=lambda v: self.analysis_bar.setValue(int(v * 100)))
        self._fill_technique()

    def _save_checklist(self, values: dict[str, Any]) -> None:
        if self.session is None:
            return
        services.save_checklist(self.ctx.store, self.session, values)
        self._html = None
        self._fill_technique()

    # ================================================================== gait
    def _fill_gait(self) -> None:
        if self.tech_kind():          # bike fit and double poling: the progress stands in their own tab
            return
        if self.analysis_box.parent() is not None:
            self.gait_lay.removeWidget(self.analysis_box)
        clear_layout(self.gait_lay)
        self.gait_lay.addWidget(self.analysis_box)
        # the strap's cadence (RSC) before the camera's, when the test has it
        gait = with_strap_cadence(self.gait, self.samples, self.athlete.height_cm if self.athlete else None)
        findings = (gait or {}).get("findings") or []
        keyframes = (gait or {}).get("keyframes") or []
        note = self._window_note((gait or {}).get("window"))
        if note:
            self.gait_lay.addWidget(label(note, "Hint", wrap=True))
        # the analysis's warnings: uncertain landings, values the frame rate cannot carry
        warns = [str(w) for w in (gait or {}).get("warnings") or []]
        if warns:
            wl = label("\n".join(warns), "Hint", wrap=True)
            wl.setStyleSheet(f"color: {theme.TOKENS['watch']};")
            self.gait_lay.addWidget(wl)
        if not findings and self.stage_gait:
            findings = self._findings_from_live(self.stage_gait)
        if self.session and (self.session.result or {}).get("no_film_consent") and not self.session.video_path:
            self.gait_lay.addWidget(label(t("ui.res.no_film_consent"), "Hint", wrap=True))
        removed = (self.session.result or {}).get("video_removed") if self.session else None
        if removed and not self.session.video_path:
            self.gait_lay.addWidget(label(t("ui.res.video_removed", date=removed), "Hint", wrap=True))
        # Two views: the second camera's findings under a heading of their own.
        second = (gait or {}).get("second") or {}
        res = (self.session.result or {}) if self.session else {}
        findings2 = second.get("findings") or []
        if not findings2:
            findings2 = self._findings_from_live([g["second"] for g in self.stage_gait if g.get("second")])
        two = bool(findings2 or second or res.get("second_view"))
        analysed = bool(self.gait) and ("summary" in self.gait or not res.get("second_video"))
        if self.session and self.session.video_path and not analysed and self._analysis is None:
            row = QHBoxLayout()
            row.addWidget(label(t("ui.res.video_ready"), "Hint", wrap=True), 1)
            b = button(t("ui.res.analyze_video"), "activity", kind="primary")
            b.clicked.connect(self.analyze_video)
            row.addWidget(b)
            self.gait_lay.addLayout(row)
        if not findings and not findings2:
            c = Card()
            c.body.addWidget(label(t("ui.res.no_gait"), "PageSubtitle", wrap=True))
            self.gait_lay.addWidget(c)
            self.gait_lay.addStretch(1)
            return
        views = [(str((gait or {}).get("view") or res.get("view") or ""), findings, keyframes, None)]
        if two:
            views.append((str(second.get("view") or res.get("second_view") or "rear"), findings2,
                          second.get("keyframes") or [], second.get("sync")))
        for view, fs, kfs, sync in views:
            if two:
                self.gait_lay.addWidget(label(t("ui.res.view_title", view=t(f"ui.view.{view}") if view else "–"),
                                              "SectionTitle"))
                if sync and sync.get("matched"):
                    self.gait_lay.addWidget(label(t("ui.res.view_sync", n=sync["matched"],
                                                    of=min(sync.get("first", 0), sync.get("second", 0)),
                                                    ms=num(abs(sync.get("lag_s", 0.0)) * 1000, 0)), "Hint", wrap=True))
            if not fs:
                self.gait_lay.addWidget(label(t("ui.res.view_empty"), "Hint", wrap=True))
            grid = QGridLayout()
            grid.setSpacing(14)
            for i, f in enumerate(fs):
                grid.addWidget(self._finding_card(f), i // 2, i % 2)
            self.gait_lay.addLayout(grid)
            if kfs:
                self.gait_lay.addWidget(self._keyframes_card(kfs))
        self.gait_lay.addStretch(1)

    def _keyframes_card(self, keyframes: list[Any]) -> Card:
        kc = Card(t("ui.res.keyframes"))
        row = QHBoxLayout()
        row.setSpacing(12)
        for kf in keyframes[:6]:
            path = kf.get("image_path") if isinstance(kf, dict) else getattr(kf, "image_path", "")
            lab = kf.get("label") if isinstance(kf, dict) else getattr(kf, "label", "")
            col = QVBoxLayout()
            img = QLabel()
            pm = QPixmap(str(path))
            if not pm.isNull():
                img.setPixmap(pm.scaled(QSize(260, 200), Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
            col.addWidget(img)
            col.addWidget(label(lab, "Hint"))
            row.addLayout(col)
        row.addStretch(1)
        kc.body.addLayout(row)
        return kc

    @staticmethod
    def _findings_from_live(stage_gait: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Simple findings from the live per-stage summaries (when no offline analysis exists)."""
        out = []
        names = {"overstride_cm": ("ui.gait.overstride", "cm"), "shin_angle_deg": ("ui.gait.shin", "°"),
                 "trunk_lean_deg": ("ui.gait.trunk", "°"), "vertical_oscillation_cm": ("ui.gait.vo", "cm"),
                 "cadence_spm": ("ui.live.cadence", t("ui.unit.spm")), "pelvic_drop_deg": ("ui.gait.pelvic_drop", "°"),
                 "knee_valgus_deg": ("ui.gait.valgus", "°"), "step_width_cm": ("ui.gait.step_width", "cm"),
                 "arm_cross_cm": ("ui.gait.arm_cross", "cm")}
        strap = any(g.get("cadence_source") == "strap" for g in stage_gait)
        for key, (title, unit) in names.items():
            vals = [g.get(key) for g in stage_gait if g.get(key) is not None]
            if not vals:
                continue
            v = sorted(vals)[len(vals) // 2]
            sev = gait_status(key, v) or "ok"
            name = cadence_label("strap" if strap else None, title) if key == "cadence_spm" else t(title)
            xkey = f"ui.gaitx.{key}"
            out.append(dict(code=key, severity=sev, title=name, value=v, unit=unit,
                            reference=gait_ref(key), explanation=t(xkey) if t(xkey) != xkey else "", cue=""))
        return out

    def _finding_card(self, f: Any) -> Card:
        g = (lambda k: f.get(k)) if isinstance(f, dict) else (lambda k: getattr(f, k, None))
        sev = getattr(g("severity"), "value", g("severity")) or "ok"
        c = Card()
        top = QHBoxLayout()
        title = label(g("title") or "", "SectionTitle")
        top.addWidget(title, 1)
        top.addWidget(StatusPill(t(f"ui.status.{sev}"), {"ok": "ok", "watch": "watch", "fix": "fix"}.get(sev, "idle")))
        c.body.addLayout(top)
        v = g("value")
        dec = 0 if g("code") in ("cadence_spm",) or (isinstance(v, (int, float)) and abs(v) >= 100) else 1
        val = label(f"{num(v, dec) if isinstance(v, (int, float)) else '–'} {g('unit') or ''}")
        val.setStyleSheet(f"font-size: 30px; font-weight: 700; color: {theme.TOKENS[SEV_COLOUR.get(sev, 'text')]};")
        c.body.addWidget(val)
        if g("reference"):
            c.body.addWidget(label(t("ui.res.target", ref=g("reference")), "Hint"))
        if g("uncertain"):
            c.body.addWidget(label(t("ui.res.uncertain"), "Hint", wrap=True))
        if g("explanation"):
            c.body.addWidget(label(g("explanation"), wrap=True))
        if g("cue"):
            cue = label(t("ui.res.cue", cue=g("cue")), wrap=True)
            cue.setStyleSheet(f"color: {theme.TOKENS['accent']}; font-weight: 600;")
            c.body.addWidget(cue)
        return c

    def analyze_video(self) -> None:
        """Offline analysis of the recording in a worker; progress is shown in the gait tab."""
        s, a = self.session, self.athlete
        if not s or not s.video_path or self._analysis is not None:
            return
        kf = paths.keyframes_dir() / f"session-{s.id or 'new'}"
        kf.mkdir(parents=True, exist_ok=True)
        self.analysis_box.show()
        self.analysis_bar.setValue(0)
        self.analysis_text.setText(t("ui.res.analyzing"))
        session_id = s.id
        window = self._gait_window()

        res0 = dict(s.result or {})
        second_path, second_view = res0.get("second_video"), res0.get("second_view") or "rear"
        offset = float(res0.get("second_offset_s") or 0.0)
        height = (a.height_cm if a else None) or 175.0
        cutoff = window.get("cutoff_s") if window else None

        def work(progress: Any) -> Any:
            if not second_path:
                return services.analyze_video(s.video_path, height, self._view_value(), progress, kf,
                                              max_seconds=cutoff)
            # Two views: one film after the other (the processor is shared), each with its own view.
            # A film that cannot be analysed does not take the other one with it.
            out: dict[str, Any] = {}
            errors: list[Exception] = []
            try:
                out = dict(services.analyze_video(s.video_path, height, self._view_value(),
                                                  lambda v, *_: progress(v * 0.5), kf, max_seconds=cutoff))
            except Exception as e:
                log.exception("analysing the first view failed")
                errors.append(e)
            try:
                second = dict(services.analyze_video(second_path, height, second_view,
                                                     lambda v, *_: progress(0.5 + v * 0.5), kf,
                                                     max_seconds=max(1.0, cutoff - offset) if cutoff else None))
                second["angle"] = "second"
                second["offset_s"] = offset
                sync = services.match_views(out, second, offset)
                if sync:
                    second["sync"] = sync
                out["second"] = second
            except Exception as e:
                log.exception("analysing the second view failed")
                errors.append(e)
            if not out:
                raise errors[0]
            return out

        def done(res: Any) -> None:
            self._analysis = None
            self.analysis_box.hide()
            if self.session is None or self.session.id != session_id:
                return  # the coach opened another test meanwhile; result is saved below anyway
            if res is None:
                self.analysis_text.setText(t("ui.res.analysis_unavailable"))
                return
            self.gait = dict(res, window=window) if window else res
            fresh = self.ctx.store.session(session_id)
            if fresh is not None and fresh.video_path is None and self.session.video_path:   # removed after the analysis
                self.session.video_path = None
                self.session.result = dict(self.session.result or {}, video_removed=fresh.result.get("video_removed"))
                self.session.result.pop("earlier_videos", None)
                self.session.result.pop("second_video", None)
            self._html = None
            self._fill_gait()

        def save(res: Any) -> None:
            if res is not None and session_id is not None:
                try:
                    if self.ctx.store.session(session_id) is None:   # erased meanwhile: its new key frames go too
                        import shutil
                        shutil.rmtree(kf, ignore_errors=True)
                        return
                    if window:
                        res = dict(res, window=window)
                    second = res.get("second")
                    main = {k: v for k, v in res.items() if k != "second"}
                    if "summary" in main:
                        self.ctx.store.save_gait(session_id, main)
                    if second:                       # a row of its own: its key frames are its own too
                        self.ctx.store.save_gait(session_id, second)
                    if str(self.ctx.setting("privacy.video_retention")) == "after_analysis":
                        self.ctx.store.db.remove_session_video(session_id)   # the analysis is saved: the film goes
                except Exception:
                    log.exception("saving gait analysis failed")

        def fail(msg: str) -> None:
            self._analysis = None
            self.analysis_box.hide()
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.analysis", detail=msg))
        self._analysis = run_async(work, on_done=lambda r: (save(r), done(r)), on_error=fail,
                                   on_progress=lambda v: self.analysis_bar.setValue(int(v * 100)))

    def plan_kind(self) -> str:
        """The kind chosen in the wizard ("combined", "max_hr_gait" ...); older tests: from the session."""
        s = self.session
        k = (s.result or {}).get("plan_kind") if s else None
        if k:
            return str(k)
        return {TestKind.GAIT: "gait", TestKind.MAX_HR: "max_hr", TestKind.HR_ZONES: "hr_zones",
                TestKind.FTP: "ftp", TestKind.BIKEFIT: "bikefit",
                TestKind.SKI_TECHNIQUE: "ski"}.get(s.kind, "lactate") if s else ""

    def _window_note(self, w: dict[str, Any] | None) -> str:
        """Which stages the technique of a max HR test was judged on, in words."""
        if not w:
            return ""
        speeds = [r.speed_kmh for r in self.records if r.index in set(w.get("stages") or [])]
        if not speeds:
            return t("ui.res.gait_window_warmup")
        last = max(self.records, key=lambda r: r.index if r.index in set(w["stages"]) else -1)
        what = f"{num(last.speed_kmh)} km/h, {num(last.incline_pct, 0)} %"
        if w.get("basis") == "hr" and w.get("peak_hr"):
            key = "ui.res.gait_window_steps_hr" if w.get("plan_kind") == "hr_zones" else "ui.res.gait_window_hr"
            return t(key, n=len(speeds), last=what, hr=int(w["peak_hr"] * 0.9))
        return t("ui.res.gait_window_count", n=len(speeds), last=what)

    def _gait_window(self) -> dict[str, Any] | None:
        """Maxpuls med löpteknik: the technique is judged on the stages below about 90 % of the effort,
        not the last minutes where it breaks down (assessments.max_hr.gait_window)."""
        kind = self.plan_kind()
        if kind not in ("max_hr_gait", "hr_zones") or not self.session:
            return None
        try:
            from lopanalys.assessments.max_hr import gait_window
            w = gait_window(self.records, self.samples, (self.session.result or {}).get("exhaustion_t_s"))
            return dict(w, plan_kind=kind) if kind == "hr_zones" else w
        except Exception:
            log.exception("gait window failed")
            return None

    def _view_value(self) -> Any:
        """The side the test was filmed from: saved with the test since the two camera views, before that the setting."""
        try:
            from lopanalys.pose.types import View
            saved = (self.session.result or {}).get("view") if self.session else None
            return View(str(saved or self.ctx.setting("camera.view") or "side_left"))
        except ValueError:
            return "side_left"

    # ================================================================== save and report
    def save(self) -> None:
        s = self.session
        if s is None:
            return
        try:
            s.result = dict(s.result or {})
            s.result["lt1_method"] = self.method1.currentData()
            s.result["lt2_method"] = self.method.currentData()
            s.result["coach_comment"] = self.comment.toPlainText().strip()
            lv = self.lactate
            if lv is not None and lv.raw is not None and hasattr(lv.raw, "to_dict"):
                s.result["lactate"] = lv.raw.to_dict()
            if lv and lv.lt1 and lv.lt2:
                s.result["lt1"] = vars(lv.lt1)
                s.result["lt2"] = vars(lv.lt2)
            s.result["max_hr"] = self.max_hr
            s.result["zone_system"] = self.zone_system.currentData()
            new_max = self._new_max_hr(s)
            s.result["garmin"] = self.garmin
            s.result["vo2max"] = self.vo2
            if self.power is not None:
                s.result["power"] = self.power
            if self._hr_zones():
                s.result["hr_speed"] = services.hr_speed_dict(self.hr_speed, self.max_hr if self._to_exhaustion() else None)
            if self.zone_list:
                s.result["zones"] = [z["raw"].to_dict() if hasattr(z.get("raw"), "to_dict") else
                                     {k: v for k, v in z.items() if k != "raw"} for z in self.zone_list]
            self.ctx.store.save_session(s)
            if s.id is not None:
                self.ctx.store.save_records(s.id, self.warmup + self.records)
                if self.samples:
                    self.ctx.store.save_samples(s.id, self.samples)
            a = self.athlete
            if a and new_max:
                a.set_max_hr(s.sport, new_max)     # a max test only moves the max HR of its own sport
                if sport_of(s.sport) is DEFAULT_SPORT:
                    a.max_hr_source = "test"
                    a.max_hr_date = s.started.date() if s.started else None
                self.ctx.store.save_athlete(a)
                self.ctx.athletes_changed.emit()
                self.recalculate()          # the zones now build on the new max HR, and say so
                self.save()                 # ... and the saved result too (the max is equal now: no loop)
                return
            self.ctx.sessions_changed.emit()
            self.save_btn.setText(t("ui.res.saved"))
        except Exception as e:
            log.exception("save failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.save_failed", detail=str(e)))

    def _new_max_hr(self, s: Session) -> int | None:
        """The athlete's new max HR after a max test, or None (a test peak below the
        saved max HR from intervals once replaced it). It is only raised by itself; a lower peak is asked once, and
        keeping the saved value is the default. The answer is kept in ``result["max_hr_asked"]``."""
        a = self.athlete
        if not (a and s.kind in MAX_KINDS and self.max_hr and self._to_exhaustion()):
            return None
        saved = a.max_hr_for(s.sport)                # the max HR of the test's own sport
        what = services.max_hr_update(saved, self.max_hr)
        if what in ("set", "raise"):
            return self.max_hr
        if what != "ask" or s.result.get("max_hr_asked"):
            return None
        keep = self.ask_keep_max_hr(self.max_hr, int(saved))
        s.result["max_hr_asked"] = "kept" if keep else "lowered"
        return None if keep else self.max_hr

    def ask_keep_max_hr(self, peak: int, saved: int) -> bool:
        """True: keep the saved max HR (Ja, the default button)."""
        B = QMessageBox.StandardButton
        ans = QMessageBox.question(self, t("ui.app.name"), t("ui.res.keep_max_q", peak=peak, saved=saved),
                                   B.Yes | B.No, B.Yes)
        return ans != B.No

    def html(self) -> str:
        if self._html is None:
            prev = self.previous_session()
            pd = None
            if prev is not None and prev.id is not None:
                precs = self._work_records(prev, self.ctx.store.records(prev.id))
                res = prev.result or {}
                pd = {"session": prev, "records": precs,
                      "lactate": services.analyze_lactate(precs, res.get("lt1_method"), res.get("lt2_method"),
                                                          self.athlete.max_hr_for(prev.sport) if self.athlete else None,
                                                          prev.sport)}
            self._html = services.render_report(self.athlete or Athlete(None, "", ""), self.session, self.records,
                                                self.lactate, self.zone_list, self.max_view, self.gait, pd,
                                                self.ctx.branding(), self.samples, self.comment.toPlainText().strip(),
                                                zone_system=self.zone_system.currentData(),
                                                zone_compare=self.zone_compare, garmin=self.garmin, vo2max=self.vo2,
                                                hr_speed=services.hr_speed_dict(self.hr_speed, self.max_hr),
                                                power=self.power, technique=self.technique)
        return self._html

    def _tab_changed(self, i: int) -> None:
        if self.tabs.widget(i) is self.report_tab:
            self.show_report(switch=False)

    def show_report(self, switch: bool = True) -> None:
        """Render the report and show it in the embedded web view."""
        if switch and self.tabs.currentWidget() is not self.report_tab:
            self.tabs.setCurrentWidget(self.report_tab)   # _tab_changed renders it
            return
        try:
            html = self.html()
        except Exception as e:
            log.exception("report failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.report", detail=str(e)))
            return
        if self._web is None:
            from lopanalys.ui.widgets.report_view import ReportView
            self._web = ReportView()
            self._web.setMinimumWidth(820)
            self._web.setMaximumWidth(1000)
            self.report_lay.addWidget(self._web, 1, Qt.AlignmentFlag.AlignHCenter)
        self._web.show_html(html)

    def _default_path(self) -> Path:
        """Neutral inside the data folder (the test's id), with the name only where the coach saves elsewhere."""
        s = self.session
        d = s.started.astimezone().strftime("%Y-%m-%d") if s else ""
        tail = f"{s.protocol_name}_{d}" if s else "rapport"
        if s is not None and s.id is not None:
            tail = f"test{s.id}_{tail}"
        return services.report_target(self.ctx.store, self.athlete, tail)

    def save_pdf(self) -> None:
        fn, _ = QFileDialog.getSaveFileName(self, t("ui.res.pdf"), str(self._default_path()), "PDF (*.pdf)")
        if not fn:
            return
        try:
            services.html_to_pdf(self.html(), Path(fn))
        except Exception as e:
            log.exception("pdf failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.pdf", detail=str(e)))
            return
        services.register_report(self.ctx.store, Path(fn), self.athlete, self.session.id if self.session else None)
        QMessageBox.information(self, t("ui.res.pdf"), t("ui.res.pdf_saved", path=fn))
        self._after_report([Path(fn)])

    def save_fit(self, dest: str | None = None) -> bool:
        """*Spara FIT-fil*: the test as an activity for Strava and Garmin Connect, under a neutral name."""
        s = self.session
        if s is None or s.id is None:
            return False
        fn = dest or QFileDialog.getSaveFileName(self, t("ui.res.fit"), str(services.fit_target(self.ctx.store, s)),
                                                 "FIT (*.fit)")[0]
        if not fn:
            return False
        try:
            _, data = services.fit_file(self.ctx.store, s, self.warmup + self.records, self.samples)
            Path(fn).write_bytes(data)
        except Exception as e:
            log.exception("fit export failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.fit", detail=str(e)))
            return False
        services.register_fit(self.ctx.store, Path(fn), self.athlete, s.id)
        log.info("test %s saved as a FIT file", s.id)
        QMessageBox.information(self, t("ui.res.fit"), t("ui.res.fit_saved", path=fn))
        return True

    def send_to_athlete(self) -> None:
        """*Skicka till idrottaren*: the report and the FIT file by mail, after the question."""
        from lopanalys.ui.views.send_mail import send_to_athlete
        s = self.session
        if s is None or s.id is None or self._mailing is not None:
            return
        self.flush()
        self.save()                # the mail carries what is on the screen, and the FIT file what is saved
        sid = s.id

        def sent(entry: dict[str, Any]) -> None:
            if self.session is not None and self.session.id == sid:
                self.session.result = dict(self.session.result or {})
                self.session.result["mailed"] = list(self.session.result.get("mailed") or []) + [entry]

        def end() -> None:
            self._mailing = None
            if self.session is not None:
                self._refresh_share()

        self._mailing = True
        self.mail_btn.setEnabled(False)
        w = send_to_athlete(self, self.ctx, self.athlete, [s], lambda p: services.html_to_pdf(self.html(), p),
                            on_sent=sent, on_end=end, after=lambda: self._after_report([]))
        if w is not None:
            self._mailing = w

    def _after_report(self, keep: list[Path]) -> None:
        """The report has been handed over. A person who has not consented to the results being kept
        is erased now, after a question. Nothing is asked when no consent is registered at all."""
        a = self.athlete
        if a is None or a.id is None or not a.consent_registered or a.consents("store"):
            return
        r = QMessageBox.question(self, t("ui.consent.title"), t("ui.consent.erase_after_report", name=a.name),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self.flush()
            self.erase_requested.emit(a.id, keep)

    def print_report(self) -> None:
        from lopanalys.ui.views.settings import pick_printer
        name = pick_printer(self, str(self.ctx.setting("report.printer") or ""))
        if name is None:
            return
        try:
            services.print_report(self.html(), name or None)
        except Exception as e:
            log.exception("print failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.print", detail=str(e)))
            return
        self._after_report([])
