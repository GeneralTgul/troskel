"""Puls- och fartzoner in the app: the wizard, the technique rule (60 s of even speed and
incline per stage), the result page, the saved result, the report and the collected report."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from lopanalys.assessments import library
from lopanalys.assessments.protocol import Phase
from lopanalys.assessments.runner import TestRunner
from lopanalys.core.bus import Bus
from lopanalys.core.models import Athlete, Session, StepRecord, TestKind
from lopanalys.devices import sim
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.new_test import KINDS
from lopanalys.ui.views.results import ResultsView

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
RULE = "Löpteknik kräver jämn fart och lutning i minst 60 s per steg."


class _Clock:
    def __init__(self, mc):
        self.mc = mc

    def now(self):
        return self.mc()

    def sleep(self, dt):
        self.mc.advance(dt)


def _run(protocol, ramp_stages=9):
    """One whole test with the simulated treadmill and runner: (runner, exhaustion time)."""
    bus, mc = Bus(), sim.ManualClock(0.0)
    tm = sim.SimTreadmill(bus, clock=mc, autorun=False, seed=1)
    hr = sim.SimHeartRate(bus, treadmill=tm, clock=mc, autorun=False, seed=1)
    tm.connect()
    hr.connect()
    r = TestRunner(protocol, tm, hr, bus, clock=_Clock(mc), threaded=False)
    r.start()

    def tick():
        mc.advance(0.25)
        tm.tick()
        hr.tick()
        r.tick()
    while not (r.phase == Phase.RUNNING and r._idx >= len(protocol.stages) - 1 + ramp_stages):
        tick()
    r.exhausted()
    while not r.done:
        tick()
    return r


def _saved_test(ctx: AppContext, athlete: Athlete | None = None) -> tuple[Athlete, Session]:
    a = athlete or ctx.store.save_athlete(Athlete(None, "Bo", "Ek", date(1988, 4, 12), height_cm=180, resting_hr=55))
    # 4 min steps (the choice beside the 3 min default): the simulated runner's VO2max from the steps lies
    # right at the 15 % limit against Uth-Sørensen with 3 min ones
    p = library.hr_zones_for_level("recreational", stage_s=240)
    r = _run(p)
    end = r.samples[-1].t_s
    s = Session(None, a.id, TestKind.HR_ZONES, T0, T0 + timedelta(seconds=end), protocol_name=p.name,
                protocol=p.to_dict(), treadmill_mode="sim",
                result={"plan_kind": "hr_zones", "exhaustion_t_s": r.exhaustion_t_s})
    ctx.store.db.save_test(s, r.records, r.samples, warmup_records=r.warmup_records)
    return a, s


def _to_kind(win, kind):
    nt = win.new_test
    win.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    nt._next()
    nt.kind_group.button([k for k, *_ in KINDS].index(kind)).click()
    nt._next()
    return nt


def test_wizard_offers_the_test_and_builds_the_protocol(demo_window, qtbot):
    assert ("hr_zones", "gauge", "ui.kind.hr_zones") in KINDS
    nt = _to_kind(demo_window, "hr_zones")
    card = nt.kind_group.button(len(KINDS) - 1)
    assert (card.title, card.desc) == ("Puls- och fartzoner", "Utan laktat: fart för dina zoner och maxpuls")
    p = nt.protocol
    assert p.kind == "hr_zones" and p.builder == "hr_zones" and p.open_ended
    assert nt.p_n.value() == 5 and nt.p_len.value() == 180 and nt.p_incline.value() == 1.0
    assert not nt.pause_field.isVisible() and nt.n_field.isVisible()
    from PySide6.QtWidgets import QLabel
    assert nt.warm_field.findChildren(QLabel)[-1].text() == "Ingår i testet."      # no word about a lactate sample
    assert not any(s.sample_lactate for s in p.stages) and {s.incline_pct for s in p.stages} == {1.0}
    assert nt.p_table.rowCount() == 7                      # warm-up, five steps and the ramp's first stage
    assert [nt.lib.itemText(i) for i in range(nt.lib.count())][0] == "Puls- och fartzoner, motionär"
    nt.p_n.setValue(4)
    nt.p_start.setValue(9.0)
    work = [s for s in nt.protocol.stages if not s.is_warmup]
    assert [s.speed_kmh for s in work] == [9.0, 10.0, 11.0, 12.0, 12.5] and work[-1].duration_s == 60
    assert nt.plan().kind == "hr_zones" and nt.protocol.name == "Puls- och fartzoner, motionär"
    assert nt._ok_to_continue() == (True, "")
    # not filmed unless the coach ticks the box: no camera step, and no gait values asked of the stages
    assert nt.film_field.isVisible() and not nt.film_chk.isChecked() and not nt.films()
    assert not any(s.record_gait for s in nt.protocol.stages) and len(nt.stepper.steps) == 5
    nt.film_chk.setChecked(True)
    assert nt.films() and all(s.record_gait for s in nt.protocol.stages) and len(nt.stepper.steps) == 6
    assert "Kamera" in nt.stepper.steps
    nt.film_chk.setChecked(False)
    nt._next()
    assert nt.stack.currentIndex() == 4 and nt.camera is None


def test_camera_step_only_for_tests_that_film(demo_window, qtbot):
    """A max HR test, and a lactate test that is not combined, has no camera step."""
    w = demo_window
    nt = _to_kind(w, "max_hr")
    assert nt.header.subtitle.text().startswith("Några korta steg")
    assert not nt.films() and nt.stepper.steps == ["Idrottare", "Testtyp", "Protokoll", "Band och puls", "Kontroll"]
    assert nt.stack.currentIndex() == 2 and nt.stepper.current == 2
    nt._next()                                             # straight to Band och puls
    assert nt.stack.currentIndex() == 4 and nt.stepper.current == 3
    assert nt.preview is None and nt.camera is None        # no preview was started
    nt.back.click()                                        # and back again past the camera
    assert nt.stack.currentIndex() == 2
    nt._next()
    qtbot.waitUntil(lambda: nt.tm_connected and nt.hr_connected, timeout=8000)
    nt._next()                                             # Kontroll
    assert nt.stack.currentIndex() == 5 and nt.stepper.current == 4
    assert not nt.framing_card.isVisible() and nt._framing_label is None
    rows = [nt.check_box.itemAt(i).layout().itemAt(1).widget().text() for i in range(nt.check_box.count())]
    assert len(rows) == 3 and not any("kamera" in r.lower() or "ramen" in r.lower() for r in rows), rows
    assert nt.ctl is not None and nt.ctl.plan.camera is None and nt.ctl.analyzer is None
    assert nt._ok_to_continue() == (True, "")
    nt._teardown_ctl()

    nt = _to_kind(w, "lactate")
    assert len(nt.stepper.steps) == 5 and "Kamera" not in nt.stepper.steps

    nt = _to_kind(w, "combined")                           # filmed: all six steps, with preview and frame
    assert nt.films() and nt.stepper.steps[3] == "Kamera" and len(nt.stepper.steps) == 6
    nt._next()
    assert nt.stack.currentIndex() == 3
    qtbot.waitUntil(lambda: nt.cam_list.count() >= 2 and nt.camera is not None, timeout=5000)
    qtbot.waitUntil(lambda: nt.preview is not None, timeout=5000)
    nt._next()
    qtbot.waitUntil(lambda: nt.tm_connected and nt.hr_connected, timeout=8000)
    nt._next()
    assert nt.stack.currentIndex() == 5 and nt.framing_card.isVisible() and nt._framing_label is not None
    rows = [nt.check_box.itemAt(i).layout().itemAt(1).widget().text() for i in range(nt.check_box.count())]
    assert len(rows) == 6
    nt.back.click()
    nt.back.click()
    assert nt.stack.currentIndex() == 3                    # backwards the camera step is there too
    nt.reset()


def test_wizard_starts_from_the_athletes_earlier_test(demo_window, qtbot):
    ctx = demo_window.ctx
    erik = next(a for a in ctx.store.athletes() if a.first_name == "Erik")
    s = ctx.store.history(erik.id)[0]
    s.result = dict(s.result or {}, lt2={"speed_kmh": 13.4, "hr": 170})
    ctx.store.save_session(s)
    assert services.estimated_threshold_kmh(ctx.store, erik) == 13.4
    nt = demo_window.new_test
    demo_window.go("new_test")
    nt.preselect(erik)
    nt.kind_group.button([k for k, *_ in KINDS].index("hr_zones")).click()
    nt._next()
    assert nt.lib.itemText(0) == "Puls- och fartzoner, utifrån förra testet"
    assert nt.p_start.value() == 9.0                       # 13.4 - 0.5 - 4 steps, to the nearest half


def test_technique_rule_locks_next_with_the_reason(demo_window, qtbot):
    """A max HR test is not bent to fit the film. Technique needs 60 s per stage at an
    even speed and an unchanged incline; else the tests are done one at a time."""
    nt = _to_kind(demo_window, "max_hr_gait")
    names = [nt.lib.itemText(i) for i in range(nt.lib.count())]
    assert names == ["Maxpuls, lutningstrappa", "Maxpuls, fartrappa"]      # the max test's own defaults
    ok, why = nt._ok_to_continue()
    assert not ok and why.startswith(RULE) and "gör testen var för sig" in why
    assert not nt.next.isEnabled() and nt.hint.text() == why
    nt.lib.setCurrentIndex(1)
    assert nt.p_len.value() == 30                          # the wizard shows the library's own 30 s
    assert [s.duration_s for s in nt.protocol.stages if not s.is_warmup] == [30]
    assert not nt._ok_to_continue()[0]
    nt.p_len.setValue(60)
    assert nt._ok_to_continue() == (True, "") and nt.next.isEnabled()
    nt.p_len.setValue(45)
    assert nt._ok_to_continue()[1].startswith(RULE)

    nt = _to_kind(demo_window, "max_hr")                   # the plain max test is never touched
    assert nt.lib.currentText() == "Maxpuls, lutningstrappa" and nt._ok_to_continue() == (True, "")

    nt = _to_kind(demo_window, "combined")
    assert nt._ok_to_continue() == (True, "")              # lactate steps of 4 min on 1 %
    nt.p_len.setValue(50)
    assert nt._ok_to_continue()[1].startswith(RULE) and not nt.next.isEnabled()
    nt.p_len.setValue(240)
    assert nt._ok_to_continue() == (True, "")
    nt.lib.setCurrentIndex(nt.lib.findText("Laktattrappa till max"))       # ends in an incline ramp
    assert nt._ok_to_continue()[1].startswith(RULE)

    nt = _to_kind(demo_window, "hr_zones")
    assert nt._ok_to_continue() == (True, "")


def test_result_page_report_and_saved_result(qapp, qtbot):
    ctx = AppContext(demo=True)
    a, s = _saved_test(ctx)
    rv = ResultsView(ctx)
    rv.show()
    rv.load_session(s.id)
    assert rv.plan_kind() == "hr_zones" and rv.hr_speed is not None and rv.hr_speed.ok
    assert rv.hr_chart.isVisible() and not rv.chart.isVisible() and not rv.methods_row.isVisible()
    assert rv.curve_card.title.text() == "Puls mot fart"
    assert rv.table.isColumnHidden(4) and not rv.table.isColumnHidden(6)
    assert rv.table.isColumnHidden(5) and rv.tabs.tabText(0) == "Puls och zoner"
    assert len(rv.hr_chart.points) == 5 and rv.hr_chart.line
    assert rv.t_lt2.title == "Samband puls–fart" and "5 steg" in rv.t_lt2.sub
    assert rv.t_lt1.title == "Lugn fart" and rv.t_lt1.value != "–"
    assert rv.table.item(0, 6).text() not in ("", "–")
    # Garmin's five zones in % of max HR, with speed from the line, marked as estimated without lactate
    assert rv.zone_system.currentData() == "garmin" and len(rv.zone_list) == 5
    z3 = rv.zone_list[2]
    assert z3["speed_low"] and z3["speed_high"] and z3["speed_basis"] == "hr"
    assert "Fart ur puls, inte ur laktat" in rv.zone_est.text() and "Uppskattade ur maxpuls" in rv.zone_est.text()
    assert "km/h" in rv.zones_table.item(2, 2).text()
    # the ramp's one-minute stages are too short for the final-stage equations: VO2max comes from the
    # settled steps drawn out to max HR, and only because the peak met the max criteria
    assert rv._to_exhaustion() and rv.max_view.likely_max
    assert rv.vo2 and rv.vo2["best"]["method"] == "hr_extrapolation"
    rv.save()
    saved = ctx.store.session(s.id).result
    assert saved["hr_speed"]["ok"] and saved["hr_speed"]["quality"] == "good" and saved["hr_speed"]["est_threshold_kmh"]
    assert saved["zones"][2]["speed_low_kmh"] and saved["zones"][2]["speed_basis"] == "hr"
    assert ctx.store.athlete(a.id).max_hr == rv.max_hr     # measured to the limit: the athlete's max HR
    # the next test starts from this one
    assert services.hr_zones_protocols(ctx.store, a)[0].name.endswith("utifrån förra testet")

    html = rv.html()
    for text in ("Puls- och fartzoner", "Puls mot fart", "Fart ur puls, inte ur laktat", "Uppskattat utan laktat",
                 "Stabil puls", "Maxpuls", "Uppskattat VO2max", "Lugn fart (Z2)", "1 % lutning"):
        assert text in html, text
    assert "Laktat och puls" not in html and "report.lactate" not in html
    data = services.report_data(a, ctx.store.session(s.id), rv.records, None, rv.zone_list, rv.max_view, None, None,
                                {}, rv.samples, hr_speed=services.hr_speed_dict(rv.hr_speed, rv.max_hr))
    assert data.kinds == ["hr_zones", "max_hr"] and data.kind == "hr_zones"

    # another zone system keeps the speeds
    rv.zone_system.setCurrentIndex(rv.zone_system.findData("olympiatoppen"))
    assert any(z["speed_low"] or z["speed_high"] for z in rv.zone_list)

    coll = services.render_collected_report(ctx.store, a, [ctx.store.session(s.id)], {})
    assert "Puls mot fart" in coll and "Fart ur puls, inte ur laktat" in coll
    ctx.shutdown()


def test_collected_report_prefers_lactate_thresholds_for_the_zones(qapp, qtbot):
    ctx = AppContext(demo=True)
    a, s = _saved_test(ctx)
    lac = Session(None, a.id, TestKind.LACTATE_STEP, T0 - timedelta(days=1), T0 - timedelta(days=1) + timedelta(minutes=30),
                  protocol_name="Laktattrappa")
    recs = [StepRecord(i, 9.0 + i, 1.0, 240, hr_end=135 + 8 * i, lactate_mmol=la)
            for i, la in enumerate([1.1, 1.0, 1.2, 1.5, 2.2, 3.4, 5.6])]
    ctx.store.db.save_test(lac, recs, [])
    html = services.render_collected_report(ctx.store, a, [ctx.store.session(lac.id), ctx.store.session(s.id)], {})
    assert "Laktat och puls" in html and "Puls mot fart" in html
    assert "Zonerna bygger på dina trösklar" in html and "Zonernas gränser är procent av din maxpuls" not in html
    ctx.shutdown()


def test_stop_before_the_ramp_is_not_a_max(qapp, qtbot):
    ctx = AppContext(demo=True)
    a = ctx.store.save_athlete(Athlete(None, "Li", "Al", height_cm=170))
    p = library.hr_zones(8.0)
    full = _run(p, ramp_stages=1)
    recs = [r for r in full.records if r.index <= 4]       # stopped on the panel after four steps
    end = 10 + 600 + 4 * 240.0
    s = Session(None, a.id, TestKind.HR_ZONES, T0, T0 + timedelta(seconds=end + 65), protocol_name=p.name,
                protocol=p.to_dict(), result={"plan_kind": "hr_zones", "exhaustion_t_s": end})
    ctx.store.db.save_test(s, recs, [x for x in full.samples if x.t_s <= end], warmup_records=full.warmup_records)
    rv = ResultsView(ctx)
    rv.load_session(s.id)
    assert rv.hr_speed.ok and not rv.hr_speed.ramp_reached and not rv._to_exhaustion()
    assert "före trappan till max" in rv.warn.text()
    rv.save()
    assert ctx.store.athlete(a.id).max_hr is None          # the highest heart rate is not written as max HR
    assert rv.vo2 is None or rv.vo2["best"]["method"] != "acsm_final_stage"
    ctx.shutdown()


def test_history_and_tests_page_know_the_test(demo_window, qtbot):
    ctx = demo_window.ctx
    erik = next(a for a in ctx.store.athletes() if a.first_name == "Erik")
    _, s = _saved_test(ctx, erik)
    demo_window.results.load_session(s.id)
    demo_window.results.save()
    h = demo_window.history
    demo_window.go("history")
    assert h.kind.findData("hr_zones") >= 0 and h.kind.itemText(h.kind.findData("hr_zones")) == "Puls- och fartzoner"
    for i in range(h.alist.count()):
        if "Erik" in h.alist.itemWidget(h.alist.item(i)).findChildren(type(h.title))[0].text():
            h.alist.setCurrentRow(i)
    rows = [[h.table.item(r, c).text() for c in range(4)] for r in range(h.table.rowCount())]
    mine = next(r for r in rows if r[1] == "Puls- och fartzoner")
    assert mine[3].startswith("Maxpuls ") and "km/h" in mine[3]
    assert "hr_zones" in [x["key"] for x in services.tests_info()]
