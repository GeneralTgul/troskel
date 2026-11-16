"""What the frame rate can carry: values marked uncertain or left out, in the analysis and the report."""
from __future__ import annotations


import pytest

from lopanalys.core.i18n import set_language
from lopanalys.pose import reliability as rel
from lopanalys.pose.offline import analyze_core
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import View


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def test_levels_follow_the_frame_rate():
    assert rel.levels(120) == {}
    assert rel.levels(None) == {}                           # unknown rate: as before
    lv = rel.levels(60)                                     # the contact values were off already at 50 fps
    assert lv["ground_contact_ms"] == rel.UNCERTAIN
    assert lv["foot_strike"] == rel.UNCERTAIN               # follows the foot angle
    assert "knee_flex_contact_deg" not in lv and "overstride_cm" not in lv
    lv = rel.levels(40)
    assert lv["knee_flex_contact_deg"] == rel.UNCERTAIN and lv["overstride_cm"] == rel.UNCERTAIN
    assert lv["ground_contact_ms"] == rel.HIDDEN
    lv = rel.levels(25)                                     # the live webcam
    assert lv["shin_angle_deg"] == rel.HIDDEN and lv["hip_extension_toe_off_deg"] == rel.UNCERTAIN
    for robust in ("cadence_spm", "vertical_oscillation_cm", "trunk_lean_deg", "knee_flex_midstance_deg"):
        assert robust not in lv
    low = rel.levels(12)
    assert low["ground_contact_ms"] == rel.HIDDEN and low["shin_angle_deg"] == rel.HIDDEN
    assert low["cadence_spm"] == rel.UNCERTAIN                 # robust values are marked, never left out
    assert rel.min_fps_for_all() == 100


def test_decimate_keeps_times_and_optionally_frame_numbers():
    poses, _ = generate(RunnerParams(fps=120, duration_s=1.0))
    d = rel.decimate(poses, 4)
    assert len(d) == 30 and [p.frame_index for p in d[:3]] == [0, 1, 2]
    assert d[1].t == pytest.approx(poses[4].t)
    k = rel.decimate(poses, 4, renumber=False)
    assert [p.frame_index for p in k[:3]] == [0, 4, 8]


def _analysis(fps: float):
    p = RunnerParams(fps=fps, duration_s=12, noise_px=1.0, view=View.SIDE_LEFT, seed=3)
    poses, truth = generate(p)
    return analyze_core(poses, p.height_cm, p.view, times=truth.times).analysis


def test_analysis_at_60_fps_marks_the_landing_values():
    a = _analysis(60)
    assert a.reliability["ground_contact_ms"] == rel.UNCERTAIN
    assert a.summary.ground_contact_ms is not None           # shown, marked
    w = " ".join(a.warnings)
    assert "Markkontakten" in w and "100 bilder/s" in w
    assert a.to_dict()["reliability"] == a.reliability
    from lopanalys.pose.types import GaitAnalysis
    assert GaitAnalysis.from_dict(a.to_dict()).reliability == a.reliability


def test_analysis_at_120_fps_has_no_marks():
    a = _analysis(120)
    assert a.reliability == {}
    assert not any("bilder/s" in w for w in a.warnings)


def test_hidden_values_are_left_out_before_the_findings():
    a = _analysis(120)
    s, lv = rel.apply(a.summary, 12)
    assert s.ground_contact_ms is None and s.shin_angle_deg is None and s.foot_strike is None
    assert s.cadence_spm == a.summary.cadence_spm
    assert "ground_contact_ms" not in s.asymmetry
    assert lv["ground_contact_ms"] == rel.HIDDEN
    assert "visas inte" in " ".join(rel.warnings(12))


def test_every_metric_has_a_name_in_both_languages():
    for lang in ("sv", "en"):
        set_language(lang)
        for m in rel.REQUIREMENTS:
            assert not rel._names([m]).startswith("pose."), (lang, m)
    set_language("sv")
    assert "Kadensen" in " ".join(rel.warnings(12))


def test_findings_on_uncertain_values_are_marked():
    a = _analysis(40)
    by = {f.code: f for f in a.findings}
    assert by["overstride"].uncertain                      # judged on the shin angle too
    assert not by["cadence"].uncertain
    assert all(not f.uncertain for f in _analysis(120).findings)
