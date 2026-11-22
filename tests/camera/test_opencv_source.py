from __future__ import annotations

import threading
import time

import cv2
import pytest

from lopanalys.camera.base import SourceInfo, SourceKind
from lopanalys.camera.discovery import _recent_infos, make_source
from lopanalys.camera.file_source import FileSource
from lopanalys.camera.gopro_sim import FakeGoPro
from lopanalys.camera.opencv_source import (CameraError, OpenCvSource, SourceState, choose_mode,
                                            parse_system_id)


@pytest.fixture
def udp():
    """A UDP MPEG-TS sender (the fake GoPro's streamer) on a free port."""
    with FakeGoPro() as cam:
        cam._start_stream(cam.stream_port)
        yield cam
        cam._stop_stream()


def udp_info(port: int) -> SourceInfo:
    return SourceInfo(SourceKind.RTSP, f"udp://@127.0.0.1:{port}?overrun_nonfatal=1&fifo_size=1000000", "UDP-test")


def test_live_source_keeps_only_newest_frame(udp):
    statuses = []
    src = OpenCvSource(udp_info(udp.stream_port), on_status=statuses.append, open_timeout_s=5)
    src.open()
    try:
        assert src.mode and src.mode[0] == 320
        got = []
        for f in src.frames():
            got.append(f)
            time.sleep(0.15)          # a slow consumer (pose estimation)
            if len(got) == 6:
                break
        idx = [f.index for f in got]
        assert idx == sorted(set(idx))
        assert idx[-1] - idx[0] > len(idx)          # frames in between were skipped, not queued
        assert src.dropped > 0
        assert all(b.t > a.t for a, b in zip(got, got[1:]))
        assert abs(got[-1].t - time.perf_counter()) < 0.5   # still real time
        assert src.actual_fps and 15 < src.actual_fps < 60
    finally:
        src.close()
    assert statuses[0].state == SourceState.OPENING and statuses[-1].state == SourceState.CLOSED
    assert not any(t.name.startswith("cam-udp") for t in threading.enumerate())


def test_reconnects_when_stream_returns(udp):
    states = []
    src = OpenCvSource(udp_info(udp.stream_port), on_status=lambda s: states.append(s.state), stall_s=0.3,
                       read_timeout_s=0.5, open_timeout_s=2)
    src.open()
    try:
        it = src.frames()
        first = next(it)
        udp.stream_paused = True
        time.sleep(1.5)
        udp.stream_paused = False
        after = next(f for f in it if f.t > first.t + 1.0)
        assert SourceState.RECONNECTING in states and states[-1] == SourceState.RUNNING
        assert after.index > first.index
    finally:
        src.close()


def test_gives_up_after_max_attempts(udp):
    src = OpenCvSource(udp_info(udp.stream_port), stall_s=0.2, read_timeout_s=0.3, open_timeout_s=0.5,
                       max_attempts=1)
    src.open()
    it = src.frames()
    next(it)
    udp._stop_stream()
    with pytest.raises(CameraError, match="kom inte tillbaka"):
        for _ in it:
            pass
    assert src.state == SourceState.FAILED
    src.close()


def test_open_failure_is_swedish():
    info = SourceInfo(SourceKind.RTSP, "rtsp://127.0.0.1:1/none", "IP-kamera")
    with pytest.raises(CameraError, match="Det gick inte att ansluta"):
        OpenCvSource(info, open_timeout_s=1).open()
    sysinfo = SourceInfo(SourceKind.SYSTEM, "v4l2:/dev/video99", "Kamera 99")
    with pytest.raises(CameraError, match="Stäng andra program"):
        OpenCvSource(sysinfo).open()


def test_parse_system_id():
    assert parse_system_id("msmf:1") == (1, cv2.CAP_MSMF)
    assert parse_system_id("dshow:0") == (0, cv2.CAP_DSHOW)
    assert parse_system_id("2")[0] == 2


class FakeCap:
    """Accepts only 1280x720 at up to 60 fps and 1920x1080 at up to 30, like many webcams."""
    def __init__(self):
        self.p = {cv2.CAP_PROP_FRAME_WIDTH: 640, cv2.CAP_PROP_FRAME_HEIGHT: 480, cv2.CAP_PROP_FPS: 30}

    def set(self, k, v):
        self.p[k] = v
        w, h = self.p[cv2.CAP_PROP_FRAME_WIDTH], self.p[cv2.CAP_PROP_FRAME_HEIGHT]
        if k == cv2.CAP_PROP_FRAME_HEIGHT and (w, h) not in ((1280, 720), (1920, 1080)):
            self.p[cv2.CAP_PROP_FRAME_WIDTH], self.p[cv2.CAP_PROP_FRAME_HEIGHT] = 1280, 720
        cap_fps = 60 if self.p[cv2.CAP_PROP_FRAME_WIDTH] == 1280 else 30
        self.p[cv2.CAP_PROP_FPS] = min(self.p[cv2.CAP_PROP_FPS], cap_fps)
        return True

    def get(self, k):
        return self.p.get(k, 0)


def test_choose_mode_prefers_fps_then_size():
    cap = FakeCap()
    assert choose_mode(cap, [(1920, 1080, 120), (1280, 720, 120), (1920, 1080, 60), (1280, 720, 60),
                             (1920, 1080, 30)]) == (1280, 720, 60)
    assert (cap.p[cv2.CAP_PROP_FRAME_WIDTH], cap.p[cv2.CAP_PROP_FRAME_HEIGHT]) == (1280, 720)
    assert choose_mode(FakeCap(), [(1920, 1080, 30), (1280, 720, 30)]) == (1920, 1080, 30)


def test_recent_and_make_source(tmp_path, video30):
    infos = _recent_infos([str(video30), str(video30), "rtsp://a:b@cam/x", "", str(tmp_path / "gone.mp4")])
    assert [i.kind for i in infos] == [SourceKind.FILE, SourceKind.RTSP]
    assert isinstance(make_source(infos[0], pace="realtime"), FileSource)
    assert isinstance(make_source(infos[1]), OpenCvSource)


def test_cli_list_preview_and_gopro(tmp_path, video30, capsys, monkeypatch):
    from lopanalys.camera.__main__ import main
    with FakeGoPro(api="legacy") as cam:
        monkeypatch.setattr("lopanalys.camera.gopro.usb_camera_candidates", lambda *a: [cam.hostport])
        assert main(["list", "--recent", str(video30)]) == 0
        out = capsys.readouterr().out
        assert "gopro_usb" in out and "clip30.mp4" in out
        assert main(["gopro-record", "--host", cam.hostport, "--seconds", "0.2", "--dest", str(tmp_path / "g")]) == 0
        assert main(["gopro-info", "--host", cam.hostport]) == 0
        assert "1 filer på minneskortet" in capsys.readouterr().out
        assert main(["gopro-download", "--host", cam.hostport, "--dest", str(tmp_path / "d")]) == 0
        assert len(list((tmp_path / "d").glob("*.MP4"))) == 1
    rec = tmp_path / "p.mp4"
    assert main(["preview", str(video30), "--seconds", "0.5", "--record", str(rec)]) == 0
    assert rec.exists() and (tmp_path / "p.mp4.json").exists()
    assert main(["gopro-info", "--host", "127.0.0.1:1"]) == 2


def test_cli_gopro_download_and_analyze(tmp_path, capsys, monkeypatch):
    """--analyze: the downloaded clip goes through the offline analysis, uncertain values marked."""
    from lopanalys.camera.__main__ import main
    from lopanalys.pose import offline
    from lopanalys.pose.synthetic import RunnerParams, generate
    seen = {}

    def fake_analyze(path, height, view, **kw):
        seen.update(path=path, height=height, view=view, speed=kw.get("speed_kmh"))
        p = RunnerParams(fps=60, duration_s=10, noise_px=1.0)   # contact time uncertain below 100 fps
        poses, truth = generate(p)
        return offline.analyze_core(poses, height, view, times=truth.times).analysis

    monkeypatch.setattr(offline, "analyze_video", fake_analyze)
    with FakeGoPro(api="legacy") as cam:
        assert main(["gopro-record", "--host", cam.hostport, "--seconds", "0.2", "--dest", str(tmp_path / "g")]) == 0
        capsys.readouterr()
        assert main(["gopro-download", "--host", cam.hostport, "--dest", str(tmp_path / "d"), "--analyze",
                     "--height", "178", "--speed", "12"]) == 0
    out = capsys.readouterr().out
    assert seen["height"] == 178 and seen["view"] == "side_left" and seen["speed"] == 12
    assert "ground_contact_ms" in out and "(uncertain)" in out
    assert "cadence_spm" in out and "! " in out


def test_a_failed_open_does_not_leave_the_source_opening():
    """Before 2026-11-23 the state stayed OPENING after a failed open, so frames() waited for ever."""
    states = []
    src = OpenCvSource(SourceInfo(SourceKind.SYSTEM, "v4l2:/dev/video99", "Kamera 99"),
                       on_status=lambda s: states.append(s.state))
    with pytest.raises(CameraError):
        src.open()
    assert src.state == SourceState.CLOSED and states == [SourceState.OPENING, SourceState.CLOSED]
    done = []
    th = threading.Thread(target=lambda: done.append(list(src.frames())), daemon=True)
    th.start()
    th.join(3)
    assert done == [[]]


def test_a_driver_that_throws_while_the_mode_is_chosen_releases_the_camera(monkeypatch):
    class ThrowingCap:
        released = 0

        def __init__(self, *a):
            caps.append(self)

        def isOpened(self):
            return True

        def set(self, *a):
            raise cv2.error("Unknown C++ exception")

        def release(self):
            self.released += 1
    caps: list[ThrowingCap] = []
    monkeypatch.setattr(cv2, "VideoCapture", ThrowingCap)
    src = OpenCvSource(SourceInfo(SourceKind.SYSTEM, "0", "Kamera 0"))
    with pytest.raises(CameraError, match="Stäng andra program"):
        src.open()
    assert len(caps) == 1 and caps[0].released == 1
