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
