"""Double poling on the SkiErg: the analysis must recover the known truth of the synthetic skier, give
feedback at the first test, mark every range as a rule of thumb, and compare with the previous test."""
from __future__ import annotations

import pytest

from lopanalys.pose import technique as tq
from lopanalys.pose.ski import analysis as sa
from lopanalys.pose.ski import synthetic as ss
from lopanalys.pose.types import Severity, View


def run(p: ss.SkierParams, **kw):
    poses, times = ss.generate(p)
    return sa.analyze_core(poses, p.height_cm, p.view, times=times, **kw)


def finding(a, code):
    return next(f for f in a.findings if f.code == code)


@pytest.mark.parametrize("view,facing", [(View.SIDE_LEFT, -1), (View.SIDE_RIGHT, 1)])
@pytest.mark.parametrize("rate,drive", [(35.0, 650.0), (45.0, 580.0), (58.0, 500.0)])
def test_strokes_rate_and_rhythm(view, facing, rate, drive):
    p = ss.SkierParams(view=view, stroke_rate_spm=rate, drive_ms=drive)
    a = run(p).analysis
    truth = ss.truth(p)
    assert a.facing == facing and a.quality["level"] == "good"
    assert a.summary["stroke_rate_spm"] == pytest.approx(rate, abs=0.5)
    assert a.summary["drive_ms"] == pytest.approx(drive, rel=0.06)
    assert a.summary["recovery_ms"] == pytest.approx(truth["recovery_ms"], rel=0.08)
    assert a.summary["recovery_ratio"] == pytest.approx(truth["recovery_ratio"], rel=0.12)
    assert a.summary["strokes"] >= 9


def test_angles_and_heights_match_the_truth():
    p = ss.SkierParams()
    a = run(p).analysis
    truth = ss.truth(p)
    for key, tol in (("hand_height_start_cm", 2.0), ("elbow_angle_start_deg", 2.0), ("trunk_lean_end_deg", 1.5),
                     ("hip_flex_end_deg", 2.0), ("knee_flex_end_deg", 1.5), ("trunk_lead_ms", 15.0),
                     ("heel_raise_cm", 1.0)):
        assert a.summary[key] == pytest.approx(truth[key], abs=tol), key


def test_good_technique_gets_only_positive_feedback_with_every_range_marked():
    a = run(ss.SkierParams()).analysis
    assert {f.code for f in a.findings} == {"hand_height", "elbow_start", "trunk", "sequence", "rhythm"}
    assert all(f.severity == Severity.OK for f in a.findings)
    judged = [r for r in a.ranges.values() if r["severity"] is not None]
    assert len(judged) == 7
    assert all(r["basis"] == tq.EXPERIENCE for r in judged)        # no range here is research-based
    assert all("erfarenhetsvärde" in f.reference for f in a.findings)


def test_first_test_gives_feedback_with_cue_and_drill():
    p = ss.SkierParams(elbow_angle_start_deg=150.0, trunk_lean_end_deg=20.0, knee_flex_end_deg=75.0,
                       stroke_rate_spm=55.0, drive_ms=620.0)
    a = run(p).analysis
    assert a.comparison == []            # nothing to compare with yet
    got = {f.code: f for f in a.findings}
    assert got["elbow_start"].severity == Severity.FIX and "nästan rak" in got["elbow_start"].explanation
    assert got["trunk"].severity == Severity.FIX and "armarna" in got["trunk"].explanation
    assert got["knee_squat"].severity == Severity.FIX
    assert got["rhythm"].severity in (Severity.WATCH, Severity.FIX)
    for f in got.values():
        if f.severity != Severity.OK:
            assert f.cue and f.drill and f.highlight is not None, f.code
    assert a.findings[0].severity == Severity.FIX            # the most important first
    assert "Böj armbågarna" in got["elbow_start"].cue        # the cue of that direction, not of the other


@pytest.mark.parametrize("cm,elbow,word,cue", [(38.0, 150.0, "för högt", "strax över ögonen"),
                                               (-20.0, 100.0, "lågt", "Res dig helt")])
def test_hands_too_high_or_too_low_at_the_start(cm, elbow, word, cue):
    a = run(ss.SkierParams(hand_height_start_cm=cm, elbow_angle_start_deg=elbow)).analysis
    assert a.summary["hand_height_start_cm"] == pytest.approx(cm, abs=2.5)
    f = finding(a, "hand_height")
    assert f.severity == Severity.FIX and word in f.explanation and cue in f.cue


def test_arm_pull_is_found_from_the_order_of_trunk_and_arms():
    a = run(ss.SkierParams(trunk_lead_ms=-150.0)).analysis
    assert a.summary["trunk_lead_ms"] == pytest.approx(-150.0, abs=25.0)
    f = finding(a, "sequence")
    assert f.severity == Severity.FIX and "armdrag" in f.explanation
    ok = finding(run(ss.SkierParams(trunk_lead_ms=80.0)).analysis, "sequence")
    assert ok.severity == Severity.OK


def test_small_deviation_inside_the_error_is_said_to_be_uncertain():
    a = run(ss.SkierParams(trunk_lean_end_deg=37.0)).analysis      # 3 deg under the range, error +-5 deg
    f = finding(a, "trunk")
    assert f.severity == Severity.WATCH and "mätfelet" in f.explanation
    assert a.ranges["trunk_lean_end_deg"]["within_error"] is True


def test_compare_with_the_previous_test():
    before = run(ss.SkierParams(trunk_lean_end_deg=22.0, elbow_angle_start_deg=150.0, stroke_rate_spm=44.0)).analysis
    now = run(ss.SkierParams(trunk_lean_end_deg=41.0, elbow_angle_start_deg=152.0, stroke_rate_spm=40.0),
              previous=before.to_dict()).analysis
    c = {x["metric"]: x for x in now.comparison}
    assert c["trunk_lean_end_deg"]["trend"] == "better" and c["trunk_lean_end_deg"]["delta"] == pytest.approx(19, abs=2)
    assert c["elbow_angle_start_deg"]["trend"] == "same"       # 2 deg: inside the measurement error
    assert c["stroke_rate_spm"]["trend"] is None               # a value without a range has no direction
    worse = run(ss.SkierParams(trunk_lean_end_deg=22.0), previous=now.to_dict()).analysis
    assert {x["metric"]: x for x in worse.comparison}["trunk_lean_end_deg"]["trend"] == "worse"
    # another kind of test is never compared
    assert tq.compare(now, {"kind": "bikefit", "summary": {}, "ranges": {}}) == []


def test_values_without_a_reference_are_shown_without_a_verdict():
    a = run(ss.SkierParams()).analysis
    for key in ("stroke_rate_spm", "drive_ms", "vertical_oscillation_cm", "heel_raise_cm", "elbow_angle_min_deg"):
        assert a.ranges[key]["value"] is not None and a.ranges[key]["severity"] is None, key


def test_low_frame_rate_and_uneven_rhythm_are_marked_uncertain():
    slow = run(ss.SkierParams(fps=25.0)).analysis
    assert slow.quality["level"] == "uncertain" and any("bilder/s" in w for w in slow.warnings)
    assert slow.ranges["trunk_lead_ms"]["uncertainty"] == 60          # 1.5 frames at 25 fps
    uneven = run(ss.SkierParams(rate_jitter=0.35, duration_s=30.0)).analysis
    assert uneven.quality["level"] == "uncertain" and any("Takten varierar" in w for w in uneven.warnings)


def test_too_few_strokes_or_wrong_view_judge_nothing():
    short = run(ss.SkierParams(duration_s=5.0)).analysis
    assert short.quality["level"] == "poor" and short.findings == []
    front = run(ss.SkierParams(view=View.FRONT)).analysis
    assert front.quality["level"] == "poor" and "från sidan" in front.warnings[0]
    nobody = sa.analyze_core([None] * 100, 178.0, View.SIDE_LEFT, times=[i / 60 for i in range(100)]).analysis
    assert nobody.warnings == ["Ingen person hittades i filmen."]


def test_small_hand_movements_are_not_strokes():
    # standing nearly still with the hands moving a few centimetres: no strokes, nothing judged
    p = ss.SkierParams(trunk_lean_end_deg=6.0, knee_flex_end_deg=7.0, elbow_angle_end_deg=100.0, arm_end_deg=150.0)
    a = run(p).analysis
    assert a.quality["level"] == "poor" and a.summary.get("strokes", 0) == 0


def test_noise_gaps_and_uneven_frame_times():
    p = ss.SkierParams(noise_px=1.5, outlier_rate=0.03, missing_rate=0.05, time_jitter=0.3, seed=9)
    a = run(p).analysis
    truth = ss.truth(p)
    assert a.summary["stroke_rate_spm"] == pytest.approx(40.0, abs=1.0)
    assert a.summary["trunk_lean_end_deg"] == pytest.approx(truth["trunk_lean_end_deg"], abs=3.0)
    assert a.summary["drive_ms"] == pytest.approx(600.0, rel=0.12)


def test_round_trip_through_a_dict():
    a = run(ss.SkierParams(trunk_lean_end_deg=22.0)).analysis
    assert tq.TechniqueAnalysis.from_dict(a.to_dict()).to_dict() == a.to_dict()
