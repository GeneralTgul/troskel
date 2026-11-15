"""Forms on the gym PC: saving a new athlete, and hints that must not be drawn over the inputs."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox

from lopanalys.core.models import Athlete, Sex
from lopanalys.ui.widgets.common import _Field


def _pump(n: int = 60) -> None:
    for _ in range(n):
        QApplication.processEvents()


def test_new_athlete_with_sex_is_saved(demo_window, monkeypatch):
    """2026-11-14: "databasen gick inte att läsa eller skriva 'str' object has no attribute 'value'"."""
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(str(a[-1]))))
    win = demo_window
    win.go("athletes")
    view = win.athletes
    view.form_new()
    f = view.form
    f.first.setText("Ny")
    f.last.setText("Löpare")
    f.sex.setCurrentIndex(f.sex.findText(f.sex.itemText(2)))   # Man
    f._save()
    assert warnings == []
    saved = [a for a in win.ctx.store.athletes() if a.first_name == "Ny"]
    assert len(saved) == 1 and saved[0].sex == Sex.MALE
    f.load(saved[0])                                          # the stored value is shown again
    assert f.collect().sex == Sex.MALE


def test_db_accepts_sex_as_plain_string(tmp_path):
    from lopanalys.storage.db import Database
    d = Database(tmp_path / "l.sqlite")
    try:
        a = Athlete(None, "Str", "Kön", sex="female")  # type: ignore[arg-type]
        d.save_athlete(a)
        assert d.get_athlete(a.id).sex == Sex.FEMALE
    finally:
        d.close()


def _assert_no_overlap(root) -> int:
    boxes = [b for b in root.findChildren(_Field) if b.isVisible()]
    for b in boxes:
        lay = b.layout()
        inp, hint = lay.itemAt(1).widget(), lay.itemAt(2).widget()
        assert hint.geometry().top() >= inp.geometry().bottom(), "hint drawn over the input"
        assert hint.height() >= hint.heightForWidth(hint.width()), "hint cut off"
        assert b.height() >= lay.heightForWidth(b.width()), "field squeezed"
    return len(boxes)


def test_hints_not_over_inputs_on_a_small_window(demo_window):
    """2026-11-14: the hint texts for height, heart rate and warm-up were drawn over their inputs."""
    win = demo_window
    win.resize(1000, 560)
    win.go("athletes")
    win.athletes.form_new()
    _pump()
    assert _assert_no_overlap(win.athletes.form) >= 2      # längd, maxpuls
    win.go("new_test")
    win.new_test.stack.setCurrentIndex(2)                   # the protocol page with pause and warm-up
    _pump()
    assert _assert_no_overlap(win.new_test.stack.currentWidget()) >= 2


def test_field_hint_does_not_follow_the_width_it_is_handed(demo_window, monkeypatch):
    """The Windows build stood in test_pin_locks_at_start_and_after_idle for 1.5 h (2026-11-15): app.setStyleSheet
    never returned, inside _Field.sizeHint. The hint followed self.width(), so the layout and the hint fed each
    other. The hint now only changes through resizeEvent, and a new style sheet on an open window is a bounded
    amount of work."""
    from lopanalys.ui import theme
    win = demo_window
    win.resize(1000, 560)
    win.go("athletes")
    win.athletes.form_new()
    _pump()
    box = next(b for b in win.athletes.form.findChildren(_Field) if b.isVisible())
    asked: list[int] = []
    orig_needed = box._needed
    monkeypatch.setattr(box, "_needed", lambda w: asked.append(w) or orig_needed(w))
    box.sizeHint()
    assert asked == [], "sizeHint computed the height from the width it is being handed"
    monkeypatch.undo()
    calls = {"n": 0}
    orig = _Field.sizeHint

    def counted(self):
        calls["n"] += 1
        return orig(self)

    monkeypatch.setattr(_Field, "sizeHint", counted)
    QApplication.instance().setStyleSheet(theme.qss())
    _pump()
    fields = len(win.findChildren(_Field))
    assert calls["n"] <= 20 * max(1, fields), (calls["n"], fields)
    win.go("athletes")
    _pump()
    assert _assert_no_overlap(win.athletes.form) >= 2


def test_native_crashes_are_logged(demo_window):
    """A crash inside a driver (real hardware 2026-11-14, heap corruption) left no trace in the log."""
    import faulthandler

    from lopanalys.ui import app
    assert faulthandler.is_enabled()
    assert app._CRASH_FILE is not None and app._CRASH_FILE.name.endswith("crash.log")


def test_choice_card_title_fits_beside_the_check_mark(qtbot):
    """A photo 2026-11-14: the selected card read "Bluetooth (FTM" with the check mark on top.
    The drawn title must fit its width, shrunk or shortened with "…" (Segoe UI on Windows is wide)."""
    from PySide6.QtGui import QFontMetricsF

    from lopanalys.ui import theme
    from lopanalys.ui.widgets.common import ChoiceCard, fit_text
    card = ChoiceCard("Bluetooth (FTMS)", "Programmet styr bandet direkt", "bluetooth")
    qtbot.addWidget(card)
    card.setChecked(True)
    card.resize(250, 110)
    assert not card.grab().isNull()
    for width in (300, 150, 90, 40):
        f, text = fit_text(theme.font(16), "Bluetooth (FTMS)", width)
        assert QFontMetricsF(f).horizontalAdvance(text) <= width
        assert text == "Bluetooth (FTMS)" or text.endswith("…")
    assert fit_text(theme.font(16), "Bluetooth (FTMS)", 300)[1] == "Bluetooth (FTMS)"


def test_birth_date_is_typed_directly():
    """2026-11-14: the date of birth had to be picked (wrong) in the calendar before it could be typed."""
    from datetime import date

    import pytest

    from lopanalys.ui.views.athletes import parse_birth_date
    today = date(2026, 11, 14)
    for text in ("2001-04-23", "20010423", "010423", "01-04-23", "2001.04.23", " 2001/4/23 "):
        assert parse_birth_date(text, today) == date(2001, 4, 23), text
    assert parse_birth_date("850101", today) == date(1985, 1, 1)
    assert parse_birth_date("", today) is None
    for bad in ("2001-02-30", "1919-12-31", "2027-01-01", "12345", "abc", "2001-0423x"):
        with pytest.raises(ValueError):
            parse_birth_date(bad, today)


def test_athlete_form_fields_start_empty_and_are_typed(demo_window):
    """2026-11-14: a "–" in längd, vikt and the pulses had to be erased, the date could not be typed."""
    from datetime import date
    win = demo_window
    win.go("athletes")
    view = win.athletes
    view.form_new()
    f = view.form
    for w in (f.birth.edit, f.height, f.weight, f.max_hr, f.rest_hr):
        assert w.text() == ""
    f.first.setText("Typ")
    f.birth.edit.setText("010423")
    f.birth.edit.editingFinished.emit()
    assert f.birth.text() == "2001-04-23"                   # tidied to the full form
    f.height.setText("181")
    f.weight.setText("72,5")
    f.max_hr.setText("195")
    f.rest_hr.setText("48")
    a = f.collect()
    assert a is not None
    assert (a.birth_date, a.height_cm, a.weight_kg, a.max_hr, a.resting_hr) == (date(2001, 4, 23), 181, 72.5, 195, 48)
    f.birth.edit.setText("")                                 # empty is fine, the field is optional
    f.height.setText("")
    a = f.collect()
    assert a is not None and a.birth_date is None and a.height_cm is None
    for widget, text in ((f.birth.edit, "2001-02-30"), (f.height, "99"), (f.max_hr, "250"), (f.rest_hr, "130"),
                         (f.weight, "201")):
        old = widget.text()
        widget.setText(text)
        assert f.collect() is None and f.error.text(), text
        widget.setText(old)
    f.rest_hr.setText("195")                                 # resting not below max
    assert f.collect() is None
    f.rest_hr.setText("48")
    f.max_hr.setFocus()                                      # entering a field with a value selects it
    _pump(5)
    assert f.max_hr.selectedText() == "195"
    f.load(a)                                                # a stored athlete is shown again
    assert f.height.text() == "" and f.weight.text() == "72,5" and f.max_hr.text() == "195"
    f.revert()


def test_unsaved_form_asks_before_it_is_left(demo_window, monkeypatch):
    """2026-11-14: *Ny idrottare* again before saving, and all that had been filled in was gone."""
    asked: list[str] = []
    answer = [QMessageBox.StandardButton.Cancel]

    def question(*a, **k):
        asked.append(str(a[2]))
        return answer[0]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    win = demo_window
    win.go("athletes")
    view = win.athletes
    view.form_new()
    f = view.form
    assert not f.dirty() and not f.cancel_btn.isEnabled()
    view.form_new()                                          # nothing typed: no question
    assert asked == []
    f.first.setText("Osparad")
    assert f.dirty() and f.cancel_btn.isEnabled()
    view.form_new()                                          # Avbryt: the text stays
    assert asked and "inte sparat" in asked[-1] and f.first.text() == "Osparad"
    view.list.setCurrentRow(0)                               # another athlete in the list: still asked
    assert len(asked) == 2 and f.first.text() == "Osparad" and view.list.currentItem() is None
    win.nav_buttons["history"].click()                       # another page: asked, and stays
    assert len(asked) == 3 and win.stack.currentWidget() is view
    assert win.nav_buttons["athletes"].isChecked()
    answer[0] = QMessageBox.StandardButton.Ok                # OK: the changes are discarded
    win.nav_buttons["history"].click()
    assert win.stack.currentWidget() is win.history and not f.dirty() and f.first.text() == ""
    win.go("athletes")
    view.list.setCurrentRow(0)
    first = view.list.item(0).data(0x0100)
    assert f.athlete.id == first.id
    f.notes.setPlainText(f.notes.toPlainText() + " ändrad")
    answer[0] = QMessageBox.StandardButton.Cancel
    view.list.setCurrentRow(1)
    assert view.list.currentRow() == 0 and f.athlete.id == first.id
    f.cancel_btn.click()                                     # Avbryt in the form: back to the saved values
    assert not f.dirty() and f.notes.toPlainText() == first.notes


def test_save_button_is_always_visible(demo_window):
    """*Spara* was at the bottom of the scrolled form and only seen after scrolling (real hardware 2026-11-14)."""
    from PySide6.QtWidgets import QScrollArea
    win = demo_window
    win.resize(1100, 700)
    win.go("athletes")
    win.athletes.form_new()
    _pump()
    f = win.athletes.form
    sa = next(w for w in win.athletes.findChildren(QScrollArea) if w.widget() is f)
    assert not sa.isAncestorOf(f.save_btn)
    assert f.save_btn.isVisible() and f.cancel_btn.isVisible()
    top = f.save_btn.mapTo(win, f.save_btn.rect().bottomLeft()).y()
    assert top <= win.height()
