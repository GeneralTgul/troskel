"""Findings: right severity for clear cases, readable texts in Swedish and English."""
from __future__ import annotations

import json
import re


from lopanalys.core import i18n
from lopanalys.pose.findings import MetricRange, cadence_limits, evaluate, live_targets, num, overstride_limits
from lopanalys.pose.offline import analyze_poses
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import GaitAnalysis, Severity, View


def analysis(**kw):
    p = RunnerParams(fps=60, noise_px=1.0, **kw)
    poses, _ = generate(p)
    return analyze_poses(poses, p.height_cm, p.view, speed_kmh=12.0)


def by_code(a):
    return {f.code: f for f in a.findings}


def test_overstrider_with_low_cadence():
    a = analysis(overstride_cm=34, cadence_spm=145, trunk_lean_deg=1)
    f = by_code(a)
    assert f["overstride"].severity == Severity.FIX
    assert f["cadence"].severity == Severity.FIX
    assert f["trunk_upright"].severity == Severity.WATCH
    assert f["overstride"].highlight == ["l_heel", "l_ankle", "l_knee"]
    assert f["overstride"].frame_index is not None
    # most important first
    order = [x.severity for x in a.findings]
    assert order == sorted(order, key=lambda s: {Severity.FIX: 0, Severity.WATCH: 1, Severity.OK: 2}[s])


def test_good_runner_has_no_fix():
    a = analysis(overstride_cm=14, cadence_spm=176, trunk_lean_deg=7, foot_strike_deg=4)
    assert not [f for f in a.findings if f.severity == Severity.FIX]
    f = by_code(a)
    assert f["cadence"].severity == Severity.OK
    assert f["foot_strike"].severity == Severity.OK


def test_rear_view_findings():
    a = analysis(view=View.REAR, pelvic_drop_deg=8.5, knee_valgus_deg=16, step_width_cm=-4, arm_cross_cm=6)
    f = by_code(a)
    assert f["pelvic_drop"].severity == Severity.FIX
    assert f["pelvic_drop"].side in ("left", "right")
    assert f["knee_valgus"].severity in (Severity.WATCH, Severity.FIX)
    assert f["step_width"].severity == Severity.FIX
    assert "arm_cross" in f
    assert "overstride" not in f


def test_texts_are_complete_in_both_languages():
    for lang in ("sv", "en"):
        i18n.set_language(lang)
        a = analysis(overstride_cm=34, cadence_spm=145, trunk_lean_deg=1)
        b = analysis(view=View.REAR, pelvic_drop_deg=8.5, knee_valgus_deg=16, step_width_cm=-4,
                     arm_cross_cm=6, heel_whip_deg=15)
        for f in a.findings + b.findings:
            for text in (f.title, f.explanation, f.cue, f.drill, f.reference):
                assert "{" not in text and "pose." not in text, (lang, f.code, text)
            assert f.title and f.explanation
            if f.severity != Severity.OK:
                assert f.cue and f.drill
        for w in a.warnings:
            assert "pose." not in w


def test_swedish_number_format():
    i18n.set_language("sv")
    assert num(6.45, 1) in ("6,5", "6,4")
    assert num(-0.2) == "0"
    assert num(-3) == "−3"
    i18n.set_language("en")
    assert num(6.5, 1) == "6.5"


def test_cadence_limit_rises_with_speed():
    assert cadence_limits(14)[0] > cadence_limits(10)[0]
    assert cadence_limits(None)[0] == 155
    lo, hi = overstride_limits(178, 12)
    assert 20 < lo < hi < 40


def test_too_few_steps_gives_no_findings():
    assert evaluate(None, [], View.SIDE_LEFT) == []


def test_finding_roundtrip():
    from lopanalys.pose.types import Finding
    a = analysis(overstride_cm=34)
    for f in a.findings:
        assert Finding.from_dict(f.to_dict()) == f
    assert re.match(r"^\w", a.findings[0].title)


# --- reference ranges: one source for findings, report table and live tiles -------------------------

_ORDER = {Severity.OK: 0, Severity.WATCH: 1, Severity.FIX: 2}


def _assert_ranges_match_findings(a):
    """Every finding's severity is the worst level of the metrics it is judged with."""
    ranges = {k: MetricRange.from_dict(v) for k, v in a.ranges.items()}
    assert ranges, "analysis without ranges"
    checked = 0
    for f in a.findings:
        own = [r for r in ranges.values() if f.code in r.codes]
        if not own:
            assert f.code in ("foot_strike", "asymmetry"), f.code
            continue
        worst = max((Severity(r.severity) for r in own), key=_ORDER.get)
        assert worst == f.severity, (f.code, f.severity, [(r.metric, r.value, r.severity) for r in own])
        checked += 1
    # and the other way round: a metric outside its green range always has a finding
    codes = {f.code for f in a.findings}
    for r in ranges.values():
        assert r.classify(r.value).value == r.severity
        if r.severity != "ok":
            assert codes & set(r.codes), r.metric
    return checked


def test_ranges_agree_with_findings_for_many_runners():
    n = 0
    for kw in (dict(overstride_cm=34, cadence_spm=145, trunk_lean_deg=1),
               dict(overstride_cm=30, cadence_spm=160, trunk_lean_deg=1, vertical_oscillation_cm=11),
               dict(overstride_cm=14, cadence_spm=176, trunk_lean_deg=7, foot_strike_deg=4),
               dict(overstride_cm=22, cadence_spm=158, trunk_lean_deg=17),
               dict(view=View.REAR, pelvic_drop_deg=8.5, knee_valgus_deg=16, step_width_cm=-4, arm_cross_cm=6),
               dict(view=View.REAR, pelvic_drop_deg=5.5, knee_valgus_deg=11, step_width_cm=1, heel_whip_deg=14),
               dict(view=View.REAR)):
        n += _assert_ranges_match_findings(analysis(**kw))
    assert n >= 15


def test_cadence_160_at_12_kmh_is_ok_and_range_says_so():
    a = analysis(cadence_spm=164)
    r = MetricRange.from_dict(a.ranges["cadence_spm"])
    assert by_code(a)["cadence"].severity == Severity.OK and r.severity == "ok"
    assert r.ok_lo == 160 and r.fix_lo == 150 and r.ok_hi is None
    assert r.text == "minst 160 steg/min"
    # judged as printed: 159,6 is shown as 160 and "160, minst 160" must not be amber
    assert r.classify(159.6) == Severity.OK and r.classify(159.4) == Severity.WATCH
    assert r.classify(150) == Severity.WATCH and r.classify(149) == Severity.FIX


def test_live_targets_follow_speed_height_and_view():
    slow, fast = live_targets(8.0, 178, View.SIDE_LEFT), live_targets(16.0, 178, View.SIDE_LEFT)
    assert slow["cadence_spm"].ok_lo < fast["cadence_spm"].ok_lo
    assert slow["cadence_spm"].classify(152) == Severity.OK and fast["cadence_spm"].classify(152) == Severity.FIX
    short, tall = live_targets(12.0, 160, "side_left"), live_targets(12.0, 195, "side_left")
    assert short["overstride_cm"].ok_hi < tall["overstride_cm"].ok_hi
    assert "overstride_cm" not in live_targets(12.0, None, View.SIDE_LEFT)   # needs the leg length
    side, rear = live_targets(12.0, 178, View.SIDE_LEFT), live_targets(12.0, 178, View.REAR)
    assert "trunk_lean_deg" in side and "pelvic_drop_deg" not in side
    assert "pelvic_drop_deg" in rear and "trunk_lean_deg" not in rear
    assert set(live_targets()) >= set(side) - {"overstride_cm"} | set(rear)   # no view: every metric
    t = side["trunk_lean_deg"]
    assert t.text == "2–15°" and t.classify(1) == Severity.WATCH and t.classify(2) == Severity.OK
    assert t.classify(None) is None
    assert side["shin_angle_deg"].text == "högst 8°" and side["shin_angle_deg"].classify(14) == Severity.FIX
    i18n.set_language("en")
    assert live_targets(12.0, 178, View.SIDE_LEFT)["cadence_spm"].text == "at least 160 steps/min"


def test_ranges_survive_storage():
    a = analysis(overstride_cm=30)
    b = GaitAnalysis.from_dict(json.loads(json.dumps(a.to_dict())))
    assert b.ranges == a.ranges and b.ranges
    old = a.to_dict()
    del old["ranges"]                      # an analysis stored before ranges existed
    assert GaitAnalysis.from_dict(old).ranges == {}
