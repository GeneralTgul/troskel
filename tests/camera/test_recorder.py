from __future__ import annotations

import json

import numpy as np
import pytest

from lopanalys.camera.base import Frame
from lopanalys.camera.file_source import FileSource, sidecar_path
from lopanalys.camera.recorder import Recorder

from cam_helpers import make_frame


def test_recording_roundtrip_with_true_timestamps(tmp_path):
    path = tmp_path / "rec" / "run.mp4"
    # A camera that wanders between 50 and 60 fps
    times = np.cumsum([0.0] + [1 / 50 if i % 2 else 1 / 60 for i in range(59)]) + 1000.0
    with Recorder(path, 60, (160, 120), meta={"source": "test"}) as rec:
        for i, ts in enumerate(times):
            assert rec.write(Frame(make_frame(i), float(ts), i))
    assert rec.written == 60 and rec.dropped == 0
    assert rec.codec in ("avc1", "H264", "mp4v")
    side = json.loads(sidecar_path(path).read_text())
    assert side["frames"] == 60 and side["t"][0] == 0.0 and side["meta"] == {"source": "test"}
    assert side["measured_fps"] == pytest.approx(59 / (times[-1] - times[0]))
    with FileSource(path) as src:
        frames = list(src.frames())
    assert len(frames) == 60
    assert [f.t for f in frames] == pytest.approx(list(times - times[0]), abs=1e-5)


def test_resizes_and_accepts_plain_images(tmp_path):
    rec = Recorder(tmp_path / "x.avi", 30, (160, 120))   # suffix forced to .mp4
    assert rec.path.suffix == ".mp4"
    rec.write(np.zeros((240, 320, 3), np.uint8))
    rec.write(np.zeros((120, 160), np.uint8))           # grey
    out = rec.close()
    assert out.exists() and rec.written == 2
    assert rec.close() == out                            # second close is harmless
    assert rec.write(np.zeros((120, 160, 3), np.uint8)) is False


def test_full_queue_drops_and_counts(tmp_path):
    rec = Recorder(tmp_path / "d.mp4", 30, (160, 120), queue_size=1)
    results = [rec.write(make_frame(i)) for i in range(200)]
    rec.close()
    assert rec.written + rec.dropped == 200
    assert results.count(False) == rec.dropped
    assert json.loads(sidecar_path(rec.path).read_text())["frames"] == rec.written
