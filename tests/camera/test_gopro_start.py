"""The GoPro's start: timed steps in the log, a second start when no stream comes, and cancel.

On real hardware (2026-11-14) the preview of a HERO8 was still starting after 14 s and showed nothing,
while the live view that started the camera again a little later had a picture at once.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest

from lopanalys.camera import gopro
from lopanalys.camera.base import SourceInfo, SourceKind
from lopanalys.camera.lease import ExclusiveSource
from lopanalys.camera.opencv_source import CameraError, OpenCvSource


class FakeGoPro:
    source_id = "C333"
    stream_port = 8554
    timeout_s = 1.0
    api = gopro.GoProApi.LEGACY

    def __init__(self, first_status=gopro.WebcamStatus.HIGH_POWER_PREVIEW) -> None:
        self.calls: list[str] = []
        self.first_status = first_status

    def source_info(self):
        return SourceInfo(SourceKind.GOPRO_USB, "C333", "GoPro HERO8 Black (USB)", 1920, 1080, 30.0)

    def start_webcam(self, res, fov):
        self.calls.append("start")
        return self.first_status if self.calls.count("start") == 1 else gopro.WebcamStatus.HIGH_POWER_PREVIEW

    def webcam_status(self):
        self.calls.append("status")
        return gopro.WebcamStatus.HIGH_POWER_PREVIEW

    def stop_webcam(self, exit_mode=True):
        self.calls.append("exit" if exit_mode else "stop")

    def stream_url(self):
        return "udp://@0.0.0.0:8554"

    def keep_alive(self):
        return True


@pytest.fixture
def stream(monkeypatch):
    """OpenCvSource.open that gives no stream for the first ``fail`` attempts (each takes ``slow`` s)."""
    st = {"fail": 1, "attempts": 0, "slow": 0.05, "timeouts": []}

    def fake_open(self):
        st["attempts"] += 1
        st["timeouts"].append(self.open_timeout_s)
        time.sleep(st["slow"])
        if st["attempts"] <= st["fail"]:
            raise CameraError("no data")
        self.mode = (1920, 1080, 30.0)
    monkeypatch.setattr(OpenCvSource, "open", fake_open)
    monkeypatch.setattr(OpenCvSource, "close", lambda self: None)
    monkeypatch.setattr(gopro, "close_webcam_app", lambda: False)
    return st


def test_no_stream_at_first_starts_webcam_again_and_logs_each_step(stream, caplog):
    cam = FakeGoPro()
    src = gopro.GoProWebcamSource(cam, attempt_s=4.0)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="lopanalys.camera.gopro"):
        src.open()
    src._stop.set()
    assert stream["attempts"] == 2
    assert cam.calls == ["start", "status", "stop", "start"]
    text = caplog.text
    for part in ("checked for GoPro Webcam in", "webcam start (HIGH_POWER_PREVIEW, legacy API) in",
                 "no stream on port 8554 (attempt 1", "webcam started again", "stream open on port 8554 (attempt 2"):
        assert part in text, part
    assert "since open)" in text


def test_idle_after_start_asks_once_more(stream):
    stream["fail"] = 0
    cam = FakeGoPro(first_status=gopro.WebcamStatus.IDLE)
    src = gopro.GoProWebcamSource(cam)  # type: ignore[arg-type]
    src.open()
    src._stop.set()
    assert cam.calls == ["start", "start"]


def test_gives_up_after_the_total_time_with_the_coachs_message(stream):
    stream["fail"] = 99
    stream["slow"] = 0.3
    cam = FakeGoPro()
    src = gopro.GoProWebcamSource(cam, open_timeout_s=1.5, attempt_s=0.3)  # type: ignore[arg-type]
    t0 = time.perf_counter()
    with pytest.raises(gopro.GoProError) as e:
        src.open()
    assert e.value.code == "no_stream"
    assert time.perf_counter() - t0 < 3.0
    assert cam.calls[-1] == "stop" and "exit" not in cam.calls
    assert all(t <= 1.5 for t in stream["timeouts"])


def test_close_during_start_cancels_it_quickly(stream):
    stream["fail"] = 99
    stream["slow"] = 0.3
    cam = FakeGoPro()
    src = ExclusiveSource(gopro.GoProWebcamSource(cam, open_timeout_s=30, attempt_s=0.3), "gopro_usb:cancel")  # type: ignore[arg-type]
    err: list[Exception] = []

    def run():
        try:
            src.open()
        except Exception as e:  # noqa: BLE001
            err.append(e)
    th = threading.Thread(target=run)
    th.start()
    time.sleep(0.4)
    t0 = time.perf_counter()
    src.close()
    th.join(3)
    assert not th.is_alive() and time.perf_counter() - t0 < 1.5
    assert isinstance(err[0], CameraError)
    assert src.wait_closed(2)


def test_found_camera_is_not_searched_again(monkeypatch):
    searched: list[int] = []
    monkeypatch.setattr(gopro.GoProUsb, "find", classmethod(lambda cls, **k: searched.append(1) or []))
    gopro._KNOWN["C3000000000830"] = ("172.21.186.51", None, "HERO8 Black", gopro.GoProApi.LEGACY, "HD8.01")
    cam = gopro.GoProUsb.from_source_id("C3000000000830")
    assert (cam.host, cam.api, cam.model, cam.serial) == ("172.21.186.51", gopro.GoProApi.LEGACY, "HERO8 Black",
                                                          "C3000000000830")
    assert searched == []
