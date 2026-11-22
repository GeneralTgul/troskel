"""The screen chosen for the live view (Inställningar): the first screen is a choice too.

Before 2026-11-22 the setting was read as ``int(value or -1)``: screen 1 is index 0, which is falsy, so it
was taken for "choose by itself" and the TV window opened on the last screen instead."""
from __future__ import annotations

from lopanalys.assessments import library
from lopanalys.core.models import Athlete
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.context import TestPlan as Plan
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.views.operator import OperatorView
from lopanalys.ui.views.settings import SettingsView


def test_the_first_screen_can_be_chosen_for_the_tv(qapp, qtbot, tmp_path):
    ctx = AppContext(demo=True, store=services.open_store(tmp_path / "db.sqlite"))
    ctx.set_setting("tv.screen", 0)
    a = ctx.store.save_athlete(Athlete(None, "Anna", "Svensson", height_cm=170, max_hr=190))
    ctl = LiveController(ctx, Plan(a, "lactate", library.lactate_step(10.0, 1.0, 3, warmup_s=0), None))
    view = OperatorView(ctx, ctl)
    qtbot.addWidget(view)
    view.show_tv()
    assert view.tv is not None and view.tv.screen_index == 0
    view.shutdown()
    ctl.abort()
    s = SettingsView(ctx)
    qtbot.addWidget(s)
    assert s.screen.currentData() == 0
    ctx.shutdown()
