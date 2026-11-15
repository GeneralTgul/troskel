"""A film of the synthetic cyclist through the whole chain: poses, analysis and the key frames on disk."""
from __future__ import annotations

import pytest

from lopanalys.pose import technique as tq
from lopanalys.pose import technique_synth as ts
from lopanalys.pose.bike import analysis as ba
from lopanalys.pose.bike import synthetic as bs
from lopanalys.pose.types import View


def test_side_film_gives_key_frames_with_the_knee_marked(tmp_path):
    p = bs.with_knee(bs.CyclistParams(duration_s=8.0, fps=30.0, width=320, height=180), 52.0)
    video = bs.write_video(str(tmp_path / "bike.mp4"), p)
    poses, _ = bs.generate(p)
    a = ba.analyze_video(video, p.height_cm, p.view, keyframes_dir=tmp_path / "kf", estimator=ts.replay(poses))
    assert [k.kind for k in a.keyframes] == ["bottom", "top", "forward", "posture", "sequence"]
    for k in a.keyframes:
        assert (tmp_path / "kf").joinpath(k.image_path.split("/")[-1]).stat().st_size > 5000
    by = {k.kind: k for k in a.keyframes}
    assert "saddle" in by["bottom"].findings
    saddle = next(f for f in a.findings if f.code == "saddle")
    assert saddle.frame_index == by["bottom"].frame_index
    assert a.quality["level"] == "uncertain"             # 30 frames/s


def test_front_film_gives_the_knee_track_frame(tmp_path):
    p = bs.CyclistParams(view=View.FRONT, duration_s=9.0, fps=30.0, width=180, height=320, knee_lateral_cm=6.0)
    video = bs.write_video(str(tmp_path / "front.mp4"), p)
    a = ba.analyze_video(video, p.height_cm, p.view, keyframes_dir=tmp_path / "kf",
                         estimator=ts.replay(bs.generate(p)[0]))
    assert [k.kind for k in a.keyframes] == ["front_track"]
    assert a.keyframes[0].findings == ["knee_track"]


def test_missing_film_raises_a_localised_error(tmp_path):
    with pytest.raises(tq.TechniqueError):
        ba.analyze_video(tmp_path / "none.mp4", 178.0, View.SIDE_LEFT)
