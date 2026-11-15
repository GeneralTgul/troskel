"""Bike fit: the analysis must recover the known truth of the synthetic cyclist, judge only what it can
measure, and say so when the measurement is uncertain."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from lopanalys.pose import technique as tq
from lopanalys.pose.bike import analysis as ba
from lopanalys.pose.bike import synthetic as bs
from lopanalys.pose.bike.cycle import detect_side, fit_circle
from lopanalys.pose.types import Severity, Side, View


def run(p: bs.CyclistParams, **kw):
    poses, times = bs.generate(p)
    return ba.analyze_core(poses, p.height_cm, p.view, times=times, **kw)


def advice(a, part):
    return next(x for x in a.advice if x["part"] == part)


def test_circle_fit_recovers_centre_and_radius():
    ang = np.linspace(0, 2 * np.pi, 50, endpoint=False)
    pts = np.c_[100 + 40 * np.cos(ang), 200 + 40 * np.sin(ang)]
    pts[7] += 25            # one outlier
    cx, cy, r, res = fit_circle(pts)
    assert abs(cx - 100) < 0.5 and abs(cy - 200) < 0.5 and abs(r - 40) < 0.5 and res < 0.05


@pytest.mark.parametrize("view,facing", [(View.SIDE_LEFT, -1), (View.SIDE_RIGHT, 1)])
@pytest.mark.parametrize("rpm", [70.0, 90.0, 105.0])
def test_cadence_and_facing_from_the_ankle_circle(view, facing, rpm):
    p = bs.CyclistParams(view=view, cadence_rpm=rpm)
    core = run(p)
    assert core.analysis.facing == facing           # from the direction of rotation
    assert core.analysis.summary["cadence_rpm"] == pytest.approx(rpm, abs=0.5)
    assert core.cycles.residual < 0.1
    assert core.analysis.quality["level"] == "good"


def test_revolutions_start_at_top_dead_centre():
    p = bs.CyclistParams(noise_px=0.0)
    poses, times = bs.generate(p)
    kin = tq.prepare(tq.series_from(poses, times))
    cyc = detect_side(kin, Side.LEFT)
    m = bs.CyclistModel(p)
    for rev in cyc.revolutions[2:8]:
        # true crank angle of the left pedal at the detected marks. The ankle sits behind the pedal axle,
        # so its circle is turned a little against the crank: within 25 degrees.
        for row, want in ((rev.i_top, 0.0), (rev.i_bottom, 180.0)):
            th = np.degrees(m.crank_angle(float(np.interp(row, np.arange(len(kin)), kin.t)), Side.LEFT)) % 360
            assert abs((th - want + 180) % 360 - 180) < 25


def test_angles_match_the_truth_at_60_fps():
    p = bs.CyclistParams()
    a = run(p).analysis
    truth = bs.truth(p)
    for key, tol in (("knee_flex_bottom_deg", 1.5), ("knee_flex_top_deg", 1.5), ("hip_angle_top_deg", 1.5),
                     ("trunk_angle_deg", 1.0), ("shoulder_angle_deg", 1.5), ("elbow_flex_deg", 1.5),
                     ("ankle_range_deg", 3.0), ("hip_bounce_cm", 0.5), ("knee_over_pedal_cm", 1.0)):
        assert a.summary[key] == pytest.approx(truth[key], abs=tol), key


def test_a_well_fitted_cyclist_gets_no_changes():
    a = run(bs.CyclistParams()).analysis
    assert all(f.severity == Severity.OK for f in a.findings), [(f.code, f.severity) for f in a.findings]
    assert advice(a, "saddle_height")["action"] == "keep"
    assert advice(a, "stem")["action"] == "keep"
    assert advice(a, "cleat")["action"] == "manual"      # fore-aft cannot be read from video


@pytest.mark.parametrize("knee,action,direction", [(52.0, "raise", "up"), (22.0, "lower", "down")])
def test_saddle_advice_in_mm_from_the_knee_angle(knee, action, direction):
    p = bs.with_knee(bs.CyclistParams(), knee)
    a = run(p).analysis
    s = advice(a, "saddle_height")
    assert s["action"] == action and s["direction"] == direction and s["certain"] and s["on_site"]
    f = next(f for f in a.findings if f.code == "saddle")
    assert f.severity == Severity.FIX
    assert "forskningsbaserat" in f.reference
    # the suggested change must really bring the knee into the range on the model
    sign = 1 if action == "raise" else -1
    for mm in (s["mm_lo"], s["mm_hi"]):
        moved = replace(p, saddle_height_cm=p.saddle_height_cm + sign * mm / 10.0)
        after = bs.truth(moved)["knee_flex_bottom_deg"]
        assert 31.0 <= after <= 45.0, (mm, after)
    assert s["mm_lo"] <= s["mm_hi"]
    assert f"{s['mm_lo']}–{s['mm_hi']} mm" in s["text"]


def test_a_deviation_inside_the_measurement_error_is_only_a_tentative_advice():
    p = bs.with_knee(bs.CyclistParams(), 46.0)      # 3 deg above the range, the error is +-5 deg
    a = run(p).analysis
    f = next(f for f in a.findings if f.code == "saddle")
    assert f.severity == Severity.WATCH
    assert "mätfelet" in f.explanation
    s = advice(a, "saddle_height")
    assert s["action"] == "raise_uncertain" and not s["certain"]
    assert "goniometer" in s["text"]
    assert a.ranges["knee_flex_bottom_deg"]["within_error"] is True


def test_practice_values_are_never_shown_as_faults():
    # straight arms, far reach, closed hip and a very low trunk: all rules of thumb, so at most "watch"
    p = bs.CyclistParams(trunk_angle_deg=22.0, shoulder_angle_deg=110.0, elbow_flex_deg=2.0, hip_bounce_cm=6.0)
    a = run(p).analysis
    codes = {f.code: f for f in a.findings}
    for code in ("reach", "trunk", "hip_rock", "hip_closed"):
        assert codes[code].severity == Severity.WATCH, code
        assert "erfarenhetsvärde" in codes[code].reference
    assert codes["trunk"].explanation.startswith("Bålen lutar 22°")
    stem = advice(a, "stem")
    assert stem["action"] == "shorter" and not stem["on_site"] and not stem["certain"]


def test_cramped_position_recommends_a_longer_stem():
    a = run(bs.CyclistParams(shoulder_angle_deg=68.0, elbow_flex_deg=40.0)).analysis
    assert advice(a, "stem")["action"] == "longer"


def test_the_ankle_and_knee_over_pedal_are_shown_without_a_verdict():
    a = run(bs.CyclistParams()).analysis
    for key in ("ankle_range_deg", "knee_over_pedal_cm", "cadence_rpm"):
        r = a.ranges[key]
        assert r["value"] is not None and r["severity"] is None and r["basis"] == tq.NO_VERDICT, key
    assert not any(f.code.startswith("ankle") for f in a.findings)


def test_hidden_hands_give_no_arm_values_and_no_stem_recommendation():
    a = run(bs.CyclistParams(hands_hidden=True)).analysis
    assert a.summary["elbow_flex_deg"] is None and a.summary["shoulder_angle_deg"] is None
    assert advice(a, "stem")["action"] == "unknown"
    assert any("styrstammen" in w for w in a.warnings)
    assert advice(a, "saddle_height")["action"] == "keep"     # the leg is still measured


def test_low_frame_rate_is_marked_uncertain_and_warned():
    p = bs.with_knee(bs.CyclistParams(fps=25.0), 52.0)
    a = run(p).analysis
    assert a.quality["level"] == "uncertain"
    assert any("bilder/s" in w for w in a.warnings)
    assert advice(a, "saddle_height")["action"] == "raise_uncertain"
    assert a.summary["knee_flex_bottom_deg"] == pytest.approx(52.0, abs=3.0)


def test_too_few_revolutions_judge_nothing():
    a = run(bs.CyclistParams(duration_s=2.5)).analysis
    assert a.quality["level"] == "poor"
    assert a.findings == []
    assert advice(a, "saddle_height")["action"] == "unknown"
    assert advice(a, "stem")["action"] == "unknown"


def test_nobody_in_the_film():
    core = ba.analyze_core([None] * 200, 178.0, View.SIDE_LEFT, times=[i / 60 for i in range(200)])
    assert core.kin is None and core.analysis.quality["level"] == "poor"
    assert core.analysis.warnings == ["Ingen cyklist hittades i filmen."]


def test_noise_gaps_and_uneven_frame_times_keep_the_knee_angle():
    p = bs.CyclistParams(noise_px=1.2, outlier_rate=0.03, missing_rate=0.05, time_jitter=0.3, seed=5)
    a = run(p).analysis
    assert a.quality["level"] in ("good", "uncertain")
    assert a.summary["knee_flex_bottom_deg"] == pytest.approx(bs.truth(p)["knee_flex_bottom_deg"], abs=3.0)
    assert a.summary["cadence_rpm"] == pytest.approx(90.0, abs=1.5)


def test_mirrored_film_is_warned():
    p = bs.CyclistParams()
    poses, times = bs.generate(p)
    back = [replace(q, t=times[i], frame_index=i) for i, q in enumerate(reversed(poses))]
    a = ba.analyze_core(back, p.height_cm, p.view, times=times).analysis
    assert a.quality["level"] == "uncertain"
    assert any("riktning" in w for w in a.warnings)


# --- from the front ------------------------------------------------------------------------------------

def test_front_view_knee_track_ok():
    p = bs.CyclistParams(view=View.FRONT, knee_lateral_cm=1.5)
    a = run(p).analysis
    assert a.summary["cadence_rpm"] == pytest.approx(90.0, abs=1.0)
    assert a.summary["knee_lateral_cm"] == pytest.approx(1.5, abs=0.8)
    assert [f.code for f in a.findings] == ["knee_track"] and a.findings[0].severity == Severity.OK
    assert advice(a, "cleat")["action"] == "keep"


@pytest.mark.parametrize("track,action", [("in", "check_in"), ("out", "check_out")])
def test_front_view_knee_track_problem_gives_cleat_advice(track, action):
    p = bs.CyclistParams(view=View.FRONT, knee_lateral_cm=6.0, knee_track=track)
    a = run(p).analysis
    assert a.summary["knee_lateral_cm"] == pytest.approx(6.0, abs=1.0)
    assert a.summary["knee_direction"] == track
    f = a.findings[0]
    assert f.code == "knee_track" and f.severity == Severity.WATCH and f.side in ("left", "right")
    c = advice(a, "cleat")
    assert c["action"] == action and c["on_site"] and c["certain"]


def test_front_view_says_what_it_cannot_see():
    a = run(bs.CyclistParams(view=View.FRONT)).analysis
    assert any("bäckenets" in w for w in a.warnings)
    assert "knee_flex_bottom_deg" not in a.ranges


# --- comparison and storage --------------------------------------------------------------------------------

def test_comparison_with_the_previous_fit():
    before = run(bs.with_knee(bs.CyclistParams(), 52.0)).analysis.to_dict()
    now = run(bs.with_knee(bs.CyclistParams(), 39.0), previous=before).analysis
    knee = next(c for c in now.comparison if c["metric"] == "knee_flex_bottom_deg")
    assert knee["trend"] == "better" and knee["delta"] == pytest.approx(-13.0, abs=1.5)
    trunk = next(c for c in now.comparison if c["metric"] == "trunk_angle_deg")
    assert trunk["trend"] == "same"           # nothing changed: inside the measurement error


def test_round_trip_through_a_dict():
    a = run(bs.with_knee(bs.CyclistParams(), 52.0)).analysis
    b = tq.TechniqueAnalysis.from_dict(a.to_dict())
    assert b.to_dict() == a.to_dict()
    assert tq.is_technique(a.to_dict()) and not tq.is_technique({"summary": {}})
