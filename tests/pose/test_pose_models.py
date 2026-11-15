"""Model lookup and download without network; estimator helpers; filters."""
from __future__ import annotations

import io
import urllib.error
import zipfile

import numpy as np
import pytest

from lopanalys.core import paths
from lopanalys.pose import models
from lopanalys.pose.estimator import ReplayEstimator, RoiTracker
from lopanalys.pose.filters import OneEuroFilter, fill_gaps, fix_leg_swaps, remove_spikes
from lopanalys.pose.series import PoseSeries
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import LM, Pose


def fake_task(size: int = 3_000_000) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("pose_detector.tflite", b"x" * size)
    return buf.getvalue()


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_env_override(tmp_path, monkeypatch):
    f = tmp_path / "m.task"
    f.write_bytes(b"x")
    monkeypatch.setenv("LOPANALYS_POSE_MODEL", str(f))
    assert models.ensure_model("full") == f


def test_download_and_sidecar(monkeypatch):
    monkeypatch.delenv("LOPANALYS_NO_DOWNLOAD", raising=False)   # the network is faked here
    monkeypatch.setattr(models, "bundled_dirs", lambda: [])
    data = fake_task()
    calls = []
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda req, timeout: FakeResp(data))
    p = models.ensure_model("lite", progress_cb=lambda d, tot: calls.append((d, tot)))
    assert p.parent == paths.models_dir() and p.read_bytes() == data
    assert calls[-1] == (len(data), len(data))
    assert models.verify(p, "lite") and models.is_downloaded("lite")
    # corruption is detected and the file is fetched again
    p.write_bytes(b"broken" + data[6:])
    assert not models.verify(p, "lite")
    assert models.find_model("lite") is None
    fracs = []
    assert models.ensure_model("lite", progress_cb=fracs.append) == p and p.read_bytes() == data
    assert fracs[-1] == 1.0


def test_download_failure_message(monkeypatch):
    monkeypatch.delenv("LOPANALYS_NO_DOWNLOAD", raising=False)   # the network is faked here
    monkeypatch.setattr(models, "bundled_dirs", lambda: [])

    def fail(req, timeout):
        raise urllib.error.URLError("nätet är nere")
    monkeypatch.setattr(models.urllib.request, "urlopen", fail)
    with pytest.raises(models.ModelError) as e:
        models.ensure_model("full")
    assert "pose_landmarker_full.task" in str(e.value) and "nätet är nere" in str(e.value)
    assert not list(paths.models_dir().glob("*.part"))


def test_bundled_model_with_powershell_sidecar(tmp_path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    f = d / "pose_landmarker_full.task"
    f.write_bytes(fake_task(4_100_000))
    digest = models.sha256_of(f).upper()
    (d / (f.name + ".sha256")).write_bytes(("﻿" + digest + "\r\n").encode("utf-16-le"))
    monkeypatch.setattr(models, "bundled_dirs", lambda: [d])
    assert models.find_model("full") == f
    (d / (f.name + ".sha256")).write_text("0" * 64)
    assert models.find_model("full") is None


def test_small_download_rejected(monkeypatch):
    monkeypatch.delenv("LOPANALYS_NO_DOWNLOAD", raising=False)   # the network is faked here
    monkeypatch.setattr(models, "bundled_dirs", lambda: [])
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda req, timeout: FakeResp(b"<html>"))
    with pytest.raises(models.ModelError):
        models.ensure_model("heavy")


def test_roi_tracker_holds_steady():
    r = RoiTracker()
    xy = np.zeros((33, 2))
    xy[:, 0] = np.linspace(900, 1000, 33)
    xy[:, 1] = np.linspace(400, 800, 33)
    pose = Pose(0, 0, xy, np.ones(33), 1920, 1080)
    r.update(pose)
    first = r.rect
    assert first is not None and first[0] < 900 and first[2] > 1000
    pose.xy = xy + 5
    r.update(pose)
    assert r.rect == first               # small movement: same crop
    pose.xy = xy + np.array([300, 0])
    r.update(pose)
    assert r.rect != first               # the runner moved to the edge: new crop
    for _ in range(20):
        r.update(None)
    assert r.rect is None


def test_replay_estimator_rescales_to_image():
    p = RunnerParams(duration_s=1)
    poses, _ = generate(p)
    est = ReplayEstimator({0: poses[0]})
    out = est.process(np.zeros((720, 1280, 3), np.uint8), 0.0, 0)
    assert out is not None and out.width == 1280 and est.process(np.zeros((2, 2, 3), np.uint8), 0, 5) is None


def test_one_euro_reduces_jitter():
    rng = np.random.default_rng(0)
    f = OneEuroFilter(min_cutoff=1.0, beta=0.0)
    raw = 100 + rng.normal(0, 2, 300)
    out = np.array([f(i / 60, np.array([v]))[0] for i, v in enumerate(raw)])
    assert np.std(out[50:]) < 0.5 * np.std(raw[50:])


def test_gap_fill_spikes_and_swaps():
    p = RunnerParams(fps=60, duration_s=3, noise_px=0.5)
    poses, _ = generate(p)
    s = PoseSeries.from_poses(poses)
    ref = s.copy()
    s.xy[50:53, LM["l_knee"]] = np.nan        # short gap
    s.xy[100:140, LM["l_wrist"]] = np.nan     # long gap stays
    filled = fill_gaps(s, 0.1)
    assert not np.isnan(filled.xy[51, LM["l_knee"]]).any()
    assert np.isnan(filled.xy[120, LM["l_wrist"]]).all()
    s2 = ref.copy()
    s2.xy[70, LM["l_ankle"]] += 150
    clean = remove_spikes(s2)
    assert np.isnan(clean.xy[70, LM["l_ankle"]]).any()
    s3 = ref.copy()
    L = [LM["l_" + k] for k in ("knee", "ankle", "heel", "foot")]
    R = [LM["r_" + k] for k in ("knee", "ankle", "heel", "foot")]
    bad = [30, 31, 90]
    for i in bad:
        s3.xy[i, L], s3.xy[i, R] = ref.xy[i, R], ref.xy[i, L]
    fixed = fix_leg_swaps(s3)
    for i in bad:
        assert np.allclose(fixed.xy[i, L], ref.xy[i, L])
    assert fixed.meta["leg_swaps_fixed"] >= len(bad)


def test_mediapipe_estimator_errors_are_readable(tmp_path, monkeypatch):
    from lopanalys.pose.estimator import make_estimator
    bogus = tmp_path / "pose_landmarker_full.task"
    bogus.write_bytes(b"not a model")
    with pytest.raises(RuntimeError) as e:
        make_estimator(model_path=bogus)
    assert str(e.value).startswith("Pose-analysen kunde inte starta")
    monkeypatch.setattr(models, "bundled_dirs", lambda: [])

    def offline(req, timeout):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(models.urllib.request, "urlopen", offline)
    with pytest.raises(models.ModelError):
        make_estimator()


def test_live_without_model_reports_instead_of_crashing(monkeypatch):
    import time

    from lopanalys.pose.live import LiveAnalyzer
    from test_pose_live import FakeSource
    monkeypatch.setattr(models, "bundled_dirs", lambda: [])
    monkeypatch.setattr(models.urllib.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    la = LiveAnalyzer(FakeSource(5, 30), None, 178, "side_left")
    la.start()
    time.sleep(0.5)
    la.stop()
    assert la.error and "pose_landmarker_full.task" in la.error
