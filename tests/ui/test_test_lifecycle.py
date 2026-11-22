"""A whole test from the start to what is saved, across the controller, the runner, the simulated
devices and the database: what is stored must be what happened, also over a crash and a continue."""
from __future__ import annotations

from lopanalys.assessments import library, library_power
from lopanalys.assessments.protocol import Phase
from lopanalys.core.models import Athlete
from lopanalys.ui import recovery
from lopanalys.ui.context import AppContext
from lopanalys.ui.context import TestPlan as Plan
from lopanalys.ui.live_controller import LiveController

from tests.ui.test_test_saving import SCALE, _athlete, _crash, _ctx, _lactate_plan, _until


def _bike_ctx() -> AppContext:
    ctx = AppContext(demo=True, time_scale=SCALE)
    ctx.devices.connect_ergometer("bike", "sim")
    ctx.devices.connect_heart_rate("sim")
    return ctx


def test_lactate_typed_after_continue_goes_to_the_stage_before_the_cut(qapp, qtbot):
    """The program died in the pause after stage 2, before its value was typed. Before 2026-11-23 the
    value typed after *Fortsätt* was offered to the stage not yet run, and one given to stage 2 was
    dropped when the test was saved."""
    ctx = _ctx()
    a = _athlete(ctx)
    ctl = LiveController(ctx, _lactate_plan(a, stages=4))
    assert ctl.start()
    sid = ctl.session.id
    _until(qtbot, lambda: len(ctl.runner.records) >= 1 and ctl.state.phase == Phase.LACTATE_PAUSE)
    ctl.enter_lactate(1.5)
    _until(qtbot, lambda: len(ctx.store.records(sid)) >= 2)   # stage 2 is saved, its value not typed
    _crash(ctl)

    ctx2 = _ctx()
    (s,) = recovery.collect_interrupted(ctx2.store)
    info = recovery.resume_info(ctx2.store, s)
    assert info is not None and info.from_index == 2
    ctl2 = LiveController(ctx2, _lactate_plan(a, stages=4))
    ctl2.resume_from(info)
    got = []
    ctl2.finished.connect(got.append)
    assert ctl2.start()
    _until(qtbot, lambda: ctl2.state is not None)
    assert ctl2.lactate_stage() == 1                          # the sample taken before the cut comes first
    ctl2.enter_lactate(2.2, ctl2.lactate_stage())
    assert ctx2.store.records(sid)[1].lactate_mmol == 2.2     # saved at once
    ctl2.enter_rpe(13, 0)                                     # and a value for a stage long done
    for i, v in ((2, 3.3), (3, 5.0)):
        _until(qtbot, lambda i=i: ctl2.state.phase == Phase.LACTATE_PAUSE and ctl2.state.stage_index == i,
               timeout=30000)
        assert ctl2.lactate_stage() == i
        ctl2.enter_lactate(v, ctl2.lactate_stage())
    _until(qtbot, lambda: bool(got), timeout=30000)
    assert [r.lactate_mmol for r in ctx2.store.records(sid)] == [1.5, 2.2, 3.3, 5.0]
    assert [r.lactate_mmol for r in got[0].records] == [1.5, 2.2, 3.3, 5.0]
    assert ctx2.store.records(sid)[0].rpe == 13
    ctx2.shutdown()


def test_what_the_trainer_measured_is_kept_over_a_cut(qapp, qtbot):
    """The per-stage values of a test in watt (target, measured, cadence) were written only when the
    test ended: a cut test had none, and a continued one only those of the stages after the cut."""
    ctx = _bike_ctx()
    a = _athlete(ctx)
    p = library_power.bike_lactate_step(100, 25, 4, stage_s=40, pause_s=0, warmup_s=40)
    plan = Plan(a, "lactate", p, None, treadmill_mode="sim", sport="bike")
    ctl = LiveController(ctx, plan)
    assert ctl.start()
    sid = ctl.session.id
    _until(qtbot, lambda: len(ctx.store.records(sid)) >= 3)
    _crash(ctl)

    ctx2 = _bike_ctx()
    (s,) = recovery.collect_interrupted(ctx2.store)
    stats = {x["index"]: x for x in s.result.get("stage_stats") or []}
    assert sorted(stats)[:3] == [0, 1, 2] and stats[1]["target"] == 100 and stats[1]["cadence"]
    info = recovery.resume_info(ctx2.store, s)
    assert info is not None
    ctl2 = LiveController(ctx2, plan)
    ctl2.resume_from(info)
    got = []
    ctl2.finished.connect(got.append)
    assert ctl2.start()
    _until(qtbot, lambda: bool(got), timeout=30000)
    saved = ctx2.store.db.get_session(sid)
    assert [x["index"] for x in saved.result["stage_stats"]] == [r.index for r in ctx2.store.records(sid)]
    ctx2.shutdown()


def test_exhaustion_is_saved_when_it_happens(qapp, qtbot):
    """*Utmattad* in a lactate test to max, then the program dies in the recovery minute: the time of
    exhaustion was only written when the test ended, so the reopened test was not one to the limit."""
    ctx = _ctx()
    a = _athlete(ctx)
    p = library.lactate_to_max(10.0, 1.0, 2, stage_s=40, pause_s=0, warmup_s=0)
    ctl = LiveController(ctx, Plan(a, "lactate", p, None, treadmill_mode="sim"))
    assert ctl.start()
    sid = ctl.session.id
    _until(qtbot, lambda: len(ctl.runner.records) >= 3, timeout=30000)
    ctl.exhausted()
    _until(qtbot, lambda: ctl.state.phase == Phase.RECOVERY)
    qtbot.wait(100)
    t_exh = ctl.runner.exhaustion_t_s
    _crash(ctl)

    ctx2 = _ctx()
    (s,) = recovery.collect_interrupted(ctx2.store)
    assert s.result.get("exhaustion_t_s") == t_exh
    ctx2.shutdown()


def test_a_test_cut_a_second_time_ends_when_it_was_cut(qapp):
    """The samples' time leaves out the break before a test was continued. A test continued once and
    cut again was given an end that much too early: the question named the wrong time, and *Fortsätt*
    was refused although the athlete had stopped a minute ago."""
    from datetime import datetime, timedelta, timezone

    from lopanalys.core.models import Session, StepRecord, TestKind, TimeSample

    ctx = _ctx()
    a = _athlete(ctx)
    now = datetime.now(timezone.utc)
    p = library.lactate_step(10.0, 1.0, 5, stage_s=240, pause_s=30, warmup_s=0)
    # started 40 min ago, cut after 10 min, continued 6 min ago (a break of 24 min less the 10) and cut 1 min ago
    s = Session(None, a.id, TestKind.LACTATE_STEP, now - timedelta(minutes=40), protocol_name=p.name,
                protocol=p.to_dict(), status="running",
                result={"plan_kind": "lactate", "resumed": [
                    {"from_stage": 2, "at": (now - timedelta(minutes=6)).isoformat(timespec="seconds"), "t_s": 600.0}]})
    recs = [StepRecord(index=i, speed_kmh=10.0 + i, incline_pct=1.0, duration_s=240.0, hr_end=140 + 8 * i)
            for i in range(3)]
    ctx.store.db.save_test(s, recs, [TimeSample(t_s=float(i), hr=150, speed_kmh=10.0) for i in range(900)])

    (cut,) = recovery.collect_interrupted(ctx.store)
    assert abs((cut.ended - (now - timedelta(minutes=1))).total_seconds()) < 5
    assert abs((ctx.store.db.get_session(s.id).ended - (now - timedelta(minutes=1))).total_seconds()) < 5
    info = recovery.resume_info(ctx.store, cut, now)
    assert info is not None and info.from_index == 3 and info.t_offset_s == 900.0
    ctx.shutdown()
