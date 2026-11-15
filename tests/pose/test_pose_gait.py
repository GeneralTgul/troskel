"""The analysis must recover the synthetic runner's parameters at 30, 60 and 120 fps."""
from __future__ import annotations

import numpy as np
import pytest

from lopanalys.pose.filters import prepare_pair
from lopanalys.pose.gait import detect_events
from lopanalys.pose.metrics import estimate_scale, step_metrics, summarize
from lopanalys.pose.series import PoseSeries, angle_from_vertical, interior_angle
from lopanalys.pose.synthetic import RunnerParams, clean_poses, generate
from lopanalys.pose.types import LM, Side, View

# per frame rate: (contact time tolerance ms, event timing tolerance ms)
TOL = {30: (25.0, 30.0), 60: (15.0, 20.0), 120: (12.0, 15.0)}


def run(p: RunnerParams):
    poses, truth = generate(p)
    ev_s, kin = prepare_pair(PoseSeries.from_poses(poses, times=[i / p.fps for i in range(len(poses))]))
    ev = detect_events(ev_s, p.view)
    scale = estimate_scale(kin, p.height_cm, p.view)
    steps = step_metrics(kin, ev, scale)
    return summarize(steps, ev, kin), steps, ev, truth, scale


def match(ev, truth):
    pairs = []
    for c in ev.contacts:
        m = [x for x in truth.contacts if x[0] == c.side and abs(x[1] - c.t_ic) < 0.08]
        if m:
            pairs.append((c, m[0]))
    return pairs


@pytest.mark.parametrize("fps", [30, 60, 120])
@pytest.mark.parametrize("view", [View.SIDE_LEFT, View.SIDE_RIGHT])
def test_side_view_recovers_parameters(fps, view):
    p = RunnerParams(fps=fps, view=view, cadence_spm=176, ground_contact_ms=230, overstride_cm=24,
                     trunk_lean_deg=8, vertical_oscillation_cm=7.5, noise_px=1.5, seed=fps)
    s, steps, ev, truth, scale = run(p)
    pairs = match(ev, truth)
    assert len(pairs) >= 0.9 * len(truth.contacts) - 1
    assert len(ev.contacts) <= len(truth.contacts) + 1
    gct_tol, ev_tol = TOL[fps]
    ic_err = [abs(c.t_ic - tr[1]) * 1000 for c, tr in pairs]
    assert np.median(ic_err) < ev_tol
    assert s.cadence_spm == pytest.approx(176, abs=1.5)
    assert s.ground_contact_ms == pytest.approx(230, abs=gct_tol)
    assert scale.px_per_cm == pytest.approx(truth.px_per_cm, rel=0.03)
    assert s.overstride_cm == pytest.approx(24, abs=3.0)
    assert s.trunk_lean_deg == pytest.approx(8, abs=1.5)
    assert s.vertical_oscillation_cm == pytest.approx(7.5, abs=1.2)
    assert s.foot_strike == "heel"
    assert ev.facing == (-1 if view == View.SIDE_LEFT else 1)


@pytest.mark.parametrize("fps", [30, 120])
def test_side_view_angles_match_clean_truth(fps):
    """Shin, knee and hip angles: compare with the same angles on noise-free poses at true events."""
    p = RunnerParams(fps=fps, view=View.SIDE_LEFT, noise_px=1.5, seed=3)
    s, steps, ev, truth, _ = run(p)
    clean = PoseSeries.from_poses(clean_poses(p))
    facing = -1

    def at(name, tt):
        return np.array([np.interp(tt, clean.t, clean.lm(name)[:, k]) for k in (0, 1)])

    shin, knee, hip = [], [], []
    for side, tic, tto in truth.contacts[1:-1]:
        pre = "l_" if side == Side.LEFT else "r_"
        shin.append(float(angle_from_vertical(at(pre + "knee", tic), at(pre + "ankle", tic), facing)))
        knee.append(180 - float(interior_angle(at(pre + "hip", tic), at(pre + "knee", tic), at(pre + "ankle", tic))))
        hip.append(-float(angle_from_vertical(at(pre + "hip", tto), at(pre + "knee", tto), facing)))
    tol = 4.0 if fps == 30 else 2.5
    assert s.shin_angle_deg == pytest.approx(np.median(shin), abs=tol)
    assert s.knee_flex_contact_deg == pytest.approx(np.median(knee), abs=tol + 2)
    assert s.hip_extension_toe_off_deg == pytest.approx(np.median(hip), abs=tol + 1)


@pytest.mark.parametrize("strike,expected", [(14.0, "heel"), (3.0, "midfoot"), (-8.0, "forefoot")])
def test_foot_strike_classification(strike, expected):
    s, *_ = run(RunnerParams(fps=60, foot_strike_deg=strike, noise_px=1.0))
    assert s.foot_strike == expected


@pytest.mark.parametrize("fps", [30, 60, 120])
@pytest.mark.parametrize("view", [View.REAR, View.FRONT])
def test_frontal_view_recovers_parameters(fps, view):
    p = RunnerParams(fps=fps, view=view, pelvic_drop_deg=6.5, knee_valgus_deg=9, step_width_cm=7,
                     arm_cross_cm=3, cadence_spm=168, noise_px=1.5, seed=fps + 1)
    s, steps, ev, truth, scale = run(p)
    assert s.cadence_spm == pytest.approx(168, abs=1.5)
    assert s.pelvic_drop_deg == pytest.approx(6.5, abs=1.5)
    assert s.knee_valgus_deg == pytest.approx(9, abs=3.0)
    assert s.step_width_cm == pytest.approx(7, abs=2.0)
    assert s.arm_cross_cm == pytest.approx(3, abs=2.0)
    assert scale.px_per_cm == pytest.approx(truth.px_per_cm, rel=0.06)
    assert s.ground_contact_ms == pytest.approx(225, abs=TOL[fps][0] + 5)


def test_pelvic_drop_side_and_camera_roll():
    """Pelvic drop is recovered on both legs even when the camera is tilted 3 degrees."""
    p = RunnerParams(fps=60, view=View.REAR, pelvic_drop_deg=7, noise_px=1.0)
    poses, _ = generate(p)
    # tilt the whole image by 3 degrees around its centre
    a = np.radians(3)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    c = np.array([p.width / 2, p.height / 2])
    for q in poses:
        q.xy = (q.xy - c) @ rot.T + c
    ev_s, kin = prepare_pair(PoseSeries.from_poses(poses))
    ev = detect_events(ev_s, View.REAR)
    steps = step_metrics(kin, ev, estimate_scale(kin, 178, View.REAR))
    left = np.median([x.pelvic_drop_deg for x in steps if x.side == Side.LEFT and x.pelvic_drop_deg is not None])
    right = np.median([x.pelvic_drop_deg for x in steps if x.side == Side.RIGHT and x.pelvic_drop_deg is not None])
    assert left == pytest.approx(7, abs=2.0)
    assert right == pytest.approx(7, abs=2.0)


def test_asymmetry_in_contact_time():
    s, *_ = run(RunnerParams(fps=120, gct_asymmetry_pct=10, noise_px=1.0))
    assert s.asymmetry["ground_contact_ms"] == pytest.approx(10, abs=4)


def test_robust_to_swaps_outliers_and_gaps():
    p = RunnerParams(fps=60, noise_px=2.0, swap_rate=0.03, outlier_rate=0.05, missing_rate=0.04, seed=7)
    s, steps, ev, truth, _ = run(p)
    assert s.cadence_spm == pytest.approx(170, abs=2.5)
    assert s.ground_contact_ms == pytest.approx(225, abs=25)
    assert s.overstride_cm == pytest.approx(20, abs=4)
    assert len(match(ev, truth)) >= 0.8 * len(truth.contacts)


def test_standing_still_gives_no_steps():
    p = RunnerParams(fps=30, noise_px=1.0)
    poses, _ = generate(p)
    first = poses[0]
    for q in poses:
        q.xy = first.xy + np.random.default_rng(q.frame_index).normal(0, 1.0, first.xy.shape)
    ev_s, kin = prepare_pair(PoseSeries.from_poses(poses))
    assert detect_events(ev_s, View.SIDE_LEFT).contacts == []


def test_scale_is_none_without_height():
    p = RunnerParams(fps=30, duration_s=3)
    poses, _ = generate(p)
    kin = prepare_pair(PoseSeries.from_poses(poses))[1]
    assert estimate_scale(kin, None, View.SIDE_LEFT) is None
    assert LM["l_heel"] == 29


def test_side_view_measures_the_arms():
    """The arms are drawn in the skeleton and must be analysed from the side too."""
    sm, *_ = run(RunnerParams(fps=60, view=View.SIDE_LEFT, noise_px=1.0, seed=3))
    assert sm.elbow_angle_deg is not None and 40 < sm.elbow_angle_deg < 180
    assert sm.arm_swing_deg is not None and sm.arm_swing_deg > 5
