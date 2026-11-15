"""The test is saved all the time (2026-11-14: a test vanished after a click on Inställningar).

Runs real tests with the simulated treadmill and strap: the session exists from the start, every
stage, lactate value and the samples are written while it runs, a test that ends is saved without a
button, a controller killed mid-test leaves an interrupted test that the next AppContext finds,
opens and can continue from the next stage, and the results page saves its changes by itself.
"""
from __future__ import annotations

import time

import pytest

from lopanalys.assessments import library
from lopanalys.assessments.protocol import Phase
from lopanalys.core.models import Athlete
from lopanalys.ui import recovery
from lopanalys.ui.context import AppContext
from lopanalys.ui.context import TestPlan as Plan
from lopanalys.ui.live_controller import LiveController

SCALE = 40.0   # test seconds per real second


def _ctx() -> AppContext:
    ctx = AppContext(demo=True, time_scale=SCALE)
    ctx.devices.connect_treadmill("sim")
    ctx.devices.connect_heart_rate("sim")
    return ctx


def _athlete(ctx: AppContext) -> Athlete:
    a = Athlete(None, "Anna", "Svensson", height_cm=170, max_hr=190)
    return ctx.store.save_athlete(a)


def _lactate_plan(a: Athlete, stages: int = 4) -> Plan:
    p = library.lactate_step(10.0, 1.0, stages, stage_s=40, pause_s=8, warmup_s=0)
    return Plan(a, "lactate", p, None, treadmill_mode="sim")


def _until(qtbot, cond, timeout=20000):
    qtbot.waitUntil(cond, timeout=timeout)


def _crash(ctl: LiveController) -> None:
    """What a crash or a power cut leaves: no finalize, no last save, the database just closed."""
    ctl._ckpt.stop()
    ctl._done = True                        # the controller reacts to nothing any more
    rn = ctl.runner
    with rn._lock:
        rn._phase = Phase.ABORTED           # the runner thread ends without publishing anything
        rn._end()
    rn.join(2)
    ctl.ctx.devices.disconnect_all()
    ctl.ctx.bridge.close()
    ctl.ctx.store.close()


def test_saved_from_start_and_per_stage(qapp, qtbot):
    ctx = _ctx()
    a = _athlete(ctx)
    ctl = LiveController(ctx, _lactate_plan(a))
    assert ctl.start()
    sid = ctl.session.id
    assert sid is not None                                    # in the database from the first second
    db = ctx.store.db
    assert db.get_session(sid).status == "running"
    _until(qtbot, lambda: ctl.state is not None and ctl.state.phase == Phase.LACTATE_PAUSE)
    assert len(db.step_records(sid)) == 1                     # the completed stage, written at once
    ctl.enter_lactate(1.4)
    assert db.step_records(sid)[0].lactate_mmol == 1.4
    assert [e["mmol"] for e in db.lactate_entries(sid)] == [1.4]
    ctl.enter_rpe(11)
    assert db.step_records(sid)[0].rpe == 11
    assert len(db.time_samples(sid)) > 30                     # samples go with the stage
    ctl.stop()
    _until(qtbot, lambda: db.get_session(sid).status == "stopped")
    ctx.shutdown()


def test_checkpoint_writes_samples_between_stages(qapp, qtbot, monkeypatch):
    ctx = _ctx()
    a = _athlete(ctx)
    ctl = LiveController(ctx, _lactate_plan(a))
    ctl._ckpt.setInterval(200)                                # CHECKPOINT_S, shortened for the test
    assert ctl.start()
    sid = ctl.session.id
    _until(qtbot, lambda: len(ctx.store.db.time_samples(sid)) > 15)
    assert ctx.store.db.step_records(sid) == []               # still in the first stage
    ctl.abort()
    ctx.shutdown()


def test_stopped_in_first_gait_stage_is_kept(qapp, qtbot):
    """The case that lost a real test: stopped before a stage was complete, nothing was saved."""
    ctx = _ctx()
    a = _athlete(ctx)
    p = library.gait_check([10.0, 12.0], stage_s=60)
    ctl = LiveController(ctx, Plan(a, "gait", p, None, treadmill_mode="sim"))
    assert ctl.start()
    _until(qtbot, lambda: ctl.state is not None and ctl.state.phase == Phase.RUNNING
           and ctl.state.stage_elapsed_s > 30)
    got = []
    ctl.finished.connect(got.append)
    ctl.stop()
    _until(qtbot, lambda: bool(got))
    s = ctx.store.db.get_session(ctl.session.id)
    assert s.status == "stopped"
    recs = ctx.store.db.step_records(s.id)
    assert len(recs) == 1 and 25 < recs[0].duration_s < 60   # the partial stage
    ctx.shutdown()


def test_window_closed_mid_test_saves_it(qapp, qtbot):
    ctx = _ctx()
    a = _athlete(ctx)
    ctl = LiveController(ctx, _lactate_plan(a))
    got = []
    ctl.finished.connect(got.append)
    assert ctl.start()
    _until(qtbot, lambda: len(ctl.runner.records) >= 1)
    ctl.enter_lactate(1.2)
    ctl.abort()                                               # what closing the program does
    s = ctx.store.db.get_session(ctl.session.id)
    assert s.status == "stopped" and not got                  # saved, and no results page
    assert ctx.store.db.step_records(s.id)[0].lactate_mmol == 1.2
    ctx.shutdown()


def test_finished_test_saved_without_button(qapp, qtbot):
    ctx = _ctx()
    a = _athlete(ctx)
    ctl = LiveController(ctx, _lactate_plan(a, stages=2))
    got = []
    ctl.finished.connect(got.append)
    assert ctl.start()
    _until(qtbot, lambda: ctl.state is not None and ctl.state.phase == Phase.LACTATE_PAUSE)
    ctl.enter_lactate(1.5)
    _until(qtbot, lambda: bool(got), timeout=30000)
    s = ctx.store.db.get_session(ctl.session.id)
    assert s.status == "finished" and len(ctx.store.db.step_records(s.id)) == 2
    from lopanalys.ui.views.results import ResultsView
    rv = ResultsView(ctx)
    rv.load_outcome(got[0])                                   # computed results saved at once
    assert "lt1_method" in ctx.store.db.get_session(s.id).result
    ctx.shutdown()


def test_crash_then_reopen_and_continue(qapp, qtbot, tmp_path):
    ctx = _ctx()
    a = _athlete(ctx)
    ctl = LiveController(ctx, _lactate_plan(a, stages=4))
    assert ctl.start()
    sid = ctl.session.id
    for n in (1, 2):
        _until(qtbot, lambda n=n: len(ctl.runner.records) >= n and ctl.state.phase == Phase.LACTATE_PAUSE)
        ctl.enter_lactate(1.0 + n * 0.5)
    _crash(ctl)

    ctx2 = _ctx()                                             # the program starts again
    cut = recovery.collect_interrupted(ctx2.store)
    assert [s.id for s in cut] == [sid]
    s = ctx2.store.db.get_session(sid)
    assert s.status == "interrupted" and s.ended is not None
    assert [r.lactate_mmol for r in ctx2.store.records(sid)] == [1.5, 2.0]
    assert recovery.collect_interrupted(ctx2.store) == []     # asked once
    info = recovery.resume_info(ctx2.store, s)
    assert info is not None and info.from_index == 2 and len(info.records) == 2
    n_before = len(info.samples)

    plan = _lactate_plan(a, stages=2)                         # whatever the wizard had: replaced
    ctl2 = LiveController(ctx2, plan)
    ctl2.resume_from(info)
    got = []
    ctl2.finished.connect(got.append)
    assert ctl2.start()
    assert ctl2.session.id == sid and ctx2.store.db.get_session(sid).status == "running"
    for i in (2, 3):
        _until(qtbot, lambda i=i: ctl2.state is not None and ctl2.state.phase == Phase.LACTATE_PAUSE
               and ctl2.state.stage_index == i, timeout=30000)
        ctl2.enter_lactate(3.0 + i)
    _until(qtbot, lambda: bool(got), timeout=30000)
    s = ctx2.store.db.get_session(sid)
    assert s.status == "finished" and s.result["resumed"][0]["from_stage"] == 2
    recs = ctx2.store.records(sid)
    assert [r.index for r in recs] == [0, 1, 2, 3]
    assert [r.lactate_mmol for r in recs] == [1.5, 2.0, 5.0, 6.0]
    smp = ctx2.store.samples(sid)
    assert len(smp) > n_before and all(b.t_s > a_.t_s for a_, b in zip(smp, smp[1:]))
    ctx2.shutdown()


def test_main_window_offers_the_cut_test(qapp, qtbot):
    ctx = _ctx()
    a = _athlete(ctx)
    ctl = LiveController(ctx, _lactate_plan(a))
    assert ctl.start()
    _until(qtbot, lambda: len(ctl.runner.records) >= 1)
    sid = ctl.session.id
    _until(qtbot, lambda: len(ctx.store.records(sid)) >= 1)   # saved, not only run (slow Windows CI)
    _crash(ctl)

    from lopanalys.ui.main_window import MainWindow
    ctx2 = _ctx()
    w = MainWindow(ctx2)                                      # QMessageBox.question answers Yes
    _until(qtbot, lambda: w.stack.currentWidget() is w.results)
    assert w.results.session.id == sid
    assert w.results.banner.isVisibleTo(w.results) and w.results.resume_btn.isVisibleTo(w.results)
    assert "Fortsätt från steg 2" == w.results.resume_btn.text()
    w.close()


def test_max_test_is_not_continued(qapp, qtbot):
    ctx = _ctx()
    a = _athlete(ctx)
    p = library.max_hr_ramp(10.0, mode="speed", step_s=20, warmup_s=0)
    ctl = LiveController(ctx, Plan(a, "max_hr", p, None, treadmill_mode="sim"))
    assert ctl.start()
    sid = ctl.session.id
    # until two stages are saved, not just run: the stage is written when its state event reaches the
    # GUI thread, and a crash before that rightly leaves one stage (the test failed 1 time in 20 on that)
    _until(qtbot, lambda: len(ctx.store.db.step_records(sid)) >= 2)
    _crash(ctl)
    ctx2 = _ctx()
    (s,) = recovery.collect_interrupted(ctx2.store)
    assert s.id == sid and len(ctx2.store.records(sid)) >= 2   # everything kept ...
    assert recovery.resume_info(ctx2.store, s) is None          # ... but not continued
    ctx2.shutdown()


def test_results_changes_are_saved(qapp, qtbot):
    ctx = _ctx()
    a = _athlete(ctx)
    ctl = LiveController(ctx, _lactate_plan(a, stages=2))
    got = []
    ctl.finished.connect(got.append)
    assert ctl.start()
    _until(qtbot, lambda: bool(got), timeout=30000)
    from lopanalys.ui.views.results import ResultsView
    rv = ResultsView(ctx)
    rv.load_session(ctl.session.id)
    rv.comment.setPlainText("Bra löpning")
    rv.table.item(0, 4).setText("2,3")                       # lactate typed afterwards
    assert rv._autosave.isActive()
    rv.flush()                                               # leaving the page
    sid = ctl.session.id
    assert ctx.store.db.get_session(sid).result["coach_comment"] == "Bra löpning"
    assert ctx.store.records(sid)[0].lactate_mmol == 2.3
    rv.method.setCurrentIndex(1)
    _until(qtbot, lambda: not rv._autosave.isActive(), timeout=3000)   # saved by itself shortly after
    assert ctx.store.db.get_session(sid).result["lt2_method"] == rv.method.currentData()
    ctx.shutdown()
