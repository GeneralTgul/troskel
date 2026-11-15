"""Touch-friendly inputs for the operator: a big number pad for lactate and the Borg RPE scale, and a
number field whose lowest value is a word (SpecialSpinBox)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFocusEvent, QFont, QKeyEvent
from PySide6.QtWidgets import QButtonGroup, QDoubleSpinBox, QGridLayout, QLabel, QVBoxLayout, QWidget

from lopanalys.core.i18n import t
from lopanalys.ui import theme
from lopanalys.ui.widgets.common import button


class NumberPad(QWidget):
    """Display + 0-9, comma, backspace and an OK key. Emits ``submitted(float)``.

    Accepts the keyboard too (digits, , or ., Backspace, Enter). Values are clamped to
    ``0 < v <= max_value`` (lactate: 30 mmol/L) so a typo cannot end up in the report.
    """

    submitted = Signal(float)

    def __init__(self, unit: str = "mmol/L", max_value: float = 30.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.unit, self.max_value = unit, max_value
        self.text = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        self.display = QLabel()
        self.display.setObjectName("CardFlat")
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.display.setMinimumHeight(64)
        self.display.setFont(theme.font(36, QFont.Weight.Bold, tabular=True))
        self.display.setStyleSheet("padding: 0 18px;")
        v.addWidget(self.display)
        g = QGridLayout()
        g.setSpacing(8)
        keys = ["7", "8", "9", "4", "5", "6", "1", "2", "3", ",", "0", "⌫"]
        for i, k in enumerate(keys):
            b = button(k, kind="key")
            b.setMinimumSize(64, 54)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, k=k: self.press(k))
            g.addWidget(b, i // 3, i % 3)
        v.addLayout(g)
        self.ok = button(t("ui.numpad.save"), "check", kind="primary", size="big")
        self.ok.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ok.clicked.connect(self.submit)
        v.addWidget(self.ok)
        self._refresh()

    def press(self, k: str) -> None:
        if k == "⌫":
            self.text = self.text[:-1]
        elif k in ",.":
            if "," not in self.text:
                self.text = (self.text or "0") + ","
        elif len(self.text.replace(",", "")) < 4:
            if "," in self.text and len(self.text.split(",")[1]) >= 2:
                return
            self.text = "" if self.text == "0" else self.text
            self.text += k
        self._refresh()

    def value(self) -> float | None:
        try:
            v = float(self.text.replace(",", "."))
        except ValueError:
            return None
        return v if 0 < v <= self.max_value else None

    def submit(self) -> None:
        v = self.value()
        if v is not None:
            self.submitted.emit(v)
            self.text = ""
            self._refresh()

    def clear(self) -> None:
        self.text = ""
        self._refresh()

    def _refresh(self) -> None:
        shown = self.text or "0"
        self.display.setText(f"{shown} <span style='font-size:18px;color:{theme.TOKENS['muted']}'>{self.unit}</span>")
        self.ok.setEnabled(self.value() is not None)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.text()
        if k.isdigit() or k in ",.":
            self.press(k)
        elif e.key() == Qt.Key.Key_Backspace:
            self.press("⌫")
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submit()
        else:
            super().keyPressEvent(e)


class RpePicker(QWidget):
    """Borg 6-20 as a grid of toggle buttons with the verbal anchors. Emits ``picked(int)``."""

    picked = Signal(int)
    ANCHORS = {6: "ui.rpe.6", 7: "ui.rpe.7", 9: "ui.rpe.9", 11: "ui.rpe.11", 13: "ui.rpe.13",
               15: "ui.rpe.15", 17: "ui.rpe.17", 19: "ui.rpe.19", 20: "ui.rpe.20"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(6)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, v in enumerate(range(6, 21)):
            b = button(str(v), kind="rpe")
            b.setCheckable(True)
            b.setMinimumHeight(40)
            if v in self.ANCHORS:
                b.setToolTip(t(self.ANCHORS[v]))
            self.group.addButton(b, v)
            g.addWidget(b, i // 8, i % 8)
        self.hint = QLabel(t("ui.rpe.hint"))
        self.hint.setObjectName("Hint")
        g.addWidget(self.hint, 2, 0, 1, 8)
        self.group.idClicked.connect(self._clicked)

    def _clicked(self, v: int) -> None:
        anchor = next((t(self.ANCHORS[k]) for k in sorted(self.ANCHORS, reverse=True) if k <= v), "")
        self.hint.setText(f"{v}: {anchor}")
        self.picked.emit(v)

    def reset(self) -> None:
        self.group.setExclusive(False)
        for b in self.group.buttons():
            b.setChecked(False)
        self.group.setExclusive(True)
        self.hint.setText(t("ui.rpe.hint"))


class SpecialSpinBox(QDoubleSpinBox):
    """A QDoubleSpinBox whose lowest value shows a word (``setSpecialValueText``), e.g. "Som bandet
    anger" for 0, and that still takes digits typed over that word.

    A plain QDoubleSpinBox keeps the word in its line edit, so a digit typed after it makes the text
    invalid and is thrown away: the field could not be changed from the keyboard. Here the whole text
    is selected when the field gets focus, and before a digit is typed while the word is shown, so the
    digit replaces it."""

    def _showing_special(self) -> bool:
        return bool(self.specialValueText()) and self.value() <= self.minimum()

    def focusInEvent(self, e: QFocusEvent) -> None:
        super().focusInEvent(e)
        QTimer.singleShot(0, self.selectAll)     # after Qt has placed the cursor for a mouse click

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if self._showing_special() and e.text() and (e.text().isdigit() or e.text() in ",."):
            self.lineEdit().selectAll()
        super().keyPressEvent(e)
