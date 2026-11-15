"""The camera preview in Nytt test: one opener per camera, and a new search keeps the preview."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from lopanalys.camera.lease import ExclusiveSource
from lopanalys.ui import services
from lopanalys.ui.views import new_test

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "camera"))
from test_lease import FakeCamera, SlowSource  # noqa: E402


def test_stop_does_not_block_and_next_preview_keeps_the_camera(monkeypatch):
    cam = FakeCamera()
    made = []

    def make(info):
        # First preview: slow and fails (the GoPro that gave no stream); the next one works.
        src = ExclusiveSource(SlowSource(cam, f"p{len(made)}", open_s=0.4, fail=not made), "gopro_usb:C333")
        made.append(src)
        return src
    monkeypatch.setattr(services, "make_source", make)
    info = SlowSource(cam, "info").info
    old = new_test.PreviewGrabber(info)
    time.sleep(0.05)
    t0 = time.perf_counter()
    old.stop()
    assert time.perf_counter() - t0 < 0.1, "stop() blocked the GUI thread"
    new = new_test.PreviewGrabber(info)
    deadline = time.time() + 3
    while new.latest() is None and time.time() < deadline:
        time.sleep(0.02)
    assert new.latest() is not None and new.error == ""
    assert old.join(2) and cam.on, "the old preview switched the camera off"
    new.stop()
    assert new.join(2)
    deadline = time.time() + 2
    while cam.on and time.time() < deadline:
        time.sleep(0.02)
    assert not cam.on


def test_new_search_keeps_the_running_preview(demo_window, qtbot):
    w = demo_window
    nt = w.new_test
    w.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    nt._next()
    nt.kind_group.button(3).click()                # Kombinerat: a test that films has the camera step
    nt._next()
    nt._next()
    qtbot.waitUntil(lambda: nt.preview is not None and nt.preview.latest() is not None, timeout=8000)
    first = nt.preview
    nt._sources_found([])                          # what "Sök igen" ends in: the same camera again
    qtbot.wait(300)
    assert nt.preview is first and first.running
    nt.cam_list.setCurrentRow(0)                   # "Ingen kamera": the preview stops
    assert nt.preview is None and not first.running


def _to_camera_page(w, qtbot, monkeypatch, cam, open_s, fail=False):
    """The wizard on Kamera with a slow fake GoPro picked (the real services.make_source with its lease)."""
    from lopanalys.camera import discovery
    made: list[SlowSource] = []

    def make(info, **kw):
        src = SlowSource(cam, f"s{len(made)}", open_s=open_s, fail=fail)
        made.append(src)
        return src
    monkeypatch.setattr(discovery, "make_source", make)
    monkeypatch.setattr(services, "discover_sources", lambda *a, **k: [])
    nt = w.new_test
    w.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    nt._next()
    nt.kind_group.button(3).click()                # Kombinerat: a test that films has the camera step
    nt._next()
    nt._next()
    info = SlowSource(cam, "info").info
    nt._sources_found([info])
    row = next(i for i in range(nt.cam_list.count())
               if getattr(nt.cam_list.item(i).data(0x0100), "id", None) == info.id)
    nt.cam_list.setCurrentRow(row)
    return nt, info, made


def test_starting_gopro_shows_spinner_and_check_page_takes_over_the_source(demo_window, qtbot, monkeypatch):
    from lopanalys.core.i18n import t
    cam = FakeCamera()
    nt, info, made = _to_camera_page(demo_window, qtbot, monkeypatch, cam, open_s=0.8)
    qtbot.waitUntil(lambda: nt.cam_view.busy, timeout=2000)
    assert nt.cam_view.placeholder == t("ui.wiz.preview_starting_gopro")
    nt.go(4)                                       # on to Enheter while the GoPro is still starting
    assert nt.preview is None and not nt.cam_view.busy
    src = services.make_source(info)               # what LiveController.open_camera does on Kontroll
    assert len(made) == 1, "the check page started the camera again"
    t0 = time.perf_counter()
    src.open()                                     # waits for the preview's start, no second start
    assert cam.on and cam.log == ["s0:open"]
    assert time.perf_counter() - t0 < 1.5
    frames = src.frames()
    assert next(frames) is not None
    frames.close()
    src.close()
    assert not cam.on and cam.log == ["s0:open", "s0:close"]


def test_back_to_the_camera_page_reuses_the_kept_source(demo_window, qtbot, monkeypatch):
    cam = FakeCamera()
    nt, info, made = _to_camera_page(demo_window, qtbot, monkeypatch, cam, open_s=0.1)
    qtbot.waitUntil(lambda: nt.preview is not None and nt.preview.latest() is not None, timeout=3000)
    qtbot.waitUntil(lambda: not nt.cam_view.busy, timeout=1000)     # the spinner goes with the first frame
    nt.go(4)
    nt.go(3)                                       # back: the new search finds the same camera
    nt._sources_found([info])
    qtbot.waitUntil(lambda: nt.preview is not None and nt.preview.latest() is not None, timeout=3000)
    assert len(made) == 1 and cam.log == ["s0:open"]
    nt.go(2)                                       # further back: the kept camera is let go
    deadline = time.time() + 3
    while cam.on and time.time() < deadline:
        time.sleep(0.02)
    assert not cam.on


def test_failed_start_shows_the_reason_in_the_preview(demo_window, qtbot, monkeypatch):
    from lopanalys.core.i18n import t
    cam = FakeCamera()
    nt, info, made = _to_camera_page(demo_window, qtbot, monkeypatch, cam, open_s=0.2, fail=True)
    qtbot.waitUntil(lambda: nt.preview is not None and nt.preview.state == "failed", timeout=3000)
    qtbot.waitUntil(lambda: not nt.cam_view.busy, timeout=2000)
    assert nt.cam_view.placeholder == t("ui.wiz.preview_failed", detail="no stream")
    nt.go(4)                                       # nothing to hand over
    assert services._take_parked(info) is None
