"""The page Testerna, and the zone systems, Garmin box and estimated VO2max in the results view."""
from datetime import date, datetime, timezone

from PySide6.QtWidgets import QLabel

from lopanalys.core.models import Athlete, Session, StepRecord, TestKind, TimeSample
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.results import ResultsView
from lopanalys.ui.views.tests_info import TestsInfoView

DISCLAIMER = "Uppskattat ur testets fart och puls. Ett riktigt VO2max-test med mätning av andningsgaser krävs för att veta."


def _texts(w):
    return " | ".join(lb.text() for lb in w.findChildren(QLabel))


def _ctx(tmp_path):
    return AppContext(store=services.open_store(tmp_path / "db.sqlite"))


def test_tests_info_view_builds_offscreen(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    v = TestsInfoView(ctx)
    qtbot.addWidget(v)
    v.resize(1200, 800)
    v.show()
    assert list(v.cards) == ["lactate", "max_hr", "hr_zones", "gait", "zones", "vo2max"]
    text = _texts(v)
    for s in ("Laktattrappa", "Maxpulstest", "Löpteknikanalys", "Träningszoner", "Uppskattat VO2max",
              "VAD DET MÄTER", "BEGRÄNSNINGAR", "modifierad Dmax", "andningsgaser"):
        assert s in text, s
    assert "assessments." not in text and "ui.tests" not in text
    html = v.html()
    assert "Om testerna" in html and "Laktattrappa" in html and "Löpning" in html
    assert v.grab().width() > 0
    # the other sports: the cards and the printout follow the choice
    assert [v.sport_box.itemText(i) for i in range(v.sport_box.count())] == ["Löpning", "Cykel", "SkiErg"]
    v.sport_box.setCurrentIndex(1)
    assert list(v.cards) == ["bike_lactate", "bike_ramp", "bike_ftp", "bikefit", "bike_zones"]
    text = _texts(v)
    assert "FTP-test 20 minuter" in text and "Bikefit" in text and "Maxpulstest" not in text
    html = v.html()
    assert "Cykel" in html and "Coggans" in html and "Löpteknikanalys" not in html
    v.set_sport("skierg")
    assert v.sport_box.currentText() == "SkiErg" and list(v.cards)[-2:] == ["ski_technique", "skierg_zones"]
    assert "Stakteknik" in _texts(v) and "Stakteknik" in v.html() and "FTP-test" not in v.html()
    v.set_sport("run")
    assert list(v.cards)[0] == "lactate" and "Löpteknikanalys" in v.html()
    ctx.shutdown()


def test_tests_page_is_in_the_sidebar(demo_window):
    w = demo_window
    assert w.nav_buttons["tests"].text() == "Testerna"
    w.go("tests")
    assert w.stack.currentWidget() is w.tests_info
    w.nav_buttons["tests"].click()
    assert w.stack.currentWidget() is w.tests_info


def _lactate_session(ctx, exhausted=True):
    a = ctx.store.save_athlete(Athlete(None, "Test", "Löpare", date(1988, 3, 2), height_cm=180, weight_kg=75,
                                       max_hr=192, resting_hr=55))   # Uth within 15 % of ACSM
    stages = [{"speed_kmh": 8.0 + i, "incline_pct": 1.0, "duration_s": 240, "label": f"Steg {i + 1}"} for i in range(7)]
    s = Session(None, a.id, TestKind.LACTATE_STEP, datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc),
                protocol_name="Laktattrappa", protocol={"stages": stages},
                result={"exhaustion_t_s": 1700.0} if exhausted else {})
    s = ctx.store.save_session(s)
    recs = [StepRecord(i, 8.0 + i, 1.0, 240, hr_end=120 + 9 * i,
                       lactate_mmol=round(0.9 + 0.03 * max(0.0, 8.0 + i - 9.5) ** 2.6, 2)) for i in range(7)]
    ctx.store.save_records(s.id, recs)
    if exhausted:   # VO2max only from a test that met the max criteria: a plateau, near the age max
        ctx.store.save_samples(s.id, [TimeSample(float(k), min(176, 110 + k // 20)) for k in range(1765)])
    return a, s


def test_results_view_zone_system_watch_box_and_vo2max(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    _, s = _lactate_session(ctx)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.resize(1400, 900)
    v.show()
    v.load_session(s.id)
    assert v.zone_system.count() == 6 and v.zone_system.currentData() == "garmin"
    assert v.zones_table.rowCount() == 5 and v.zone_est.text() == ""
    lt1, lt2 = round(v.lactate.raw.lt1.hr), round(v.lactate.raw.lt2.hr)
    # The watch box: zone 2 ends at LT1, zone 4 at LT2, with LTHR and threshold pace.
    assert v.garmin["lthr"] == lt2 and v.garmin["zones"][1]["hr_high"] == lt1 and v.garmin["zones"][3]["hr_high"] == lt2
    v._toggle_watch_more()                 # "Läs mer": the zone limits and the why
    watch = _texts(v.watch_card)
    assert "Fyll i klockan" in watch and f"{lt2} slag/min" in watch and "Tröskeltempo" in watch
    assert f"{v.garmin['zones'][1]['hr_low']}–{lt1} slag/min" in watch and "Varför visar klockan andra zoner?" in watch
    # Estimated VO2max: the word, the interval and the disclaimer, never a lone number.
    vo2 = _texts(v.vo2_card)
    assert "Uppskattat VO2max" in vo2 and DISCLAIMER in vo2 and "±10 %" in vo2 and "ACSM" in vo2
    assert f"{services.vo2max_card(v.vo2)['value']} ml/kg/min" in vo2
    # Every system can be chosen; the table follows.
    for i in range(v.zone_system.count()):
        v.zone_system.setCurrentIndex(i)
        key = v.zone_system.currentData()
        assert v.zones_table.rowCount() == len(v.zone_list) > 0, key
        assert all(z["raw"].system == key for z in v.zone_list)
    v.zone_system.setCurrentIndex(v.zone_system.findData("friel"))
    assert v.zones_table.rowCount() == 7 and v.zones_table.item(0, 1).text().startswith("<")
    # The choice is saved with the session and comes back.
    v.save()
    saved = ctx.store.session(s.id).result
    assert saved["zone_system"] == "friel" and saved["garmin"]["lthr"] == lt2 and saved["vo2max"]["best"]["method"]
    v.load_session(s.id)
    assert v.zone_system.currentData() == "friel"
    html = v.html()
    assert "7 zoner · Friel" in html and "Fyll i klockan" in html and DISCLAIMER in html
    ctx.shutdown()


def test_results_view_without_thresholds_says_estimated(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    a = ctx.store.save_athlete(Athlete(None, "Max", "Puls", date(1990, 1, 1), max_hr=188, resting_hr=52))
    s = ctx.store.save_session(Session(None, a.id, TestKind.MAX_HR, datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc),
                                       protocol_name="Maxpuls", protocol={"stages": []}))
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.zone_est.text() == "Uppskattade ur maxpuls" and v.zones_table.rowCount() == 5
    assert v.garmin["method"] == "max" and v.garmin["lthr"] is None
    v._toggle_watch_more()
    assert "uppskattade ur maxpuls" in _texts(v.watch_card).lower()
    assert v.vo2["best"]["method"] == "uth_sorensen" and DISCLAIMER in _texts(v.vo2_card)
    v.zone_system.setCurrentIndex(v.zone_system.findData("polar"))
    assert v.zone_est.text() == ""                     # Polar is defined by max HR: not an estimate
    ctx.shutdown()


def test_submaximal_test_without_a_known_max_does_not_invent_one(qtbot, tmp_path):
    """No max HR on the athlete and the test stopped before the limit: the highest heart rate of the
    test is not used as max HR for VO2max, and not given as max HR for the watch."""
    ctx = _ctx(tmp_path)
    a, s = _lactate_session(ctx, exhausted=False)
    a.max_hr = a.resting_hr = None
    ctx.store.save_athlete(a)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.max_hr == 174 and v.vo2 is None
    assert v.garmin["max_hr"] is None and v.garmin["lthr"] and v.garmin["zones"][4]["hr_high"] is None
    v._toggle_watch_more()
    assert "inte mätt" in _texts(v.watch_card) and "174 slag/min" not in _texts(v.watch_card)
    ctx.shutdown()


def test_results_view_with_nothing_to_compute(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    a = ctx.store.save_athlete(Athlete(None, "Ingen", "Data"))
    s = ctx.store.save_session(Session(None, a.id, TestKind.GAIT, datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)))
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.zone_list == [] and v.garmin is None and v.vo2 is None
    assert "maxpuls" in _texts(v.watch_card) and "uppskatta" in _texts(v.vo2_card)
    assert "Uppskattat VO2max" not in v.html()
    ctx.shutdown()


def test_services_zone_systems_and_vo2max():
    assert [k for k, _ in services.zone_system_choices()][0] == "garmin"
    assert services.zone_system_info("nonsense")["key"] == "garmin"
    zl = services.zones(None, 190, 50, "garmin")
    assert len(zl) == 5 and all(z["estimated"] for z in zl)
    assert services.zones(None, 190, 50, "nonsense") == []
    assert [c["key"] for c in services.zone_comparison(None, 190, 50)] == ["seiler", "olympiatoppen", "garmin"]
    assert services.garmin_setup(None, None, None) is None and services.garmin_card(None) is None
    assert services.estimate_vo2max([], None, None) is None and services.vo2max_card(None) is None
    assert services.estimate_vo2max([], 190, 50)["best"]["method"] == "uth_sorensen"
    assert len(services.tests_info()) == 6 and len(services.tests_info("bike")) == 5
