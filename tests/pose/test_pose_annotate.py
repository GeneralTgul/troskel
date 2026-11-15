"""Key frames, the phase strip and the live overlay draw without errors and look plausible."""
from __future__ import annotations

import numpy as np

from lopanalys.pose import annotate
from lopanalys.pose.offline import analyze_core, plan_keyframes, render_keyframes, _row_frame
from lopanalys.pose.synthetic import RunnerModel, RunnerParams, background, generate, render
from lopanalys.pose.types import GaitSummary, View


def frames_for(core, plans, p):
    model, bg = RunnerModel(p), background(p)
    need = set()
    for pl in plans:
        need |= {_row_frame(core.kin, r) for _, r in pl.rows} if pl.rows else {pl.frame_index}
    return {i: render(model.joints(i / p.fps), p, bg) for i in need}


def test_side_keyframes(tmp_path):
    p = RunnerParams(fps=60, overstride_cm=33, cadence_spm=158, width=960, height=540, duration_s=5)
    poses, _ = generate(p)
    core = analyze_core(poses, p.height_cm, p.view, speed_kmh=12)
    plans = plan_keyframes(core)
    kinds = [x.kind for x in plans]
    assert kinds == ["contact", "midstance", "toe_off", "flight", "sequence"]
    kfs = render_keyframes(core, plans, frames_for(core, plans, p), tmp_path)
    assert 4 <= len(kfs) <= 8
    import cv2
    for k in kfs:
        img = cv2.imread(k.image_path)
        assert img is not None and img.shape[0] >= 800
        assert k.label and k.caption
    contact = next(k for k in kfs if k.kind == "contact")
    assert "overstride" in contact.findings
    # the drawing uses the fix colour (red) for the overstride ring
    img = cv2.imread(contact.image_path)
    red = (img[..., 2] > 200) & (img[..., 1] < 100) & (img[..., 0] < 110)
    assert red.sum() > 500


def test_rear_keyframes(tmp_path):
    p = RunnerParams(fps=60, view=View.REAR, pelvic_drop_deg=8, knee_valgus_deg=14, width=960, height=540,
                     duration_s=5)
    poses, _ = generate(p)
    core = analyze_core(poses, p.height_cm, p.view)
    plans = plan_keyframes(core)
    kfs = render_keyframes(core, plans, frames_for(core, plans, p), tmp_path)
    assert {k.kind for k in kfs} >= {"midstance", "sequence"}
    assert any("pelvic_drop" in k.findings for k in kfs)


def test_live_overlay_is_fast_and_draws():
    import time
    p = RunnerParams(width=1920, height=1080, duration_s=1)
    poses, _ = generate(p)
    img = render(RunnerModel(p).joints(0.2), p)
    s = GaitSummary(0, 10, 20, 172, 231, 8, 20, 8, 18, 42, 6, 10, "heel")
    out = annotate.draw_live_overlay(img, poses[12], View.SIDE_LEFT, s)
    assert out.shape == img.shape and not np.array_equal(out, img)
    assert annotate.draw_live_overlay(img, None, View.SIDE_LEFT) is not img
    t0 = time.perf_counter()
    for _ in range(10):
        annotate.draw_live_overlay(img, poses[12], View.REAR, s)
    assert (time.perf_counter() - t0) / 10 < 0.05


def test_live_angles():
    p = RunnerParams(duration_s=1, noise_px=0)
    poses, _ = generate(p)
    a = annotate.live_angles(poses[0], View.SIDE_LEFT)
    assert set(a) == {"knee", "trunk", "shin"}
    assert abs(a["trunk"] - p.trunk_lean_deg) < 1.0


def test_font_found():
    assert annotate.font(20, True) is not None


def test_save_png_non_ascii(tmp_path):
    path = annotate.save_png(np.zeros((10, 10, 3), np.uint8), tmp_path / "löpare åäö" / "bild.png")
    assert (tmp_path / "löpare åäö" / "bild.png").stat().st_size > 0
    assert path.endswith("bild.png")
