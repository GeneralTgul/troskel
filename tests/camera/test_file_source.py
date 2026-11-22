from __future__ import annotations

import json
import time

import pytest

from lopanalys.camera.base import SourceKind
from lopanalys.camera.discovery import make_source
from lopanalys.camera.file_source import FileSource, file_info, sidecar_path
from lopanalys.camera.opencv_source import CameraError


def test_info_and_pts(video120):
    info = file_info(video120)
    assert info.kind == SourceKind.FILE and info.live is False
    assert (info.width, info.height) == (160, 120)
    assert info.fps == pytest.approx(120, rel=0.01)
    with FileSource(info) as src:
        assert src.frame_count == 120
        frames = list(src.frames())
    assert len(frames) == 120
    assert [f.index for f in frames] == list(range(120))
    assert frames[0].t == pytest.approx(0.0, abs=1e-6)
    assert frames[60].t == pytest.approx(0.5, abs=1e-3)
    assert all(b.t > a.t for a, b in zip(frames, frames[1:]))
    assert src.duration == pytest.approx(1.0, abs=0.01)


def test_seek(video30):
    with FileSource(video30) as src:
        src.seek(1.0)
        f = src.read()
        assert f is not None and f.index == 30
        assert f.t == pytest.approx(1.0, abs=1e-3)
        src.seek_frame(10_000)          # clamped to the last frame
        assert src.read().index == 44
        assert src.read() is None


def test_realtime_pacing_and_loop(video30):
    src = make_source(file_info(video30), pace="realtime", loop=True, speed=3.0)
    start = time.perf_counter()
    got = []
    for f in src.frames():
        got.append(f)
        if len(got) == 60:   # 45 frames + 15 into the second loop
            break
    elapsed = time.perf_counter() - start
    src.close()
    # 59 intervals of 1/30 s at 3x speed = 0.66 s
    assert 0.5 < elapsed < 1.5
    assert [f.index for f in got] == list(range(60))
    assert all(b.t > a.t for a, b in zip(got, got[1:]))
    assert got[45].t == pytest.approx(1.5, abs=1e-3)


def test_sidecar_timestamps_win(video30):
    stamps = [i * 0.04 for i in range(45)]   # 25 fps real timing in a 30 fps container
    sidecar_path(video30).write_text(json.dumps({"t": stamps}))
    with FileSource(video30) as src:
        frames = list(src.frames())
        assert frames[10].t == pytest.approx(0.4)
        src.seek(0.8)
        assert src.read().index == 20


def test_bad_sidecar_ignored(video30):
    sidecar_path(video30).write_text("{broken")
    with FileSource(video30) as src:
        assert list(src.frames())[30].t == pytest.approx(1.0, abs=1e-3)


def test_missing_and_broken_files(tmp_path):
    with pytest.raises(CameraError, match="hittas inte"):
        FileSource(tmp_path / "nope.mp4")
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(CameraError):
        file_info(bad)


def test_frames_past_a_short_sidecar_keep_the_time_rising(video30):
    """A sidecar a frame or two shorter than the file is accepted; the frames after its last stamp
    then got index/fps of the container, which jumped back in time when the real rate was lower."""
    stamps = [i * 0.1 for i in range(43)]    # 10 fps real timing in a 30 fps container, two stamps short
    sidecar_path(video30).write_text(json.dumps({"t": stamps}))
    with FileSource(video30) as src:
        frames = list(src.frames())
    assert len(frames) == 45
    assert frames[42].t == pytest.approx(4.2, abs=1e-6)
    assert all(b.t > a.t for a, b in zip(frames, frames[1:]))
    assert frames[44].t == pytest.approx(4.2 + 2 / 30, abs=1e-3)
