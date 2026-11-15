"""Fixtures for the pose tests: app data in a temp dir, Swedish texts by default."""
from __future__ import annotations

import pytest

from lopanalys.core import i18n


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPANALYS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOPANALYS_POSE_MODEL", raising=False)
    i18n.set_language("sv")
    yield
    i18n.set_language("sv")
