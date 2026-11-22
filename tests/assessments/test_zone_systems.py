"""Zone systems: every system from the same anchors, the Garmin values, and the fallback without thresholds."""
import json
import re
from pathlib import Path

import numpy as np
import pytest

from lopanalys.assessments import zone_systems as zs
from lopanalys.assessments import zones
from lopanalys.assessments.lactate import analyze
from lopanalys.core.i18n import set_language
from lopanalys.core.models import StepRecord

I18N = Path(__file__).resolve().parents[2] / "src/lopanalys/i18n"


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def lactate_result(max_hr=195):
    sp = np.arange(8.0, 16.01, 1.0)
    lac = 0.9 + 0.06 * np.exp(0.6 * (sp - 8.0))
    rs = [StepRecord(i + 1, float(s), 1.0, 240, int(120 + 8 * (s - 8)), lactate_mmol=round(float(la), 2))
          for i, (s, la) in enumerate(zip(sp, lac))]
    return analyze(rs, max_hr=max_hr)


TYPED = zs.Anchors(max_hr=192, resting_hr=48, lt1_hr=150, lt2_hr=172, lt1_speed_kmh=11.0, lt2_speed_kmh=13.5)
MAX_ONLY = zs.Anchors(max_hr=190, resting_hr=50)


def _contiguous(zl):
    """No gaps and no overlaps: every zone starts where the one below ends, and has a width."""
    for a, b in zip(zl, zl[1:]):
        assert a.hr_high == b.hr_low, (a.code, b.code)
        if a.speed_high_kmh is not None and b.speed_low_kmh is not None:
            assert a.speed_high_kmh == b.speed_low_kmh
    for z in zl:
        assert z.hr_high is not None
        assert z.hr_low is None or z.hr_low < z.hr_high, z


def test_the_required_systems_are_defined_as_data():
    assert set(zs.SYSTEMS) >= {"seiler", "swedish", "olympiatoppen", "garmin", "polar", "friel"}
    assert [zs.SYSTEMS[k].n for k in ("seiler", "swedish", "olympiatoppen", "garmin", "polar", "friel")] == [3, 4, 5, 5, 5, 7]
    for sys in zs.SYSTEMS.values():
        for borders in (sys.anchored, sys.pct_max, sys.pct_hrr):
            assert borders is None or len(borders) == sys.n + 1, sys.key
        assert sys.lactate_ranges is None or len(sys.lactate_ranges) == sys.n, sys.key
        assert sys.name and sys.note and not sys.name.startswith("assessments.")
    with pytest.raises(ValueError):
        zs.convert(TYPED, "nonsense")


def test_olympiatoppen_definitions_live_in_one_place():
    assert zones.OLT_TABLE is zs.OLT_TABLE and zones.Zone is zs.Zone
    assert zs.SYSTEMS["olympiatoppen"].pct_max == (55, 72, 82, 87, 92, 100)
    r = lactate_result()
    assert zones.from_lactate(r, 195, 50) == zs.convert(zs.Anchors.from_lactate(r, 195, 50), "olympiatoppen")
    assert zones.from_heart_rate(190, 50) == zs.convert(MAX_ONLY, "olympiatoppen")


def test_olympiatoppen_lactate_is_olts_own_guide_values_per_zone():
    """olt_2024_no.pdf (checked 2026-11-15): I-1 below 1.5, I-2 ~1.0-2.0, I-3 ~1.5-3.5,
    none for I-4, and lactate does not define I-5. The ranges overlap, so they are per zone."""
    olt = zs.SYSTEMS["olympiatoppen"]
    assert olt.lactate_ranges == ((None, 1.5), (1.0, 2.0), (1.5, 3.5), (None, None), (None, None))
    nz = zs.nominal_zones(190, system="olympiatoppen")
    assert [(z.code, z.lactate_low, z.lactate_high) for z in nz] == [
        ("I1", None, 1.5), ("I2", 1.0, 2.0), ("I3", 1.5, 3.5), ("I4", None, None), ("I5", None, None)]
    assert all(s.lactate_ranges is None for k, s in zs.SYSTEMS.items() if k != "olympiatoppen")


@pytest.mark.parametrize("anchors", [TYPED, MAX_ONLY, zs.Anchors.from_lactate(lactate_result(), 195, 50)],
                         ids=["typed", "max-only", "lactate-test"])
@pytest.mark.parametrize("system", list(zs.SYSTEMS))
def test_borders_meet_without_gaps_or_overlaps(anchors, system):
    zl = zs.convert(anchors, system)
    assert len(zl) == zs.SYSTEMS[system].n
    assert [z.code for z in zl] == list(zs.SYSTEMS[system].codes)
    _contiguous(zl)
    assert zl[-1].hr_high <= anchors.max_hr
    assert all(z.name and z.purpose and "assessments." not in z.name + z.purpose for z in zl)


def test_thresholds_sit_on_the_borders_in_every_threshold_system():
    by = {k: {z.code: z for z in zs.convert(TYPED, k)} for k in zs.SYSTEMS}
    assert by["seiler"]["Z1"].hr_high == 150 and by["seiler"]["Z2"].hr_high == 172 and by["seiler"]["Z3"].hr_high == 192
    assert by["swedish"]["A1"].hr_high == 150 and by["swedish"]["A2"].hr_high == 172
    assert by["swedish"]["A3+"].hr_low == round(0.95 * 192)
    assert by["olympiatoppen"]["I1"].hr_high == 150 and by["olympiatoppen"]["I3"].hr_high == 172
    assert by["olympiatoppen"]["I2"].hr_high == 161                       # halfway between the thresholds
    assert by["friel"]["Z4"].hr_high == 172 and by["friel"]["Z1"].hr_low is None
    assert by["friel"]["Z2"].hr_low == round(0.85 * 172) and by["friel"]["Z5b"].hr_high == round(1.06 * 172)
    # Polar is defined by max HR alone: the thresholds do not move it, and it is not an estimate.
    assert [(z.hr_low, z.hr_high) for z in by["polar"].values()] == [(96, 115), (115, 134), (134, 154), (154, 173), (173, 192)]
    assert not any(z.estimated for sysz in by.values() for z in sysz.values())
    # Speeds only where the test measured them: at and between the thresholds.
    assert by["seiler"]["Z2"].speed_low_kmh == 11.0 and by["seiler"]["Z2"].speed_high_kmh == 13.5
    assert by["garmin"]["Z3"].speed_high_kmh == pytest.approx(12.2, abs=0.06)
    assert by["polar"]["Z3"].speed_low_kmh is None and by["garmin"]["Z1"].speed_high_kmh is None


def test_garmin_zone_2_ends_at_lt1_and_zone_4_at_lt2():
    g = zs.garmin_setup(TYPED)
    z = {r["zone"]: r for r in g.zones}
    assert len(z) == 5
    assert z[2]["hr_high"] == 150 and z[3]["hr_low"] == 150          # LT1
    assert z[4]["hr_high"] == 172 and z[5]["hr_low"] == 172          # LT2
    assert z[1]["hr_low"] == 96 and z[1]["hr_high"] == round(0.60 * 192) and z[5]["hr_high"] == 192
    assert g.method == "lthr" and g.lthr == 172 and g.max_hr == 192 and g.resting_hr == 48
    assert z[4]["pct_high"] == 100 and z[2]["pct_high"] == round(100 * 150 / 172)
    assert g.threshold_speed_kmh == 13.5 and g.threshold_pace == "4:27" and not g.estimated
    assert g.to_dict()["threshold_pace"] == "4:27"


def test_garmin_never_hands_out_a_submaximal_peak_as_max_hr():
    """A lactate test that stopped short of the limit: its highest heart rate is not the max HR."""
    g = zs.garmin_setup(TYPED, max_measured=False)
    assert g.max_hr is None and g.lthr == 172 and g.zones[3]["hr_high"] == 172
    assert g.zones[4] == {"zone": 5, "hr_low": 172, "hr_high": None, "pct_low": 100, "pct_high": None}
    card = zs.garmin_card(g)
    assert card["fields"][0]["value"].startswith("inte mätt") and card["zones"][4]["bpm"] == "från 172 slag/min"


def test_garmin_from_a_real_lactate_test():
    r = lactate_result()
    a = zs.Anchors.from_lactate(r, 195, 50)
    g = zs.garmin_setup(a)
    z = {x["zone"]: x for x in g.zones}
    assert z[2]["hr_high"] == round(r.lt1.hr) and z[4]["hr_high"] == round(r.lt2.hr) == g.lthr
    assert g.threshold_speed_kmh == r.lt2.speed_kmh
    card = zs.garmin_card(g)
    assert card["title"] == "Fyll i klockan" and len(card["zones"]) == 5 and len(card["fields"]) == 4
    assert f"{g.lthr} slag/min" in [f["value"] for f in card["fields"]]
    assert "LT1" in card["intro"] and "procent av maxpulsen" in card["why"]


def test_without_thresholds_everything_comes_from_max_hr_and_is_marked():
    for key, sys in zs.SYSTEMS.items():
        zl = zs.convert(MAX_ONLY, key)
        assert zl, key
        assert all(z.estimated for z in zl) == (key != "polar"), key
        assert all(z.basis in ("hr_max", "karvonen") for z in zl)
        assert all(z.speed_low_kmh is None and z.speed_high_kmh is None for z in zl)
    assert [(z.hr_low, z.hr_high) for z in zs.convert(MAX_ONLY, "garmin")] == \
        [(95, 114), (114, 133), (133, 152), (152, 171), (171, 190)]
    assert zs.convert(MAX_ONLY, "friel")[3].hr_high == round(0.87 * 190)      # estimated threshold HR
    g = zs.garmin_setup(MAX_ONLY)
    assert g.method == "max" and g.lthr is None and g.estimated and g.threshold_pace == ""
    assert [(r["pct_low"], r["pct_high"]) for r in g.zones] == [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]
    card = zs.garmin_card(g.to_dict())
    assert card["estimated"] and "uppskattade ur maxpuls" in card["intro"].lower() and len(card["fields"]) == 3


def test_thresholds_the_test_could_not_separate_fall_back_to_max_hr():
    recs = [StepRecord(i, sp, 1.0, 240, hr_end=hr, lactate_mmol=la)
            for i, (sp, hr, la) in enumerate([(8.5, 136, 1.3), (9.5, 144, 1.6), (10.5, 150, 2.4), (11.5, 158, 3.9)])]
    res = analyze(recs, max_hr=188)
    a = zs.Anchors.from_lactate(res, 188, 48)
    for key in zs.SYSTEMS:
        zl = zs.convert(a, key)
        _contiguous(zl)
        assert all(z.hr_low is None or z.hr_high - z.hr_low >= 3 for z in zl), key


def test_nothing_to_compute_gives_nothing_and_no_crash():
    for a in (zs.Anchors(), zs.Anchors(max_hr=60), zs.Anchors(max_hr=None, resting_hr=50),
              zs.Anchors.from_lactate(None)):
        assert all(zs.convert(a, k) == [] for k in zs.SYSTEMS)
        assert zs.garmin_setup(a) is None and zs.compare(a) == {}
    assert zs.garmin_card(None) is None
    # Thresholds without a max HR: the threshold systems still work, the % of max systems cannot.
    a = zs.Anchors(lt1_hr=150, lt2_hr=172)
    assert [z.hr_high for z in zs.convert(a, "friel")][:4] == [146, 155, 163, 172]
    assert zs.convert(a, "polar") == [] and zs.convert(a, "garmin") == []


def test_borders_stay_in_order_when_a_threshold_is_close_to_max():
    """LT2 within 6 % of max HR leaves no room for Friel's top zones: they are dropped, not inverted."""
    a = zs.Anchors(max_hr=180, lt1_hr=150, lt2_hr=174)
    zl = zs.convert(a, "friel")
    _contiguous(zl)
    assert zl[-1].hr_high == 180 and len(zl) < 7
    # A low LT1 never puts Garmin zone 1 above zone 2.
    _contiguous(zs.convert(zs.Anchors(max_hr=200, lt1_hr=112, lt2_hr=160), "garmin"))


def test_compare_gives_three_five_and_garmin():
    c = zs.compare(TYPED)
    assert list(c) == ["seiler", "olympiatoppen", "garmin"] and [len(v) for v in c.values()] == [3, 5, 5]
    assert c["seiler"][0].hr_high == c["olympiatoppen"][0].hr_high == c["garmin"][1].hr_high == 150


def test_assessment_texts_exist_in_both_languages():
    sv = json.loads((I18N / "assessments.sv.json").read_text(encoding="utf-8"))
    en = json.loads((I18N / "assessments.en.json").read_text(encoding="utf-8"))
    assert sv.keys() == en.keys()
    for k in sv:
        assert set(re.findall(r"{(\w+)}", sv[k])) == set(re.findall(r"{(\w+)}", en[k])), k
    for key, sys in zs.SYSTEMS.items():
        for code in sys.codes:
            assert f"assessments.zones.{key}.{code}.name" in sv and f"assessments.zones.{key}.{code}.purpose" in sv


def _steps(rows):
    return [StepRecord(i + 1, s, 1.0, 240, hr, lactate_mmol=la) for i, (s, la, hr) in enumerate(rows)]


def _no_empty_zone(zl):
    for z in zl:
        if z.hr_low is not None and z.hr_high is not None:
            assert z.hr_low < z.hr_high, (z.code, z.hr_low, z.hr_high)
        if z.speed_low_kmh is not None and z.speed_high_kmh is not None:
            assert z.speed_low_kmh < z.speed_high_kmh, (z.code, z.speed_low_kmh, z.speed_high_kmh)


def test_a_submaximal_test_with_its_own_peak_as_max_gives_no_empty_top_zone():
    # No max HR is known, so the highest heart rate of the test stands in for it. The I4/I5 border
    # lies past the last stage and its heart rate is capped at that "max": I5 was "148-148".
    res = analyze(_steps([(9.1, 1.2, 121), (9.6, 1.4, 129), (10.1, 2.2, 138), (10.6, 3.0, 148)]), max_hr=148)
    zl = zs.curve_zones(res, 148, "olympiatoppen")
    assert [z.code for z in zl] == ["I1", "I2", "I3", "I4", "I5"]
    _no_empty_zone(zl)
    assert zl[-1].hr_low < 148 and zl[-1].hr_high == 148


def test_lt2_at_the_heart_rate_handed_in_as_max_leaves_the_heart_rate_above_it_out():
    # LT2 lands at the last stage, whose heart rate is also the "max": what lies above is not known.
    res = analyze(_steps([(157.0, 1.8, 117), (172.0, 2.6, 129), (187.0, 2.7, 135), (202.0, 3.4, 138)]),
                  max_hr=138, sport="bike")
    zl = zs.curve_zones(res, 138, "olympiatoppen")
    _no_empty_zone(zl)
    i4, i5 = zl[-2], zl[-1]
    assert (i4.hr_low, i4.hr_high) == (138, None)
    assert (i5.hr_low, i5.hr_high) == (None, None)
    assert i5.speed_low_kmh < i5.speed_high_kmh      # the loads still stand


def test_seiler_top_zone_reaches_max_hr_when_lt2_is_at_the_last_stage():
    res = analyze(_steps([(10.2, 1.4, 106), (11.7, 2.4, 113), (13.2, 2.4, None), (14.7, 2.7, None),
                          (16.2, 3.6, 139)]), max_hr=142)
    assert res.lt2.speed_kmh == 16.2
    z3 = zs.curve_zones(res, 142, "seiler")[-1]
    assert (z3.hr_low, z3.hr_high) == (139, 142)       # was 139-139
    assert (z3.speed_low_kmh, z3.speed_high_kmh) == (16.2, None)   # was 16.2-16.2
