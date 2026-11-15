"""The protocol "Maxpuls med löpteknik": the max HR ramp filmed like "Kombinerat", the technique
judged only on the stages before the end, and both in the report and the collected report."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lopanalys.core.models import Athlete, Session, StepRecord, TestKind, TimeSample
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.new_test import KINDS
from lopanalys.ui.views.results import ResultsView

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)


def test_wizard_builds_a_filmed_max_ramp(demo_window, qtbot):
    nt = demo_window.new_test
    demo_window.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    nt._next()
    i = [k for k, *_ in KINDS].index("max_hr_gait")
    nt.kind_group.button(i).click()
    assert nt.kind == "max_hr_gait"
    p = nt.protocol
    assert p.kind == "max_hr" and p.open_ended and p.name.startswith("Maxpuls med löpteknik: ")
    assert all(s.record_gait for s in p.stages)
    assert nt.plan().kind == "max_hr_gait"


def _session(ctx: AppContext) -> tuple[Athlete, Session]:
    a = ctx.store.save_athlete(Athlete(None, "Bo", "Ek", height_cm=180))
    recs = [StepRecord(0, 8.0, 1.0, 600, hr_end=130)] + [
        StepRecord(1 + i, 10 + 0.5 * i, 1.0, 60, hr_end=h, gait={"cadence_spm": 170 + i})
        for i, h in enumerate([150, 158, 165, 170, 176, 182, 187, 190])]
    end = 600 + 10 + 8 * 60.0
    smp = [TimeSample(float(k), 120 + int(70 * k / end)) for k in range(int(end) + 60)]
    p = services.max_hr_ramp(10.0, 0.5, 60, 1.0).to_dict()
    s = Session(None, a.id, TestKind.MAX_HR, T0, T0 + timedelta(seconds=end + 60), protocol_name="Maxpuls med löpteknik",
                protocol=p, video_path="/nonexistent/film.mp4",
                result={"plan_kind": "max_hr_gait", "exhaustion_t_s": end})
    ctx.store.db.save_test(s, recs[1:], smp, warmup_records=recs[:1])
    return a, s


def test_film_analysed_only_before_the_end_and_reported_with_max(qapp, qtbot, monkeypatch):
    ctx = AppContext(demo=True)
    a, s = _session(ctx)
    seen = {}

    def fake(path, height, view, progress, kf, max_seconds=None):
        seen["max_seconds"] = max_seconds
        return {"summary": {"cadence_spm": 172.0}, "findings": [], "keyframes": []}
    monkeypatch.setattr(services, "analyze_video", fake)
    rv = ResultsView(ctx)
    rv.load_session(s.id)
    assert rv.plan_kind() == "max_hr_gait"
    rv.analyze_video()
    qtbot.waitUntil(lambda: rv._analysis is None, timeout=5000)
    assert seen["max_seconds"] == 600 + 10 + 4 * 60          # up to the last stage below 171 bpm
    g = ctx.store.gait(s.id)
    assert g["window"]["stages"] == [1, 2, 3, 4]
    data = services.report_data(a, ctx.store.session(s.id), ctx.store.records(s.id), None, [], None, g, None, {},
                                ctx.store.samples(s.id))
    assert data.kinds == ["max_hr", "gait"]
    html = rv.html()
    assert "Maxpuls" in html and "de 4 första stegen i maxpulstrappan" in html
    coll = services.render_collected_report(ctx.store, a, [ctx.store.session(s.id)], {})
    assert "de 4 första stegen" in coll
    ctx.shutdown()
