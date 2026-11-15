"""A film of the synthetic skier through the whole chain: poses, analysis and the key frames on disk."""
from __future__ import annotations

from lopanalys.pose import technique_synth as ts
from lopanalys.pose.ski import analysis as sa
from lopanalys.pose.ski import synthetic as ss


def test_film_gives_key_frames_with_the_findings_marked(tmp_path):
    p = ss.SkierParams(duration_s=9.0, fps=30.0, width=180, height=320, trunk_lean_end_deg=22.0,
                       elbow_angle_start_deg=150.0)
    video = ss.write_video(str(tmp_path / "ski.mp4"), p)
    poses, _ = ss.generate(p)
    steps = []
    a = sa.analyze_video(video, p.height_cm, p.view, progress_cb=lambda f, m="": steps.append(f),
                         keyframes_dir=tmp_path / "kf", estimator=ts.replay(poses))
    assert a.video_path == str(video) and a.frames == len(poses)
    kinds = [k.kind for k in a.keyframes]
    assert kinds == ["start", "mid_drive", "finish", "recovery", "sequence"]
    for k in a.keyframes:
        assert (tmp_path / "kf").joinpath(k.image_path.split("/")[-1]).stat().st_size > 5000
        assert k.label and k.caption
    by = {k.kind: k for k in a.keyframes}
    assert "elbow_start" in by["start"].findings            # the straight arms are marked in the start frame
    assert "trunk" in by["finish"].findings
    trunk = next(f for f in a.findings if f.code == "trunk")
    assert trunk.frame_index == by["finish"].frame_index
    assert steps and steps[-1] == 1.0
    assert any("bilder/s" in w for w in a.warnings)         # 30 frames/s: said to be uncertain


def test_film_that_cannot_be_judged_gets_no_key_frames(tmp_path):
    p = ss.SkierParams(duration_s=3.0, fps=30.0, width=180, height=320)
    video = ss.write_video(str(tmp_path / "short.mp4"), p)
    a = sa.analyze_video(video, p.height_cm, p.view, keyframes_dir=tmp_path / "kf", estimator=ts.replay(ss.generate(p)[0]))
    assert a.quality["level"] == "poor" and a.keyframes == [] and a.findings == []
