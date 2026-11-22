"""Changing values on the results page after the test."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lopanalys.assessments import library
from lopanalys.core.models import Athlete, Session, StepRecord, TestKind
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.history import HistoryView
from lopanalys.ui.views.results import ResultsView

T0 = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)
LACTATE = [1.1, 1.2, 1.5, 2.1, 3.2, 5.0, 8.1]


def _lactate_test(ctx: AppContext) -> tuple[Athlete, Session]:
    a = ctx.store.save_athlete(Athlete(None, "Olle", "Löpare", max_hr=190))
    p = library.lactate_step(9.0, 1.0, 7, stage_s=240, pause_s=30, warmup_s=0)
    recs = [StepRecord(i, 9.0 + i, 1.0, 240, hr_end=130 + 9 * i, lactate_mmol=v) for i, v in enumerate(LACTATE)]
    s = Session(None, a.id, TestKind.LACTATE_STEP, T0, T0 + timedelta(minutes=32), protocol_name=p.name,
                protocol=p.to_dict(), result={"plan_kind": "lactate"})
    ctx.store.db.save_test(s, recs, [])
    return a, s


def test_removed_lactate_values_take_the_saved_thresholds_with_them(qtbot, tmp_path):
    """Before 2026-11-22 the thresholds saved with the test stayed when the values they were computed
    from were removed: the results page showed no LT2, the history list still showed the old one."""
    ctx = AppContext(store=services.open_store(tmp_path / "db.sqlite"))
    a, s = _lactate_test(ctx)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    v.save()
    assert ctx.store.session(s.id).result["lt2"]["speed_kmh"] > 12
    for row in range(2, 7):                          # the coach empties five cells: two values are left
        v.table.item(row, 4).setText("")
    assert v.lactate is None or v.lactate.lt2 is None
    v.flush()
    res = ctx.store.session(s.id).result
    assert not res.get("lt2") and not res.get("lt1")
    h = HistoryView(ctx)
    qtbot.addWidget(h)
    h.select_athlete(a.id)
    assert "LT2" not in h.table.item(0, 3).text()
    ctx.shutdown()


def test_a_finished_max_test_does_not_write_old_athlete_fields_back(qtbot, tmp_path):
    """Before 2026-11-22 the results page saved the athlete as the wizard had read it before the test:
    what the coach typed in Idrottare while the test ran (height, weight, notes) was written over
    when the max test saved its new max HR."""
    import dataclasses
    from datetime import date

    from lopanalys.core.models import TimeSample
    from lopanalys.ui.context import TestOutcome
    from lopanalys.ui.context import TestPlan as Plan
    ctx = AppContext(store=services.open_store(tmp_path / "db.sqlite"))
    a = ctx.store.save_athlete(Athlete(None, "Olle", "Löpare", max_hr=180))
    before = dataclasses.replace(a)                          # what the wizard handed the test
    p = library.max_hr_ramp(12.0, warmup_s=0, start_incline_pct=1.0)
    recs = [StepRecord(i, 12.0, 1.0 + i, 60, hr_end=150 + 4 * i) for i in range(12)]
    smp = [TimeSample(float(k), int(round(120 + 76 * k / 720)), 12.0) for k in range(721)]
    s = Session(None, a.id, TestKind.MAX_HR, T0, T0 + timedelta(seconds=790), protocol_name=p.name,
                protocol=p.to_dict(), result={"plan_kind": "max_hr", "exhaustion_t_s": 720.0})
    ctx.store.db.save_test(s, recs, smp)
    a.height_cm, a.weight_kg, a.notes, a.birth_date = 181, 74.0, "Ont i vaden", date(1990, 5, 17)
    ctx.store.save_athlete(a)                                # typed in Idrottare while the test ran
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_outcome(TestOutcome(Plan(before, "max_hr", p), s, recs, smp))
    saved = ctx.store.athlete(a.id)
    assert saved.max_hr == 196                                # the test's max HR
    assert (saved.height_cm, saved.weight_kg, saved.notes, saved.birth_date) == (181, 74.0, "Ont i vaden",
                                                                                 date(1990, 5, 17))
    ctx.shutdown()


def test_the_change_since_last_time_uses_the_same_method_on_both_tests(qtbot, tmp_path, monkeypatch):
    """Before 2026-11-23 the report compared this test's thresholds with the previous test's as each
    was saved: with OBLA 4 mmol chosen now and modified Dmax then, the page *Sedan förra testet* showed
    the difference between two methods as the athlete's progress."""
    ctx = AppContext(store=services.open_store(tmp_path / "db.sqlite"))
    a, old = _lactate_test(ctx)
    p = library.lactate_step(9.0, 1.0, 7, stage_s=240, pause_s=30, warmup_s=0)
    recs = [StepRecord(i, 9.0 + i, 1.0, 240, hr_end=130 + 9 * i, lactate_mmol=v) for i, v in enumerate(LACTATE)]
    new = Session(None, a.id, TestKind.LACTATE_STEP, T0 + timedelta(days=60), T0 + timedelta(days=60, minutes=32),
                  protocol_name=p.name, protocol=p.to_dict(), result={"plan_kind": "lactate"})
    ctx.store.db.save_test(new, recs, [])
    seen = {}

    def render(athlete, session, records, lactate, zone_list, max_view, gait, previous, *args, **kw):
        seen["now"], seen["then"] = lactate, previous["lactate"]
        return "<html></html>"

    monkeypatch.setattr(services, "render_report", render)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(new.id)
    v.method.setCurrentIndex(v.method.findData("obla_4"))
    v._html = None
    v.html()
    assert seen["now"].lt2_method == "obla_4" and seen["then"].lt2_method == "obla_4"
    # the same values in both tests: no change, whatever the method
    assert seen["then"].lt2.speed_kmh == seen["now"].lt2.speed_kmh
    ctx.shutdown()
