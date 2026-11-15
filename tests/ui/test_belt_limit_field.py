"""The belt's highest speed and incline in Inställningar: at 0 the field says "Som bandet anger", and
digits typed over it must be taken (in a test on the treadmill 18 could not be typed)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


def _type(qapp, box, text):
    box.setFocus()
    QTest.mouseClick(box.lineEdit(), Qt.MouseButton.LeftButton)   # the cursor lands after the word
    qapp.processEvents()
    QTest.keyClicks(box, text)
    QTest.keyClick(box, Qt.Key.Key_Return)
    qapp.processEvents()


def test_digits_replace_the_word_at_zero(demo_window, qapp):
    s = demo_window.settings
    demo_window.ctx.set_setting("devices.max_speed_kmh", 0.0)
    s.belt_speed.setValue(0)
    s.belt_incline.setValue(0)
    assert s.belt_speed.text() == "Som bandet anger"
    _type(qapp, s.belt_speed, "18")
    assert s.belt_speed.value() == 18.0
    assert demo_window.ctx.setting("devices.max_speed_kmh") == 18.0
    _type(qapp, s.belt_incline, "12")
    assert s.belt_incline.value() == 12.0


def test_the_widget_alone(qapp):
    from lopanalys.ui.widgets.inputs import SpecialSpinBox
    b = SpecialSpinBox()
    b.setRange(0, 40)
    b.setSuffix(" %")
    b.setSpecialValueText("Som bandet anger")
    b.setValue(0)
    b.show()
    _type(qapp, b, "12,5" if b.locale().decimalPoint() == "," else "12.5")
    assert b.value() == 12.5
