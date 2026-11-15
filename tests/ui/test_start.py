"""Start-up guards: one running instance only."""
import os
import tempfile

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QMessageBox

from lopanalys.ui import app as app_mod


def test_second_start_is_refused(qtbot, monkeypatch):
    """A second start while one is running shows a message and exits without opening a window."""
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(app_mod, "build", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not build")))
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "lopanalys.lock"))
    assert lock.tryLock(100)
    try:
        assert app_mod.main(["--demo"]) == 0
    finally:
        lock.unlock()
    assert shown and "redan igång" in shown[0]
