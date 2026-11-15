"""Test-wide settings: no test may download anything."""
import os

os.environ["LOPANALYS_NO_DOWNLOAD"] = "1"

# Test files must have unique names: the folders are not packages, and on Windows CI two files with the
# same name stop the whole collection ("import file mismatch", tests/pose and tests/ui, 2026-11-14).
def _duplicate_test_names() -> list[str]:
    from collections import Counter
    from pathlib import Path
    names = Counter(p.name for p in Path(__file__).parent.rglob("test_*.py"))
    return sorted(n for n, k in names.items() if k > 1)


if _duplicate_test_names():
    raise RuntimeError(f"test files with the same name in different folders: {_duplicate_test_names()}")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _delete_windows_after_each_test():
    """Every window a test opened is deleted afterwards, not only closed. A closed window stays alive, and
    app.setStyleSheet repolishes every live widget: on Windows CI the hundreds of windows the earlier tests left
    made the style sheet in test_pin_locks_at_start_and_after_idle take hours (the build stood in Tester for
    1.5 h, 2026-11-15)."""
    yield
    import sys
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
