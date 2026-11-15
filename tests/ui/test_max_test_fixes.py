"""A real max test 2026-11-14: the saved max HR is never lowered by itself, a peak that did not meet
the max criteria is not called a max HR, no lactate is shown for a test without lactate, and the guide
shows the ramp's time to the belt's limits and the final sprint."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lopanalys.assessments import library
from lopanalys.core.models import Athlete, Session, StepRecord, TestKind, TimeSample
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.new_test import KINDS
from lopanalys.ui.views.results import ResultsView

T0 = datetime(2026, 11, 14, 19, 0, tzinfo=timezone.utc)


def _max_session(ctx: AppContext, max_hr: int | None, peak: int, started=T0) -> tuple[Athlete, Session]:
    a = ctx.store.save_athlete(Athlete(None, "Olle", "Test", max_hr=max_hr))
    p = library.max_hr_ramp(12.0, warmup_s=600, start_incline_pct=1.0)
    recs = [StepRecord(0, 10.0, 1.0, 600, hr_end=130)] + [
        StepRecord(1 + i, 12.0, 1.0 + i, 60, hr_end=140 + 3 * i) for i in range(12)]
    end = 600 + 12 * 60.0
    # still rising at the end: no plateau, so the max criteria are not met
    smp = [TimeSample(float(k), int(round(120 + (peak - 120) * k / end))) for k in range(int(end) + 1)]
    smp += [TimeSample(end + k, peak - k // 3) for k in range(1, 70)]
    s = Session(None, a.id, TestKind.MAX_HR, started, started + timedelta(seconds=end + 70),
                protocol_name=p.name, protocol=p.to_dict(), result={"plan_kind": "max_hr", "exhaustion_t_s": end})
    ctx.store.db.save_test(s, recs[1:], smp, warmup_records=recs[:1])
    return a, s


def test_max_hr_update_rules():
    assert services.max_hr_update(None, 176) == "set"
    assert services.max_hr_update(188, 192) == "raise"
    assert services.max_hr_update(188, 176) == "ask"
    assert services.max_hr_update(188, 188) == "same"
    assert services.max_hr_update(188, None) == "same"


def test_lower_peak_keeps_the_saved_max_by_default(qapp, monkeypatch):
    ctx = AppContext(demo=True)
    a, s = _max_session(ctx, 188, 176)
    asked = []
    rv = ResultsView(ctx)
    monkeypatch.setattr(rv, "ask_keep_max_hr", lambda peak, saved: asked.append((peak, saved)) or True)
    rv.load_session(s.id)
    rv.save()
    rv.save()                                   # asked once, not on every save
    assert asked == [(176, 188)]
    assert ctx.store.athlete(a.id).max_hr == 188
    assert ctx.store.session(s.id).result["max_hr_asked"] == "kept"
    ctx.shutdown()


def test_lower_peak_replaces_when_the_coach_says_so(qapp, monkeypatch):
    ctx = AppContext(demo=True)
    a, s = _max_session(ctx, 188, 176)
    rv = ResultsView(ctx)
    monkeypatch.setattr(rv, "ask_keep_max_hr", lambda peak, saved: False)
    rv.load_session(s.id)
    rv.save()
    assert ctx.store.athlete(a.id).max_hr == 176
    ctx.shutdown()


def test_the_question_text_and_default(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    seen = {}

    def question(parent, title, text, buttons, default):
        seen.update(text=text, default=default)
        return default
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    ctx = AppContext(demo=True)
    rv = ResultsView(ctx)
    assert rv.ask_keep_max_hr(176, 188) is True
    assert seen["text"].startswith("Testets toppuls 176 är lägre än din sparade maxpuls 188. Behåll 188?")
    assert seen["default"] == QMessageBox.StandardButton.Yes
    ctx.shutdown()


def test_higher_peak_is_raised_without_asking_and_not_max_is_said(qapp, monkeypatch):
    ctx = AppContext(demo=True)
    a, s = _max_session(ctx, 172, 176)
    rv = ResultsView(ctx)
    monkeypatch.setattr(rv, "ask_keep_max_hr", lambda *_: (_ for _ in ()).throw(AssertionError("asked")))
    rv.load_session(s.id)
    rv.save()
    assert ctx.store.athlete(a.id).max_hr == 176
    assert rv.max_view.likely_max is False
    assert "Testet nådde troligen inte maxpuls" in rv.t_max.sub
    html = rv.html()
    assert "Testet nådde troligen inte maxpuls." in html and "Toppuls i testet" in html
    assert rv.t_max.title == "Toppuls i testet"
    # the peak became the new max: the zones say so
    assert "Zonerna bygger på maxpulsen från det här testet, 176 slag/min." in rv.zone_note.text()
    assert "Zonerna bygger på maxpulsen från det här testet, 176 slag/min." in html
    ctx.shutdown()


def test_max_test_without_lactate_shows_no_lactate(qapp):
    ctx = AppContext(demo=True)
    a, s = _max_session(ctx, 188, 176)
    rv = ResultsView(ctx)
    rv.resize(1400, 900)
    rv.show()
    rv.load_session(s.id)
    assert not rv.lactate_measured()
    assert rv.lactate_card.isHidden() and rv.t_lt1.isHidden() and rv.t_lt2.isHidden()
    assert rv.table.isColumnHidden(4)
    # a zones test: what to type into the watch comes first, the zones right under the tiles
    assert rv._overview.indexOf(rv.watch_card) == 0 and rv._overview.indexOf(rv.zone_card) == 2
    data = services.report_data(a, ctx.store.session(s.id), rv.records, rv.lactate, rv.zone_list, rv.max_view,
                                None, None, {}, rv.samples)
    assert data.kinds == ["max_hr"]
    html = rv.html()
    assert 'class="section lactate"' not in html and 'class="section maxhr"' in html
    rv.close()
    ctx.shutdown()


def test_lactate_test_with_values_shows_lactate(qapp):
    ctx = AppContext(demo=True)
    a = ctx.store.save_athlete(Athlete(None, "Lisa", "Lakt", max_hr=190))
    p = library.lactate_step(10.0, 1.0, 5, warmup_s=0)
    recs = [StepRecord(i, 10.0 + i, 1.0, 240, hr_end=140 + 8 * i, lactate_mmol=la)
            for i, la in enumerate([1.1, 1.3, 1.9, 3.2, 5.6])]
    s = Session(None, a.id, TestKind.LACTATE_STEP, T0, T0 + timedelta(minutes=25), protocol_name=p.name,
                protocol=p.to_dict(), result={})
    ctx.store.db.save_test(s, recs, [TimeSample(float(k), 140) for k in range(1200)])
    rv = ResultsView(ctx)
    rv.show()
    rv.load_session(s.id)
    assert rv.lactate_measured()
    assert not rv.lactate_card.isHidden() and not rv.table.isColumnHidden(4)
    assert rv._overview.indexOf(rv.zone_card) == 2
    assert 'class="section lactate"' in rv.html()
    rv.close()
    ctx.shutdown()


def test_max_test_due_after_two_years(qapp):
    ctx = AppContext(demo=True)
    a, s = _max_session(ctx, 188, 176, started=T0 - timedelta(days=800))
    assert "gör ett nytt maxtest" in services.max_test_due(ctx.store, a, today=T0)
    assert services.max_test_due(ctx.store, a, today=T0 - timedelta(days=700)) == ""
    assert services.max_test_due(ctx.store, Athlete(None, "Ny", "Person")) == ""
    ctx.shutdown()


def test_belt_limits_from_the_settings():
    vals = {"devices.max_speed_kmh": 18.0, "devices.max_incline_pct": 12.0}
    lim = services.treadmill_limits(None, vals.get)
    assert (lim.max_speed_kmh, lim.max_incline_pct) == (18.0, 12.0)
    assert services.belt_limits({}.get) is None


def test_guide_shows_the_ramp_plan_and_the_sprint_box(demo_window, qtbot):
    ctx = demo_window.ctx
    ctx.set_setting("devices.max_speed_kmh", 18.0)
    ctx.set_setting("devices.max_incline_pct", 12.0)
    nt = demo_window.new_test
    demo_window.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    nt._next()
    nt.kind_group.button([k for k, *_ in KINDS].index("max_hr")).click()
    names = [nt.lib.itemText(i) for i in range(nt.lib.count())]
    nt.lib.setCurrentIndex(names.index("Maxpuls, lutningstrappa"))
    assert not nt.sprint_box.isHidden() and not nt.p_sprint.isChecked()
    assert nt.protocol.final_sprint_s == 0
    assert "1 % varje minut till bandets högsta lutning (12 %)" in nt.ramp_note.text()
    assert nt.p_table.item(1, 2).text() == "1,0 %"                 # step 1 has the start incline
    nt.p_sprint.setChecked(True)
    assert nt.protocol.final_sprint_auto and nt.protocol.final_sprint_s == 30
    nt.lib.setCurrentIndex(names.index("Maxpuls, fartrappa"))
    assert nt.p_sprint.isChecked() and nt.protocol.final_sprint_auto
    # what the ramp does, not the minutes to each limit (2026-11-15: "efter 21 min, efter 32 min" confused)
    assert ("Trappan ökar 0,5 km/h var 30:e sekund till bandets högsta fart (18,0 km/h) och sedan 1 % "
            "var 30:e sekund till högsta lutningen (12 %).") in nt.ramp_note.text()


def test_ramp_advice_warns_and_suggests_a_start_speed():
    lim = library.TreadmillLimits(max_speed_kmh=18.0, max_incline_pct=12.0)
    base = library.max_hr_ramp(12.0, warmup_s=600, limits=lim)
    fit = 70.0                                   # a VO2max well above what 12 km/h at 12 % costs
    adv = services.ramp_advice(base, lim, fit, lambda v: services.rebuild_protocol(base, speed_kmh=v))
    assert adv["warning"].startswith("Trappan blir troligen längre än 12 min. Ett maxtest bör ta 8–12 min: höj startfarten")
    assert adv["suggest_kmh"] > 12.0
    ok = services.ramp_advice(services.rebuild_protocol(base, speed_kmh=adv["suggest_kmh"]), lim, fit)
    assert ok["warning"] == "" and "trappan tar troligen" in ok["text"]
    assert services.ramp_advice(library.lactate_step(10.0), lim) is None
    zones = services.ramp_advice(library.hr_zones(8.0, limits=lim), lim)
    assert zones["text"] == ("Trappan ökar 0,5 km/h varje minut till bandets högsta fart (18,0 km/h) och sedan "
                             "1 % varje minut till högsta lutningen (12 %).")
    assert services.ramp_advice(base, lim)["text"] == (
        "Trappan ökar 1 % varje minut till bandets högsta lutning (12 %) och sedan 0,5 km/h varje minut "
        "till högsta farten (18,0 km/h).")


def test_start_speed_from_the_previous_max_test(qapp):
    ctx = AppContext(demo=True)
    a, s = _max_session(ctx, 188, 176)              # 12 steps of 1 min, up to 12 % at 12 km/h
    prev = services.previous_max_ramp(ctx.store, a.id)
    assert prev["minutes"] == 12 and prev["max_incline_pct"] == 12.0 and prev["start_kmh"] == 12.0
    lim = library.TreadmillLimits(max_speed_kmh=18.0, max_incline_pct=12.0)
    base = library.max_hr_ramp(12.0, warmup_s=600, limits=lim)
    adv = services.ramp_advice(base, lim, None, lambda v: services.rebuild_protocol(base, speed_kmh=v), prev)
    assert adv["warning"] == "Förra testet tog 12 min och nådde 12 %: välj fartrappan eller +1 km/h."
    assert adv["suggest_kmh"] == 13.0
    ctx.shutdown()


def test_runner_logs_the_targets(caplog):
    import logging
    from lopanalys.assessments.runner import TestRunner
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(   # not "tests." as a package: that import fails on Windows CI
        "assessment_fakes", Path(__file__).resolve().parents[1] / "assessments" / "fakes.py")
    import sys
    fakes = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fakes                   # dataclasses look the module up while it is executed
    spec.loader.exec_module(fakes)
    FakeClock, FakeTreadmill, run = fakes.FakeClock, fakes.FakeTreadmill, fakes.run
    clock = FakeClock()
    r = TestRunner(library.max_hr_ramp(12.0, warmup_s=0), FakeTreadmill(), None, clock=clock, threaded=False)
    with caplog.at_level(logging.INFO, logger="lopanalys.assessments.runner"):
        r.start()
        run(r, clock, 10 + 65)
    assert "stage 0: 12.0 km/h, 1 %" in caplog.text and "stage 1: 12.0 km/h, 2 %" in caplog.text


def test_zones_say_they_use_the_saved_max(qapp):
    """A peak below the saved max HR said "Maxpuls <peak>" while zone 5 ended at the saved value: the zones use
    the saved max HR, and must say so."""
    from datetime import date
    ctx = AppContext(demo=True)
    a, s = _max_session(ctx, 188, 176)
    a.max_hr_source, a.max_hr_date = "entered", date(2026, 11, 14)
    ctx.store.save_athlete(a)
    rv = ResultsView(ctx)
    rv.load_session(s.id)
    want = ("Zonerna bygger på din sparade maxpuls 188 slag/min (inskriven 2026-11-14). "
            "Testets topp var 176 och nådde troligen inte max.")
    assert want in rv.zone_note.text()
    html = rv.html()
    assert html.count(want) >= 2                        # the zone section and the cover
    assert "Toppuls i testet" in html
    ctx.shutdown()


def test_vo2max_none_for_a_ramp_below_max_and_the_watch_value(qapp):
    from datetime import date
    ctx = AppContext(demo=True)
    a, s = _max_session(ctx, 188, 176)
    a.watch_vo2max, a.watch_vo2max_date = 51.0, date(2026, 11, 1)
    ctx.store.save_athlete(a)
    rv = ResultsView(ctx)
    rv.load_session(s.id)
    assert rv.vo2["best"] is None and rv.vo2["reasons"] == ["not_max"]
    html = rv.html()
    assert "Kan inte uppskattas ur det här testet." in html and "VO2max enligt klockan: 51,0 (2026-11-01)" in html
    from lopanalys.assessments import vo2max
    assert f"{vo2max.acsm_running_vo2(12.0, 12.0):.1f}".replace(".", ",") not in html   # no half-good value
    ctx.shutdown()
