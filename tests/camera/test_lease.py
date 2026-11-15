"""ExclusiveSource: one opener per camera, so a late close cannot stop the next source.

The fake is a GoPro in miniature: open() starts "webcam mode" slowly, close() leaves it. Before the
lease, a preview still in open() when the next one started would finish, give up and switch the
camera off under the new preview (real hardware 2026-11-14).
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.camera.lease import ExclusiveSource, camera_busy
from lopanalys.camera.opencv_source import CameraError


class FakeCamera:
    """The physical camera: webcam mode on or off, and who switched it."""

    def __init__(self) -> None:
        self.on = False
        self.opening = 0
        self.max_opening = 0
        self.lock = threading.Lock()
        self.log: list[str] = []


class SlowSource:
    def __init__(self, cam: FakeCamera, name: str, open_s: float = 0.3, fail: bool = False) -> None:
        self.cam, self.name, self.open_s, self.fail = cam, name, open_s, fail
        self.info = SourceInfo(SourceKind.GOPRO_USB, "C333", "GoPro HERO8 Black (USB)")
        self._stop = threading.Event()
        self.extra = "proxied"

    def open(self) -> None:
        with self.cam.lock:
            self.cam.opening += 1
            self.cam.max_opening = max(self.cam.max_opening, self.cam.opening)
        self.cam.log.append(f"{self.name}:open")
        time.sleep(self.open_s)
        with self.cam.lock:
            self.cam.opening -= 1
        if self.fail:
            raise CameraError("no stream")
        self.cam.on = True

    def frames(self):
        i = 0
        while not self._stop.is_set():
            yield Frame(np.zeros((4, 4, 3), np.uint8), time.perf_counter(), i)
            i += 1
            time.sleep(0.01)

    def close(self) -> None:
        self._stop.set()
        self.cam.log.append(f"{self.name}:close")
        self.cam.on = False


def run_preview(src: ExclusiveSource, got: list[int]) -> threading.Thread:
    def run() -> None:
        try:
            src.open()
            for _ in src.frames():
                got.append(1)
        except CameraError:
            pass
        finally:
            src.close()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def test_close_during_open_returns_at_once_and_cleans_up():
    cam = FakeCamera()
    a = ExclusiveSource(SlowSource(cam, "a", open_s=0.4), "gopro:lease1")
    th = run_preview(a, [])
    time.sleep(0.1)
    t0 = time.perf_counter()
    a.close()                                  # GUI thread: must not wait for the slow open
    assert time.perf_counter() - t0 < 0.05
    th.join(2)
    assert a.closed and not cam.on and not camera_busy("gopro:lease1")
    assert cam.log == ["a:open", "a:close"]


def test_next_source_waits_until_the_previous_one_has_closed():
    cam = FakeCamera()
    a = ExclusiveSource(SlowSource(cam, "a", open_s=0.4, fail=True), "gopro:lease2")
    b = ExclusiveSource(SlowSource(cam, "b", open_s=0.1), "gopro:lease2")
    got_b: list[int] = []
    th_a = run_preview(a, [])
    time.sleep(0.05)
    a.close()                                  # new search: the old preview is told to stop
    th_b = run_preview(b, got_b)               # ... and the new one starts right away
    th_a.join(2)
    deadline = time.time() + 2
    while not got_b and time.time() < deadline:
        time.sleep(0.01)
    assert got_b, "the new preview got no frames"
    assert cam.on, "the old preview's close switched the camera off under the new one"
    assert cam.max_opening == 1
    assert cam.log.index("a:close") < cam.log.index("b:open")
    b.close()
    th_b.join(2)
    assert not cam.on and not camera_busy("gopro:lease2")


def test_live_view_after_a_closing_preview():
    cam = FakeCamera()
    preview = ExclusiveSource(SlowSource(cam, "preview", open_s=0.3), "gopro:lease3")
    th = run_preview(preview, [])
    time.sleep(0.05)
    preview.close()
    live = ExclusiveSource(SlowSource(cam, "live", open_s=0.05), "gopro:lease3")
    live.open()                                # the analyzer's capture thread waits here
    assert cam.on and cam.log[-1] == "live:open"
    th.join(2)
    assert cam.on
    live.close()
    assert not cam.on


def test_other_cameras_do_not_wait_and_attributes_pass_through():
    cam1, cam2 = FakeCamera(), FakeCamera()
    a = ExclusiveSource(SlowSource(cam1, "a", open_s=0.0), "system:0")
    b = ExclusiveSource(SlowSource(cam2, "b", open_s=0.0), "system:1")
    a.open()
    b.open()
    assert a.extra == "proxied" and a.info.id == "C333"
    a.close()
    b.close()
    with pytest.raises(CameraError):
        a.open()                               # closed for good


def test_gives_up_when_the_camera_stays_busy():
    cam = FakeCamera()
    a = ExclusiveSource(SlowSource(cam, "a", open_s=0.0), "gopro:lease5")
    a.open()
    b = ExclusiveSource(SlowSource(cam, "b", open_s=0.0), "gopro:lease5", wait_s=0.3)
    with pytest.raises(CameraError):
        b.open()
    a.close()
    assert not camera_busy("gopro:lease5")
