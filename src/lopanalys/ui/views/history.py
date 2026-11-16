"""Historik: all tests per athlete, open a result, compare two lactate tests, and one collected report
for several tests (a lactate test, a max HR test and a gait film the same day)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QHeaderView, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from lopanalys.assessments import css
from lopanalys.core import paths
from lopanalys.core.i18n import t
from lopanalys.core.models import Session, TestKind
from lopanalys.core.sport import DEFAULT_SPORT, profile, sport_of
from lopanalys.ui import services, theme
from lopanalys.ui.context import AppContext
from lopanalys.ui.fmt import num, speed, speed_range
from lopanalys.ui.views.athletes import AthleteRow
from lopanalys.ui.widgets.charts import LactateChart
from lopanalys.ui.widgets.common import Card, PageHeader, button, label, page

log = logging.getLogger(__name__)

KIND_ICON = {TestKind.GAIT: "run", TestKind.LACTATE_STEP: "droplet", TestKind.MAX_HR: "heart",
             TestKind.HR_ZONES: "gauge", TestKind.FTP: "gauge",
             TestKind.BIKEFIT: "camera", TestKind.SKI_TECHNIQUE: "camera", TestKind.SWIM_CSS: "droplet"}
COMPARE_COLOURS = ["accent", "accent2", "watch", "muted"]


class HistoryView(QWidget):
    """Emits ``open_session(session_id)``."""

    open_session = Signal(int)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.sessions: list[Session] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        w, v = page()
        outer.addWidget(w)
        hdr = PageHeader(t("ui.nav.history"), t("ui.hist.subtitle"))
        self.cmp_btn = button(t("ui.hist.compare"), "compare")
        self.cmp_btn.clicked.connect(self.compare)
        self.open_btn = button(t("ui.hist.open"), "eye", kind="primary")
        self.open_btn.clicked.connect(self._open)
        self.report_btn = button(t("ui.hist.collected"), "pdf")
        self.report_btn.setToolTip(t("ui.hist.collected_tip"))
        self.report_btn.clicked.connect(self.collected_report)
        self.css_btn = button(t("ui.hist.css"), "droplet")
        self.css_btn.setToolTip(t("ui.hist.css_tip"))
        self.css_btn.clicked.connect(self.new_css)
        hdr.add_action(self.css_btn)
        hdr.add_action(self.cmp_btn)
        hdr.add_action(self.report_btn)
        hdr.add_action(self.open_btn)
        v.addWidget(hdr)
        body = QHBoxLayout()
        body.setSpacing(20)
        v.addLayout(body, 1)
        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("ui.athletes.search"))
        self.search.addAction(theme.icon("search", "muted"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        left.addWidget(self.search)
        self.alist = QListWidget()
        self.alist.currentItemChanged.connect(self._athlete)
        left.addWidget(self.alist, 1)
        lw = QWidget()
        lw.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)
        lw.setFixedWidth(340)
        body.addWidget(lw)
        card = Card()
        top = QHBoxLayout()
        self.title = label(t("ui.hist.tests"), "SectionTitle")
        top.addWidget(self.title, 1)
        self.kind = QComboBox()
        self.kind.addItem(t("ui.hist.all"), None)
        for k in TestKind:
            self.kind.addItem(t(f"ui.testkind.{k.value}"), k.value)
        self.kind.currentIndexChanged.connect(lambda _: self._load_sessions())
        top.addWidget(self.kind)
        card.body.addLayout(top)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([t("ui.hist.col.date"), t("ui.hist.col.test"), t("ui.hist.col.protocol"),
                                              t("ui.hist.col.result"), t("ui.hist.col.notes")])
        self.table.verticalHeader().hide()
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(lambda _: self._open())
        self.table.itemSelectionChanged.connect(self._sel_changed)
        card.body.addWidget(self.table, 1)
        self.empty = label(t("ui.hist.empty"), "Hint", wrap=True)
        card.body.addWidget(self.empty)
        body.addWidget(card, 1)
        ctx.athletes_changed.connect(self.refresh)
        ctx.sessions_changed.connect(self._load_sessions)
        self.refresh()

    def refresh(self, *_: Any) -> None:
        cur = self.alist.currentItem().data(Qt.ItemDataRole.UserRole).id if self.alist.currentItem() else None
        self.alist.blockSignals(True)
        self.alist.clear()
        try:
            rows = self.ctx.store.athletes(self.search.text())
        except Exception:
            rows = []
        for a in rows:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, a)
            it.setSizeHint(QSize(300, 62))
            self.alist.addItem(it)
            self.alist.setItemWidget(it, AthleteRow(a))
            if a.id == cur:
                self.alist.setCurrentItem(it)
        self.alist.blockSignals(False)
        if self.alist.currentItem() is None and self.alist.count():
            self.alist.setCurrentRow(self._most_recent_row())
        self._load_sessions()

    def _most_recent_row(self) -> int:
        """Row of the athlete with the latest test (so the page opens on something useful)."""
        fn = getattr(self.ctx.store.db, "recent_sessions", None)
        try:
            recent = fn(1) if callable(fn) else []
        except Exception:
            recent = []
        if recent:
            aid = recent[0][1].id
            for i in range(self.alist.count()):
                if self.alist.item(i).data(Qt.ItemDataRole.UserRole).id == aid:
                    return i
        return 0

    def select_athlete(self, athlete_id: int) -> None:
        for i in range(self.alist.count()):
            if self.alist.item(i).data(Qt.ItemDataRole.UserRole).id == athlete_id:
                self.alist.setCurrentRow(i)
                return

    def _athlete(self, *_: Any) -> None:
        self._load_sessions()

    def _load_sessions(self) -> None:
        it = self.alist.currentItem()
        self.sessions = []
        if it is not None:
            a = it.data(Qt.ItemDataRole.UserRole)
            self.title.setText(t("ui.hist.tests_for", name=a.name))
            try:
                self.sessions = self.ctx.store.history(a.id, self.kind.currentData())
            except Exception as e:
                QMessageBox.warning(self, t("ui.app.name"), t("ui.err.db", detail=str(e)))
        self.table.setRowCount(len(self.sessions))
        for r, s in enumerate(self.sessions):
            res = s.result or {}
            if not res.get("lt2") and s.kind == TestKind.LACTATE_STEP and s.id is not None:
                try:  # older or imported tests without a stored result: compute it for the list
                    warm = {k for k, st in enumerate((s.protocol or {}).get("stages", [])) if st.get("is_warmup")}
                    lv = services.analyze_lactate([r for r in self.ctx.store.records(s.id) if r.index not in warm],
                                                  sport=s.sport)
                    if lv and lv.lt2:
                        res = dict(res, lt2={"speed_kmh": lv.lt2.speed_kmh, "hr": lv.lt2.hr})
                except Exception:
                    pass
            easy = services.easy_zone(res.get("zones") or []) if s.kind == TestKind.HR_ZONES else None
            lo, hi = (easy or {}).get("speed_low_kmh"), (easy or {}).get("speed_high_kmh")
            if (lo or hi) and res.get("max_hr"):   # the zones test without lactate: the easy zone's speed
                span = speed_range(lo, hi)            # the pace first, km/h after
                summary = t("ui.hist.zones_speed", n=res["max_hr"], zone=easy.get("code", ""), span=span)
            elif res.get("lt2"):
                summary = f"LT2 {speed(res['lt2'].get('speed_kmh'), s.sport)} · {num(res['lt2'].get('hr'), 0)}"
            elif (res.get("power") or {}).get("ftp_w"):      # cycling: FTP from the 20 minute test or the ramp
                summary = f"FTP {num(res['power']['ftp_w'], 0)} W" + (
                    f" · {t('ui.hist.maxhr', n=res['max_hr'])}" if res.get("max_hr") and s.kind == TestKind.MAX_HR else "")
            elif s.kind == TestKind.SWIM_CSS and res.get("css_s100"):
                summary = t("ui.hist.css_pace", pace=css.pace_str(res["css_s100"]))
            elif res.get("max_hr"):
                summary = t("ui.hist.maxhr", n=res["max_hr"])
            else:
                summary = "–"
            kind_text = t(f"ui.testkind.{s.kind.value}")
            if sport_of(s.sport) is not DEFAULT_SPORT:      # the sport first: a cycling test is never read as a running test
                kind_text = f"{t('ui.sport.' + sport_of(s.sport).value)} · {kind_text}"
            vals = [s.started.astimezone().strftime("%Y-%m-%d %H:%M"), kind_text,
                    s.protocol_name, summary,
                    f"{t('ui.hist.interrupted')} {s.notes}".strip() if s.status == "interrupted" else s.notes]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 1:
                    item.setIcon(theme.icon(KIND_ICON.get(s.kind, "activity"), "accent"))
                self.table.setItem(r, c, item)
        self.empty.setVisible(not self.sessions)
        self._sel_changed()

    def _selected(self) -> list[Session]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.sessions[r] for r in rows if r < len(self.sessions)]

    def _sel_changed(self) -> None:
        sel = self._selected()
        measured = not any(s.kind == TestKind.SWIM_CSS for s in sel)   # a manual test has no curve and no report
        self.open_btn.setEnabled(len(sel) == 1)
        self.cmp_btn.setEnabled(len(sel) >= 2 and measured)
        self.report_btn.setEnabled(len(sel) >= 2 and measured)
        self.css_btn.setEnabled(self.alist.currentItem() is not None)

    def _open(self) -> None:
        sel = self._selected()
        if len(sel) == 1 and sel[0].kind == TestKind.SWIM_CSS:
            self.edit_css(sel[0])
        elif len(sel) == 1 and sel[0].id is not None:
            self.open_session.emit(sel[0].id)

    def new_css(self) -> None:
        """Write in a CSS test (swimming) for the chosen athlete."""
        it = self.alist.currentItem()
        if it is not None:
            self._css_dialog(it.data(Qt.ItemDataRole.UserRole), None)

    def edit_css(self, session: Session) -> None:
        """Open a saved CSS test in its form (it has no result page)."""
        a = self.ctx.store.athlete(session.athlete_id)
        if a is not None:
            self._css_dialog(a, session)

    def _css_dialog(self, athlete: Any, session: Session | None) -> None:
        from lopanalys.ui.views.css_test import CssTestDialog
        self.css_dialog = CssTestDialog(self.ctx, athlete, session, self)
        self.css_dialog.exec()

    def compare(self) -> None:
        sel = self._selected()[:4]
        if len(sel) < 2:
            return
        if len({sport_of(s.sport) for s in sel}) > 1:       # km/h and watt do not share an axis
            QMessageBox.information(self, t("ui.hist.compare"), t("ui.hist.collect_one_sport"))
            return
        CompareDialog(self.ctx, sel, self).exec()

    def collected_report(self) -> None:
        """One report for the selected tests of the athlete (report.builder.collect)."""
        sel = [s for s in self._selected() if s.id is not None]
        it = self.alist.currentItem()
        if len(sel) < 2 or it is None:
            return
        athlete = it.data(Qt.ItemDataRole.UserRole)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            html = services.render_collected_report(self.ctx.store, athlete, sel, self.ctx.branding())
        except Exception as e:
            log.exception("collected report failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.report", detail=str(e)))
            return
        finally:
            QApplication.restoreOverrideCursor()
        day = min(s.started for s in sel).astimezone().strftime("%Y-%m-%d")
        CollectedReportDialog(self.ctx, html, f"{t('ui.hist.collected_file')}_{day}", self, athlete=athlete,
                              sessions=sel).exec()


class CompareDialog(QDialog):
    """Lactate curves and thresholds of 2-4 tests on one chart, with a table."""

    def __init__(self, ctx: AppContext, sessions: list[Session], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("ui.hist.compare"))
        self.resize(1100, 720)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(14)
        v.addWidget(label(t("ui.hist.compare_title"), "PageTitle"))
        chart = LactateChart()
        chart.setMinimumHeight(380)
        sport = sessions[0].sport if sessions else None
        prof = profile(sport)
        chart.x_unit = prof.load_unit
        chart.x_decimals = prof.load_decimals
        dec = prof.load_decimals
        table = QTableWidget(len(sessions), 5)
        table.setHorizontalHeaderLabels([t("ui.hist.col.date"), f"LT1 {prof.load_unit}", "LT1 " + t("ui.res.col.hr"),
                                         f"LT2 {prof.load_unit}", "LT2 " + t("ui.res.col.hr")])
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ordered = sorted(sessions, key=lambda s: s.started, reverse=True)
        for i, s in enumerate(ordered):
            warm = {k for k, st in enumerate((s.protocol or {}).get("stages", [])) if st.get("is_warmup")}
            recs = [r for r in (ctx.store.records(s.id) if s.id is not None else []) if r.index not in warm]
            res = s.result or {}
            lv = services.analyze_lactate(recs, res.get("lt1_method"), res.get("lt2_method"), sport=s.sport)
            pts = [(sp, la) for sp, la, _ in lv.points] if lv else []
            d = s.started.astimezone().strftime("%Y-%m-%d")
            if i == 0:
                lt1 = (lv.lt1.speed_kmh, lv.lt1.lactate_mmol or 0) if lv and lv.lt1 and lv.lt1.speed_kmh else None
                lt2 = (lv.lt2.speed_kmh, lv.lt2.lactate_mmol or 0) if lv and lv.lt2 and lv.lt2.speed_kmh else None
                chart.set_data(pts, lv.curve if lv else [], lt1, lt2)
                chart.legend = d
            else:
                chart.others.append((d, COMPARE_COLOURS[i % len(COMPARE_COLOURS)], pts))
            vals = [d, num(lv.lt1.speed_kmh, dec) if lv and lv.lt1 else "–", num(lv.lt1.hr, 0) if lv and lv.lt1 else "–",
                    num(lv.lt2.speed_kmh, dec) if lv and lv.lt2 else "–", num(lv.lt2.hr, 0) if lv and lv.lt2 else "–"]
            for c, val in enumerate(vals):
                table.setItem(i, c, QTableWidgetItem(val))
        v.addWidget(chart, 1)
        table.setFixedHeight(46 + 36 * len(sessions))
        v.addWidget(table)
        row = QHBoxLayout()
        row.addStretch(1)
        b = button(t("ui.common.close"), kind="primary")
        b.clicked.connect(self.accept)
        row.addWidget(b)
        v.addLayout(row)


class CollectedReportDialog(QDialog):
    """The collected report: a preview, Spara PDF, Skriv ut and Skicka till idrottaren (as on the result page)."""

    def __init__(self, ctx: AppContext, html: str, filename: str, parent: QWidget | None = None,
                 athlete: Any = None, sessions: list[Session] | None = None) -> None:
        super().__init__(parent)
        self.ctx, self.html, self.filename, self.athlete = ctx, html, filename, athlete
        self.sessions = list(sessions or [])
        self._mailing: Any = None
        self.setWindowTitle(t("ui.hist.collected"))
        self.resize(1000, 900)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        # Loaded from a file, not setHtml: setHtml shows nothing over 2 MB and the report embeds images.
        from lopanalys.ui.widgets.report_view import ReportView
        self.web = ReportView(self)
        v.addWidget(self.web, 1)
        self.web.show_html(html)
        row = QHBoxLayout()
        self.mail_note = label("", "Hint", wrap=True)      # why the mail button is grey
        row.addWidget(self.mail_note, 1)
        pr = button(t("ui.res.print"), "printer")
        pr.clicked.connect(self.print_report)
        pdf = button(t("ui.res.pdf"), "pdf", kind="primary")
        pdf.clicked.connect(self.save_pdf)
        self.mail_btn = button(t("ui.res.mail"), "mail")
        self.mail_btn.clicked.connect(self.send_to_athlete)   # no lambda: it would keep the dialog (and its web view) alive
        close = button(t("ui.common.close"))
        close.clicked.connect(self.accept)
        for b in (close, pr, self.mail_btn, pdf):
            row.addWidget(b)
        v.addLayout(row)
        self._refresh_mail()

    def _refresh_mail(self) -> None:
        try:
            reason = services.mail_block_reason(self.ctx.store, self.athlete) if self.sessions else \
                t("ui.mail.block.no_athlete")
        except Exception:
            log.exception("mail check failed")
            reason = t("ui.mail.block.not_set_up")
        self.mail_btn.setEnabled(reason is None and self._mailing is None)
        self.mail_btn.setToolTip(reason or "")
        self.mail_note.setText(t("ui.mail.blocked", reason=reason) if reason else "")

    def send_to_athlete(self) -> None:
        """The collected report and each test's FIT file by mail, after the question."""
        from lopanalys.ui.views.send_mail import send_to_athlete
        if self._mailing is not None:
            return

        def end() -> None:
            self._mailing = None
            self._refresh_mail()

        self._mailing = True
        self.mail_btn.setEnabled(False)
        w = send_to_athlete(self, self.ctx, self.athlete, self.sessions, lambda p: services.html_to_pdf(self.html, p),
                            on_sent=lambda _e: self.ctx.sessions_changed.emit(), on_end=end)
        if w is not None:
            self._mailing = w

    def save_pdf(self) -> None:
        target = services.report_target(self.ctx.store, self.athlete, self.filename)
        fn, _ = QFileDialog.getSaveFileName(self, t("ui.res.pdf"), str(target), "PDF (*.pdf)")
        if not fn:
            return
        try:
            services.html_to_pdf(self.html, Path(fn))
            services.register_report(self.ctx.store, Path(fn), self.athlete)
        except Exception as e:
            log.exception("pdf failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.pdf", detail=str(e)))
            return
        QMessageBox.information(self, t("ui.res.pdf"), t("ui.res.pdf_saved", path=fn))

    def print_report(self) -> None:
        from lopanalys.ui.views.settings import pick_printer
        name = pick_printer(self, str(self.ctx.setting("report.printer") or ""))
        if name is None:
            return
        try:
            services.print_report(self.html, name or None)
        except Exception as e:
            log.exception("print failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.print", detail=str(e)))
