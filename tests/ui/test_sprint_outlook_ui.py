"""The operator page says how near the automatic final sprint is, under the button *Slutspurt*,
and updates the line with the live values. The TV does not show it: it is for the coach."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel

from lopanalys.assessments import library
from lopanalys.assessments.protocol import Phase, RunnerState, SprintOutlook
from lopanalys.core.models import Athlete
from lopanalys.ui.context import AppContext, TestPlan
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.views.operator import OperatorView


def _state(p, sprint, phase=Phase.RUNNING) -> RunnerState:
    s = p.stages[-1]
    return RunnerState(phase=phase, stage_index=len(p.stages) - 1, stage=s, next_stage=None,
                       stage_elapsed_s=10, stage_remaining_s=50, total_elapsed_s=900, hr_bpm=171, sprint=sprint)


def _outlook(**kw) -> SprintOutlook:
    d = dict(in_ramp=True, ramp_elapsed_s=330.0, hr_rise_bpm=5.0, limit_bpm=2.0, min_ramp_s=240.0,
             after_s=720.0, remaining_s=390.0)
    d.update(kw)
    return SprintOutlook(**d)


def test_operator_shows_and_updates_the_sprint_line_and_the_tv_does_not(qapp):
    ctx = AppContext(demo=True)
    p = library.max_hr_ramp(10.0, mode="speed", step_s=30, final_sprint_s=30, final_sprint_auto=True)
    a = ctx.store.save_athlete(Athlete(None, "Test", "Person"))
    ctl = LiveController(ctx, TestPlan(a, "max_hr", p))
    op = OperatorView(ctx, ctl)
    try:
        assert op.sprint_btn.isVisibleTo(op) and not op.sprint_note.isVisibleTo(op)   # nothing before the start
        ctl.state_changed.emit(_state(p, _outlook(in_ramp=False, ramp_elapsed_s=0.0, hr_rise_bpm=None,
                                                  remaining_s=None), Phase.COUNTDOWN))
        assert op.sprint_note.isVisibleTo(op) and "tidigast 4:00 in i trappan" in op.sprint_note.text()
        ctl.state_changed.emit(_state(p, _outlook()))
        assert op.sprint_note.text() == ("Slutspurten: pulsen +5 slag senaste minuten (startar vid högst 2, "
                                         "tidigast 4:00 in i trappan) · senast om 6:30")
        ctl.state_changed.emit(_state(p, _outlook(ramp_elapsed_s=420.0, hr_rise_bpm=1.4, remaining_s=300.0)))
        assert "pulsen +1 slag" in op.sprint_note.text() and "senast om 5:00" in op.sprint_note.text()
        ctl.state_changed.emit(_state(p, _outlook(hr_rise_bpm=None)))
        assert "ingen puls" in op.sprint_note.text()
        ctl.state_changed.emit(_state(p, _outlook(started_s=552.0, reason="plateau", remaining_s=0.0)))
        assert op.sprint_note.text() == "Slutspurten startade 9:12 in i trappan (pulsen planade ut)"
        ctl.state_changed.emit(_state(p, _outlook(started_s=720.0, reason="time", remaining_s=0.0), Phase.RECOVERY))
        assert op.sprint_note.text() == "Slutspurten startade 12:00 in i trappan (12 min)"
        ctl.state_changed.emit(_state(p, None))                   # a test without the automatic sprint
        assert not op.sprint_note.isVisibleTo(op)
        # the TV: the same state, no line about the sprint
        op.show_tv()
        op.tv.board.on_state(_state(p, _outlook()))
        assert not any("Slutspurten" in w.text() for w in op.tv.findChildren(QLabel))
    finally:
        op.shutdown()
        ctl.abort()
        ctx.shutdown()
