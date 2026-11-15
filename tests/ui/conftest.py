"""UI tests run offscreen (source scripts/headless_env.sh). Modal dialogs are stubbed so nothing blocks."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPANALYS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOPANALYS_KEY_DIR", str(tmp_path / "keys"))     # share.secret: never the real home
    from lopanalys.core import i18n, paths
    paths.set_demo(False)
    i18n.set_language("sv")
    yield


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def demo_window(qapp):
    from lopanalys.ui.app import build
    _, win = build(["--demo", "--fast", "40"], qapp)
    win.resize(1440, 900)
    win.show()
    yield win
    win.close()
