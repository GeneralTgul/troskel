from __future__ import annotations

import threading

import pytest

from lopanalys.camera.base import SourceKind
from lopanalys.camera.discovery import discover_sources, make_source
from lopanalys.camera.gopro import (GoProApi, GoProError, GoProUsb, GoProWebcamSource, WebcamStatus,
                                    ip_from_serial, parse_media_list, usb_camera_candidates)
from lopanalys.camera.gopro_sim import FakeGoPro


@pytest.fixture(params=["legacy", "open"])
def cam(request):
    with FakeGoPro(api=request.param) as c:
        yield c


def gp_for(c: FakeGoPro) -> GoProUsb:
    return GoProUsb("127.0.0.1", port=c.port, stream_port=c.stream_port)


def test_ip_from_serial_and_candidates():
    assert ip_from_serial("C3000000000548") == "172.25.148.51"
    assert ip_from_serial("C3441325123456") == "172.24.156.51"
    assert usb_camera_candidates(["192.0.2.20", "172.25.148.50", "172.25.148.54", "172.31.0.5", "127.0.0.1"]) \
        == ["172.25.148.51"]
    assert usb_camera_candidates(["172.25.148.51"]) == []   # that is the camera itself


def test_identify(cam):
    gp = gp_for(cam).identify()
    assert gp.api == (GoProApi.LEGACY if cam.api == "legacy" else GoProApi.OPEN)
    assert gp.serial == cam.serial and cam.model in gp.model
    info = gp.source_info()
    assert info.kind == SourceKind.GOPRO_USB and info.live and "(USB)" in info.label
    if cam.api == "open":
        assert any("wired_usb?p=1" in c for c in cam.calls)


def test_identify_legacy_without_info_endpoint():
    with FakeGoPro(api="legacy") as c:
        c.has_info = False
        gp = gp_for(c).identify()
        assert gp.api == GoProApi.LEGACY and gp.model


def test_find_with_candidates(cam):
    found = GoProUsb.find(candidates=[cam.hostport, "127.0.0.1:1"], timeout_s=0.5)
    assert len(found) == 1 and found[0].serial == cam.serial


def test_discover_sources_lists_gopro_first(cam, monkeypatch):
    monkeypatch.setattr("lopanalys.camera.gopro.usb_camera_candidates", lambda *a: [cam.hostport])
    srcs = discover_sources(recent=["rtsp://user:pw@10.0.0.9/stream", "/no/such/file.mp4"])
    assert srcs[0].kind == SourceKind.GOPRO_USB and srcs[0].id == cam.hostport
    rtsp = [s for s in srcs if s.kind == SourceKind.RTSP]
    assert len(rtsp) == 1 and "pw" not in rtsp[0].label


def test_unreachable_message_is_swedish():
    gp = GoProUsb("127.0.0.1", port=1, timeout_s=0.3)
    with pytest.raises(GoProError) as e:
        gp.identify()
    assert e.value.code == "unreachable" and "GoPro Connect" in str(e.value)


def test_webcam_unavailable_tells_coach_the_setting(cam):
    cam.webcam_error_on_start = 7
    with pytest.raises(GoProError) as e:
        gp_for(cam).start_webcam()
    assert "Inställningar → Anslutningar → USB-anslutning → GoPro Connect" in str(e.value)


def test_webcam_while_recording(cam):
    gp = gp_for(cam)
    gp.start_recording()
    with pytest.raises(GoProError, match="spelar in"):
        gp.start_webcam()


def test_webcam_stream_frames_and_close(cam):
    src = GoProWebcamSource(gp_for(cam), keepalive_s=0.2)
    src.open()
    try:
        assert cam.webcam_status == 2
        frames = []
        for f in src.frames():
            frames.append(f)
            if len(frames) >= 10:
                break
        assert frames[0].image.shape[2] == 3
        assert all(b.t > a.t for a, b in zip(frames, frames[1:]))
    finally:
        src.close()
    assert cam.webcam_status == 0              # left webcam mode
    assert any(("EXIT" in c or "exit" in c) for c in cam.calls)
    src.close()                                # twice is fine


def test_webcam_stream_recovers_after_drop(cam):
    gp = gp_for(cam)
    src = GoProWebcamSource(gp, keepalive_s=0.2, stall_s=0.5)
    src.open()
    try:
        it = src.frames()
        next(it)
        cam._stop_stream()                     # camera fell out of webcam mode
        cam.webcam_status = 1
        n_start = sum("START" in c.upper() and "webcam" in c.lower() for c in cam.calls)
        got = None
        for f in it:
            if sum("START" in c.upper() and "webcam" in c.lower() for c in cam.calls) > n_start:
                got = f
                break
        assert got is not None and cam.webcam_status == 2
    finally:
        src.close()


def test_make_source_for_gopro(cam):
    src = make_source(gp_for(cam).identify().source_info())
    assert isinstance(src, GoProWebcamSource) and src.gopro.port == cam.port


def test_precise_workflow_records_and_downloads(cam, tmp_path):
    gp = gp_for(cam)
    src = GoProWebcamSource(gp, stall_s=0.3)
    src.open()
    it = src.frames()
    next(it)
    stages: list[str] = []
    clip = src.record_clip(0.3, tmp_path / "dl", on_progress=lambda s, f: stages.append(s), settle_s=0)
    assert cam.webcam_status == 2              # live view resumed
    t_after = next(it).t
    assert next(f for f in it if f.t > t_after + 0.2)   # and frames flow again in the same loop
    src.close()
    assert clip.path.exists() and clip.path.stat().st_size == clip.media.size
    assert clip.warnings == []
    assert {"prepare", "recording", "saving", "download"} <= set(stages)
    if cam.api == "legacy":
        assert cam.settings == {2: 9, 3: 1, 4: 4}
    else:
        assert cam.settings == {2: 9, 3: 1, 121: 4}
    from lopanalys.camera.file_source import FileSource
    with FileSource(clip.path) as f:
        assert f.fps == pytest.approx(120, rel=0.01)


def test_list_media_and_download_progress(cam, tmp_path):
    gp = gp_for(cam)
    gp.start_recording()
    gp.stop_recording()
    media = gp.list_media()
    assert len(media) == 1 and media[0].is_video and media[0].folder == "100GOPRO"
    seen = []
    path = gp.download(media[0], tmp_path, lambda d, t: seen.append((d, t)))
    assert path.read_bytes() == (cam.sd / media[0].path).read_bytes()
    assert seen[-1][0] == seen[-1][1] == media[0].size
    assert not list(tmp_path.glob("*.part"))


def test_download_cancel_cleans_up(cam, tmp_path):
    gp = gp_for(cam)
    gp.start_recording()
    gp.stop_recording()
    stop = threading.Event()
    stop.set()
    with pytest.raises(GoProError):
        gp.download(gp.list_media()[0], tmp_path, cancel=stop)
    assert list(tmp_path.iterdir()) == []


def test_record_clip_cancel(cam, tmp_path):
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    with pytest.raises(GoProError) as e:
        gp_for(cam).record_clip(5, tmp_path, cancel=stop, settle_s=0)
    assert e.value.code == "cancelled" and not cam.recording


def test_settings_refused_gives_warnings(cam):
    gp = gp_for(cam)
    gp.start_recording()                       # the fake refuses settings while recording
    warnings = gp.set_mode(fps=120)
    assert len(warnings) == 3 and any("120 bilder/s" in w for w in warnings)


def test_parse_media_list_tolerates_junk():
    assert parse_media_list(None) == []
    assert parse_media_list({"media": [{"d": "100GOPRO", "fs": [{"n": "A.MP4", "s": "x"}, {"n": ""}]}]})[0].size == 0


def test_status_unknown_when_missing():
    with FakeGoPro(api="legacy") as c:
        gp = gp_for(c)
        assert gp.webcam_status() == WebcamStatus.OFF


def test_an_answer_that_is_not_http_counts_as_no_answer():
    """Something else than a GoPro on the address (or a camera that breaks the answer off) gave
    http.client.BadStatusLine instead of GoProError, past every ``_try``."""
    from lopanalys.camera.network_sim import FakeRtspPort
    with FakeRtspPort(400) as other:
        gp = GoProUsb("127.0.0.1", port=other.port, timeout_s=2)
        assert gp._try("/gopro/version", 8080) is None
        with pytest.raises(GoProError) as e:
            gp.identify()
        assert e.value.code == "unreachable"
        assert GoProUsb.find(timeout_s=2, candidates=[f"127.0.0.1:{other.port}"]) == []
