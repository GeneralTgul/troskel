"""Testerna: one card per test the program offers (what it measures, how it is done, by which
standard, what you get, its limits), and the one-page printout "Om testerna" for a client.

The sport is chosen at the top (running, cycling, SkiErg): the cards and the printout show that
sport's tests, so each sport's sheet fits on one A4.

The texts come from assessments.catalog through services, so the screen and the printout always
say the same thing."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                               QScrollArea, QVBoxLayout, QWidget)

from lopanalys.core import paths
from lopanalys.core.i18n import t
from lopanalys.core.sport import DEFAULT_SPORT, Sport, sport_of
from lopanalys.ui import services, theme
from lopanalys.ui.context import AppContext
from lopanalys.ui.widgets.common import Card, PageHeader, button, field, field_label, label

log = logging.getLogger(__name__)

ICONS = {"lactate": "droplet", "max_hr": "heart", "hr_zones": "gauge", "gait": "run", "zones": "gauge",
         "vo2max": "activity", "bike_lactate": "droplet", "bike_ramp": "heart", "bike_ftp": "timer",
         "bikefit": "camera", "bike_zones": "gauge", "skierg_lactate": "droplet", "skierg_ramp": "heart",
         "ski_technique": "camera", "skierg_zones": "gauge"}
# Reading order on the card: the short facts first, then two columns.
LEFT, RIGHT = ("measures", "how", "get"), ("standard", "limits")


class TestsInfoView(QWidget):
    """The page "Testerna" in the sidebar."""

    __test__ = False  # not a pytest test class despite the name

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._html: str | None = None
        self.sport: Sport = DEFAULT_SPORT
        self.cards: dict[str, Card] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 26, 32, 28)
        outer.setSpacing(16)
        header = PageHeader(t("ui.tests.title"), t("ui.tests.subtitle"))
        self.print_btn = button(t("ui.tests.print"), "printer")
        self.print_btn.clicked.connect(self.print_sheet)
        self.pdf_btn = button(t("ui.res.pdf"), "pdf", kind="primary")
        self.pdf_btn.clicked.connect(self.save_pdf)
        header.add_action(self.print_btn)
        header.add_action(self.pdf_btn)
        outer.addWidget(header)

        self.sport_box = QComboBox()
        for value, text in services.sports():
            self.sport_box.addItem(text, value)
        self.sport_box.currentIndexChanged.connect(lambda _i: self.set_sport(self.sport_box.currentData()))
        row = QHBoxLayout()
        row.addWidget(field(t("ui.wiz.sport"), self.sport_box))
        row.addStretch(1)
        outer.addLayout(row)

        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 8, 0)
        self.body_lay.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)
        self._fill()
        ctx.settings_changed.connect(self._branding_changed)

    def set_sport(self, sport: object) -> None:
        """Show the tests of a sport ("run", "bike", "skierg"); the printout follows."""
        sp = sport_of(sport)
        if self.sport_box.currentData() != sp.value:
            self.sport_box.setCurrentIndex(max(0, self.sport_box.findData(sp.value)))   # comes back here
            return
        if sp is self.sport and self.cards:
            return
        self.sport, self._html = sp, None
        self._fill()

    def _fill(self) -> None:
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)          # gone from the page now, not at the next event loop turn
                w.deleteLater()
        self.cards = {}
        for test in services.tests_info(self.sport):
            card = self._card(test)
            self.cards[test["key"]] = card
            self.body_lay.addWidget(card)
        self.body_lay.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)

    def _card(self, test: dict) -> Card:
        fields = {f["key"]: f for f in test["fields"]}
        c = Card()
        top = QHBoxLayout()
        top.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(theme.pixmap(ICONS.get(test["key"], "chart"), "accent", 26))
        ic.setFixedSize(QSize(28, 28))
        top.addWidget(ic)
        top.addWidget(label(test["title"], "SectionTitle"))
        top.addSpacing(14)
        top.addWidget(label("  ".join(f"{fields[k]['label']}: {fields[k]['text']}" for k in ("time", "equipment")),
                            "Hint", wrap=True), 1)
        c.body.addLayout(top)
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(4)
        for col, keys in enumerate((LEFT, RIGHT)):
            box = QVBoxLayout()
            box.setSpacing(4)
            for k in keys:
                box.addWidget(field_label(fields[k]["label"]))
                box.addWidget(label(fields[k]["text"], wrap=True))
                box.addSpacing(8)
            box.addStretch(1)
            grid.addLayout(box, 0, col)
            grid.setColumnStretch(col, 1)
        c.body.addLayout(grid)
        return c

    def _branding_changed(self) -> None:
        self._html = None

    # ------------------------------------------------------------------ the printout "Om testerna"
    def html(self) -> str:
        if self._html is None:
            self._html = services.render_tests_info(self.ctx.branding(), self.sport)
        return self._html

    def save_pdf(self) -> None:
        name = t("ui.tests.pdf_name") if self.sport is DEFAULT_SPORT else \
            t("ui.tests.pdf_name_sport", sport=self.sport_box.currentText())
        fn, _ = QFileDialog.getSaveFileName(self, t("ui.res.pdf"), str(paths.reports_dir() / name), "PDF (*.pdf)")
        if not fn:
            return
        try:
            services.html_to_pdf(self.html(), Path(fn))
        except Exception as e:
            log.exception("pdf failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.pdf", detail=str(e)))
            return
        QMessageBox.information(self, t("ui.res.pdf"), t("ui.res.pdf_saved", path=fn))

    def print_sheet(self) -> None:
        from lopanalys.ui.views.settings import pick_printer
        name = pick_printer(self, str(self.ctx.setting("report.printer") or ""))
        if name is None:
            return
        try:
            services.print_report(self.html(), name or None)
        except Exception as e:
            log.exception("print failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.print", detail=str(e)))
