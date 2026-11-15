import math

import numpy as np
import pytest

from lopanalys.assessments import lactate
from lopanalys.assessments.lactate import LactateResult, analyze, pace_str
from lopanalys.core.models import StepRecord


def exp_curve(a, b, c, x0):
    return lambda x: a + b * np.exp(c * (np.asarray(x, float) - x0))


def recs(speeds, lac, hr0=120, hr_per_kmh=8.0, first_index=1):
    return [StepRecord(first_index + i, float(s), 1.0, 240, int(round(hr0 + hr_per_kmh * (s - speeds[0]))),
                       lactate_mmol=round(float(l), 2)) for i, (s, l) in enumerate(zip(speeds, lac))]


def crossing(a, b, c, x0, level):
    return x0 + math.log((level - a) / b) / c


def dmax_exp(b, c, x0, xa, ya, xb, yb):
    s = (yb - ya) / (xb - xa)
    return x0 + math.log(s / (b * c)) / c


# Recreational runner: ~1 mmol at rest, ~3.9 mmol at 14.5 km/h, ~8 mmol at 16 km/h.
A, B, C, X0 = 0.9, 0.06, 0.6, 8.0
SPEEDS = np.arange(8.0, 16.01, 1.0)


def test_known_thresholds_exponential_curve():
    f = exp_curve(A, B, C, X0)
    lac = f(SPEEDS)
    r = analyze(recs(SPEEDS, lac), max_hr=195)
    assert r.model == "exp" and r.r2 > 0.999
    th = r.thresholds
    base = float(np.round(lac, 2).min())
    assert r.baseline_mmol == pytest.approx(base)
    assert th["bsln_0_5"].speed_kmh == pytest.approx(crossing(A, B, C, X0, base + 0.5), abs=0.05)
    assert th["bsln_1_0"].speed_kmh == pytest.approx(crossing(A, B, C, X0, base + 1.0), abs=0.05)
    assert th["bsln_1_5"].speed_kmh == pytest.approx(crossing(A, B, C, X0, base + 1.5), abs=0.05)
    assert th["obla_4"].speed_kmh == pytest.approx(crossing(A, B, C, X0, 4.0), abs=0.05)
    assert th["obla_4"].lactate_mmol == pytest.approx(4.0, abs=0.01)
    assert th["fixed_2_0"].speed_kmh == pytest.approx(crossing(A, B, C, X0, 2.0), abs=0.05)
    x1, x2 = SPEEDS[0], SPEEDS[-1]
    assert th["dmax"].speed_kmh == pytest.approx(dmax_exp(B, C, X0, x1, f(x1), x2, f(x2)), abs=0.08)
    # Bishop: line from the point before the first rise >= 0.4 mmol/L to the last point
    rl = np.round(lac, 2)
    i = next(i for i in range(len(rl) - 1) if rl[i + 1] - rl[i] >= 0.4 - 1e-9)
    assert th["first_rise"].speed_kmh == SPEEDS[i]
    assert th["mod_dmax"].speed_kmh == pytest.approx(dmax_exp(B, C, X0, SPEEDS[i], rl[i], x2, rl[-1]), abs=0.1)
    # log-log breakpoint is an LT1-type marker: between the first rise and LT2
    assert SPEEDS[0] < th["log_log"].speed_kmh < th["mod_dmax"].speed_kmh
    # recommended defaults
    assert r.lt1_method == "bsln_1_0" and r.lt2_method == "mod_dmax"
    assert r.lt1.speed_kmh < r.lt2.speed_kmh
    # HR, %max and pace
    lt2 = r.lt2
    assert lt2.hr == pytest.approx(120 + 8 * (lt2.speed_kmh - 8), abs=0.6)
    assert lt2.hr_pct_max == pytest.approx(100 * lt2.hr / 195, abs=0.1)
    assert lt2.pace == pace_str(lt2.speed_kmh)
    assert r.warnings == []


def test_pace_format():
    assert pace_str(12.0) == "5:00"
    assert pace_str(15.0) == "4:00"
    assert pace_str(13.3) == "4:31"
    assert pace_str(0) == ""


def test_very_fit_runner():
    f = exp_curve(0.8, 0.02, 0.75, 12.0)
    sp = np.arange(12.0, 20.01, 1.0)
    r = analyze(recs(sp, f(sp), hr0=130, hr_per_kmh=6), max_hr=188)
    assert r.lt2.speed_kmh > 17.5
    assert 15.0 < r.lt1.speed_kmh < r.lt2.speed_kmh
    assert r.thresholds["obla_4"].speed_kmh == pytest.approx(crossing(0.8, 0.02, 0.75, 12.0, 4.0), abs=0.05)


def test_unfit_runner_with_high_baseline():
    f = exp_curve(1.3, 0.3, 0.7, 6.0)
    sp = np.arange(6.0, 11.01, 1.0)
    r = analyze(recs(sp, f(sp)))
    assert r.baseline_mmol == pytest.approx(round(float(f(6.0)), 2))
    assert r.lt2.speed_kmh < 10.0
    assert r.lt1.speed_kmh < r.lt2.speed_kmh
    assert r.thresholds["obla_4"].speed_kmh == pytest.approx(crossing(1.3, 0.3, 0.7, 6.0, 4.0), abs=0.05)


def test_four_points_still_works_with_warning():
    f = exp_curve(A, B, C, X0)
    sp = np.array([10.0, 12.0, 14.0, 16.0])
    r = analyze(recs(sp, f(sp)))
    codes = [w.code for w in r.warnings]
    assert "few_points" in codes
    assert r.thresholds["obla_4"].speed_kmh == pytest.approx(crossing(A, B, C, X0, 4.0), abs=0.1)
    assert r.lt2 is not None and r.lt1 is not None
    assert r.lt1.speed_kmh < r.lt2.speed_kmh


def test_lactate_dip_and_noise():
    f = exp_curve(A, B, C, X0)
    rng = np.random.default_rng(3)
    lac = f(SPEEDS) + rng.normal(0, 0.12, SPEEDS.size)
    lac[0] = 2.0                       # elevated first value after a hard warm-up
    r = analyze(recs(SPEEDS, lac))
    assert r.baseline_mmol < 1.3       # the dip is ignored
    assert r.thresholds["obla_4"].speed_kmh == pytest.approx(crossing(A, B, C, X0, 4.0), abs=0.35)
    true_mod = dmax_exp(B, C, X0, 11.0, f(11.0), 16.0, f(16.0))
    assert r.lt2.speed_kmh == pytest.approx(true_mod, abs=0.6)
    assert r.lt1.speed_kmh < r.lt2.speed_kmh


def test_non_monotonic_values_are_flagged():
    sp = np.arange(8.0, 15.01, 1.0)
    lac = [1.0, 1.1, 1.3, 2.4, 1.6, 3.2, 4.8, 7.5]   # one bad sample at 11 km/h
    r = analyze(recs(sp, lac))
    assert "noisy" in [w.code for w in r.warnings]
    assert r.lt2 is not None


def test_too_few_points_and_no_rise():
    r = analyze(recs([10.0, 11.0], [1.0, 1.2]))
    assert r.thresholds == {} and r.lt1 is None and "too_few" in [w.code for w in r.warnings]
    assert r.model == "none"
    r = analyze(recs([8.0, 9.0, 10.0, 11.0, 12.0], [1.0, 1.0, 1.1, 1.2, 1.4]))
    codes = [w.code for w in r.warnings]
    assert "no_rise" in codes and "obla_not_reached" in codes
    assert "obla_4" not in r.thresholds
    assert analyze([]).warnings[0].code == "too_few"


def test_warmup_none_values_and_dicts_are_ignored_or_accepted():
    f = exp_curve(A, B, C, X0)
    rs = recs(SPEEDS, f(SPEEDS))
    rs.append(StepRecord(99, 17.0, 1.0, 30))          # partial last stage, no lactate
    dicts = [dict(index=x.index, speed_kmh=x.speed_kmh, incline_pct=1.0, lactate_mmol=x.lactate_mmol,
                  hr_end=x.hr_end) for x in rs]
    a, b = analyze(rs), analyze(dicts)
    assert len(a.points) == len(SPEEDS)
    assert a.lt2.speed_kmh == b.lt2.speed_kmh


def test_poly_model_and_select_and_roundtrip():
    f = exp_curve(A, B, C, X0)
    r = analyze(recs(SPEEDS, f(SPEEDS)), model="poly3", max_hr=190)
    assert r.model == "poly3"
    # Dmax on the cubic: p'(x) = slope of the end-point line
    p = np.polyfit(SPEEDS, np.round(f(SPEEDS), 2), 3)
    xa, xb = SPEEDS[0], SPEEDS[-1]
    s = (np.polyval(p, xb) - np.polyval(p, xa)) / (xb - xa)
    roots = np.roots(np.polyder(p) - np.array([0, 0, s]))
    roots = [x.real for x in roots if abs(x.imag) < 1e-9 and xa < x.real < xb]
    best = max(roots, key=lambda x: np.polyval(p, xa) + s * (x - xa) - np.polyval(p, x))
    assert r.thresholds["dmax"].speed_kmh == pytest.approx(best, abs=0.05)

    r2 = r.select("log_log", "obla_4")
    assert r2.lt1_method == "log_log" and r2.lt2.method == "obla_4" and r.lt2_method == "mod_dmax"
    with pytest.raises(ValueError):
        r.select("nope")
    d = r2.to_dict()
    back = LactateResult.from_dict(d)
    assert back.lt2.speed_kmh == r2.lt2.speed_kmh and back.model == "poly3"
    assert back.lactate_at(12.0) == pytest.approx(r2.lactate_at(12.0))
    assert back.speed_at(4.0) == pytest.approx(r2.speed_at(4.0))
    assert d["lt2"]["pace"] and d["thresholds"]["obla_4"]["name"] == "OBLA 4,0 mmol/l"


def test_auto_model_prefers_exponential_for_exponential_data():
    f = exp_curve(A, B, C, X0)
    assert analyze(recs(SPEEDS, f(SPEEDS))).model == "exp"


def test_forced_missing_method_falls_back_with_warning():
    sp = np.array([8.0, 9.0, 10.0, 11.0, 12.0])
    r = analyze(recs(sp, [1.0, 1.1, 1.2, 1.4, 1.7]), lt2_method="obla_4")
    assert r.lt2_method != "obla_4"
    assert "method_missing" in [w.code for w in r.warnings]


def test_rise_index_ignores_single_spike():
    ys = np.array([1.0, 1.0, 1.6, 1.1, 1.2, 1.7, 2.5, 4.0])
    assert lactate._rise_index(ys) == 4
