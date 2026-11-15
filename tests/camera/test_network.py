"""The phone as a camera: the MJPEG reader, addresses, probing, mDNS and the stream statistics."""
from __future__ import annotations

import threading
import time

import pytest

from lopanalys.camera.base import SourceKind
from lopanalys.camera.discovery import _recent_infos, make_source
from lopanalys.camera.network import (HttpStreamSource, MjpegParser, MjpegSource, NotMjpeg, StreamStats,
                                      candidate_urls, find_network_cameras, format_stats, mdns_browse,
                                      mdns_query, normalize_address, parse_mdns, probe_address, probe_url,
                                      safe_url, split_credentials, stream_info, with_credentials)
from lopanalys.camera.network_sim import FakeMdns, FakePhoneCamera, FakeRtspPort
from lopanalys.camera.opencv_source import CameraError, SourceState


def take(src, n, timeout=6.0, delay=0.0):
    got = []
    end = time.perf_counter() + timeout
    for f in src.frames():
        got.append(f)
        if delay:
            time.sleep(delay)
        if len(got) >= n or time.perf_counter() > end:
            break
    return got


# ---------------------------------------------------------------- addresses
def test_normalize_address():
    assert normalize_address("rtsp://u:p@10.0.0.5:8554/live") == ["rtsp://u:p@10.0.0.5:8554/live"]
    assert normalize_address("  10.0.0.5:8081/video ") == ["http://10.0.0.5:8081/video", "rtsp://10.0.0.5:8081/video"]
    assert normalize_address("10.0.0.5:8554/live")[0] == "rtsp://10.0.0.5:8554/live"
    assert normalize_address("10.0.0.5:8080")[:2] == ["http://10.0.0.5:8080/video", "http://10.0.0.5:8080/mjpeg"]
    bare = normalize_address("10.0.0.5")
    assert bare == candidate_urls("10.0.0.5") and "http://10.0.0.5:8081/video" in bare
    assert all("admin:admin@10.0.0.5" in u for u in normalize_address("admin:admin@10.0.0.5"))
    assert normalize_address("") == [] and normalize_address("not an address") == []
    assert normalize_address("ftp://x") == [] and normalize_address("10.0.0.5:port") == []


def test_safe_url_and_labels_hide_the_password():
    assert safe_url("rtsp://admin:hemligt@10.0.0.5:8554/live") == "rtsp://10.0.0.5:8554/live"
    info = stream_info("http://admin:hemligt@10.0.0.5:8081/video")
    assert "hemligt" not in info.label and info.kind == SourceKind.RTSP
    assert "hemligt" not in _recent_infos(["http://admin:hemligt@10.0.0.5:8081/video"])[0].label


def test_credentials_in_and_out_of_an_address():
    assert split_credentials("rtsp://admin:hemligt@10.0.0.5:8554/live") == ("rtsp://10.0.0.5:8554/live", "admin", "hemligt")
    assert split_credentials("admin:x@10.0.0.5") == ("10.0.0.5", "admin", "x")
    assert split_credentials("http://10.0.0.5:8081/video") == ("http://10.0.0.5:8081/video", "", "")
    assert with_credentials("http://old:pw@10.0.0.5:8081/video", "admin", "a@b:c/d") == \
        "http://admin:a%40b%3Ac%2Fd@10.0.0.5:8081/video"
    assert with_credentials("10.0.0.5", "admin", "admin") == "admin:admin@10.0.0.5"
    assert with_credentials("http://u:p@10.0.0.5/video", "", "") == "http://10.0.0.5/video"
    u = with_credentials("rtsp://10.0.0.5/live", "admin", "a@b:c")
    assert split_credentials(u) == ("rtsp://10.0.0.5/live", "admin", "a@b:c")
    assert "a%40b" not in safe_url(u) and "admin" not in safe_url(u)


# ---------------------------------------------------------------- the parser
def part(data: bytes, length: bool = True) -> bytes:
    head = b"--b\r\nContent-Type: image/jpeg\r\n"
    if length:
        head += b"Content-Length: %d\r\n" % len(data)
    return head + b"\r\n" + data + b"\r\n"


def test_parser_cuts_by_length_by_boundary_and_by_markers():
    a, b = b"\xff\xd8AAAA\xff\xd9", b"\xff\xd8BB--b\xff\xd9"     # b holds the boundary text inside the picture
    p = MjpegParser(b"--b")
    stream = part(a) + part(b)
    out = []
    for i in range(0, len(stream), 7):          # arbitrary chunking
        out += p.feed(stream[i:i + 7])
    assert out == [a, b]
    p = MjpegParser(b"--b")                     # no Content-Length: cut at the next boundary
    assert p.feed(part(a, False) + part(a, False) + b"--b\r\n") == [a, a]
    p = MjpegParser(None)                       # no boundary at all: JPEG start and end markers
    assert p.feed(b"junk" + a + b"xx" + a[:4]) == [a]
    assert p.feed(a[4:]) == [a]


# ---------------------------------------------------------------- the MJPEG source
def test_mjpeg_source_delivers_frames_and_measures():
    with FakePhoneCamera(fps=20) as cam:
        states = []
        src = MjpegSource(stream_info(cam.url), on_status=lambda s: states.append(s.state))
        src.open()
        try:
            got = take(src, 25)
            assert len(got) == 25 and got[0].image.shape == (180, 320, 3)
            assert [f.index for f in got] == sorted({f.index for f in got})
            assert all(b.t > a.t for a, b in zip(got, got[1:]))
            assert abs(got[-1].t - time.perf_counter()) < 0.5          # stamped on our own clock
            assert src.actual_fps and 14 < src.actual_fps < 26
            s = src.stats.snapshot()
            assert s["fps"] and 14 < s["fps"] < 26 and 30 < s["interval_ms"] < 70
            assert s["connect_ms"] is not None and s["first_frame_ms"] is not None and s["kbit_s"] > 0
            assert src.mode[:2] == (320, 180)
            line = format_stats("phone", s)
            assert "fps" in line and "skipped" in line and "backlog" in line
        finally:
            src.close()
        assert states[0] == SourceState.OPENING and states[-1] == SourceState.CLOSED
    assert not any(th.name == "cam-mjpeg" for th in threading.enumerate())


def test_mjpeg_without_content_length():
    with FakePhoneCamera(fps=20, content_length=False) as cam, MjpegSource(stream_info(cam.url)) as src:
        assert len(take(src, 8)) == 8


def test_slow_consumer_gets_the_newest_frame():
    with FakePhoneCamera(fps=30) as cam, MjpegSource(stream_info(cam.url)) as src:
        got = take(src, 6, delay=0.2)            # pose estimation at 5 fps
        idx = [f.index for f in got]
        assert idx[-1] - idx[0] > len(idx)       # the ones in between were skipped, not queued
        assert src.dropped > 0
        assert time.perf_counter() - 0.2 - got[-1].t < 0.25      # the last one was fresh when it was handed over


def test_a_burst_is_not_decoded_picture_by_picture():
    with FakePhoneCamera(fps=5, burst=6) as cam, MjpegSource(stream_info(cam.url)) as src:
        take(src, 6)
        s = src.stats.snapshot()
        assert s["skipped"] > 0 and s["frames"] > src._grabbed      # received more than it decoded


def test_stream_that_drops_comes_back():
    with FakePhoneCamera(fps=20) as cam:
        states = []
        src = MjpegSource(stream_info(cam.url), on_status=lambda s: states.append(s.state), stall_s=0.4,
                          max_reconnect_s=0.5, open_timeout_s=2)
        src.open()
        try:
            assert len(take(src, 5)) == 5
            cam.drop()
            time.sleep(1.2)
            assert SourceState.RECONNECTING in states
            cam.resume()
            before = src._grabbed
            got = take(src, 5, timeout=8)
            assert len(got) == 5 and src._grabbed > before
            assert src.stats.reconnects >= 1 and states[-1] == SourceState.RUNNING
        finally:
            src.close()


def test_stream_that_never_returns_fails_with_a_message():
    with FakePhoneCamera(fps=20) as cam:
        src = MjpegSource(stream_info(cam.url), stall_s=0.3, max_reconnect_s=0.2, max_attempts=2, open_timeout_s=1)
        src.open()
        take(src, 3)
        cam.drop()
        with pytest.raises(CameraError):
            take(src, 1000, timeout=10)
        assert src.state == SourceState.FAILED
        src.close()


def test_login_and_wrong_address():
    with FakePhoneCamera(login=("admin", "admin")) as cam:
        with pytest.raises(CameraError) as e:
            MjpegSource(stream_info(cam.url), open_timeout_s=2).open()
        assert "admin:admin@" in str(e.value)           # the message says how to write the login
        url = cam.url.replace("://", "://admin:admin@")
        with MjpegSource(stream_info(url)) as src:
            assert len(take(src, 3)) == 3
    with FakePhoneCamera(login=("admin", "p@ss:1")) as cam:     # a password with @ and : (percent-encoded)
        assert probe_url(with_credentials(cam.url, "admin", "p@ss:1")).needs_login is False
        assert probe_url(with_credentials(cam.url, "admin", "fel")).needs_login
    with FakePhoneCamera() as cam:
        with pytest.raises(NotMjpeg):
            MjpegSource(stream_info(f"http://127.0.0.1:{cam.port}/")).open()
    with pytest.raises(CameraError):
        MjpegSource(stream_info("http://127.0.0.1:9/video"), open_timeout_s=1).open()


def test_make_source_picks_the_mjpeg_reader_for_http():
    with FakePhoneCamera(fps=20) as cam:
        src = make_source(stream_info(cam.url))
        assert isinstance(src, HttpStreamSource)
        src.open()
        try:
            assert len(take(src, 4)) == 4 and src.stats.frames >= 4
        finally:
            src.close()


# ---------------------------------------------------------------- probing
def test_probe_finds_mjpeg_rtsp_and_login():
    with FakePhoneCamera() as cam, FakeRtspPort(200) as ok, FakeRtspPort(401) as locked, FakeRtspPort(404) as no:
        r = probe_url(cam.url)
        assert r.ok and r.protocol == "mjpeg" and not r.needs_login
        assert not probe_url(f"http://127.0.0.1:{cam.port}/").ok
        r = probe_url(f"rtsp://u:p@127.0.0.1:{ok.port}/live")
        assert r.ok and r.protocol == "rtsp"
        assert "u:p" not in ok.requests[0] and ok.requests[0].startswith("OPTIONS rtsp://127.0.0.1")
        r = probe_url(f"rtsp://127.0.0.1:{locked.port}/live")
        assert r.ok and r.needs_login
        assert not probe_url(f"rtsp://127.0.0.1:{no.port}/live").ok
        assert not probe_url("rtsp://127.0.0.1:9/live", timeout_s=0.5).ok
        found = probe_address(f"127.0.0.1:{cam.port}", timeout_s=1.0)
        assert [f.url for f in found] == [cam.url]
    with FakePhoneCamera(login=("a", "b")) as cam:
        r = probe_url(cam.url)
        assert r.ok and r.needs_login


# ---------------------------------------------------------------- mDNS
def test_mdns_packets_roundtrip():
    q = mdns_query([("_rtsp._tcp.local", 12)])
    assert q[4:6] == b"\x00\x01" and b"\x05_rtsp\x04_tcp\x05local\x00" in q and q.endswith(b"\x80\x01")
    assert parse_mdns(b"\x00" * 5) == [] and parse_mdns(b"\x00\x00\x84\x00\x00\x00\x00\x05\x00\x00\x00\x00\xc0") == []


@pytest.mark.parametrize("split", [False, True])
def test_mdns_browse_finds_an_announced_camera(split):
    with FakePhoneCamera() as cam, \
            FakeMdns("_http._tcp.local", "Telefonen", cam.port, txt={"path": "/video"}, split=split) as dns:
        found = mdns_browse(timeout_s=1.0, targets=[dns.target])
        assert len(found) == 1
        s = found[0]
        assert (s.name, s.address, s.port, s.txt.get("path")) == ("Telefonen", "127.0.0.1", cam.port, "/video")
        assert s.urls() == [cam.url]
        assert dns.queries >= (2 if split else 1)


def test_find_network_cameras_only_lists_what_answers(monkeypatch):
    from lopanalys.camera import network
    with FakePhoneCamera() as cam, FakeMdns("_http._tcp.local", "Telefonen", cam.port) as dns, \
            FakeMdns("_http._tcp.local", "Skrivaren", 9) as printer:
        real = network.mdns_browse
        monkeypatch.setattr(network, "mdns_browse",
                            lambda timeout_s=1.0: real(timeout_s=timeout_s, targets=[dns.target, printer.target]))
        cams = find_network_cameras(timeout_s=1.0, probe_timeout_s=0.5)
        assert [c.id for c in cams] == [cam.url] and "Telefonen" in cams[0].label


def test_mdns_browse_without_answer_is_empty():
    assert mdns_browse(timeout_s=0.3, targets=[("127.0.0.1", 9)]) == []


def test_stream_stats_numbers():
    st = StreamStats()
    for i in range(50):
        st.tick(100.0 + i * 0.04, 1000)
    s = st.snapshot()
    assert abs(s["fps"] - 25.0) < 0.1 and abs(s["interval_ms"] - 40) < 0.5 and s["frames"] == 50
