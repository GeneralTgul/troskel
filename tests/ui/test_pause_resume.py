"""Pause and continue from the coach's live page.

Before 2026-11-22 the controller's attribute ``resume`` (the cut-off test being continued, None for a
new test) hid its method ``resume()``: *Fortsätt* after *Paus* raised a TypeError and the test stayed
paused until it was stopped.
"""
from __future__ import annotations

from lopanalys.assessments import library
from lopanalys.assessments.protocol import Phase
from lopanalys.core.models import Athlete
from lopanalys.ui.context import AppContext
from lopanalys.ui.context import TestPlan as Plan
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.views.operator import OperatorView


def test_pause_then_continue_from_the_operator_view(qapp, qtbot):
    ctx = AppContext(demo=True, time_scale=40.0)
    ctx.devices.connect_treadmill("sim")
    ctx.devices.connect_heart_rate("sim")
    a = ctx.store.save_athlete(Athlete(None, "Anna", "Svensson", height_cm=170, max_hr=190))
    p = library.lactate_step(10.0, 1.0, 3, stage_s=60, pause_s=8, warmup_s=0)
    ctl = LiveController(ctx, Plan(a, "lactate", p, None, treadmill_mode="sim"))
    view = OperatorView(ctx, ctl)
    qtbot.addWidget(view)
    view.start()
    qtbot.waitUntil(lambda: ctl.state is not None and ctl.state.phase == Phase.RUNNING, timeout=20000)
    view.toggle_pause()
    qtbot.waitUntil(lambda: ctl.state.paused, timeout=5000)
    assert ctl.paused
    view.toggle_pause()                                   # raised TypeError: 'NoneType' is not callable
    qtbot.waitUntil(lambda: not ctl.state.paused, timeout=5000)
    assert not ctl.paused
    ctl.abort()
    view.shutdown()
    ctx.shutdown()
