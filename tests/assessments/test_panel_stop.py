"""Alone on the treadmill the athlete stops the belt on the panel: the test must end by itself and the
belt must never be commanded again."""
from dataclasses import dataclass
from types import SimpleNamespace

from lopanalys.assessments import library
from lopanalys.assessments.protocol import Phase
from lopanalys.assessments.runner import PANEL_STOP_S, TestRunner
from lopanalys.core.bus import Bus

from .fakes import FakeClock, FakeHR, FakeTreadmill, run, run_until


@dataclass
class PanelTreadmill(FakeTreadmill):
    panel_stopped: bool = False

    @property
    def state(self):
        return SimpleNamespace(speed_kmh=0.0 if self.panel_stopped else self.speed, incline_pct=self.incline)


def _make(protocol):
    clock = FakeClock()
    tm = PanelTreadmill()
    hr = FakeHR(clock, lambda: (0.0, 0.0) if tm.panel_stopped else (tm.speed, tm.incline))
    r = TestRunner(protocol, tm, hr, Bus(), clock=clock, threaded=False)
    return r, clock, tm


def test_max_test_ends_as_exhausted_when_the_belt_is_stopped_on_the_panel():
    p = library.max_hr_ramp(10.0, mode="speed", warmup_s=120)
    r, clock, tm = _make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RUNNING and r._idx >= 3)
    n_cmds = len(tm.log)
    tm.panel_stopped = True
    run(r, clock, PANEL_STOP_S + 1.0)
    assert r.phase == Phase.RECOVERY and r.exhaustion_t_s is not None
    run_until(r, clock, lambda: r.done)
    assert r.phase == Phase.FINISHED
    assert tm.log[n_cmds:] == []          # nothing sent to the belt after it was stopped


def test_a_short_dip_is_not_a_stop():
    p = library.max_hr_ramp(10.0, mode="speed", warmup_s=120)
    r, clock, tm = _make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.RUNNING and r._idx >= 2)
    tm.panel_stopped = True
    run(r, clock, PANEL_STOP_S - 1.0)
    tm.panel_stopped = False
    run(r, clock, 5.0)
    assert r.phase == Phase.RUNNING


def test_a_lactate_test_is_stopped_and_its_pause_does_not_count():
    p = library.lactate_step(10.0, 1.0, 3, stage_s=240, pause_s=30, warmup_s=120)
    r, clock, tm = _make(p)
    r.start()
    run_until(r, clock, lambda: r.phase == Phase.LACTATE_PAUSE)
    tm.running = False
    run(r, clock, 10.0)
    assert r.phase == Phase.LACTATE_PAUSE           # standing still in the pause is expected
    r.enter_lactate(0, 1.2)
    run_until(r, clock, lambda: r.phase == Phase.RUNNING)
    run(r, clock, 30.0)
    tm.panel_stopped = True
    run(r, clock, PANEL_STOP_S + 1.0)
    assert r.done and r.phase != Phase.FINISHED
