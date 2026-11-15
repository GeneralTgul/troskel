"""Fixtures for the camera tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from cam_helpers import write_video


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPANALYS_HOME", str(tmp_path / "home"))


@pytest.fixture
def video30(tmp_path) -> Path:
    return write_video(tmp_path / "clip30.mp4", 45, 30.0)


@pytest.fixture
def video120(tmp_path) -> Path:
    return write_video(tmp_path / "clip120.mp4", 120, 120.0)


@pytest.fixture(autouse=True)
def _fresh_gopro_caches():
    """The GoPro module remembers found cameras and a recent "GoPro Webcam is not running": not across tests."""
    from lopanalys.camera import gopro
    gopro._KNOWN.clear()
    gopro._app_checked_at = -1e9
    yield
    gopro._KNOWN.clear()
    gopro._app_checked_at = -1e9
