"""A laptop screen (Tom 2026-11-16): the pages fit the width, a page that does not gets a visible scroll
bar, and the mouse wheel scrolls the page instead of changing a list box that was not clicked."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QScrollBar, QSpinBox

LAPTOP = 1280          # 1920 px at 150 %: the content beside the sidebar is 1032 px


def _wheel(w, down: bool = True) -> None:
    d = -120 if down else 120
    e = QWheelEvent(QPointF(5, 5), QPointF(w.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, d),
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(w, e)


def test_the_pages_fit_a_laptop(demo_window, qapp):
    win = demo_window
    win.resize(LAPTOP, 680)
    for page in (win.athletes, win.history, win.settings, win.tests_info, win.plan):
        win.stack.setCurrentWidget(page)
        qapp.processEvents()
        qapp.processEvents()
        assert win.page_scroll.horizontalScrollBar().maximum() == 0, page
    assert win.settings.scroll.horizontalScrollBar().maximum() == 0
    assert not win.settings.cols.side_by_side()                # one column on the laptop
    win.stack.setCurrentWidget(win.settings)
    win.resize(1600, 900)
    qapp.processEvents()
    qapp.processEvents()
    assert win.settings.cols.side_by_side()                    # two on a big screen


def test_a_page_wider_than_the_window_scrolls(demo_window, qapp):
    win = demo_window
    assert win.minimumWidth() <= 1000                          # the window itself fits the screen
    win.stack.setCurrentWidget(win.plan)
    win.resize(win.minimumWidth(), 680)
    qapp.processEvents()
    assert win.page_scroll.horizontalScrollBar().maximum() > 0  # the bar is there, nothing is cut off


def test_the_wheel_does_not_change_a_box_that_was_not_clicked(demo_window, qapp):
    win = demo_window
    win.stack.setCurrentWidget(win.settings)
    qapp.processEvents()
    combo = win.settings.findChild(QComboBox)
    combo.clearFocus()
    before = combo.currentIndex()
    _wheel(combo)
    _wheel(combo, down=False)
    _wheel(combo)
    assert combo.currentIndex() == before
    spin = QSpinBox(win.settings)
    spin.setRange(0, 10)
    spin.setValue(5)
    spin.show()
    _wheel(spin)
    assert spin.value() == 5
    spin.setFocus()
    qapp.processEvents()
    if spin.hasFocus():                                        # offscreen gives focus only to an active window
        _wheel(spin)
        assert spin.value() != 5                               # clicked: the wheel works as before


def test_the_scroll_bars_still_take_the_wheel(demo_window, qapp):
    from lopanalys.ui.widgets.common import WheelGuard
    bar = QScrollBar(Qt.Orientation.Vertical)
    bar.setRange(0, 100)
    bar.setValue(50)
    assert not WheelGuard().eventFilter(bar, QWheelEvent(QPointF(), QPointF(), QPoint(), QPoint(0, -120),
                                                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                                         Qt.ScrollPhase.NoScrollPhase, False))
