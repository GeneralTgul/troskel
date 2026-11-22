import time

import pytest

from lopanalys.assessments import library
from lopanalys.assessments.protocol import Phase, Protocol, Stage
from lopanalys.assessments.runner import TOPIC_TEST_CUE, SystemClock, TestRunner
from lopanalys.core.bus import TOPIC_TEST_STATE, Bus

from .fakes import FakeClock, FakeHR, FakeTreadmill, run, run_until


def make(protocol, controllable=True, **kw):
    clock = FakeClock()
    tm = FakeTreadmill(controllable=controllable)
    hr = FakeHR(clock, lambda: (tm.speed, tm.incline) if controllable else runner_belt[0]())
    bus = Bus()
    states, cues = [], []
    bus.subscribe(TOPIC_TEST_STATE, states.append)
    bus.subscribe(TOPIC_TEST_CUE, cues.append)
    r = TestRunner(protocol, tm, hr, bus, clock=clock, threaded=False, **kw)
    runner_belt[0] = lambda: (r._prev_set or (0.0, 0.0)) if r.phase == Phase.RUNNING else (0.0, 0.0)
    return r, clock, tm, states, cues


runner_belt = [lambda: (0.0, 0.0)]


def small_lactate(pause_s=30, stages=4):
    return library.lactate_step(10.0, 1.0, stages, stage_s=240, pause_s=pause_s, warmup_s=300)


def test_controllable_lactate_test_end_to_end():
    p = small_lactate()
    r, clock, tm, states, cues = make(p)
    r.start()
    assert r.phase == Phase.COUNTDOWN
    run(r, clock, 10.25)
    assert r.phase == Phase.RUNNING
    assert tm.log[0] == ("start",) and tm.log[1][0] == "set" and tm.log[1][1] == p.stages[0].speed_kmh
    # warm-up 300 s, then lactate pause
    run_until(r, clock, lambda: r.phase == Phase.LACTATE_PAUSE)
    assert ("stop",) in tm.log
    assert states[-1].prompt == "Ta laktatprov"
    assert states[-1].pending_lactate == [0]
    r.enter_lactate(0, 1.1)
    run_until(r, clock, lambda: r.phase == Phase.RUNNING)
    assert tm.speed == 10.0 and tm.running
    for i in range(1, 5):
        run_until(r, clock, lambda: r.phase == Phase.LACTATE_PAUSE)
        r.enter_lactate(i, 1.0 + i * 0.6)
        r.enter_rpe(i, 10 + i)
        if i < 4:
            run_until(r, clock, lambda: r.phase == Phase.RUNNING)
    run_until(r, clock, lambda: r.done)
    assert r.phase == Phase.FINISHED
    recs = r.records
    assert [x.index for x in recs] == [1, 2, 3, 4]
    assert [x.speed_kmh for x in recs] == [10.0, 11.0, 12.0, 13.0]
    assert all(abs(x.duration_s - 240) < 0.6 for x in recs)
    assert r.warmup_records[0].lactate_mmol == 1.1
    assert recs[2].lactate_mmol == pytest.approx(2.8) and recs[2].rpe == 13
    # HR rises with speed and the stage HR is the mean of the last 30 s
    hrs = [x.hr_end for x in recs]
    assert hrs == sorted(hrs) and hrs[0] > 100
    # 1 Hz samples cover the whole test
    sm = r.samples
    assert sm[0].t_s == 0 and all(abs(b.t_s - a.t_s - 1) < 1e-6 for a, b in zip(sm, sm[1:]))
    assert len(sm) >= 10 + 300 + 4 * 240 + 5 * 30 - 2
    # cues: pre-announcements, countdown beeps and lactate cues
    kinds = [c.kind for c in cues]
    assert kinds.count("lactate") == 5
    assert kinds.count("pre_announce") >= 5
    assert "countdown" in kinds and kinds[-1] == "finish"
    # state was published at about 4 Hz
    assert len(states) > 4 * 1300 * 0.9


def test_lactate_on_the_move_keeps_belt_running():
    p = small_lactate(pause_s=0, stages=3)
    r, clock, tm, states, cues = make(p)
    r.start()
    run_until(r, clock, lambda: r.state.stage_index == 1 and r.phase == Phase.RUNNING)
    assert ("stop",) not in tm.log
    assert states[-1].prompt == "Ta laktatprov"
    run(r, clock, 25)
    assert r.state.prompt == ""          # flash cleared after 20 s
    assert r.state.pending_lactate == [0]
    run_until(r, clock, lambda: r.done)
    assert sorted(r.state.pending_lactate) == [0, 1, 2, 3]
    r.enter_lactate(3, 4.2)              # entered after the test
    assert r.records[-1].lactate_mmol == 4.2


def test_manual_mode_prompts_and_waits_for_operator():
    p = library.lactate_step(9.0, 1.0, 2, stage_s=120, pause_s=20, warmup_s=0)
    r, clock, tm, states, cues = make(p, controllable=False)
    r.start()
    assert r.phase == Phase.AWAIT_OPERATOR
    assert states[-1].prompt == "Sätt bandet på 6:40 min/km (9,0 km/h) · 1 %"
    run(r, clock, 30)                    # nothing happens until the operator confirms
    assert r.phase == Phase.AWAIT_OPERATOR and r.state.stage_elapsed_s == 0
    assert r.operator_ready()
    assert r.phase == Phase.RUNNING
    run(r, clock, 105.25)                # 15 s before the end: pre-announcement
    assert r.state.announcement.startswith("Om 15 s")
    assert any(c.kind == "pre_announce" for c in cues)
    run_until(r, clock, lambda: r.phase == Phase.LACTATE_PAUSE)
    assert r.state.announcement == "Sedan: 6:00 min/km (10,0 km/h) · 1 %"
    run_until(r, clock, lambda: r.phase == Phase.AWAIT_OPERATOR)
    assert r.state.prompt == "Sätt bandet på 6:00 min/km (10,0 km/h) · 1 %"
    assert tm.log == []                  # never commands a manual treadmill
    assert r.operator_ready()
    assert not r.operator_ready()        # second press ignored
    run_until(r, clock, lambda: r.done)
    assert [x.speed_kmh for x in r.records] == [9.0, 10.0]
    # manual mode: speed in samples is the target while running
    assert any(s.speed_kmh == 10.0 for s in r.samples)


def test_open_ended_ramp_to_exhaustion_and_recovery():
    p = library.max_hr_ramp(12.0, warmup_s=120, step_s=60, incline_step_pct=1.0, start_incline_pct=1.0)
    r, clock, tm, states, cues = make(p, recovery_s=65)
    r.start()
    run(r, clock, 10 + 120 + 60 * 7 + 30)   # 7.5 min into the ramp
    assert r.phase == Phase.RUNNING
    st = r.state
    assert st.stage.incline_pct == 8.0 and st.stage.label == "Trappa 8"
    assert st.next_stage.incline_pct == 9.0
    r.exhausted()
    assert r.phase == Phase.RECOVERY
    assert tm.speed == 5.0 and tm.incline == 0.0
    ex = r.exhaustion_t_s
    assert ex == pytest.approx(10 + 120 + 450, abs=0.5)
    recs = r.records
    assert len(recs) == 8 and recs[-1].duration_s == pytest.approx(30, abs=0.5)
    assert [x.incline_pct for x in recs] == [1, 2, 3, 4, 5, 6, 7, 8]
    run_until(r, clock, lambda: r.done)
    assert r.phase == Phase.FINISHED
    assert r.samples[-1].t_s >= ex + 60
    from lopanalys.assessments import max_hr
    res = max_hr.analyze(r.samples, end_t_s=ex, records=recs)
    assert res.peak_hr and res.recovery_hr_60 and res.recovery_drop_60 > 0


def test_ramp_clamps_to_treadmill_and_has_safety_cap():
    p = library.max_hr_ramp(12.0, warmup_s=0, step_s=60)
    p.max_open_ended_s = 15 * 60
    r, clock, tm, *_ = make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RECOVERY)
    assert max(x.incline_pct for x in r.records) == tm.max_incline_pct
    assert r.exhaustion_t_s == pytest.approx(10 + 15 * 60, abs=1)


def test_final_sprint_ends_the_test():
    p = library.max_hr_ramp(12.0, warmup_s=0, step_s=60, final_sprint_s=30, final_sprint_kmh=2.0)
    r, clock, tm, *_ = make(p)
    r.start()
    run(r, clock, 10 + 150)
    assert r.final_sprint()
    assert tm.speed == 14.0 and r.state.stage.label == "Slutspurt"
    run_until(r, clock, lambda: r.phase == Phase.RECOVERY)
    recs = r.records
    assert recs[-1].speed_kmh == 14.0 and recs[-1].duration_s == pytest.approx(30, abs=0.5)
    assert recs[-2].duration_s == pytest.approx(30, abs=0.5)   # the interrupted ramp stage


def test_pause_resume_and_abort():
    p = small_lactate(stages=2)
    r, clock, tm, states, cues = make(p)
    r.start()
    run(r, clock, 10 + 100)
    el = r.state.stage_elapsed_s
    r.pause()
    assert r.state.paused and not tm.running
    run(r, clock, 60)
    assert r.state.stage_elapsed_s == el
    r.resume()
    assert r.phase == Phase.COUNTDOWN
    run(r, clock, 5.25)
    assert r.phase == Phase.RUNNING and tm.running
    assert r.state.stage_elapsed_s == pytest.approx(el, abs=0.5)
    r.stop()
    assert r.phase == Phase.ABORTED and not tm.running
    assert cues[-1].kind == "abort"
    r.stop()   # idempotent
    run(r, clock, 5)
    assert r.phase == Phase.ABORTED


def test_treadmill_failure_falls_back_to_manual():
    p = small_lactate(stages=2)
    r, clock, tm, states, cues = make(p)
    r.start()
    tm.fail_on_set = True
    run(r, clock, 10.25)
    assert r.phase == Phase.AWAIT_OPERATOR
    assert not r.state.controllable
    assert r.state.prompt.startswith("Sätt bandet på")
    assert r.operator_ready()
    assert r.phase == Phase.RUNNING


def test_input_validation():
    p = small_lactate(stages=2)
    r, *_ = make(p)
    r.start()
    with pytest.raises(ValueError):
        r.enter_lactate(1, 45)
    with pytest.raises(ValueError):
        r.enter_lactate(1, "abc")
    with pytest.raises(ValueError):
        r.enter_rpe(1, 25)
    with pytest.raises(ValueError):
        r.enter_rpe(99, 12)
    r.enter_lactate(2, 3.3)              # before the stage ends: kept and applied
    r.enter_rpe(2, 15.0)
    assert r._early_values[2] == {"lactate_mmol": 3.3, "rpe": 15}


def test_no_devices_does_not_crash():
    p = library.gait_check([10, 12], stage_s=60)
    clock = FakeClock()
    r = TestRunner(p, None, None, clock=clock, threaded=False)
    r.start()
    assert r.phase == Phase.AWAIT_OPERATOR
    r.operator_ready()
    run(r, clock, 61)
    assert r.phase == Phase.AWAIT_OPERATOR
    r.operator_ready()
    run(r, clock, 61)
    assert r.done and len(r.records) == 2 and r.records[0].hr_end is None


def test_gait_summary_from_bus_is_stored_per_stage():
    from lopanalys.core.bus import TOPIC_GAIT_SUMMARY
    p = library.gait_check([10.0], stage_s=60)
    r, clock, tm, *_ = make(p)
    r.start()
    while not r.records:                                   # the camera sends a summary every second
        r.bus.publish(TOPIC_GAIT_SUMMARY, {"cadence_spm": 172.0})
        run(r, clock, 1.0)
    assert r.records[0].gait == {"cadence_spm": 172.0}


def test_a_stage_after_the_camera_stopped_gets_no_gait_values():
    # The runner kept the last summary for ever: every later stage was saved with the cadence of the
    # moment the camera stopped, and the samples carried it as the camera's cadence.
    from lopanalys.core.bus import TOPIC_GAIT_SUMMARY
    p = library.gait_check([10.0, 14.0], stage_s=60, warmup_s=0)
    r, clock, tm, *_ = make(p)
    r.start()
    while not r.records:
        r.bus.publish(TOPIC_GAIT_SUMMARY, {"cadence_spm": 172.0})
        run(r, clock, 1.0)
    run_until(r, clock, lambda: len(r.records) == 2)        # the camera is silent through the second stage
    assert r.records[0].gait == {"cadence_spm": 172.0} and r.records[1].gait == {}


def test_a_strap_that_went_silent_gives_no_heart_rate():
    # ``last`` of a strap is never cleared. After a dropout its last heart rate was saved in every
    # sample and as every stage's heart rate for the rest of the test, and shown on the TV.
    from lopanalys.core.bus import TOPIC_HEART_RATE
    from lopanalys.devices.base import HeartRateSample

    class Strap:
        last = None

    clock, bus, strap = FakeClock(), Bus(), Strap()
    r = TestRunner(library.lactate_step(10.0, 1.0, 2, stage_s=240, pause_s=0, warmup_s=0), FakeTreadmill(), strap,
                   bus, clock=clock, threaded=False)
    r.start()
    for _ in range(100):
        strap.last = HeartRateSample(t=clock.now(), bpm=150)
        bus.publish(TOPIC_HEART_RATE, strap.last)
        run(r, clock, 1.0)
    run_until(r, clock, lambda: r.done)
    assert {x.hr for x in r.samples if x.t_s < 95} == {150}
    assert {x.hr for x in r.samples if x.t_s > 110} == {None}
    assert [x.hr_end for x in r.records] == [None, None] and r.state.hr_bpm is None


def test_stages_set_by_hand_after_the_link_was_lost_keep_their_own_speed():
    # The treadmill's ``state`` keeps the last reading when Bluetooth is lost. The runner went over to
    # manual prompts but still read that frozen speed as measured: 11 and 12 km/h were saved as 10.
    p = library.lactate_step(10.0, 1.0, 3, stage_s=240, pause_s=0, warmup_s=0)
    r, clock, tm, states, cues = make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RUNNING)
    run(r, clock, 120)
    tm.fail_on_set = True
    while not r.done:
        run(r, clock, 1.0)
        if r.phase == Phase.AWAIT_OPERATOR:
            r.operator_ready()
    assert [x.speed_kmh for x in r.records] == [10.0, 11.0, 12.0] and not r.belt_stats
    assert {x.speed_kmh for x in r.samples if x.t_s > 520} == {12.0}


def test_a_pause_in_a_stage_is_not_the_stages_speed_and_not_a_change_by_hand():
    # A belt needs some 20 s from standstill to 10 km/h. The stop, the standstill and the way back up
    # counted as the stage's readings: the stage was saved at 9.8 km/h, marked "set by hand", and
    # the lactate point landed at the wrong speed.
    from lopanalys.core.bus import TOPIC_LOG
    from lopanalys.devices import sim

    class Adapter:
        def __init__(self, mc): self.mc = mc
        def now(self): return self.mc()
        def sleep(self, dt): self.mc.advance(dt)

    bus, logs = Bus(), []
    bus.subscribe(TOPIC_LOG, logs.append)
    mc = sim.ManualClock(0.0)
    tm = sim.SimTreadmill(bus, clock=mc, autorun=False, seed=1)
    hr = sim.SimHeartRate(bus, treadmill=tm, clock=mc, autorun=False, seed=1)
    tm.connect(), hr.connect()
    r = TestRunner(library.lactate_step(10.0, 1.0, 2, stage_s=240, pause_s=0, warmup_s=0), tm, hr, bus,
                   clock=Adapter(mc), threaded=False)

    def step(seconds):
        for _ in range(int(seconds / 0.25)):
            mc.advance(0.25), tm.tick(), hr.tick(), r.tick()

    r.start()
    step(10 + 120)
    r.pause(), step(30), r.resume()
    while len(r.records) < 2:
        step(1)
    assert [x.speed_kmh for x in r.records] == [10.0, 11.0]
    assert not r.belt_stats and not logs


def test_threaded_with_real_clock():
    p = Protocol("t", "gait", [Stage(10, 1, 0.6), Stage(11, 1, 0.6)])
    tm = FakeTreadmill()
    bus = Bus()
    r = TestRunner(p, tm, None, bus, clock=SystemClock(), countdown_s=0.2, tick_s=0.05, recovery_s=0)
    r.start()
    t0 = time.perf_counter()
    while not r.done and time.perf_counter() - t0 < 5:
        time.sleep(0.05)
    r.close()
    assert r.phase == Phase.FINISHED
    assert [x.speed_kmh for x in r.records] == [10, 11]
    assert r._thread is not None and not r._thread.is_alive()


def test_time_scale_runs_faster():
    p = Protocol("t", "gait", [Stage(10, 1, 4.0)])
    r = TestRunner(p, FakeTreadmill(), None, Bus(), countdown_s=1.0, tick_s=0.02, time_scale=20, recovery_s=0)
    r.start()
    t0 = time.perf_counter()
    while not r.done and time.perf_counter() - t0 < 3:
        time.sleep(0.02)
    r.close()
    assert r.phase == Phase.FINISHED and time.perf_counter() - t0 < 1.5


def test_final_sprint_while_paused_does_not_start_the_belt():
    # The belt stands still in a pause. "Slutspurt" then started it at the sprint speed while the
    # test still said paused, and the stage clock stood still.
    p = library.max_hr_ramp(12.0, warmup_s=0, step_s=60, final_sprint_s=30, final_sprint_kmh=2.0)
    r, clock, tm, states, cues = make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RUNNING)
    run(r, clock, 20)
    r.pause()
    assert not tm.running
    assert not r.final_sprint()
    assert not tm.running and r.state.paused and r.state.stage_index == 0
    r.resume()
    run_until(r, clock, lambda: r.phase == Phase.RUNNING)
    assert r.final_sprint() and tm.speed == 14.0
