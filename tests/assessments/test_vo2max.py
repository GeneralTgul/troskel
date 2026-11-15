"""Estimated VO2max: worked examples, the fixed ranking, and missing inputs."""
import pytest

from lopanalys.assessments import vo2max
from lopanalys.core.i18n import set_language
from lopanalys.core.models import StepRecord


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def steps(n=5, start=8.0, hr0=120, dhr=10, incline=0.0, dur=240):
    return [StepRecord(i, start + i, incline, dur, hr_end=hr0 + dhr * i) for i in range(n)]


def test_acsm_running_equation_worked_examples():
    # 12 km/h = 200 m/min, level: 3.5 + 0.2 * 200 = 43.5
    assert vo2max.acsm_running_vo2(12.0) == pytest.approx(43.5)
    # 12 km/h at 5 %: 43.5 + 0.9 * 200 * 0.05 = 52.5
    assert vo2max.acsm_running_vo2(12.0, 5.0) == pytest.approx(52.5)
    # 16 km/h at 1 %: 3.5 + 53.33 + 2.4 = 59.23
    assert vo2max.acsm_running_vo2(16.0, 1.0) == pytest.approx(59.233, abs=0.01)


def test_final_stage_gives_acsm_and_leger_mercier_on_the_level():
    acsm, lm = vo2max.from_final_stage(16.0, 0.0, 240)
    assert (acsm.method, acsm.value) == ("acsm_final_stage", 56.8)        # 3.5 + 0.2 * 266.67
    assert (lm.method, lm.value) == ("leger_mercier", 52.8)               # 2.209 + 3.163 * 16
    assert (acsm.low, acsm.high, acsm.uncertainty_pct) == (48.3, 65.4, 15.0)      # 15 % either side of 56.83
    assert acsm.inputs == {"speed_kmh": 16.0, "incline_pct": 0.0, "stage_s": 240.0}
    # Uphill there is no level-ground formula.
    (only,) = vo2max.from_final_stage(12.0, 4.0, 240)
    assert only.method == "acsm_final_stage" and only.value == 50.7
    # a stage steeper than 5 % or shorter than 2 min is not used at all
    assert vo2max.from_final_stage(12.0, 8.0, 240) == [] and vo2max.from_final_stage(12.0, 1.0, 60) == []
    assert vo2max.from_final_stage(None) == [] and vo2max.from_final_stage(3.0) == []


def test_heart_rate_line_extrapolated_to_max():
    # Stages 8, 10, 12 km/h level at 120, 140, 160 bpm: VO2 30.17, 36.83, 43.5 -> 1/3 ml per beat -> 53.5 at 190.
    recs = [StepRecord(i, v, 0.0, 240, hr_end=hr) for i, (v, hr) in enumerate([(8, 120), (10, 140), (12, 160)])]
    e = vo2max.from_stages(recs, 190)
    assert e.method == "hr_extrapolation" and e.value == pytest.approx(53.5, abs=0.05)
    assert e.uncertainty_pct == 15.0 and e.low == pytest.approx(45.5, abs=0.1) and e.high == pytest.approx(61.5, abs=0.1)
    assert e.inputs == {"stages": 3, "max_hr": 190, "hr_low": 120, "hr_high": 160}


def test_uth_sorensen_worked_example():
    e = vo2max.from_heart_rates(190, 50)
    assert e.method == "uth_sorensen" and e.value == pytest.approx(58.1, abs=0.05)      # 15.3 * 190 / 50
    assert e.inputs == {"max_hr": 190, "resting_hr": 50}


def test_missing_or_bad_inputs_give_none_and_never_crash():
    assert vo2max.estimate() is None
    assert vo2max.estimate([], None, None, to_exhaustion=True) is None
    assert vo2max.estimate([{"speed_kmh": None, "hr_end": "x", "duration_s": None}], "abc", object(), to_exhaustion=True) is None
    assert vo2max.from_stages(steps(2), 190) is None                      # fewer than 3 stages
    assert vo2max.from_stages(steps(5), None) is None                     # no max HR
    assert vo2max.from_stages(steps(5), 150) is None                      # "max" below the test's own heart rate
    assert vo2max.from_stages(steps(5, dhr=0), 190) is None               # heart rate does not rise
    assert vo2max.from_stages(steps(5, dur=60), 190) is None              # 1-min stages: heart rate not settled
    assert vo2max.from_stages([StepRecord(i, 8 + i, 0, 240) for i in range(5)], 190) is None   # no heart rate
    assert vo2max.from_heart_rates(190, None) is None and vo2max.from_heart_rates(None, 50) is None
    assert vo2max.from_heart_rates(190, 0) is None and vo2max.from_heart_rates(190, 200) is None
    assert vo2max.from_heart_rates(60, 50) is None
    assert vo2max.card(None) is None and vo2max.card({}) is None


def test_ranking_is_fixed_and_the_last_stage_counts_only_after_exhaustion():
    recs = steps(6)                                                       # 8..13 km/h, 120..170 bpm
    full = vo2max.estimate(recs, 185, 60, to_exhaustion=True)
    assert [e.method for e in full.estimates] == list(vo2max.RANK)
    assert full.best.method == "acsm_final_stage" and full.best.value == 46.8          # 13 km/h level
    sub = vo2max.estimate(recs, 185, 60, to_exhaustion=False)
    assert [e.method for e in sub.estimates] == ["hr_extrapolation", "uth_sorensen"]
    only_hr = vo2max.estimate(None, 185, 50)
    assert [e.method for e in only_hr.estimates] == ["uth_sorensen"]
    d = full.to_dict()
    assert d["best"]["method"] == "acsm_final_stage" and len(d["estimates"]) == 4 and d["best"]["name"]


def test_the_stage_the_athlete_stepped_off_in_does_not_count():
    recs = steps(5) + [StepRecord(5, 13.0, 0.0, 70, hr_end=172)]          # 70 s into a 4-min stage
    r = vo2max.estimate(recs, 180, None, to_exhaustion=True, stage_durations=[240] * 6)
    assert r.best.inputs["speed_kmh"] == 12.0
    # An open-ended ramp repeats its last planned stage: index 9 is judged by the last duration.
    ramp = [StepRecord(i, 12.0, 1.0 + i, 60) for i in range(9)] + [StepRecord(9, 12.0, 10.0, 20)]
    last = vo2max.last_completed_stage(ramp, [60, 60])
    assert last.incline_pct == 9.0
    assert vo2max.last_completed_stage(ramp).incline_pct == 10.0          # without the plan every record counts


def test_card_always_says_estimated_and_gives_the_interval():
    c = vo2max.card(vo2max.estimate(steps(6), 185, 60, to_exhaustion=True))
    assert c["title"].startswith("Uppskattat")
    assert c["disclaimer"] == ("Uppskattat ur testets fart och puls. Ett riktigt VO2max-test med mätning av "
                               "andningsgaser krävs för att veta.")
    assert c["value"] == "46,8" and "39,8–53,9" in c["range"] and "±15 %" in c["range"]
    assert "ACSM" in c["method"] and len(c["others"]) == 3
    set_language("en")
    c = vo2max.card(vo2max.estimate(steps(6), 185, 60, to_exhaustion=True).to_dict())
    assert c["title"].startswith("Estimated") and c["value"] == "46.8" and "assessments." not in str(c)


def test_steep_incline_ramp_gives_no_value():
    """12 km/h, 12 % on a 60-s last step, max criteria not met -> nothing (a real ramp gave a value far above the watch)."""
    ramp = [StepRecord(i, 12.0, float(i), 60, hr_end=140 + 3 * i) for i in range(1, 13)]
    r = vo2max.estimate(ramp, 176, None, to_exhaustion=True, stage_durations=[600, 60], likely_max=False)
    assert r.estimates == [] and r.reasons == ["not_max"]
    c = vo2max.card(r.to_dict())
    assert c["none"] and c["text"] == "Kan inte uppskattas ur det här testet." and "maxpuls" in c["reasons"][0]
    # even when the criteria were met, a steep 60-s last step gives no ACSM value
    r = vo2max.estimate(ramp, 188, None, to_exhaustion=True, stage_durations=[600, 60], likely_max=True)
    assert r.estimates == [] and r.reasons == ["steep"]
    assert "5 % lutning" in vo2max.card(r)["reasons"][0]
    flat = [StepRecord(i, 10.0 + 0.5 * i, 1.0, 60) for i in range(10)]
    r = vo2max.estimate(flat, 190, None, to_exhaustion=True, likely_max=True)
    assert r.reasons == ["short"] and not r.estimates


def test_implausible_estimates_are_dropped():
    recs = steps(6)                                    # ACSM 46,8; Uth-Sørensen 15.3 * 185 / 50 = 56,6
    r = vo2max.estimate(recs, 185, 50, to_exhaustion=True)
    assert "implausible" in r.reasons
    assert all(abs(e.value - 56.6) <= 0.15 * 56.6 for e in r.estimates)
    assert "acsm_final_stage" not in [e.method for e in r.estimates]


def test_watch_value_next_to_the_estimate():
    c = vo2max.card(vo2max.estimate(steps(6), 185, 60, to_exhaustion=True), 53.0, "2026-11-01")
    assert c["watch"]["text"] == "VO2max enligt klockan: 53,0 (2026-11-01)"
    assert c["watch"]["warn"].startswith("Testets uppskattning skiljer 12 % från klockans")   # 46,8 vs 53
    close = vo2max.card(vo2max.estimate(steps(6), 185, 60, to_exhaustion=True), 48.0)
    assert close["watch"] == {"text": "VO2max enligt klockan: 48,0", "warn": ""}
    assert vo2max.card(None, 53.0) is None and vo2max.watch_line(None) is None
