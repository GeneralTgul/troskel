"""The test suite deletes the windows a test opened (tests/conftest.py), so a style sheet late in the run does
not repolish hundreds of dead windows (Windows CI stood still for 1.5 h)."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication


def test_a_window_is_opened(demo_window):
    assert demo_window.isVisible()


def test_no_window_is_left_from_the_test_before(qapp):
    from lopanalys.ui.main_window import MainWindow
    left = [w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow)]
    assert left == []
