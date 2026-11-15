"""The operator page during the warm-up: *Börja testet nu* and the start speed (-/+ 0,5 km/h)."""
from __future__ import annotations

from lopanalys.assessments import library
from lopanalys.assessments.runner import TestRunner
from lopanalys.core.bus import Bus
from lopanalys.core.models import Athlete
from lopanalys.ui.context import AppContext, TestPlan
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.views.operator import OperatorView

from tests.assessments.fakes import FakeClock, FakeTreadmill, run


def test_warmup_buttons(qapp):
    ctx = AppContext(demo=True)
    p = library.hr_zones(9.0, 1.0, 4, stage_s=180, warmup_s=360)
    a = ctx.store.save_athlete(Athlete(None, "Olle", "Löpare"))
    ctl = LiveController(ctx, TestPlan(a, "hr_zones", p))
    op = OperatorView(ctx, ctl)
    clock = FakeClock()
    r = TestRunner(p, FakeTreadmill(), None, Bus(), clock=clock, threaded=False)
    try:
        assert not op.warm_box.isVisibleTo(op)                 # before the start
        ctl.runner, op.started = r, True
        r.start()
        run(r, clock, 30)
        ctl.state_changed.emit(r.state)
        assert op.warm_box.isVisibleTo(op) and op.start_now_btn.isEnabled()
        assert op.start_speed.text() == "Första steget: 6:40 /km (9,0 km/h)"
        op.faster_btn.click()
        assert op.start_speed.text() == "Första steget: 6:19 /km (9,5 km/h)"
        assert ctl.session.result["start_shift"] == 0.5
        assert ctl.session.protocol["stages"][1]["speed_kmh"] == 9.5     # a cut-off test continues moved
        op.start_now_btn.click()
        ctl.state_changed.emit(r.state)
        assert r.state.stage_index == 1 and not op.warm_box.isVisibleTo(op)
    finally:
        r.close()
        op.shutdown()
        ctl.abort()
        ctx.shutdown()
