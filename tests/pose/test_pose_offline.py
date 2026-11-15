"""analyze_video end to end on a synthetic clip, with a replay estimator instead of MediaPipe."""
from __future__ import annotations

import json
import threading

import cv2
import pytest

from lopanalys.pose.estimator import ReplayEstimator
from lopanalys.pose.offline import AnalysisError, analyze_poses, analyze_video
from lopanalys.pose.synthetic import RunnerParams, frames_and_poses, generate
from lopanalys.pose.types import GaitAnalysis, View


def write_clip(path, p):
    frames, poses, truth = frames_and_poses(p)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), p.fps, (p.width, p.height))
    assert vw.isOpened()
    for f in frames:
        vw.write(f)
    vw.release()
    return {i: q for i, q in enumerate(poses)}, truth


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    p = RunnerParams(fps=60, width=640, height=360, duration_s=4.0, overstride_cm=30, noise_px=0.8)
    path = tmp_path_factory.mktemp("v") / "löpning.mp4"
    poses, truth = write_clip(path, p)
    return path, poses, p


def test_analyze_video(clip, tmp_path):
    path, poses, p = clip
    seen = []
    a = analyze_video(path, p.height_cm, View.SIDE_LEFT, progress_cb=lambda f, msg: seen.append(f),
                      keyframes_dir=tmp_path / "kf", estimator=ReplayEstimator(poses), speed_kmh=12)
    assert a.summary is not None
    assert a.summary.cadence_spm == pytest.approx(170, abs=2)
    assert a.fps == pytest.approx(60, abs=0.5)
    assert 4 <= len(a.keyframes) <= 8
    for k in a.keyframes:
        assert (tmp_path / "kf").joinpath(k.image_path.split("/")[-1].split("\\")[-1]).exists()
    assert seen and seen[-1] == 1.0
    assert any(f.code == "overstride" for f in a.findings)
    d = json.loads(json.dumps(a.to_dict()))
    b = GaitAnalysis.from_dict(d)
    assert b.summary == a.summary and b.findings == a.findings and b.keyframes == a.keyframes
    assert b.steps == a.steps


def test_single_argument_progress(clip, tmp_path):
    path, poses, p = clip
    seen = []
    analyze_video(path, p.height_cm, View.SIDE_LEFT, progress_cb=seen.append, keyframes_dir=tmp_path,
                  estimator=ReplayEstimator(poses))
    assert seen and all(isinstance(x, float) for x in seen)


def test_cancel(clip, tmp_path):
    path, poses, p = clip
    ev = threading.Event()
    ev.set()
    with pytest.raises(AnalysisError):
        analyze_video(path, p.height_cm, View.SIDE_LEFT, keyframes_dir=tmp_path,
                      estimator=ReplayEstimator(poses), cancel=ev)


def test_missing_file_and_no_runner(tmp_path, clip):
    with pytest.raises(AnalysisError) as e:
        analyze_video(tmp_path / "finns-inte.mp4", 180, View.SIDE_LEFT, estimator=ReplayEstimator({}))
    assert "finns-inte.mp4" in str(e.value)
    path, _, p = clip
    with pytest.raises(AnalysisError):
        analyze_video(path, 180, View.SIDE_LEFT, keyframes_dir=tmp_path, estimator=ReplayEstimator({}))


def test_analyze_poses_warnings():
    p = RunnerParams(fps=30, duration_s=4, missing_rate=0.3)
    poses, _ = generate(p)
    a = analyze_poses(poses, None, View.SIDE_LEFT, times=[i / 30 for i in range(len(poses))])
    text = " ".join(a.warnings)
    assert "bilder/s" in text and "Längden saknas" in text and "%" in text
    assert a.summary is not None and a.summary.overstride_cm is None


# --- real hardware 2026-11-14: cadence 233 from a GoPro that delivered 24.8 fps against a nominal 30 ---------

def _uneven_clip(tmp_path, with_sidecar: bool):
    """A runner at 170 spm seen at ~25 fps with uneven stamps (17/35/52 ms), stored as a 30 fps MP4.

    That is what the live recording looks like: the writer uses the camera's nominal rate, the
    sidecar keeps the real time of each frame.
    """
    import numpy as np

    from lopanalys.pose.synthetic import Projector, RunnerModel
    from lopanalys.pose.types import Pose

    p = RunnerParams(fps=30, width=640, height=360, cadence_spm=170, noise_px=0.8)
    rng = np.random.default_rng(3)
    dts = rng.choice([1 / 60, 2 / 60, 3 / 60], size=400, p=[0.05, 0.5, 0.45])   # mean 25 fps
    times = np.r_[0.0, np.cumsum(dts)]
    times = times[times < 10.0]
    model, proj = RunnerModel(p), Projector(p)
    vis = np.full(33, 0.9)
    poses = {i: Pose(float(tt), i, proj(model.joints(float(tt))) + rng.normal(0, 0.8, (33, 2)), vis.copy(),
                     p.width, p.height) for i, tt in enumerate(times)}
    path = tmp_path / "live.mp4"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (160, 90))
    assert vw.isOpened()
    black = np.zeros((90, 160, 3), np.uint8)
    for _ in times:
        vw.write(black)
    vw.release()
    if with_sidecar:
        path.with_name(path.name + ".json").write_text(json.dumps(
            {"version": 1, "nominal_fps": 30.0, "measured_fps": (len(times) - 1) / times[-1],
             "frames": len(times), "t": [round(float(x), 6) for x in times]}), encoding="utf-8")
    return path, poses, p, len(times) / times[-1]


def test_cadence_uses_the_recorded_frame_times(tmp_path):
    path, poses, p, real_fps = _uneven_clip(tmp_path, with_sidecar=True)
    assert 23 < real_fps < 27
    a = analyze_video(path, p.height_cm, View.SIDE_LEFT, keyframes_dir=tmp_path / "kf",
                      estimator=ReplayEstimator(poses), speed_kmh=12)
    assert a.summary is not None
    assert a.summary.cadence_spm == pytest.approx(170, abs=4)
    assert a.fps == pytest.approx(real_fps, abs=0.5)      # the achieved rate, not the median interval
    assert a.duration_s == pytest.approx(9.9, abs=0.2)


def test_without_sidecar_the_container_rate_is_all_there_is(tmp_path):
    """The failure itself: container times (nominal 30 fps) make the clip short and cadence high."""
    path, poses, p, real_fps = _uneven_clip(tmp_path, with_sidecar=False)
    a = analyze_video(path, p.height_cm, View.SIDE_LEFT, keyframes_dir=tmp_path / "kf",
                      estimator=ReplayEstimator(poses), speed_kmh=12)
    assert a.summary is not None
    assert a.summary.cadence_spm == pytest.approx(170 * 30 / real_fps, rel=0.05)


def test_bad_sidecar_falls_back_to_the_container(tmp_path):
    from lopanalys.pose.offline import recorded_times

    path, _, _, _ = _uneven_clip(tmp_path, with_sidecar=True)
    side = path.with_name(path.name + ".json")
    assert recorded_times(path, 0)[0] == 0.0
    assert recorded_times(path, 10) is None                      # frame count does not match
    side.write_text('{"t": [0, 0.1, 0.05]}', encoding="utf-8")
    assert recorded_times(path) is None                          # not increasing
    side.write_text("trasig", encoding="utf-8")
    assert recorded_times(path) is None
