"""Idrottare: the athlete register with search, and the form to create or edit an athlete."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, QRegularExpression, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFocusEvent, QPainter, QPaintEvent, QRegularExpressionValidator
from PySide6.QtWidgets import (QCalendarWidget, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget, QWidgetAction)

from lopanalys.core.i18n import t
from lopanalys.core.models import CONSENT_KEYS, Athlete, Sex, valid_email
from lopanalys.ui import services, theme
from lopanalys.ui.context import AppContext
from lopanalys.ui.widgets.common import (
    Avatar,
    Card,
    PageHeader,
    button,
    field,
    label,
    page,
    scrolled,
)


def _age(b: date | None) -> int | None:
    if not b:
        return None
    today = date.today()
    return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


_DATE_RE = re.compile(r"^(\d{2}|\d{4})[-./ ]?(\d{1,2})[-./ ]?(\d{1,2})$")


def parse_birth_date(text: str, today: date | None = None) -> date | None:
    """A typed date of birth: ``2001-04-23``, ``20010423``, ``010423`` or ``01-04-23`` (also with . or /).

    Raises ValueError for text that is not a date between 1920 and today. Empty text is no date.
    Two-digit years are the latest year that is not in the future (``25`` is 2025, ``30`` is 1930).
    """
    s = text.strip()
    if not s:
        return None
    today = today or date.today()
    m = _DATE_RE.match(s)
    if not m or (len(m.group(1)) == 2 and not re.fullmatch(r"\d{6}|\d{2}\D\d{1,2}\D\d{1,2}", s)):
        raise ValueError(text)
    y = int(m.group(1))
    if len(m.group(1)) == 2:
        y += 2000 if 2000 + y <= today.year else 1900
    d = date(y, int(m.group(2)), int(m.group(3)))   # ValueError for 2001-02-30
    if not date(1920, 1, 1) <= d <= today:
        raise ValueError(text)
    return d


def parse_plan_date(text: str, today: date | None = None) -> date | None:
    """A typed date in a training plan (a start, a race): the forms of :func:`parse_birth_date`, from a year
    back to ten years ahead. Two-digit years are 20xx. Raises ValueError otherwise; empty text is no date."""
    s = text.strip()
    if not s:
        return None
    today = today or date.today()
    m = _DATE_RE.match(s)
    if not m or (len(m.group(1)) == 2 and not re.fullmatch(r"\d{6}|\d{2}\D\d{1,2}\D\d{1,2}", s)):
        raise ValueError(text)
    y = int(m.group(1)) + (2000 if len(m.group(1)) == 2 else 0)
    d = date(y, int(m.group(2)), int(m.group(3)))
    if not date(today.year - 1, 1, 1) <= d <= date(today.year + 10, 12, 31):
        raise ValueError(text)
    return d


class SelectAllEdit(QLineEdit):
    """A line edit that selects its text when it gets focus, so a typed value replaces the old one."""

    def focusInEvent(self, e: QFocusEvent) -> None:
        super().focusInEvent(e)
        # after the mouse press that gave the focus, which would otherwise place the cursor instead
        QTimer.singleShot(0, self.selectAll)


class NumberEdit(SelectAllEdit):
    """An optional number: empty means no value, the unit is drawn muted at the right edge.

    Replaces the spin boxes with "–" for no value, which had to be erased before typing (real hardware
    2026-11-14). The limits are checked when the form is saved, with a message.
    """

    def __init__(self, unit: str, decimals: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.unit, self.decimals = unit, decimals
        pattern = r"\d{0,3}([.,]\d?)?" if decimals else r"\d{0,3}"
        self.setValidator(QRegularExpressionValidator(QRegularExpression(pattern), self))
        self.setTextMargins(0, 0, self.fontMetrics().horizontalAdvance(unit) + 6, 0)

    def value(self) -> float | None:
        s = self.text().strip().replace(",", ".").rstrip(".")
        try:
            v = float(s)
        except ValueError:
            return None
        return v or None

    def setValue(self, v: float | None) -> None:
        if not v:
            self.clear()
        elif self.decimals:
            self.setText(f"{v:.{self.decimals}f}".rstrip("0").rstrip(".").replace(".", ","))
        else:
            self.setText(f"{v:.0f}")

    def paintEvent(self, e: QPaintEvent) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        p.setPen(QColor(theme.TOKENS["muted"]))
        r = self.rect().adjusted(0, 0, -12, 0)
        p.drawText(r, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), self.unit)
        p.end()


class DateField(QWidget):
    """Date of birth typed on the keyboard, or picked in a calendar from the button beside it.

    The QDateEdit always held a date, so a new athlete had to get a (wrong) date from the calendar before
    the field could be typed in (real hardware 2026-11-14). The field starts empty; :func:`parse_birth_date`
    reads it when the form is saved.
    """

    def __init__(self, parent: QWidget | None = None, placeholder: str | None = None, *,
                 plan: bool = False) -> None:
        """``plan``: a date in a training plan (today and ahead) instead of a date of birth."""
        super().__init__(parent)
        self._plan = plan
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.edit = SelectAllEdit()
        self.edit.setPlaceholderText(placeholder or t("ui.athletes.birth_placeholder"))
        self.edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9 ./-]{0,10}"), self.edit))
        self.edit.editingFinished.connect(self._tidy)
        h.addWidget(self.edit, 1)
        self.cal_btn = QToolButton()
        self.cal_btn.setIcon(theme.icon("calendar", "muted"))
        self.cal_btn.setIconSize(QSize(22, 22))
        self.cal_btn.setMinimumSize(40, 38)
        self.cal_btn.setToolTip(t("ui.athletes.calendar"))
        self.cal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cal_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cal_btn.clicked.connect(self._pick)
        h.addWidget(self.cal_btn)
        self.menu = QMenu(self)
        self.calendar = QCalendarWidget()
        if plan:
            self.calendar.setMinimumDate(QDate(QDate.currentDate().year() - 1, 1, 1))
            self.calendar.setMaximumDate(QDate(QDate.currentDate().year() + 10, 12, 31))
        else:
            self.calendar.setMinimumDate(QDate(1920, 1, 1))
            self.calendar.setMaximumDate(QDate.currentDate())
        self.calendar.setGridVisible(False)
        self.calendar.clicked.connect(self._picked)
        self.calendar.activated.connect(self._picked)
        act = QWidgetAction(self.menu)
        act.setDefaultWidget(self.calendar)
        self.menu.addAction(act)

    def text(self) -> str:
        return self.edit.text()

    def date(self) -> date | None:
        """The date, or None when empty. Raises ValueError when the text is not a date."""
        return (parse_plan_date if self._plan else parse_birth_date)(self.edit.text())

    def set_date(self, d: date | None) -> None:
        self.edit.setText(d.isoformat() if d else "")

    def _tidy(self) -> None:
        """Show a readable typed date in the full form (``010423`` becomes ``2001-04-23``)."""
        try:
            d = self.date()
        except ValueError:
            return
        if d is not None and self.edit.text() != d.isoformat():
            self.edit.setText(d.isoformat())

    def _pick(self) -> None:
        try:
            d = self.date()
        except ValueError:
            d = None
        fallback = QDate.currentDate() if self._plan else QDate(QDate.currentDate().year() - 30, 1, 1)
        self.calendar.setSelectedDate(QDate(d) if d else fallback)
        self.menu.popup(self.mapToGlobal(self.rect().bottomLeft()))

    def _picked(self, qd: QDate) -> None:
        self.set_date(date(qd.year(), qd.month(), qd.day()))
        self.menu.close()
        self.edit.setFocus()


class AthleteRow(QWidget):
    def __init__(self, a: Athlete, parent: QWidget | None = None, review: bool = False) -> None:
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(12)
        h.addWidget(Avatar(a.name, 38))
        v = QVBoxLayout()
        v.setSpacing(1)
        n = label(a.name)
        n.setStyleSheet("font-weight: 600; font-size: 15px;")
        bits = []
        age = _age(a.birth_date)
        if age is not None:
            bits.append(t("ui.athletes.age", n=age))
        if a.height_cm:
            bits.append(f"{a.height_cm:.0f} cm")
        if a.max_hr:
            bits.append(t("ui.athletes.maxhr_short", n=a.max_hr))
        if not a.consent_registered:
            bits.append(t("ui.consent.missing"))
        if review:
            bits.append(t("ui.remind.row"))
        v.addWidget(n)
        v.addWidget(label("  ·  ".join(bits) or t("ui.athletes.no_data"), "Hint"))
        h.addLayout(v, 1)


class AthleteForm(Card):
    """Create/edit form. Emits ``saved(Athlete)`` and ``deleted(int)``.

    The buttons (*Ta bort*, *Avbryt*, *Spara*) are in ``actions``, which the view keeps below the scroll area
    so that *Spara* is always visible. ``dirty()`` says whether the fields differ from the loaded athlete,
    and ``confirm_discard()`` asks before unsaved changes are thrown away.
    """

    saved = Signal(object)
    deleted = Signal(int)
    start_test = Signal(object)
    extract = Signal(object)        # the athlete: "Exportera allt"
    open_plan = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent, pad=28)
        self.athlete: Athlete | None = None
        self.heading = label("", "PageTitle")
        self.heading.setStyleSheet("font-size: 22px;")
        self.reminder = label("", wrap=True)      # the 24-month reminder, set by the view
        self.reminder.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        self.reminder.hide()
        top = QHBoxLayout()
        top.addWidget(self.heading, 1)
        self.plan_btn = button(t("ui.plan.title"), "calendar")
        self.plan_btn.clicked.connect(lambda: self.athlete and self.open_plan.emit(self.athlete))
        top.addWidget(self.plan_btn)
        self.test_btn = button(t("ui.athletes.start_test"), "play", kind="primary")
        self.test_btn.clicked.connect(lambda: self.athlete and self.start_test.emit(self.athlete))
        self.extract_btn = button(t("ui.athletes.extract"), "download")
        self.extract_btn.setToolTip(t("ui.athletes.extract_tip"))
        self.extract_btn.clicked.connect(lambda: self.athlete and self.extract.emit(self.athlete))
        top.addWidget(self.extract_btn)
        top.addWidget(self.test_btn)
        self.body.addLayout(top)
        self.body.addWidget(self.reminder)
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(16)
        self.first = QLineEdit()
        self.last = QLineEdit()
        self.birth = DateField()
        self.sex = QComboBox()
        # Plain strings as item data: PySide turns a str-Enum into a bare str on the way back, and saving then
        # failed with "'str' object has no attribute 'value'" (real hardware 2026-11-14).
        for key, val in (("ui.sex.none", ""), ("ui.sex.female", Sex.FEMALE.value), ("ui.sex.male", Sex.MALE.value),
                         ("ui.sex.other", Sex.OTHER.value)):
            self.sex.addItem(t(key), val)
        self.height = NumberEdit("cm")
        self.weight = NumberEdit("kg", decimals=1)
        self.max_hr = NumberEdit(t("ui.unit.bpm"))
        self.rest_hr = NumberEdit(t("ui.unit.bpm"))
        # Max HR per sport: it differs between running, cycling and double poling, so each
        # sport has its own value and none is ever filled in from another.
        self.max_hr_bike = NumberEdit(t("ui.unit.bpm"))
        self.max_hr_skierg = NumberEdit(t("ui.unit.bpm"))
        self.watch_vo2 = NumberEdit("ml/kg/min", decimals=1)
        self.watch_vo2_date = DateField()
        self.email = QLineEdit()
        self.email.setPlaceholderText(t("ui.athletes.email_placeholder"))
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(90)
        g.addWidget(field(t("ui.athletes.first_name"), self.first), 0, 0)
        g.addWidget(field(t("ui.athletes.last_name"), self.last), 0, 1)
        g.addWidget(field(t("ui.athletes.birth_date"), self.birth), 1, 0)
        g.addWidget(field(t("ui.athletes.sex"), self.sex), 1, 1)
        g.addWidget(field(t("ui.athletes.height"), self.height, t("ui.athletes.height_hint")), 2, 0)
        g.addWidget(field(t("ui.athletes.weight"), self.weight), 2, 1)
        g.addWidget(field(t("ui.athletes.max_hr"), self.max_hr, t("ui.athletes.max_hr_hint")), 3, 0)
        g.addWidget(field(t("ui.athletes.rest_hr"), self.rest_hr), 3, 1)
        g.addWidget(field(t("ui.athletes.max_hr_bike"), self.max_hr_bike, t("ui.athletes.max_hr_sport_hint")), 4, 0)
        g.addWidget(field(t("ui.athletes.max_hr_skierg"), self.max_hr_skierg), 4, 1)
        g.addWidget(field(t("ui.athletes.watch_vo2max"), self.watch_vo2, t("ui.athletes.watch_vo2max_hint")), 5, 0)
        g.addWidget(field(t("ui.athletes.watch_vo2max_date"), self.watch_vo2_date), 5, 1)
        g.addWidget(field(t("ui.athletes.email"), self.email, t("ui.athletes.email_hint")), 6, 0, 1, 2)
        g.addWidget(field(t("ui.athletes.notes"), self.notes), 7, 0, 1, 2)
        for i in range(g.count()):
            g.itemAt(i).setAlignment(Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(g)
        # --- consent: the date on the signed form and its four boxes
        self.body.addWidget(label(t("ui.consent.title"), "SectionTitle"))
        self.body.addWidget(label(t("ui.consent.hint"), "Hint", wrap=True))
        cg = QGridLayout()
        cg.setHorizontalSpacing(18)
        cg.setVerticalSpacing(8)
        self.consent_date = DateField(placeholder=t("ui.consent.date_placeholder"))
        cg.addWidget(field(t("ui.consent.date"), self.consent_date), 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        self.consent_boxes: dict[str, QCheckBox] = {}
        for i, key in enumerate(CONSENT_KEYS):
            cb = QCheckBox(t(f"ui.consent.{key}"))
            cb.toggled.connect(self._consent_toggled)
            self.consent_boxes[key] = cb
            cg.addWidget(cb, i % 2, 1 + i // 2)
        cg.setColumnStretch(0, 2)
        cg.setColumnStretch(1, 3)
        cg.setColumnStretch(2, 3)
        self.body.addLayout(cg)
        self.error = label("", wrap=True)
        self.error.setStyleSheet(f"color: {theme.TOKENS['fix']}; font-weight: 600;")
        self.body.addWidget(self.error)
        self.body.addStretch(1)
        self.actions = QWidget()
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        self.del_btn = button(t("ui.athletes.delete"), "trash", kind="ghost")
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(self.del_btn)
        self.withdraw_btn = button(t("ui.consent.withdraw"), kind="ghost")
        self.withdraw_btn.clicked.connect(self._withdraw)
        row.addWidget(self.withdraw_btn)
        row.addStretch(1)
        self.cancel_btn = button(t("ui.common.cancel"), "x", kind="ghost")
        self.cancel_btn.clicked.connect(self.revert)
        row.addWidget(self.cancel_btn)
        self.save_btn = button(t("ui.common.save"), "save", kind="primary")
        self.save_btn.clicked.connect(self._save)
        row.addWidget(self.save_btn)
        self._loaded: tuple = ()
        for w in (self.first, self.last, self.birth.edit, self.height, self.weight, self.max_hr, self.rest_hr,
                  self.email, self.consent_date.edit, self.watch_vo2, self.watch_vo2_date.edit):
            w.textChanged.connect(self._changed)
        self.sex.currentIndexChanged.connect(self._changed)
        self.notes.textChanged.connect(self._changed)

    def _snapshot(self) -> tuple:
        return (self.first.text().strip(), self.last.text().strip(), self.birth.text().strip(),
                self.sex.currentIndex(), self.height.value(), self.weight.value(), self.max_hr.value(),
                self.rest_hr.value(), self.notes.toPlainText().strip(), self.email.text().strip(),
                self.consent_date.text().strip(),
                tuple(cb.isChecked() for cb in self.consent_boxes.values()),
                self.watch_vo2.value(), self.watch_vo2_date.text().strip())

    def _consent_toggled(self, on: bool) -> None:
        """A ticked box without a date gets today's date: the form is filled in when it is signed."""
        if on and not self.consent_date.text().strip():
            self.consent_date.set_date(date.today())
        self._changed()

    def dirty(self) -> bool:
        """True when a field differs from what was loaded (or from empty, for a new athlete)."""
        return self._snapshot() != self._loaded

    def _changed(self, *_: Any) -> None:
        self.cancel_btn.setEnabled(self.dirty())

    def revert(self) -> None:
        """*Avbryt*: back to the saved values (an empty form for a new athlete)."""
        self.load(self.athlete)

    def confirm_discard(self) -> bool:
        """True when it is fine to leave the form: nothing unsaved, or the coach chose to discard it.

        *Ny idrottare* pressed again before saving (real hardware 2026-11-14) lost everything that had been filled in. After a discard the form shows the saved values again.
        """
        if not self.dirty():
            return True
        r = QMessageBox.question(self, t("ui.app.name"), t("ui.athletes.unsaved"),
                                 QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                                 QMessageBox.StandardButton.Cancel)
        if r != QMessageBox.StandardButton.Ok:
            return False
        self.revert()
        return True

    def load(self, a: Athlete | None) -> None:
        self.athlete = a
        self.error.clear()
        new = a is None or a.id is None
        self.heading.setText(t("ui.athletes.new") if new else a.name)
        self.del_btn.setVisible(not new)
        self.test_btn.setVisible(not new)
        self.extract_btn.setVisible(not new)
        self.withdraw_btn.setVisible(not new and bool(a and a.consent_registered))
        self.plan_btn.setVisible(not new)
        a = a or Athlete(None, "", "")
        self.first.setText(a.first_name)
        self.last.setText(a.last_name)
        self.birth.set_date(a.birth_date)
        self.sex.setCurrentIndex(max(0, self.sex.findData(Sex(a.sex).value if a.sex else "")))
        self.height.setValue(a.height_cm or 0)
        self.weight.setValue(a.weight_kg or 0)
        self.max_hr.setValue(a.max_hr or 0)
        self.max_hr_bike.setValue(a.max_hr_for("bike") or 0)
        self.max_hr_skierg.setValue(a.max_hr_for("skierg") or 0)
        self.rest_hr.setValue(a.resting_hr or 0)
        self.watch_vo2.setValue(a.watch_vo2max or 0)
        self.watch_vo2_date.set_date(a.watch_vo2max_date)
        self.notes.setPlainText(a.notes)
        self.email.setText(a.email or "")
        c = a.consent or {}
        for key, cb in self.consent_boxes.items():
            cb.blockSignals(True)
            cb.setChecked(bool(c.get(key)))
            cb.blockSignals(False)
        try:
            self.consent_date.set_date(date.fromisoformat(c["date"]) if c.get("date") else None)
        except (ValueError, TypeError):
            self.consent_date.set_date(None)
        self._loaded = self._snapshot()
        self._changed()
        self.first.setFocus()

    def collect(self) -> Athlete | None:
        """The form as an Athlete, or None (with a message) when something is wrong."""
        first, last = self.first.text().strip(), self.last.text().strip()
        if not first:
            self.error.setText(t("ui.athletes.err_name"))
            return None
        h = self.height.value() or None
        if h is not None and not 100 <= h <= 230:
            self.error.setText(t("ui.athletes.err_height"))
            return None
        mx, rs = self.max_hr.value() or None, self.rest_hr.value() or None
        if mx is not None and not 120 <= mx <= 230:
            self.error.setText(t("ui.athletes.err_maxhr"))
            return None
        if rs is not None and rs > 120:
            self.error.setText(t("ui.athletes.err_resthr_range"))
            return None
        other: dict[str, int] = {}
        for sport, w in (("bike", self.max_hr_bike), ("skierg", self.max_hr_skierg)):
            v = w.value() or None
            if v is not None and not 120 <= v <= 230:
                self.error.setText(t("ui.athletes.err_maxhr"))
                return None
            if v:
                other[sport] = int(v)
        if mx and rs and rs >= mx:
            self.error.setText(t("ui.athletes.err_resthr"))
            return None
        w = self.weight.value()
        if w is not None and w > 200:
            self.error.setText(t("ui.athletes.err_weight"))
            return None
        try:
            birth = self.birth.date()
        except ValueError:
            self.error.setText(t("ui.athletes.err_birth"))
            return None
        email = self.email.text().strip()
        if email and not valid_email(email):
            self.error.setText(t("ui.athletes.err_email"))
            return None
        try:
            cdate = self.consent_date.date()
        except ValueError:
            self.error.setText(t("ui.consent.err_date"))
            return None
        base = self.athlete or Athlete(None, "", "")
        consent = {k: v for k, v in (base.consent or {}).items() if k not in CONSENT_KEYS and k != "date"}
        boxes = {key: cb.isChecked() for key, cb in self.consent_boxes.items()}
        if cdate is not None or any(boxes.values()):
            consent.update(boxes, date=(cdate or date.today()).isoformat())
        wv = self.watch_vo2.value() or None
        if wv is not None and not 15 <= wv <= 95:
            self.error.setText(t("ui.athletes.err_watch_vo2max"))
            return None
        try:
            wdate = self.watch_vo2_date.date()
        except ValueError:
            self.error.setText(t("ui.athletes.err_watch_date"))
            return None
        sex = Sex(self.sex.currentData()) if self.sex.currentData() else None
        mxi = int(mx) if mx else None
        # a max HR typed in the form is "entered" today; an unchanged one keeps where it came from
        src, mdate = (base.max_hr_source, base.max_hr_date) if mxi == base.max_hr else ("entered", date.today())
        return Athlete(base.id, first, last, birth, sex, h, w, mxi, int(rs) if rs else None,
                       self.notes.toPlainText().strip(), base.created, consent, email,
                       max_hr_source=src if mxi else "", max_hr_date=mdate if mxi else None,
                       watch_vo2max=wv, watch_vo2max_date=(wdate or date.today()) if wv else None,
                       sport_max_hr=other)

    def _save(self) -> None:
        a = self.collect()
        if a is not None:
            self.error.clear()
            self.saved.emit(a)

    def _withdraw(self) -> None:
        """The person takes the consent back: that leads to the erasure, after the same kind of question."""
        if self.athlete is None or self.athlete.id is None:
            return
        r = QMessageBox.question(self, t("ui.consent.withdraw"),
                                 t("ui.consent.withdraw_confirm", name=self.athlete.name),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self.deleted.emit(self.athlete.id)

    def _delete(self) -> None:
        if self.athlete is None or self.athlete.id is None:
            return
        r = QMessageBox.question(self, t("ui.athletes.delete"),
                                 t("ui.athletes.delete_confirm", name=self.athlete.name))
        if r == QMessageBox.StandardButton.Yes:
            self.deleted.emit(self.athlete.id)


class AthletesView(QWidget):
    """List + search on the left, the form on the right."""

    start_test = Signal(object)
    due_changed = Signal(int)       # how many athletes are on the reminder list
    open_plan = Signal(object)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        w, v = page()
        outer.addWidget(w)
        hdr = PageHeader(t("ui.nav.athletes"), t("ui.athletes.subtitle"))
        add = button(t("ui.athletes.add"), "plus", kind="primary")
        add.clicked.connect(lambda: self.form_new())
        hdr.add_action(add)
        v.addWidget(hdr)
        body = QHBoxLayout()
        body.setSpacing(20)
        v.addLayout(body, 1)
        left = QVBoxLayout()
        left.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("ui.athletes.search"))
        self.search.addAction(theme.icon("search", "muted"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        left.addWidget(self.search)
        # the reminder: people not tested for 24 months, or kept without consent to keep
        self.due: dict[int, dict[str, Any]] = {}
        self.remind = Card(flat=True, pad=12)
        self.remind_text = label("", wrap=True)
        self.remind_text.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        self.remind.body.addWidget(self.remind_text)
        self.remind_btn = button(t("ui.remind.show"))
        self.remind_btn.clicked.connect(self._show_due)
        self.remind.body.addWidget(self.remind_btn)
        self.remind.hide()
        left.addWidget(self.remind)
        self.list = QListWidget()
        self.list.setSpacing(2)
        self.list.currentItemChanged.connect(self._selected)
        left.addWidget(self.list, 1)
        lw = QWidget()
        lw.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)
        lw.setFixedWidth(360)
        body.addWidget(lw)
        self.stack = QStackedWidget()
        self.empty = Card()
        self.empty.body.addStretch(1)
        e = label(t("ui.athletes.empty"), "PageSubtitle", wrap=True)
        e.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.body.addWidget(e)
        self.empty.body.addStretch(1)
        self.form = AthleteForm()
        self.form.saved.connect(self._save)
        self.form.deleted.connect(self._delete)
        self.form.start_test.connect(self.start_test)
        self.form.extract.connect(self.export_all)
        self.form.open_plan.connect(self.open_plan)
        self.stack.addWidget(self.empty)
        # small screens: the fields scroll instead of being squeezed, the buttons stay below the scroll area
        self.form_page = QWidget()
        fp = QVBoxLayout(self.form_page)
        fp.setContentsMargins(0, 0, 0, 0)
        fp.setSpacing(12)
        fp.addWidget(scrolled(self.form), 1)
        fp.addWidget(self.form.actions)
        self.stack.addWidget(self.form_page)
        body.addWidget(self.stack, 1)
        ctx.athletes_changed.connect(self.refresh)
        ctx.sessions_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_: Any) -> None:
        cur = self.form.athlete.id if self.form.athlete else None
        self.list.blockSignals(True)
        self.list.clear()
        try:
            rows = self.ctx.store.athletes(self.search.text())
        except Exception as e:
            rows = []
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.db", detail=str(e)))
        try:
            self.due = self.ctx.store.retention_due()
        except Exception:
            self.due = {}
        self.remind_text.setText(t("ui.remind.banner", n=len(self.due)))
        self.remind.setVisible(bool(self.due))
        self.due_changed.emit(len(self.due))
        for a in rows:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, a)
            it.setSizeHint(QSize(300, 62))
            self.list.addItem(it)
            self.list.setItemWidget(it, AthleteRow(a, review=a.id in self.due))
            if a.id == cur:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        if self.list.count() and self.list.currentItem() is None and self.stack.currentWidget() is self.empty:
            self.list.setCurrentRow(0)
            self._selected(self.list.currentItem())

    def _selected(self, it: QListWidgetItem | None, prev: QListWidgetItem | None = None, *_: Any) -> None:
        if it is None:
            return
        if self.stack.currentWidget() is self.form_page and not self.form.confirm_discard():
            # stay on the unsaved form: put the selection back without loading anything
            self.list.blockSignals(True)
            if prev is not None:
                self.list.setCurrentItem(prev)
            else:
                self.list.setCurrentRow(-1)
            self.list.blockSignals(False)
            return
        self.form.load(it.data(Qt.ItemDataRole.UserRole))
        self._show_reminder()
        self.stack.setCurrentWidget(self.form_page)

    def _show_reminder(self) -> None:
        a = self.form.athlete
        d = self.due.get(a.id) if a is not None and a.id is not None else None
        texts = [t(f"ui.remind.{d['reason']}", name=a.name, date=d["last"])] if d is not None else []
        texts.append(services.max_test_due(self.ctx.store, a))   # a max test older than two years
        texts = [x for x in texts if x]
        if not texts:
            self.form.reminder.hide()
            return
        self.form.reminder.setText("\n\n".join(texts))
        self.form.reminder.show()

    def _show_due(self) -> None:
        """*Visa*: open the first athlete on the reminder list."""
        self.search.clear()
        if self.due:
            self.select(next(iter(self.due)))

    def select(self, athlete_id: int | None) -> None:
        """Show this athlete's form (used when a test could not start for lack of consent)."""
        for i in range(self.list.count()):
            a = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            if a.id == athlete_id:
                self.list.setCurrentRow(i)
                return

    def leave_ok(self) -> bool:
        """True when the page can be left: no unsaved changes, or the coach chose to discard them."""
        return self.stack.currentWidget() is not self.form_page or self.form.confirm_discard()

    def form_new(self) -> None:
        if not self.leave_ok():
            return
        self.list.blockSignals(True)
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self.form.load(None)
        self.form.reminder.hide()
        self.stack.setCurrentWidget(self.form_page)

    def _save(self, a: Athlete) -> None:
        try:
            saved = self.ctx.store.save_athlete(a)
        except Exception as e:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.db", detail=str(e)))
            return
        self.form.load(saved)
        self.ctx.athletes_changed.emit()
        self._show_reminder()

    def _delete(self, athlete_id: int) -> None:
        self.erase(athlete_id)

    def export_all(self, a: Athlete) -> None:
        """The extract a person can ask for: everything about them in one zip."""
        if a.id is None:
            return
        name = f"{t('ui.athletes.extract_file')}_{a.name.replace(' ', '_')}_{date.today().isoformat()}.zip"
        fn, _ = QFileDialog.getSaveFileName(self, t("ui.athletes.extract"), str(Path.home() / name), "Zip (*.zip)")
        if not fn:
            return
        try:
            self.ctx.store.export_athlete(a.id, Path(fn))
        except Exception as e:
            QMessageBox.warning(self, t("ui.app.name"), str(e))
            return
        QMessageBox.information(self, t("ui.athletes.extract"), t("ui.athletes.extract_done", path=fn))

    def erase(self, athlete_id: int, keep: list[Any] | None = None) -> bool:
        """Erase the person completely and show what the program could not reach. The question
        was asked by the caller. ``keep``: saved reports that stay (the one just handed over)."""
        a = self.ctx.store.athlete(athlete_id)
        name = a.name if a else "–"
        live = self.ctx.live
        if live is not None and getattr(live.plan.athlete, "id", None) == athlete_id:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.erase.running", name=name))
            return False
        self.ctx.athlete_erased.emit(athlete_id)        # before the rows go: nothing saves them back
        try:
            summary = self.ctx.store.erase_athlete(athlete_id, keep)
        except Exception as e:
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.db", detail=str(e)))
            return False
        if self.form.athlete is not None and self.form.athlete.id == athlete_id:
            self.form.athlete = None
            self.stack.setCurrentWidget(self.empty)
        self.ctx.athletes_changed.emit()
        self.ctx.sessions_changed.emit()
        self.last_erasure = services.erasure_text(name, summary)
        QMessageBox.information(self, t("ui.erase.title"), self.last_erasure)
        return True
