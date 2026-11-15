import numpy as np
import pytest

from lopanalys.assessments import compare, library, max_hr, zones
from lopanalys.assessments.lactate import analyze
from lopanalys.assessments.protocol import Protocol
from lopanalys.core.models import StepRecord, TimeSample


def lactate_result(shift=0.0, max_hr_=195):
    sp = np.arange(8.0, 16.01, 1.0)
    lac = 0.9 + 0.06 * np.exp(0.6 * (sp - 8.0 - shift))
    rs = [StepRecord(i + 1, float(s), 1.0, 240, int(120 + 8 * (s - 8)), lactate_mmol=round(float(l), 2))
          for i, (s, l) in enumerate(zip(sp, lac))]
    return analyze(rs, max_hr=max_hr_)


# ------------------------------------------------------------------ zones
def test_olympiatoppen_zones_from_lactate():
    r = lactate_result()
    zs = zones.from_lactate(r, 195, 50)
    assert [z.code for z in zs] == ["I1", "I2", "I3", "I4", "I5"]
    assert zs[0].speed_high_kmh == pytest.approx(r.lt1.speed_kmh, abs=0.05)
    assert zs[2].speed_high_kmh == pytest.approx(r.lt2.speed_kmh, abs=0.05)
    assert zs[3].speed_low_kmh == zs[2].speed_high_kmh
    for a, b in zip(zs, zs[1:]):
        assert a.hr_high == b.hr_low
        assert a.speed_high_kmh == b.speed_low_kmh
    assert zs[0].hr_low == round(0.55 * 195) and zs[-1].hr_high == 195
    assert zs[2].lactate_high == pytest.approx(r.lt2.lactate_mmol, abs=0.05)
    assert zs[3].lactate_high >= 6.0 - 0.05
    assert zs[1].name.startswith("I2") and zs[1].purpose
    d = zs[2].to_dict()
    assert d["pace_low"] and d["pace_high"] and d["basis"] == "lactate"


def test_seiler_zones_from_lactate():
    r = lactate_result()
    zs = zones.from_lactate(r, 195, system="seiler")
    assert [z.code for z in zs] == ["Z1", "Z2", "Z3"]
    assert zs[1].speed_low_kmh == pytest.approx(r.lt1.speed_kmh, abs=0.05)
    assert zs[1].speed_high_kmh == pytest.approx(r.lt2.speed_kmh, abs=0.05)


def test_zones_fallback_when_thresholds_missing():
    r = analyze([StepRecord(1, 10, 1, 240, lactate_mmol=1.0)])
    zs = zones.from_lactate(r, 190)
    assert zs[0].basis == "hr_max"
    assert zones.from_lactate(r, None) == []


def test_zones_from_heart_rate_and_karvonen():
    zs = zones.from_heart_rate(190)
    assert [(z.hr_low, z.hr_high) for z in zs][0] == (round(0.55 * 190), round(0.72 * 190))
    assert zs[2].lactate_high == 3.5 and zs[4].lactate_low is None and zs[4].lactate_high is None
    kz = zones.from_heart_rate(190, 50)
    assert kz[0].basis == "karvonen"
    assert kz[1].hr_low == round(50 + 0.64 * 140)
    assert all(a.hr_high == b.hr_low for a, b in zip(kz, kz[1:]))
    s = zones.from_heart_rate(190, system="seiler")
    assert len(s) == 3 and s[2].hr_high == 190
    with pytest.raises(ValueError):
        zones.from_heart_rate(None)


# ------------------------------------------------------------------ max HR
def ramp_samples(plateau=True, spikes=True, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    end = 600
    for t in range(0, end + 90):
        if t <= end:
            hr = 150 + 40 * t / 540 if t < 540 else 190 + (0 if plateau else 40 * (t - 540) / 540)
            if not plateau:
                hr = 150 + 40 * t / 540
        else:
            peak = 190 if plateau else 150 + 40 * end / 540
            hr = peak - 30 * (1 - np.exp(-(t - end) / 25))
        out.append(TimeSample(float(t), int(round(hr + rng.normal(0, 0.7)))))
    if spikes:
        out[100].hr = 250        # cable spike outside range
        out[300].hr = 215        # single-beat spike
        out[560].hr = 230
        out[200].hr = None       # dropout
        out[201].hr = None
        out[202].hr = None
    return out, float(end)


def test_max_hr_peak_plateau_and_recovery():
    s, end = ramp_samples()
    r = max_hr.analyze(s, end_t_s=end, age=30, final_rpe=19)
    assert r.peak_hr in (189, 190, 191)
    assert r.artefacts_removed >= 3
    assert r.plateau is True
    assert 530 <= r.peak_t_s <= end
    assert r.recovery_hr_60 == pytest.approx(190 - 30 * (1 - np.exp(-60 / 25)), abs=3)
    assert r.recovery_drop_60 >= 20
    assert {c.code: c.met for c in r.criteria} == {"plateau": True, "age": True, "rpe": True, "lactate": None}
    assert r.likely_max
    back = max_hr.MaxHrResult.from_dict(r.to_dict())
    assert back.peak_hr == r.peak_hr and back.criteria[0].code == "plateau"


def test_max_hr_no_plateau_not_likely_max():
    s, end = ramp_samples(plateau=False, spikes=False)
    r = max_hr.analyze(s, end_t_s=end)
    assert r.plateau is False
    assert not r.likely_max
    assert r.peak_hr == pytest.approx(150 + 40 * end / 540, abs=2)


def test_max_hr_peak_not_taken_from_recovery_and_stage_hr():
    s, end = ramp_samples(spikes=False)
    for x in s[int(end) + 10:int(end) + 20]:
        x.hr = 199                # suspicious bump after the end must not count
    recs = [StepRecord(i, 12, i, 60, hr_end=150 + 4 * i, rpe=15 + i) for i in range(5)]
    r = max_hr.analyze(s, end_t_s=end, records=recs, peak_lactate=9.5)
    assert r.peak_hr <= 192
    assert [x["hr"] for x in r.stage_hr] == [150, 154, 158, 162, 166]
    crit = {c.code: c for c in r.criteria}
    assert crit["rpe"].met is True and crit["rpe"].value == 19
    assert crit["lactate"].met is True


def test_max_hr_without_data():
    r = max_hr.analyze([TimeSample(0.0, None), TimeSample(1.0, None)])
    assert r.peak_hr is None and r.warnings[0]["code"] == "no_hr"
    assert max_hr.analyze([]).peak_hr is None


# ------------------------------------------------------------------ compare
def test_compare_right_shift():
    # Fixed-lactate methods follow the curve exactly; Dmax variants also depend on where the test
    # ended, so OBLA is used to check the threshold delta here.
    before, after = lactate_result(0.0), lactate_result(0.6).select(lt2_method="obla_4")
    c = compare.compare_lactate(after, before.to_dict())
    assert c.shift == "right"
    assert c.curve_shift_kmh == pytest.approx(0.6, abs=0.1)
    assert c.lt2.speed_delta == pytest.approx(0.6, abs=0.15)
    assert c.lt2.pace_delta_s < 0
    assert all(x["delta"] < 0 for x in c.lactate_at_speed)   # less lactate at the same speed
    assert "höger" in c.summary
    assert c.to_dict()["lt1"]["which"] == "lt1"
    same = compare.compare_lactate(before, before)
    assert same.shift == "none"


def test_compare_max_hr():
    d = compare.compare_max_hr({"peak_hr": 190, "recovery_hr_60": 160, "recovery_drop_60": 30},
                               {"peak_hr": 192, "recovery_hr_60": 165, "recovery_drop_60": 27})
    assert d["peak_hr"]["delta"] == -2 and d["recovery_drop_60"]["delta"] == 3


# ------------------------------------------------------------------ library
def test_library_builders_and_limits():
    p = library.lactate_step(10, 1, 6)
    assert p.kind == "lactate" and p.stages[0].is_warmup and p.stages[0].label == "Uppvärmning"
    assert [s.speed_kmh for s in p.stages[1:]] == [10, 11, 12, 13, 14, 15]
    assert all(s.sample_lactate for s in p.stages) and all(s.incline_pct == 1 for s in p.stages)
    assert "4 min" in p.description and "30 s" in p.description
    lim = library.TreadmillLimits(max_speed_kmh=13.0, max_incline_pct=8)
    q = library.lactate_step(10, 1, 6, limits=lim)
    assert max(s.speed_kmh for s in q.stages) == 13.0
    assert library.start_speed_for("trained") == 11.0
    assert library.start_speed_for(estimated_lt2_kmh=8.0) == 6.0
    assert library.lactate_step_for_level("elite").stages[1].speed_kmh == 14.0
    m = library.max_hr_ramp(12, final_sprint_s=30)
    assert m.open_ended and m.increment_incline_pct == 1.0 and m.final_sprint_kmh == 2.0
    assert "slutspurt" in m.description
    sp = library.max_hr_ramp(10, mode="speed", step_s=30)
    assert sp.increment_speed_kmh == 0.5 and sp.increment_incline_pct == 0
    c = library.lactate_to_max(10)
    assert c.open_ended and not c.stages[-1].sample_lactate and c.stages[-2].sample_lactate
    g = library.gait_check([10, 12, 14])
    assert all(s.record_gait for s in g.stages) and all(s.duration_s == 120 for s in g.stages)   # 120 s per speed
    with pytest.raises(ValueError):
        library.gait_check([])
    with pytest.raises(ValueError):
        library.max_hr_ramp(mode="x")
    for p in library.default_protocols():
        assert p.description and p.name
        assert Protocol.from_dict(p.to_dict()) == p


def test_protocol_from_old_dict_without_new_fields():
    d = library.lactate_step(10, 1, 3).to_dict()
    for k in ("final_sprint_s", "final_sprint_kmh", "max_open_ended_s", "builder", "params"):
        d.pop(k)
    d["future_field"] = 1
    p = Protocol.from_dict(d)
    assert p.max_open_ended_s == 1800 and len(p.stages) == 4
