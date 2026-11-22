"""The dialog for an unexpected error: it is shown, and one at a time.

Before 2026-11-22 the dialog was never seen: the box was a local of the function that showed it and
was destroyed when the function returned, so an uncaught error only reached the log. Kept alive, it
must not open once per error either: one that repeats (a timer that fires 30 times a second, as the
live picture's) would fill the screen in the middle of a test."""
from __future__ import annotations

import sys
import threading

from PySide6.QtWidgets import QApplication, QMessageBox

from lopanalys.ui import app as app_mod


def _open_boxes() -> list[QMessageBox]:
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]


def test_a_repeating_error_shows_one_dialog(qapp, qtbot, tmp_path):
    old, old_thread = sys.excepthook, threading.excepthook
    for b in _open_boxes():       # a box an earlier test in the same run left open is not this test's
        b.close()
    qapp.processEvents()
    try:
        app_mod.install_exception_hook(str(tmp_path / "log.txt"))
        hook = sys.excepthook
        for n in range(5):
            hook(ValueError, ValueError(f"fault {n}"), None)
        qapp.processEvents()
        boxes = _open_boxes()
        assert len(boxes) == 1
        boxes[0].close()
        qapp.processEvents()
        hook(ValueError, ValueError("a later fault"), None)       # the next error is shown again
        qapp.processEvents()
        assert len(_open_boxes()) == 1
    finally:
        sys.excepthook, threading.excepthook = old, old_thread
        for b in _open_boxes():
            b.close()
        qapp.processEvents()
