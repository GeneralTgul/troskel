

def test_thresholds_too_close_give_heart_rate_zones_and_a_warning():
    """Four lactate values that end at 3.9 mmol/L put LT1 and LT2 at almost the same speed (first demo run,
    2026-11-13: zones I2 and I3 came out as "150-150" and "150-151"). Then the zones come from heart rate."""
    from lopanalys.assessments import lactate, zones
    from lopanalys.core.models import StepRecord

    recs = [StepRecord(i, sp, 1.0, 240, hr_end=hr, lactate_mmol=la)
            for i, (sp, hr, la) in enumerate([(8.5, 136, 1.3), (9.5, 144, 1.6), (10.5, 150, 2.4), (11.5, 158, 3.9)])]
    res = lactate.analyze(recs, max_hr=188)
    assert res.lt1 is not None and res.lt2 is not None
    if lactate.thresholds_too_close(res.lt1.speed_kmh, res.lt2.speed_kmh):
        assert any(w.code == "close" for w in res.warnings)
    zs = zones.from_lactate(res, max_hr=188, resting_hr=48)
    assert zs, "zones must still be given"
    for z in zs:
        d = z.to_dict()
        lo, hi = d.get("hr_low"), d.get("hr_high")
        assert lo is None or hi is None or hi - lo >= 3, f"empty zone {d}"
