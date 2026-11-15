"""LOPANALYS_NO_DOWNLOAD=1 stops the model download before any network call."""
import urllib.request

import pytest

from lopanalys.pose import models


def test_download_refused_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("LOPANALYS_NO_DOWNLOAD", "1")

    def no_net(*a, **k):
        raise AssertionError("network was used")

    monkeypatch.setattr(urllib.request, "urlopen", no_net)
    with pytest.raises(models.ModelError):
        models.download("full", tmp_path / "pose_landmarker_full.task")
