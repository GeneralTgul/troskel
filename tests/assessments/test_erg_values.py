"""What the coach writes in on a test in watt: a stage's mean power and the SkiErg's drag factor."""
from datetime import datetime, timezone

import pytest

from lopanalys.assessments import erg_values
from lopanalys.core.models import Session, StepRecord
from lopanalys.core.models import TestKind as Kind


def _session():
    stats = [{"index": 1, "target": 120.0, "measured": 118.0, "cadence": 40.0, "self_paced": True, "free": False,
              "in_band": True},
             {"index": 2, "target": 135.0, "measured": None, "cadence": None, "self_paced": True, "free": False}]
    return Session(5, 1, Kind.LACTATE_STEP, datetime(2026, 11, 15, tzinfo=timezone.utc), sport="skierg",
                   result={"stage_stats": stats})


def test_a_written_in_mean_power_replaces_the_load_and_can_be_taken_back():
    s = _session()
    rec = StepRecord(2, 135.0, 0.0, 240.0)          # the PM5 dropped: the stage got the target
    erg_values.set_manual_load(s, rec, 128)
    st = erg_values.stage_stat(s, 2)
    assert rec.speed_kmh == 128 and st["manual"] == 128 and st["auto"] == 135 and st["in_band"] is False
    assert erg_values.is_manual(s, 2) and not erg_values.is_manual(s, 1)
    erg_values.set_manual_load(s, rec, 134)          # a second correction keeps the first automatic value
    assert erg_values.stage_stat(s, 2)["auto"] == 135
    assert erg_values.stage_stat(s, 2)["in_band"] is True
    erg_values.set_manual_load(s, rec, None)
    assert rec.speed_kmh == 135 and not erg_values.is_manual(s, 2) and "auto" not in erg_values.stage_stat(s, 2)
    with pytest.raises(ValueError):
        erg_values.set_manual_load(s, rec, 0)
    with pytest.raises(ValueError):
        erg_values.set_manual_load(s, rec, 5000)


def test_a_stage_without_noted_values_gets_one():
    s = Session(6, 1, Kind.LACTATE_STEP, datetime(2026, 11, 15, tzinfo=timezone.utc), sport="bike")
    rec = StepRecord(0, 100.0, 0.0, 300.0)
    erg_values.set_manual_load(s, rec, 104.5)
    assert s.result["stage_stats"] == [{"index": 0, "target": None, "measured": None, "cadence": None,
                                        "self_paced": False, "free": False, "manual": 104.5, "auto": 100.0}]


def test_drag_factor():
    s = _session()
    assert erg_values.drag_factor(s) is None
    erg_values.set_drag_factor(s, 112, "pm5")
    assert erg_values.drag_factor(s) == {"value": 112, "source": "pm5"}
    erg_values.set_drag_factor(s, "98")
    assert erg_values.drag_factor(s) == {"value": 98, "source": "manual"}
    with pytest.raises(ValueError):
        erg_values.set_drag_factor(s, 5)
    erg_values.set_drag_factor(s, None)
    assert erg_values.drag_factor(s) is None and "drag_factor" not in s.result
    assert erg_values.median_int([110, 0, 95, 120]) == 110 and erg_values.median_int([]) is None
