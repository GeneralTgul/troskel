"""Sport as a concept: the profiles, and sport on tests, protocols, thresholds and zones."""
from datetime import datetime, timezone

import pytest

from lopanalys.assessments import lactate, library, zone_systems, zones
from lopanalys.assessments.protocol import Protocol, Stage
from lopanalys.assessments.runner import stage_text
from lopanalys.core.models import Athlete, Session, StepRecord, TestKind as Kind, TimeSample
from lopanalys.core.sport import PROFILES, Sport, profile, selectable_sports, sport_of


def test_every_sport_has_a_profile_and_can_be_chosen():
    assert set(PROFILES) == set(Sport)
    assert selectable_sports() == [Sport.RUN, Sport.BIKE, Sport.SKIERG]
    for sp, p in PROFILES.items():
        assert p.sport is sp and p.load_sources and p.load_unit


def test_running_is_speed_in_kmh_with_pace_per_km():
    p = profile(Sport.RUN)
    assert (p.load_quantity, p.load_unit, p.load_decimals, p.has_incline) == ("speed", "km/h", 1, True)
    assert p.pace_unit == "min/km" and p.pace_str(12.0) == "5:00" and p.pace_str(14.6) == "4:07"
    assert p.load_sources == ("ftms", "qz", "manual", "sim") and p.controllable
    assert p.load_str(12) == "12.0"


def test_bike_is_watt_without_pace():
    p = profile("bike")
    assert (p.load_quantity, p.load_unit, p.load_decimals, p.has_incline) == ("power", "W", 0, False)
    assert p.pace_unit is None and p.pace_str(250) == "" and p.pace_s(250) is None
    assert p.controllable and "qz" not in p.load_sources      # no QZ path for the bike
    assert p.load_str(250.4) == "250"


def test_skierg_is_watt_with_pace_per_500_m_and_cannot_be_set():
    p = profile(Sport.SKIERG)
    assert (p.load_quantity, p.load_unit, p.pace_unit) == ("power", "W", "/500 m")
    assert not p.controllable and p.load_sources[0] == "pm5"
    # Concept2: W = 2.80 / (s/m)^3, so 2:00 /500 m is 202.5 W
    assert p.pace_s(2.80 / (120 / 500) ** 3) == pytest.approx(120.0)
    assert p.pace_str(202.5) == "2:00" and p.pace_str(100) == "2:32"
    assert p.pace_str(0) == "" and p.pace_str(None) == ""


def test_a_missing_sport_is_running_and_an_unknown_one_is_refused():
    assert sport_of(None) is Sport.RUN and sport_of("") is Sport.RUN
    assert sport_of("skierg") is Sport.SKIERG and sport_of(Sport.BIKE) is Sport.BIKE
    assert profile().sport is Sport.RUN
    with pytest.raises(ValueError):
        sport_of("rowing")


def test_session_and_protocol_carry_the_sport_and_old_json_is_running():
    s = Session(None, 1, Kind.LACTATE_STEP, datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert s.sport is Sport.RUN
    assert Session(None, 1, Kind.MAX_HR, s.started, sport="bike").sport is Sport.BIKE
    p = library.lactate_step(10.0, 1.0, 5)
    assert p.sport == "run" and p.to_dict()["sport"] == "run"
    old = {k: v for k, v in p.to_dict().items() if k != "sport"}      # stored before sport existed
    assert Protocol.from_dict(old).sport == "run"
    assert Protocol.from_dict({**old, "sport": "bike"}).sport == "bike"


def test_load_is_the_name_for_new_code():
    assert StepRecord(0, 12.5, 1.0, 240).load == 12.5
    assert TimeSample(1.0, speed_kmh=11.0).load == 11.0 and TimeSample(1.0).load is None
    assert Stage(13.0, 1.0, 240).load == 13.0
    p = Protocol("x", "max_hr", [Stage(10, 1, 60)], increment_speed_kmh=0.5)
    assert p.increment_load == 0.5


def test_every_sport_has_its_own_protocols():
    assert library.default_protocols() and library.default_protocols(sport="run")
    assert {p.sport for p in library.default_protocols()} == {"run"}
    assert {p.sport for p in library.default_protocols(sport=Sport.BIKE)} == {"bike"}
    assert {p.sport for p in library.default_protocols(sport="skierg")} == {"skierg"}


def test_stage_text_follows_the_sport():
    assert stage_text(12.0, 1.0) == "12,0 km/h · 1 %" == stage_text(12.0, 1.0, "run")
    assert stage_text(200, 0, Sport.BIKE) == "200 W"
    assert stage_text(202.5, 0, "skierg") == "202 W · 2:00 /500 m"


def _records():
    la = [1.1, 1.0, 1.2, 1.4, 1.9, 2.6, 3.9, 6.2]
    return [StepRecord(i, 9.0 + i, 1.0, 240, hr_end=131 + 8 * i, lactate_mmol=v) for i, v in enumerate(la)]


def test_thresholds_and_zones_carry_the_sport_of_the_test():
    res = lactate.analyze(_records(), 192)
    assert res.sport == "run" and res.lt2.sport == "run" and res.lt2.load == res.lt2.speed_kmh
    assert res.to_dict()["sport"] == "run" and res.lt2.to_dict()["pace"] == res.lt2.pace != ""
    zs = zones.from_lactate(res, 192)
    assert zs and {z.sport for z in zs} == {"run"} and zs[1].load_low == zs[1].speed_low_kmh
    # a result stored before sport existed is running
    d = res.to_dict()
    d.pop("sport")
    for th in d["thresholds"].values():
        th.pop("sport")
    back = lactate.LactateResult.from_dict(d)
    assert back.sport == "run" and back.lt1.sport == "run" and back.lt1.speed_kmh == res.lt1.speed_kmh

    bike = lactate.analyze(_records(), 185, sport=Sport.BIKE)
    assert bike.sport == "bike" and bike.lt2.sport == "bike" and bike.lt2.pace == ""
    bz = zone_systems.convert(zone_systems.Anchors.from_lactate(bike, 185))
    assert bz and {z.sport for z in bz} == {"bike"} and bz[0].pace_high == ""


def test_zones_from_max_hr_belong_to_the_sport_of_that_max_hr():
    a = Athlete(1, "Demo", "Löpare", max_hr=192)
    a.set_max_hr(Sport.BIKE, 184)
    assert a.max_hr_for() == 192 == a.max_hr_for("run") and a.max_hr_for("bike") == 184
    assert a.max_hr_for(Sport.SKIERG) is None          # never borrowed from another sport
    run = zones.from_heart_rate(a.max_hr_for("run"))
    bike = zones.from_heart_rate(a.max_hr_for("bike"), sport="bike")
    assert {z.sport for z in run} == {"run"} and {z.sport for z in bike} == {"bike"}
    assert all(z.estimated for z in bike) and bike[-1].hr_high < run[-1].hr_high
    a.set_max_hr("run", 190)
    a.set_max_hr("bike", None)
    assert a.max_hr == 190 and a.sport_max_hr == {}
