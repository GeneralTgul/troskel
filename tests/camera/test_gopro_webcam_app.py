"""GoPro's Webcam program and its virtual camera (real hardware 2026-11-14).

"GoPro Webcam" was chosen in the camera list. OpenCV threw "Unknown C++ exception" from that driver, and
the program then died of heap corruption. The program GoPro Webcam had also started with Windows and
held the camera.
"""
from __future__ import annotations

import cv2
import pytest

from lopanalys.camera import discovery, gopro
from lopanalys.camera.base import SourceInfo, SourceKind
from lopanalys.camera.opencv_source import CameraError, OpenCvSource, probe_system_camera


def test_gopro_virtual_camera_is_not_listed(monkeypatch):
    monkeypatch.setattr(discovery, "_dshow_names", lambda: ["Intel(R) AVStream Camera", "GoPro Webcam"])
    found = discovery.list_system_cameras()
    assert [(s.id, s.label) for s in found] == [("0", "Intel(R) AVStream Camera")]


def test_opencv_exception_becomes_camera_error(monkeypatch):
    src = OpenCvSource(SourceInfo(SourceKind.SYSTEM, "1", "GoPro Webcam"))

    def boom(self):
        raise cv2.error("Unknown C++ exception from OpenCV code")
    monkeypatch.setattr(OpenCvSource, "_open_system", boom)
    with pytest.raises(CameraError):
        src.open()


def test_probe_skips_camera_that_throws(monkeypatch):
    class Cap:
        def isOpened(self): return True
        def release(self): pass
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: Cap())

    def boom(cap, modes):
        raise cv2.error("Unknown C++ exception from OpenCV code")
    import lopanalys.camera.opencv_source as ocs
    monkeypatch.setattr(ocs, "choose_mode", boom)
    assert probe_system_camera("1") is None


def test_webcam_app_is_closed_before_the_gopro_starts(monkeypatch):
    calls: list[list[str]] = []
    running = {"yes": True}

    def run(args, timeout=5.0):
        calls.append(args)
        if args[0] == "tasklist":
            return '"GoPro Webcam.exe","6684","Console","1","120 000 K"' if running["yes"] else "INFO: No tasks"
        running["yes"] = False
        return ""
    monkeypatch.setattr(gopro, "_run_hidden", run)
    monkeypatch.setattr(__import__("sys"), "platform", "win32")
    monkeypatch.setattr(gopro.time, "sleep", lambda s: None)
    assert gopro.webcam_app_running()
    assert gopro.close_webcam_app() is True
    assert ["taskkill", "/F", "/IM", "GoPro Webcam.exe"] in calls
    assert gopro.close_webcam_app() is False          # not running any more: nothing to do


def test_webcam_source_open_closes_the_app_first(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(gopro, "close_webcam_app", lambda: order.append("close") or False)

    class Cam:
        timeout_s = 1.0
        stream_port = 8554
        def source_info(self): return SourceInfo(SourceKind.GOPRO_USB, "x", "GoPro")
        def start_webcam(self, res, fov):
            order.append("start")
            raise gopro.GoProError("stop here", "test")
    src = gopro.GoProWebcamSource(Cam())  # type: ignore[arg-type]
    with pytest.raises(gopro.GoProError):
        src.open()
    assert order == ["close", "start"]


def test_no_app_check_outside_windows(monkeypatch):
    monkeypatch.setattr(__import__("sys"), "platform", "linux")
    monkeypatch.setattr(gopro, "_run_hidden", lambda *a, **k: pytest.fail("must not run tasklist"))
    assert gopro.webcam_app_running() is False
