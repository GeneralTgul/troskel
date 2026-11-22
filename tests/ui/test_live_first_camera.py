"""The live board says when the first camera never delivered or stopped, as it does for the second."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.pose.estimator import ReplayEstimator
from lopanalys.pose.live import LiveAnalyzer
from lopanalys.pose.types import View
from lopanalys.ui import demo, services
from lopanalys.ui.context import AppContext, TestPlan
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.views.live_board import LiveBoard


class FailingSource:
    """``good`` frames and then the camera is lost (0: it never delivers a picture)."""

    def __init__(self, good: int) -> None:
        self.info = SourceInfo(SourceKind.SYSTEM, "a", "sim a", 320, 180, 30)
        self.good = good
        self.closed = threading.Event()

    def open(self) -> None:
        pass

    def frames(self):
        img = np.zeros((180, 320, 3), np.uint8)
        for i in range(self.good):
            if self.closed.is_set():
                return
            yield Frame(img, time.perf_counter(), i)
            time.sleep(0.02)
        raise RuntimeError("kameran svarar inte")

    def close(self) -> None:
        self.closed.set()


@pytest.fixture
def one_camera(qapp, monkeypatch):
    made = []

    def start(good: int, make_fails: bool = False):
        ctx = AppContext(demo=True, time_scale=40)
        if not ctx.store.athletes():
            demo.seed_demo_data(ctx.store)
        src = FailingSource(good)

        def make_source(info):
            if make_fails:
                raise RuntimeError("ingen kamera")
            return src
        monkeypatch.setattr(services, "make_source", make_source)
        monkeypatch.setattr(services, "make_live_analyzer", lambda source, bus, height, view, **k: LiveAnalyzer(
            source, bus, height, View(view), estimator=ReplayEstimator(lambda i, t, img: None), publish_every_s=0.5))
        plan = TestPlan(ctx.store.athletes()[0], "gait", services.gait_check([10.0, 12.0], 60), src.info, "side_left")
        ctl = LiveController(ctx, plan)
        ctl.open_camera()
        made.append((ctx, ctl))
        return ctl
    yield start
    for ctx, ctl in made:
        ctl.abort()
        ctx.shutdown()


def test_a_first_camera_that_never_delivers_is_named(one_camera, qtbot):
    ctl = one_camera(0)
    board = LiveBoard(ctl)
    assert board.video.placeholder == "Startar kameran …"
    qtbot.waitUntil(lambda: ctl.analyzer.error is not None, timeout=5000)
    board._check_cameras()
    assert board.video.placeholder == "Kameran gick inte att starta: kameran svarar inte"
    board.detach_video()


def test_a_first_camera_that_could_not_be_made_is_named(one_camera):
    ctl = one_camera(0, make_fails=True)
    assert ctl.analyzer is None
    board = LiveBoard(ctl)
    board._check_cameras()
    assert board.video.placeholder == "Ingen bild"
    board.detach_video()


def test_a_first_camera_that_is_lost_during_the_test_is_marked(one_camera, qtbot):
    ctl = one_camera(10)
    board = LiveBoard(ctl)
    qtbot.waitUntil(lambda: ctl.analyzer.source_lost, timeout=5000)
    assert ctl.latest() is not None
    board._check_cameras()
    assert board.video.caption == "Bilden försvann"
    board.detach_video()


def test_a_working_first_camera_has_no_marker(one_camera, qtbot):
    ctl = one_camera(100000)
    board = LiveBoard(ctl)
    qtbot.waitUntil(lambda: ctl.latest() is not None, timeout=5000)
    board._check_cameras()
    assert board.video.caption == "" and board.video.placeholder == "Startar kameran …"
    board.detach_video()
