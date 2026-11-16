"""The descriptions of the tests: complete in both languages, and true to the methods in the code."""
import pytest

from lopanalys.assessments import catalog, lactate, power
from lopanalys.core.i18n import set_language, t
from lopanalys.core.sport import Sport


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.mark.parametrize("lang", ["sv", "en"])
@pytest.mark.parametrize("sport", ["run", "bike", "skierg", "swim"])
def test_every_test_has_every_field(lang, sport):
    set_language(lang)
    info = catalog.tests_info(sport)
    assert [x["key"] for x in info] == list(catalog.SPORT_TESTS[Sport(sport)])
    assert catalog.tests_info() == catalog.tests_info("run")                      # running when none is given
    for test in info:
        assert test["title"] and "assessments." not in test["title"]
        assert [f["key"] for f in test["fields"]] == list(catalog.FIELDS)
        for f in test["fields"]:
            assert f["label"] and f["text"] and "assessments." not in f["label"] + f["text"], (test["key"], f["key"])
            assert len(f["text"]) <= 420, (test["key"], f["key"], len(f["text"]))    # short enough for one A4


def test_texts_name_the_methods_the_program_uses():
    by = {x["key"]: {f["key"]: f["text"] for f in x["fields"]} for x in catalog.tests_info()}
    # The default threshold methods in lactate.py are the ones the sheet promises.
    assert lactate.LT1_METHODS[0] == "bsln_1_0" and "1,0 mmol/l" in by["lactate"]["standard"]
    assert lactate.LT2_METHODS[0] == "mod_dmax" and "modifierad Dmax" in by["lactate"]["standard"]
    assert "Olympiatoppen" in by["zones"]["standard"] and "Garmin" in by["zones"]["get"]
    assert "ACSM" in by["vo2max"]["standard"]
    assert t("assessments.vo2max.disclaimer") in by["vo2max"]["limits"]
    assert "två dimensioner" in by["gait"]["limits"] and "uppskatta" in by["vo2max"]["measures"]


def test_bike_and_skierg_texts_name_the_methods_the_program_uses():
    by = {x["key"]: {f["key"]: f["text"] for f in x["fields"]}
          for sport in ("bike", "skierg") for x in catalog.tests_info(sport)}
    assert power.FTP_FROM_20MIN == 0.95 and "95 %" in by["bike_ftp"]["standard"]
    assert power.FTP_FROM_MAP == 0.75 and "75 %" in by["bike_ramp"]["standard"]
    borders = [round(hi * 100) for _, _, hi in power.COGGAN if hi]
    assert borders == [55, 75, 90, 105, 120, 150] and "121–150" in by["bike_ftp"]["standard"]
    assert "modifierad Dmax" in by["bike_lactate"]["standard"] and "modifierad Dmax" in by["skierg_lactate"]["standard"]
    assert "33–43°" in by["bikefit"]["standard"] and "erfarenhetsvärden" in by["ski_technique"]["standard"]
    assert "2,80" in by["skierg_zones"]["standard"] and "FTP räknas inte" in by["skierg_ramp"]["standard"]
    # no running words on the other sports' sheets
    for key, fields in by.items():
        assert "km/h" not in " ".join(fields.values()) and "löpband" not in fields["equipment"], key
