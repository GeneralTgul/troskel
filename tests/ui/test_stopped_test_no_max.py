"""A test that was stopped before its end and is not a max test (a technique test stopped half-way)
gives neither a max HR nor zones: on 2026-11-15 one got "max HR 89" and zones computed from it."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lopanalys.core.models import Athlete, Session, StepRecord, TestKind, TimeSample
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.results import ResultsView

T0 = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)


def _session(ctx, status, kind=TestKind.GAIT, result=None):
    a = ctx.store.save_athlete(Athlete(None, "Olle", "Löpare", resting_hr=55))
    s = Session(None, a.id, kind, T0, T0 + timedelta(seconds=200), protocol_name="Löpteknik",
                protocol={}, treadmill_mode="sim", status=status, result=dict(result or {}, plan_kind="gait"))
    recs = [StepRecord(0, 10.0, 1.0, 120, hr_end=86, hr_mean=84), StepRecord(1, 12.0, 1.0, 60, hr_end=89, hr_mean=88)]
    smp = [TimeSample(float(i), hr=80 + i // 20, speed_kmh=10.0, incline_pct=1.0) for i in range(180)]
    ctx.store.db.save_test(s, recs, smp)
    return a, s


def test_rule():
    stopped = Session(None, 1, TestKind.GAIT, T0, status="stopped")
    assert not services.own_peak_counts(stopped)
    assert not services.own_peak_counts(Session(None, 1, TestKind.LACTATE_STEP, T0, status="interrupted"))
    assert services.own_peak_counts(Session(None, 1, TestKind.GAIT, T0, status="finished"))
    assert services.own_peak_counts(Session(None, 1, TestKind.MAX_HR, T0, status="stopped"))
    assert services.own_peak_counts(Session(None, 1, TestKind.LACTATE_STEP, T0, status="stopped",
                                            result={"exhaustion_t_s": 900.0}))


def test_stopped_technique_test_has_no_max_and_no_zones(qapp):
    ctx = AppContext(demo=True)
    try:
        a, s = _session(ctx, "stopped")
        rv = ResultsView(ctx)
        rv.load_session(s.id)
        assert rv.max_hr is None and rv.zone_list == []
        assert rv.t_max.value == "–"
        _, s2 = _session(ctx, "finished")                 # the same test run to its end: as before
        rv.load_session(s2.id)
        assert rv.max_hr is not None
    finally:
        ctx.shutdown()
