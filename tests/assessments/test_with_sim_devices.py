"""Integration: the runner with the devices agent's simulators (deterministic clock)."""
import pytest

sim = pytest.importorskip("lopanalys.devices.sim")

from lopanalys.assessments import library, max_hr  # noqa: E402
from lopanalys.assessments.protocol import Phase  # noqa: E402
from lopanalys.assessments.runner import TestRunner  # noqa: E402
from lopanalys.core.bus import Bus  # noqa: E402


class Adapter:
    def __init__(self, mc):
        self.mc = mc

    def now(self):
        return self.mc()

    def sleep(self, dt):
        self.mc.advance(dt)


def test_max_hr_ramp_with_simulators():
    bus = Bus()
    mc = sim.ManualClock(0.0)
    tm = sim.SimTreadmill(bus, clock=mc, autorun=False, seed=1)
    hr = sim.SimHeartRate(bus, treadmill=tm, clock=mc, autorun=False, seed=1)
    tm.connect()
    hr.connect()
    p = library.max_hr_ramp(12.0, warmup_s=300, step_s=60)
    r = TestRunner(p, tm, hr, bus, clock=Adapter(mc), threaded=False)
    r.start()

    def step(seconds):
        for _ in range(int(seconds / 0.25)):
            mc.advance(0.25)
            tm.tick()
            hr.tick()
            r.tick()

    step(10 + 300 + 60 * 10 + 20)
    assert r.phase == Phase.RUNNING
    r.exhausted()
    ex = r.exhaustion_t_s
    while not r.done:
        step(1)
    res = max_hr.analyze(r.samples, end_t_s=ex, records=r.records, age=35)
    assert res.peak_hr is not None and 150 < res.peak_hr <= 195
    assert res.recovery_drop_60 is not None and res.recovery_drop_60 > 0
    assert [x.hr_end for x in r.records] == sorted(x.hr_end for x in r.records)
