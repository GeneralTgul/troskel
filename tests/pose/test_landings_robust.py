"""Landings from a 25 fps camera with uneven frame times, mixed-up legs and a heel landmark above the sole.

These are the faults behind the first real clip (real hardware 2026-11-14: contact time 110 ms instead of
about 300, overstride with the wrong sign, 37 % difference between the feet). The synthetic runner
reproduces each of them; no data from that clip is used here.
"""
from __future__ import annotations

import numpy as np
import pytest

from lopanalys.core.i18n import t
from lopanalys.pose.filters import fix_leg_swaps, mask_low_visibility
from lopanalys.pose.findings import num
from lopanalys.pose.gait import GaitEvents, event_quality
from lopanalys.pose.offline import analyze_core, landing_warnings
from lopanalys.pose.series import PoseSeries, angle_from_vertical, interior_angle
from lopanalys.pose.synthetic import RunnerParams, clean_poses, generate
from lopanalys.pose.types import LM, Pose, Side, View

# A GoPro over USB: 25 fps achieved, 17-75 ms between frames, the legs mixed up for a few frames where
# they cross, the far foot on a higher belt line in the image, the heel landmark 4 cm above the sole.
GOPRO_USB = dict(fps=25, time_jitter=0.8, far_lift_px=18, swap_rate=0.5, swap_frames=(2, 6),
                 swap_at_crossing=True, heel_marker_cm=4.0, noise_px=1.5, cadence_spm=168,
                 ground_contact_ms=290, overstride_cm=12, duration_s=30, view=View.SIDE_LEFT)


def _pair(ev: GaitEvents, truth) -> list[tuple]:
    out = []
    for c in ev.contacts:
        m = [x for x in truth.contacts if x[0] == c.side and abs(x[1] - c.t_ic) < 0.15]
        if m:
            out.append((c, m[0]))
    return out


@pytest.mark.parametrize("seed", [1, 2])
def test_leg_swaps_are_undone_with_uneven_frame_times(seed):
    """The old frame-by-frame fix locked in on a wrong swap when the frame times were uneven."""
    kw = dict(GOPRO_USB, duration_s=20, seed=seed)
    poses, truth = generate(RunnerParams(**kw))
    clean, _ = generate(RunnerParams(**{**kw, "swap_rate": 0.0}))
    s = PoseSeries.from_poses(poses, times=truth.times)
    ref = PoseSeries.from_poses(clean, times=truth.times)
    L = LM["l_ankle"]
    before = np.mean(np.abs(s.xy[:, L, 0] - ref.xy[:, L, 0]) > 10)
    fixed = fix_leg_swaps(mask_low_visibility(s))
    after = np.mean(np.abs(fixed.xy[:, L, 0] - ref.xy[:, L, 0]) > 10)
    assert before > 0.1
    assert after < 0.02
    assert fixed.meta["leg_swap_share"] == pytest.approx(before, abs=0.03)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_landings_hit_the_frame_at_25_fps(seed):
    p = RunnerParams(**GOPRO_USB, seed=seed)
    poses, truth = generate(p)
    core = analyze_core(poses, p.height_cm, p.view, times=truth.times)
    a, ev = core.analysis, core.events
    pairs = _pair(ev, truth)
    # one landing per stride and foot: none missing, none extra
    for side in (Side.LEFT, Side.RIGHT):
        n_true = sum(1 for x in truth.contacts if x[0] == side)
        assert n_true - 2 <= len(ev.side(side)) <= n_true
    assert len(pairs) == len(ev.contacts)
    # within one frame (40 ms), most within half a frame
    ic_err = np.array([abs(c.t_ic - tr[1]) * 1000 for c, tr in pairs])
    assert np.median(ic_err) < 15 and np.percentile(ic_err, 90) < 40
    s = a.summary
    assert s.cadence_spm == pytest.approx(168, abs=1.5)
    assert s.ground_contact_ms == pytest.approx(290, abs=25)
    assert abs(s.asymmetry.get("ground_contact_ms", 0.0)) < 8
    # values at landing with the right sign: the heel lands in front of the hip
    assert s.overstride_cm == pytest.approx(12, abs=4)
    clean = PoseSeries.from_poses(clean_poses(p), times=truth.times)

    def at(name, tt):
        return np.array([np.interp(tt, clean.t, clean.lm(name)[:, k]) for k in (0, 1)])

    shin, knee = [], []
    for side, tic, _ in truth.contacts[2:-2]:
        pre = "l_" if side == Side.LEFT else "r_"
        shin.append(float(angle_from_vertical(at(pre + "knee", tic), at(pre + "ankle", tic), -1)))
        knee.append(180 - float(interior_angle(at(pre + "hip", tic), at(pre + "knee", tic), at(pre + "ankle", tic))))
    assert s.shin_angle_deg == pytest.approx(np.median(shin), abs=4)
    assert s.knee_flex_contact_deg == pytest.approx(np.median(knee), abs=6)
    assert landing_warnings(ev, core.events_series) == []


def test_far_foot_on_a_higher_belt_line():
    """From the side the far foot stands higher in the image; both feet must still be found alike."""
    p = RunnerParams(fps=60, far_lift_px=30, noise_px=1.0, duration_s=10, seed=4)
    poses, truth = generate(p)
    a = analyze_core(poses, p.height_cm, p.view, times=truth.times).analysis
    assert a.summary.ground_contact_ms == pytest.approx(225, abs=15)
    assert abs(a.summary.asymmetry.get("ground_contact_ms", 0.0)) < 5


def test_standing_on_the_belt_before_the_run_is_left_out():
    """The first real clip began with 40 s of standing still; those frames gave false landings."""
    p = RunnerParams(fps=25, noise_px=1.5, duration_s=20, t0=0.1, seed=5, cadence_spm=170)
    run, truth = generate(p)
    first = run[0]
    rng = np.random.default_rng(0)
    stand_s, n_stand = 6.0, int(6.0 * p.fps)
    standing = [Pose(i / p.fps, i, first.xy + rng.normal(0, 1.5, first.xy.shape), first.visibility, first.width,
                     first.height) for i in range(n_stand)]
    for q in run:
        q.t += stand_s
        q.frame_index += n_stand
    times = [i / p.fps for i in range(n_stand)] + [x + stand_s for x in truth.times]
    core = analyze_core(standing + run, p.height_cm, p.view, times=times)
    assert all(c.t_ic > stand_s - 0.05 for c in core.events.contacts)
    assert core.analysis.summary.cadence_spm == pytest.approx(170, abs=2)


def test_the_coach_is_warned_when_the_landings_are_uncertain():
    p = RunnerParams(fps=60, noise_px=1.0, duration_s=10, seed=6)
    poses, truth = generate(p)
    core = analyze_core(poses, p.height_cm, p.view, times=truth.times)
    ev, s = core.events, core.events_series
    assert landing_warnings(ev, s) == []
    # contact times like the first real clip's: implausibly short
    short = GaitEvents([type(c)(c.side, c.t_ic, c.t_ic + 0.105, c.i_ic, c.i_ic + 6) for c in ev.contacts],
                       ev.facing, ev.view, ev.ground_y, ev.threshold_px, ev.stride_s)
    w = landing_warnings(short, s)
    assert t("pose.warn.contact_implausible", ms="105", lo="120", hi="450") in w
    # half of one foot's landings missing (the foot hidden behind the treadmill)
    left = [c for c in ev.contacts if c.side == Side.LEFT]
    gaps = GaitEvents([c for c in ev.contacts if c.side == Side.RIGHT or left.index(c) % 3 == 0],
                      ev.facing, ev.view, ev.ground_y, ev.threshold_px, ev.stride_s)
    cov = event_quality(gaps)["coverage"]
    assert cov < 0.8
    assert t("pose.warn.landings_missing", pct=num(100 * cov)) in landing_warnings(gaps, s)
    # the legs had to be corrected in a large share of the frames
    s2 = s.copy()
    s2.meta["leg_swap_share"] = 0.4
    assert t("pose.warn.leg_swaps", pct="40") in landing_warnings(ev, s2)
