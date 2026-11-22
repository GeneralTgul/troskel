"""The TV window closed (Esc, Alt+F4) and shown again with *Visa på TV* during a test.

Before 2026-11-22 closing it stopped the camera picture's polling for good: the window came back with
a frozen picture for the rest of the test."""
from __future__ import annotations

from lopanalys.assessments import library
from lopanalys.core.models import Athlete
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.context import TestPlan as Plan
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.views.operator import OperatorView


def test_the_picture_runs_again_when_the_tv_window_is_reopened(qapp, qtbot, tmp_path):
    ctx = AppContext(demo=True, store=services.open_store(tmp_path / "db.sqlite"))
    a = ctx.store.save_athlete(Athlete(None, "Anna", "Svensson", height_cm=170, max_hr=190))
    ctl = LiveController(ctx, Plan(a, "lactate", library.lactate_step(10.0, 1.0, 3, warmup_s=0), None))
    view = OperatorView(ctx, ctl)
    qtbot.addWidget(view)
    view.show_tv()
    tv = view.tv
    assert tv is not None and tv.board.video._timer.isActive()
    tv.close()                                     # Esc in the window, or its close button
    assert not tv.board.video._timer.isActive()
    view.show_tv()                                 # the coach's button brings the same window back
    assert view.tv is tv and tv.isVisible()
    assert tv.board.video._timer.isActive()
    view.shutdown()
    ctl.abort()
    ctx.shutdown()
