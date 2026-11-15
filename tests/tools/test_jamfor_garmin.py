"""tools/jamfor_garmin.py: the film at several rates against the watch's running dynamics."""
from __future__ import annotations

import pickle
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import Pose
from lopanalys.share.fit_read import DynSample

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import jamfor_garmin as jg  # noqa: E402

FILM_START = 1_794_650_000.0          # Unix time of the film's first frame
# three speeds, as in a real run: (seconds, cadence, contact time)
SEGMENTS = [(40, 160, 300), (40, 170, 260), (40, 180, 225)]


def _film(fps: float) -> tuple[list, list]:
    poses: list = []
    t_start = 0.0
    for i, (dur, cad, gct) in enumerate(SEGMENTS):
        ps, truth = generate(RunnerParams(fps=fps, duration_s=dur, cadence_spm=cad, ground_contact_ms=gct,
                                          noise_px=1.0, seed=i + 1))
        for p in ps:
            poses.append(Pose(p.t + t_start, len(poses), p.xy, p.visibility, p.width, p.height))
        t_start += dur
    return poses, [p.t for p in poses]


def _watch(lead_s: float = 300.0, tail_s: float = 200.0) -> list[DynSample]:
    """1 Hz records from 5 minutes before the film to after it; walking (no dynamics) outside it."""
    rng = np.random.default_rng(7)
    out, t = [], FILM_START - lead_s
    total = sum(s[0] for s in SEGMENTS)
    while t < FILM_START + total + tail_s:
        rel = t - FILM_START
        seg = None
        acc = 0.0
        for dur, cad, gct in SEGMENTS:
            if acc <= rel < acc + dur:
                seg = (cad, gct)
            acc += dur
        if seg is None:
            cad, gct = 110 + rng.normal(0, 2), 380 + rng.normal(0, 8)
        else:
            cad, gct = seg[0] + rng.normal(0, 1), seg[1] + rng.normal(0, 5)
        out.append(DynSample(t, 140, cad, gct, 8.0 + rng.normal(0, 0.2), 7.0, 50.0, 1.1, 12.0))
        t += 1.0
    return out


@pytest.fixture(scope="module")
def runs():
    poses, times = _film(120)
    return jg.analyse_rates(poses, times, 178.0, "side_left", None, [1, 4]), float(times[-1] - times[0])


def test_the_clock_is_found_from_cadence_and_contact_time(runs):
    rr, duration = runs
    al = jg.align(rr[0], _watch(), duration)
    assert al.offset == pytest.approx(FILM_START, abs=3)
    assert al.score > 0.7
    # from a creation time 10 minutes wrong (a camera clock), the search still lands right
    al = jg.align(rr[0], _watch(), duration, guess=FILM_START + 600)
    assert al.offset == pytest.approx(FILM_START, abs=3)


def test_windows_statistics_and_requirements(runs):
    rr, duration = runs
    assert rr[0].fps == pytest.approx(120, abs=1) and rr[1].fps == pytest.approx(30, abs=1)
    rows, stats = jg.compare(rr, _watch(), FILM_START, duration, 20.0)
    assert len(rows) == 6
    cad = next(s for s in stats if s.metric == "cadence_spm" and s.against == "watch" and s.fps > 100)
    assert cad.n >= 4 and cad.ok and abs(cad.bias) < 2
    vs_full = [s for s in stats if s.against == "full rate"]
    assert {s.metric for s in vs_full} == set(jg.TOLERANCE)
    req = jg.requirements(rr, stats)
    assert req["cadence_spm"] == pytest.approx(30, abs=1)        # holds at 30 fps
    text = jg.report(rr, stats, req, None)
    assert "cadence_spm" in text and "lowest tested rate" in text


def test_main_without_a_watch_file(tmp_path):
    poses, times = generate(RunnerParams(fps=60, duration_s=20, noise_px=1.0))
    pk = tmp_path / "poses.pkl"
    with pk.open("wb") as f:
        pickle.dump({"poses": poses, "times": [p.t for p in poses]}, f)
    assert jg.main([str(pk), "--height", "178", "--every", "2", "--window", "10"]) == 0
    assert (tmp_path / "jamforelse.csv").is_file()
    assert "every 2" in (tmp_path / "jamforelse.txt").read_text(encoding="utf-8")


def test_mp4_creation_time(tmp_path):
    def atom(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I4s", 8 + len(body), kind) + body
    created = int(FILM_START) + jg.MP4_EPOCH
    mvhd = atom(b"mvhd", b"\0\0\0\0" + struct.pack(">II", created, created) + b"\0" * 88)
    f = tmp_path / "GX010001.MP4"
    f.write_bytes(atom(b"ftyp", b"mp41") + atom(b"mdat", b"\0" * 100) + atom(b"moov", mvhd))
    assert jg.mp4_creation_time(f) == FILM_START
    assert jg.mp4_creation_time(tmp_path / "missing.mp4") is None
    # the GoPro's wall clock in Stockholm (UTC+1 in November) is one hour after UTC
    assert jg.local_as_unix(FILM_START) == FILM_START - 3600
