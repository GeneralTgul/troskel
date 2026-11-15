"""Cadence from the heart rate strap (RSC) before the camera's: runner samples, storage, analysis."""
from __future__ import annotations

import pytest

from lopanalys.assessments import library
from lopanalys.assessments.cadence import strap_cadence, with_strap_cadence
from lopanalys.assessments.runner import TestRunner
from lopanalys.core import i18n
from lopanalys.core.bus import Bus
from lopanalys.core.models import TimeSample
from lopanalys.devices import sim
from lopanalys.pose.offline import analyze_poses
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import GaitAnalysis, View
from lopanalys.storage.db import pack_samples, unpack_samples


@pytest.fixture(autouse=True)
def _sv():
    i18n.set_language("sv")
    yield


class _Adapter:
    def __init__(self, mc):
        self.mc = mc

    def now(self):
        return self.mc()

    def sleep(self, dt):
        self.mc.advance(dt)


@pytest.mark.parametrize("rsc", [True, False])
def test_runner_samples_take_the_strap_cadence(rsc):
    bus = Bus()
    mc = sim.ManualClock(0.0)
    tm = sim.SimTreadmill(bus, clock=mc, autorun=False, seed=1)
    hr = sim.SimHeartRate(bus, treadmill=tm, clock=mc, autorun=False, seed=1, rsc=rsc)
    tm.connect()
    hr.connect()
    r = TestRunner(library.max_hr_ramp(12.0, warmup_s=60, step_s=60), tm, hr, bus, clock=_Adapter(mc), threaded=False)
    r.start()
    for _ in range(int(150 / 0.25)):
        mc.advance(0.25)
        tm.tick()
        hr.tick()
        r.tick()
    running = [s for s in r.samples if (s.speed_kmh or 0) > 5]
    assert running
    if rsc:
        assert all(s.cadence_source == "strap" and 160 < s.cadence_spm < 200 for s in running[2:])
        assert strap_cadence(r.samples) == pytest.approx(150 + 2.6 * running[-1].speed_kmh, abs=6)
    else:
        assert all(s.cadence_source is None and s.cadence_spm is None for s in running)   # no camera here
        assert strap_cadence(r.samples) is None


def test_samples_keep_the_source_in_storage():
    s = [TimeSample(0.0, 120, 10.0, 1.0, 172.0, "strap"), TimeSample(1.0, 121, 10.0, 1.0, 168.0, "camera")]
    assert unpack_samples(pack_samples(s)[1]) == s
    import json
    import zlib
    old = zlib.compress(json.dumps({"t": [0.0], "hr": [120], "v": [10.0], "inc": [1.0], "cad": [170.0]}).encode())
    assert unpack_samples(old) == [TimeSample(0.0, 120, 10.0, 1.0, 170.0, None)]   # saved before the source


def _samples(cad: float, n: int = 30, speed: float = 12.0) -> list[TimeSample]:
    return [TimeSample(float(i), 150, speed, 1.0, cad, "strap") for i in range(n)]


def test_strap_cadence_needs_running_samples():
    assert strap_cadence(_samples(176)) == 176
    assert strap_cadence(_samples(176, n=5)) is None                 # too few
    assert strap_cadence(_samples(110, speed=2.0)) is None           # walking/standing
    assert strap_cadence([TimeSample(float(i), 150, 12.0, 1.0, 230.0, "camera") for i in range(30)]) is None


@pytest.fixture(scope="module")
def low_cadence_analysis():
    p = RunnerParams(fps=60, width=640, height=360, duration_s=6.0, cadence_spm=150, noise_px=0.5)
    poses, _ = generate(p)
    return analyze_poses(poses, p.height_cm, View.SIDE_LEFT, speed_kmh=12.0)


def test_strap_cadence_replaces_the_camera_in_the_analysis(low_cadence_analysis):
    a = low_cadence_analysis
    cam = next(f for f in a.findings if f.code == "cadence")
    assert cam.severity.value != "ok" and a.summary.cadence_spm == pytest.approx(150, abs=3)
    d = with_strap_cadence(a, _samples(178), a.height_cm)
    assert d["summary"]["cadence_spm"] == 178 and d["summary"]["cadence_source"] == "strap"
    assert d["summary"]["cadence_camera_spm"] == pytest.approx(150, abs=3)
    f = next(f for f in d["findings"] if f["code"] == "cadence")
    assert f["severity"] == "ok" and f["value"] == 178 and f["title"] == "Kadens (pulsbandet)"
    assert f["frame_index"] == cam.frame_index
    assert d["ranges"]["cadence_spm"]["value"] == 178 and d["ranges"]["cadence_spm"]["severity"] == "ok"
    assert GaitAnalysis.from_dict(d).summary.cadence_source == "strap"    # survives the stored round trip
    # without strap cadence nothing changes
    assert with_strap_cadence(a, [], a.height_cm) is a
    assert with_strap_cadence(None, _samples(178)) is None
