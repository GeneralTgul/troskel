"""Cycling and SkiErg in the app: the sport in the wizard, tests and protocols per
sport, the trainer or the PM5 in the device step, watt and stroke rate live, and the result and the
report in watt (W/kg and FTP on the bike, pace per 500 m on the SkiErg). All with the simulators."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import Qt

from lopanalys.assessments import library_power
from lopanalys.assessments.protocol import Phase
from lopanalys.assessments.runner import TestRunner
from lopanalys.core.bus import Bus
from lopanalys.core.i18n import t
from lopanalys.core.models import Athlete, Session, TestKind
from lopanalys.devices import sim
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.results import ResultsView

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
LACTATE = [1.1, 1.0, 1.2, 1.6, 2.3, 3.6, 5.9]


class _Clock:
    def __init__(self, mc):
        self.mc = mc

    def now(self):
        return self.mc()

    def sleep(self, dt):
        self.mc.advance(dt)


def _run(protocol, erg_cls, lactate=None, **erg_kw):
    """One whole test with a simulated trainer or SkiErg: the finished runner."""
    bus, mc = Bus(), sim.ManualClock(0.0)
    erg = erg_cls(bus, clock=mc, autorun=False, seed=1, **erg_kw)
    hr = sim.SimHeartRate(bus, treadmill=erg, clock=mc, autorun=False, seed=1)
    erg.connect()
    hr.connect()
    r = TestRunner(protocol, erg, hr, bus, clock=_Clock(mc), threaded=False)
    r.start()
    for _ in range(40000):
        mc.advance(0.25)
        erg.tick()
        hr.tick()
        r.tick()
        if r.done:
            break
    assert r.done
    if lactate:
        r.enter_lactate(r.warmup_records[0].index, 1.2)
        for rec, la in zip(r.records, lactate):
            r.enter_lactate(rec.index, la)
    return r


def _save(ctx: AppContext, a: Athlete, r: TestRunner, protocol, sport: str, kind: TestKind, mode: str = "sim",
          when: datetime = T0) -> Session:
    end = r.samples[-1].t_s
    res = {"weight_kg": a.weight_kg, "stage_stats": r.stage_stats}
    if r.exhaustion_t_s is not None:
        res["exhaustion_t_s"] = r.exhaustion_t_s
    if r.drag_factor is not None:              # as live_controller saves it
        res["drag_factor"] = {"value": r.drag_factor, "source": "pm5"}
    s = ctx.store.save_session(Session(None, a.id, kind, when, when + timedelta(seconds=end), protocol.name,
                                       protocol.to_dict(), mode, result=res, sport=sport))
    ctx.store.save_records(s.id, r.warmup_records + r.records)
    ctx.store.save_samples(s.id, r.samples)
    return s


@pytest.fixture
def ctx(qapp):
    c = AppContext(demo=False)
    yield c
    c.shutdown()


@pytest.fixture
def rider(ctx):
    return ctx.store.save_athlete(Athlete(None, "Bo", "Ek", date(1988, 4, 12), height_cm=180, weight_kg=78.0,
                                          max_hr=190, resting_hr=52))


def _text(html: str) -> str:
    html = re.sub(r"<(style|svg)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ------------------------------------------------------------------------------------- the wizard
def test_sport_is_chosen_first_and_decides_tests_protocols_and_devices(qapp, qtbot, monkeypatch):
    from lopanalys.ui.app import build
    monkeypatch.setattr(services, "discover_sources", lambda *a, **k: [])
    _, win = build([], qapp)
    try:
        nt = win.new_test
        assert nt.sport_field.isVisibleTo(nt) or nt.sport_box.count() == 3
        assert [nt.sport_box.itemText(i) for i in range(3)] == ["Löpning", "Cykel", "SkiErg"]
        assert len(nt.kind_group.buttons()) == 6 and nt.mode == "ftms"          # running, as before
        assert [b.title for b in nt.mode_group.buttons()][:2] == ["Bluetooth (FTMS)", "QZ"]

        nt.set_sport("bike")
        assert [k for k, _, _ in nt._kinds] == ["lactate", "max_hr", "ftp", "bikefit"] and nt.kind == "lactate"
        assert [b.title for b in nt.kind_group.buttons()] == ["Laktattrappa", "Ramptest", "FTP 20 minuter", "Bikefit"]
        assert [m for m, _ in nt.modes] == ["ftms", "manual", "sim"] and nt.mode == "ftms"
        assert nt.tm_card.title.text() == "Trainern" and "Zwift" in nt.tm_sub.text()
        assert nt.mode_group.buttons()[-1].title == "Övning: simulator" and "Trainer och puls" in nt.stepper.steps
        assert not nt.films() and nt.steps() == [0, 1, 2, 4, 5]                 # never a camera step
        p = nt.protocol
        assert p.sport == "bike" and p.builder == "bike_lactate_step"
        assert [s.duration_s for s in p.stages if not s.is_warmup][0] == 300
        assert nt.p_start.suffix() == " W" and nt.p_start.value() == 100 and nt.p_step.value() == 25
        assert not nt.incline_field.isVisibleTo(nt.incline_field.parentWidget()) or nt.incline_field.isHidden()
        assert nt.p_table.horizontalHeaderItem(1).text() == "W" and nt.p_table.isColumnHidden(2)
        nt.p_start.setValue(120)                                               # the coach edits the start watt
        assert [s.load for s in nt.protocol.stages if not s.is_warmup][:2] == [120, 145]
        nt.kind_group.button(2).click()                                        # FTP 20 minutes
        assert nt.kind == "ftp" and nt.protocol.kind == "ftp" and nt.protocol.stages[-1].free
        assert nt.step_field.isHidden() and nt.n_field.isHidden() and "fritt" in nt.p_table.item(1, 1).text()

        nt.set_sport("skierg")
        assert [b.title for b in nt.kind_group.buttons()] == ["Laktattrappa", "Ramptest", "Stakteknik"]
        assert nt.kind == "lactate"
        assert [m for m, _ in nt.modes] == ["pm5", "manual", "sim"] and nt.mode == "pm5"
        assert nt.tm_card.title.text() == "SkiErgen" and nt.mode_group.buttons()[0].title == "Bluetooth (Concept2 PM5)"
        assert nt.protocol.sport == "skierg" and nt.protocol.lactate_pause_s == 30
        assert not nt.p_table.isColumnHidden(3) and nt.p_table.horizontalHeaderItem(3).text() == "/500 m"
        assert re.fullmatch(r"\d:\d\d", nt.p_table.item(1, 3).text())

        nt.set_sport("run")                                                    # and back: nothing is left behind
        assert len(nt.kind_group.buttons()) == 6 and nt.p_start.suffix() == " km/h" and nt.protocol.sport == "run"
        assert nt.tm_card.title.text() == "Löpbandet" and [m for m, _ in nt.modes] == ["ftms", "qz", "manual", "sim"]
        assert not nt.p_table.isColumnHidden(2) and nt.p_table.horizontalHeaderItem(1).text() == "km/h"
    finally:
        win.close()


def test_body_weight_is_asked_for_before_a_bike_test(qapp, qtbot, monkeypatch):
    from lopanalys.ui.views.new_test import NewTestView
    monkeypatch.setattr(services, "discover_sources", lambda *a, **k: [])
    c = AppContext(demo=False)
    try:
        a = c.store.save_athlete(Athlete(None, "Ny", "Cyklist", max_hr=188))
        nt = NewTestView(c)
        nt.athlete = a
        nt.go(1)
        nt.set_sport("bike")
        asked = []
        monkeypatch.setattr(nt, "ask_weight", lambda: asked.append(1) or None)
        nt._next()
        assert asked == [1] and nt.stack.currentIndex() == 1 and nt.hint.text() == t("ui.wiz.need_weight")
        monkeypatch.setattr(nt, "ask_weight", lambda: 81.5)
        nt._next()
        assert nt.stack.currentIndex() == 2 and c.store.athlete(a.id).weight_kg == 81.5
        plan = nt.plan()
        assert plan.sport == "bike" and c.new_session(plan).result == {"weight_kg": 81.5}
        nt.go(1)
        nt.set_sport("skierg")                    # the SkiErg shows watt and pace, not W/kg: no question
        nt.athlete.weight_kg = None
        assert not nt.needs_weight()
    finally:
        c.shutdown()


def test_demo_bike_test_runs_live_with_watt_and_cadence(demo_window, qtbot):
    w = demo_window
    nt = w.new_test
    w.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    if not nt.athlete.weight_kg:
        nt.athlete.weight_kg = 75.0
    nt._next()
    nt.set_sport("bike")
    nt._next()
    assert nt.stack.currentIndex() == 2 and nt.protocol.sport == "bike"
    nt.p_warm.setValue(0)
    nt._next()
    assert nt.stack.currentIndex() == 4                                        # the camera step is skipped
    qtbot.waitUntil(lambda: nt.tm_connected and nt.hr_connected, timeout=8000)
    assert w.ctx.devices.ergometer_sport == "bike"
    nt._next()
    assert nt.stack.currentIndex() == 5 and not nt.framing_card.isVisibleTo(nt)
    nt._next()
    op = w.operator
    assert op is not None and op.tv is not None
    board = op.tv.board
    assert board.power and board.t_speed.title == "Effekt" and board.t_speed.unit == "W"
    assert board.t_cadence.unit == "varv/min" and board.power_chart.isVisibleTo(board)
    op.start()
    qtbot.waitUntil(lambda: op.ctl.state is not None and op.ctl.state.phase == Phase.RUNNING, timeout=15000)
    qtbot.waitUntil(lambda: board.t_speed.value.isdigit() and int(board.t_speed.value) >= 90
                    and board.t_cadence.value.isdigit() and int(board.t_cadence.value) >= 75, timeout=10000)
    assert 80 <= int(board.t_speed.value) <= 110 and "W/kg" in board.t_speed.sub
    assert board.t_incline.value == "100" and 75 <= int(board.t_cadence.value) <= 95
    assert op.ctl.state.sport == "bike" and op.ctl.state.target_load == 100
    assert "100 W" in op.lac_for.text()
    op.tv.resize(1920, 1080)
    assert op.tv.grab().width() == 1920
    op.stop()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.results, timeout=10000)
    s = w.results.session
    assert s.sport.value == "bike" and s.result.get("weight_kg") and w.ctx.store.session(s.id).sport.value == "bike"


def test_demo_skierg_test_is_put_on_the_monitor_and_the_operator_page_says_so(demo_window, qtbot):
    w = demo_window
    nt = w.new_test
    w.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    nt._next()
    nt.set_sport("skierg")
    nt._next()
    nt.p_warm.setValue(0)
    nt._next()
    qtbot.waitUntil(lambda: nt.tm_connected and nt.hr_connected, timeout=8000)
    nt._next()
    nt._next()
    op = w.operator
    assert op.device_note.isHidden()
    op.start()
    ski = w.ctx.devices.ergometer
    qtbot.waitUntil(lambda: op.device_note.text() != "", timeout=5000)
    assert ski.programmed and len(ski.workout) == 7 and ski.workout[0].rest_s == 30 and ski.workout[0].target_w == 80
    assert op.device_note.text().startswith("Testet är inlagt som pass på monitorn") and not op.device_note.isHidden()
    qtbot.waitUntil(lambda: op.ctl.state is not None and op.ctl.state.phase == Phase.RUNNING, timeout=15000)
    board = op.tv.board
    qtbot.waitUntil(lambda: board.t_speed.value.isdigit() and int(board.t_speed.value) > 50, timeout=10000)
    assert board.t_cadence.unit == "drag/min" and "/500 m" in board.t_incline.unit
    op.stop()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.results, timeout=10000)


# ------------------------------------------------------------------------------------- results and report
def test_bike_lactate_result_and_report_are_in_watt_and_w_per_kg(ctx, rider, qtbot):
    p = library_power.bike_lactate_step_for_level("recreational")
    r = _run(p, sim.SimBike, LACTATE)
    s = _save(ctx, rider, r, p, "bike", TestKind.LACTATE_STEP)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.t_lt2.unit == "W" and 200 < int(v.t_lt2.value) < 245 and "W/kg" in v.t_lt2.sub
    assert v.table.horizontalHeaderItem(1).text() == "W" and v.table.horizontalHeaderItem(2).text() == "W/kg"
    assert v.table.item(0, 1).text() == "100" and v.table.item(0, 2).text() == "1,28"
    assert not v.table.isColumnHidden(6) and 80 <= int(v.table.item(0, 6).text()) <= 90        # cadence
    assert not v.vo2_card.isVisibleTo(v) and not v.tabs.isTabVisible(1) and not v.power_card.isVisibleTo(v)
    assert v.zones_table.horizontalHeaderItem(2).text() == "Effekt"
    # the rider has no max HR on the bike: running's 190 is never used, and the page says what the zones build on
    assert v._zone_basis[1] != 190 and "190" not in v.zone_note.text() and "inte är en maxpuls" in v.zone_note.text()
    assert v.fit_btn.isEnabled() and v.fit_btn.toolTip() == t("ui.res.fit_tip")             # a ride as a FIT file
    from lopanalys.share import fit
    ride = fit.decode(services.fit_file(ctx.store, s)[1])            # what the mail to the rider attaches
    assert (ride["session"][0]["sport"], ride["session"][0]["sub_sport"]) == (2, 6)
    assert ride["session"][0]["max_power"] >= 200 and ride["record"][-1]["speed"] is None
    v.save()
    saved = ctx.store.session(s.id)
    assert saved.result["lt2"]["speed_kmh"] == pytest.approx(float(v.t_lt2.value), abs=1) and saved.sport.value == "bike"
    assert ctx.store.athlete(rider.id).max_hr == 190 and ctx.store.athlete(rider.id).sport_max_hr == {}
    text = _text(v.html())
    assert "Laktat och puls mot effekt" in v.html() and "Effekt W" in text and "W/kg" in text
    assert "km/h" not in text and "min/km" not in text and "Lutning" not in text and "löpband" not in text.lower()
    assert "Tröskeleffekt (LT2)" in text and "Cyklist" in text and "report." not in text and "ui." not in text
    assert "inget eget maxpulstest eller laktattest i cykel" not in text      # this IS the bike's lactate test


def test_bike_ramp_gives_max_hr_for_cycling_only_and_ftp(ctx, rider, qtbot):
    p = library_power.bike_ramp(100, 20, 60, warmup_s=120)
    r = _run(p, sim.SimBike, max_power_w=310)
    s = _save(ctx, rider, r, p, "bike", TestKind.MAX_HR)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.t_lt1.title == "FTP" and v.t_lt2.title == "Bästa minuten" and v.power_card.isVisibleTo(v)
    ftp, best = int(v.t_lt1.value), int(v.t_lt2.value)
    assert ftp == round(best * 0.75) and 280 <= best <= 310 and "75 %" in v.t_lt1.sub
    v.save()
    a = ctx.store.athlete(rider.id)
    assert a.max_hr == 190 and a.max_hr_for("bike") == v.max_hr and a.max_hr_source == ""     # running untouched
    assert ctx.store.session(s.id).result["power"]["ftp_w"] == ftp
    text = _text(v.html())
    for part in ("FTP", "Bästa minuten", "Effektzoner", "gäller bara cykel", "75 % av bästa minuteffekten"):
        assert part in text, part
    for part in ("km/h", "report.", "min/km"):
        assert part not in text, part
    # history: the sport first, FTP in the summary
    from lopanalys.ui.views.history import HistoryView
    h = HistoryView(ctx)
    qtbot.addWidget(h)
    h.refresh()
    for i in range(h.alist.count()):
        h.alist.setCurrentRow(i)
        if h.sessions:
            break
    rows = [(h.table.item(i, 1).text(), h.table.item(i, 3).text()) for i in range(h.table.rowCount())]
    assert rows and rows[0][0].startswith("Cykel · ") and rows[0][1].startswith(f"FTP {ftp} W")


def test_ftp_20_result(ctx, rider, qtbot):
    p = library_power.bike_ftp_20(250, warmup_s=300)
    r = _run(p, sim.SimBike, rider_power_w=262)
    s = _save(ctx, rider, r, p, "bike", TestKind.FTP)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.t_lt1.title == "FTP" and int(v.t_lt1.value) == pytest.approx(249, abs=2) and "95 %" in v.t_lt1.sub
    assert not v.lactate_card.isVisibleTo(v) and v.power_card.isVisibleTo(v)
    assert "(mål" not in v.table.item(0, 1).text() and int(v.table.item(0, 1).text()) == pytest.approx(262, abs=2)
    text = _text(v.html())
    assert "FTP-test" in text and "95 % av medeleffekten" in text and "Medel 20 min" in text and "W/kg" in text
    assert "report." not in text and "km/h" not in text
    assert "inget eget maxpulstest eller laktattest i cykel" in text          # an FTP test gives no max HR


def test_skierg_result_shows_measured_watt_pace_and_stroke_rate(ctx, rider, qtbot):
    p = library_power.skierg_lactate_step_for_level("recreational")
    r = _run(p, sim.SimSkiErg, LACTATE, follow=0.97)
    s = _save(ctx, rider, r, p, "skierg", TestKind.LACTATE_STEP)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.table.horizontalHeaderItem(2).text() == "/500 m" and v.table.horizontalHeaderItem(6).text() == "Takt"
    assert v.table.item(0, 1).text() == "78 (mål 80)" and re.fullmatch(r"\d:\d\d", v.table.item(0, 2).text())
    assert 30 <= int(v.table.item(0, 6).text()) <= 45
    assert v.t_lt2.unit == "W" and "/500 m" in v.t_lt2.sub and 120 < int(v.t_lt2.value) < 165
    assert "/500 m" in v.zones_table.item(1, 2).text() and " W" in v.zones_table.item(1, 2).text()
    text = _text(v.html())
    assert "Tempo /500 m" in text and "Åkare" in text and "medeleffekt som faktiskt hölls" in text
    assert "km/h" not in text and "W/kg" not in text and "report." not in text
    from lopanalys.share import fit
    ski = fit.decode(services.fit_file(ctx.store, s)[1])             # the SkiErg session as a FIT file
    assert (ski["session"][0]["sport"], ski["session"][0]["sub_sport"]) == (4, 25)
    assert ski["session"][0]["total_distance"] > 0 and 30 <= ski["session"][0]["avg_cadence"] <= 45
    # the drag factor the PM5 (here the simulator) reported, and the mean power written in by hand
    assert r.drag_factor == 95 and all(x.get("drag") == 95 for x in r.stage_stats)
    assert v.drag_row.isVisibleTo(v) and v.drag_edit.text() == "95" and "monitorn" in v.drag_note.text()
    assert "Dragfaktor 95, läst från monitorn." in _text(v.html())
    v.drag_edit.setText("102")
    v.drag_edit.editingFinished.emit()
    assert v.session.result["drag_factor"] == {"value": 102, "source": "manual"} and "för hand" in v.drag_note.text()
    it = v.table.item(2, 1)
    assert it.flags() & Qt.ItemFlag.ItemIsEditable and not v.table.item(2, 3).flags() & Qt.ItemFlag.ItemIsEditable
    before = v.records[2].speed_kmh
    it.setText("150")                           # the PM5 dropped in stage 3: the coach types the monitor's mean
    assert v.records[2].speed_kmh == 150 and v.table.item(2, 1).text().startswith("150* (mål ")
    assert str(round(before)) in v.table.item(2, 1).toolTip()
    lt2_manual = v.lactate.lt2.speed_kmh
    v.save()
    saved = ctx.store.session(s.id)
    assert saved.result["drag_factor"]["value"] == 102
    assert [x.get("manual") for x in saved.result["stage_stats"] if x["index"] == v.records[2].index] == [150]
    assert ctx.store.records(s.id)[v.records[2].index].speed_kmh == 150
    text = _text(v.html())
    assert "150*" in text and "inskriven för hand från monitorn" in text and "Dragfaktor 102, inskriven för hand." in text
    v.load_session(s.id)                        # it comes back, and an empty cell takes the entry back
    assert v.table.item(2, 1).text().startswith("150*") and v.lactate.lt2.speed_kmh == lt2_manual
    v.table.item(2, 1).setText("")
    assert v.records[2].speed_kmh == before and "*" not in v.table.item(2, 1).text()


def test_skierg_ramp_shows_the_best_minute_and_no_ftp_or_watch(ctx, rider, qtbot):
    p = library_power.skierg_ramp(80, 15, 60, warmup_s=60)
    s = _save(ctx, rider, _run(p, sim.SimSkiErg, max_power_w=170), p, "skierg", TestKind.MAX_HR)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.t_lt1.title == "Bästa minuten" and int(v.t_lt1.value) > 120 and v.t_lt2.isHidden()
    assert v.watch_card.isHidden() and v.vo2_card.isHidden() and v.garmin is None
    v.save()
    a = ctx.store.athlete(rider.id)
    assert a.max_hr == 190 and a.max_hr_for("skierg") == v.max_hr and a.max_hr_for("bike") is None
    text = _text(v.html())
    assert "Bästa minuteffekt" in text and "FTP" not in text and "gäller bara" in text and "km/h" not in text


def test_collected_report_never_mixes_sports(ctx, rider):
    pb = library_power.bike_ramp(100, 20, 60, warmup_s=60)
    bike = _save(ctx, rider, _run(pb, sim.SimBike, max_power_w=200), pb, "bike", TestKind.MAX_HR)
    ps = library_power.skierg_ramp(80, 15, 60, warmup_s=60)
    ski = _save(ctx, rider, _run(ps, sim.SimSkiErg, max_power_w=140), ps, "skierg", TestKind.MAX_HR,
                when=T0 + timedelta(hours=1))
    with pytest.raises(ValueError) as e:
        services.render_collected_report(ctx.store, rider, [bike, ski], {})
    assert str(e.value) == t("ui.hist.collect_one_sport")
    html = services.render_collected_report(ctx.store, rider, [bike], {})
    assert "km/h" not in _text(html)


def test_athlete_form_keeps_the_max_hr_of_every_sport(ctx, rider, qtbot):
    from lopanalys.ui.views.athletes import AthleteForm
    rider.set_max_hr("bike", 183)
    ctx.store.save_athlete(rider)
    f = AthleteForm()
    qtbot.addWidget(f)
    f.load(ctx.store.athlete(rider.id))
    assert f.max_hr.value() == 190 and f.max_hr_bike.value() == 183 and not f.max_hr_skierg.value()
    f.max_hr_skierg.setValue(176)
    a = f.collect()
    assert a.max_hr == 190 and a.sport_max_hr == {"bike": 183, "skierg": 176}
    ctx.store.save_athlete(a)
    assert ctx.store.athlete(rider.id).max_hr_for("skierg") == 176 and ctx.store.athlete(rider.id).max_hr_for("bike") == 183


def test_csv_export_puts_watt_under_watt_with_the_sport(ctx, rider):
    from lopanalys.assessments import erg_values
    from lopanalys.core.models import StepRecord
    st = ctx.store
    run = st.save_session(Session(None, rider.id, TestKind.LACTATE_STEP, T0, protocol_name="Löp"))
    st.save_records(run.id, [StepRecord(0, 10.5, 1.0, 240, hr_end=140, lactate_mmol=1.4)])
    ride = st.save_session(Session(None, rider.id, TestKind.LACTATE_STEP, T0 + timedelta(days=1), protocol_name="Cykel",
                                   sport="bike"))
    recs = [StepRecord(0, 150.0, 0.0, 300, hr_end=130), StepRecord(1, 175.0, 0.0, 300, hr_end=138)]
    erg_values.set_manual_load(ride, recs[1], 172)
    st.save_session(ride)
    st.save_records(ride.id, recs)
    rows = services.export_rows(st, rider.id)
    head = rows[0]
    assert head[:10] == ["idrottare", "datum", "test", "protokoll", "steg", "km/h", "lutning", "puls", "laktat", "rpe"]
    by = {(r[3], r[4]): dict(zip(head, r)) for r in rows[1:]}
    assert by[("Löp", 1)]["km/h"] == "10,5" and by[("Löp", 1)]["sport"] == "Löpning" and by[("Löp", 1)]["watt"] == ""
    assert by[("Cykel", 1)]["km/h"] == "" and by[("Cykel", 1)]["lutning"] == "" and by[("Cykel", 1)]["watt"] == "150,0"
    assert by[("Cykel", 2)]["watt"] == "172,0" and by[("Cykel", 2)]["watt för hand"] == "ja"
    assert by[("Cykel", 1)]["sport"] == "Cykel" and by[("Cykel", 1)]["watt för hand"] == ""
