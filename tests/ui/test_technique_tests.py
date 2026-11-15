"""Bike fit and double poling as tests in the guide: the test types per sport, the views
and the camera step, the film that starts after the warm-up, the TV without running values, the
analysis after the test (pose.bike, pose.ski) and the result page with its own tab only. The demo
runs with the synthetic cyclist and skier."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from lopanalys.assessments import library_power
from lopanalys.assessments.protocol import Phase
from lopanalys.camera.base import Frame
from lopanalys.core.i18n import t
from lopanalys.core.models import TestKind
from lopanalys.ui import services
from lopanalys.ui.context import AppContext


def _views(group) -> list[str]:
    from lopanalys.ui.views.new_test import VIEWS
    return [VIEWS[i][0] for i, b in enumerate(group.buttons()) if not b.isHidden()]


def test_protocols_film_one_even_stage_after_a_warmup_that_is_not_filmed():
    p = library_power.bike_fit()
    assert p.kind == "bikefit" and p.sport == "bike" and p.builder == "bike_fit"
    assert [s.is_warmup for s in p.stages] == [True, False] and p.stages[0].duration_s == 300
    assert p.stages[1].load == 150 and p.stages[1].duration_s == 180 and p.stages[1].label == "Filmning"
    assert not any(s.sample_lactate or s.record_gait for s in p.stages) and not p.open_ended
    q = library_power.skierg_technique(110, 90, warmup_s=0)
    assert q.kind == "ski" and q.sport == "skierg" and [s.load for s in q.stages] == [110]
    rebuilt = services.rebuild_protocol(p, work_w=180.0, film_s=240.0, warmup_s=600.0)
    assert [s.load for s in rebuilt.stages] == [125, 180] and rebuilt.stages[1].duration_s == 240
    assert any(x.builder == "bike_fit" for x in services.default_protocols("bike"))
    assert any(x.builder == "skierg_technique" for x in services.default_protocols("skierg"))


def test_guide_offers_bikefit_and_double_poling_with_their_own_views(qapp, qtbot, monkeypatch):
    from lopanalys.ui.views.new_test import NewTestView
    monkeypatch.setattr(services, "discover_sources", lambda *a, **k: [])
    c = AppContext(demo=False)
    try:
        from lopanalys.core.models import Athlete
        a = c.store.save_athlete(Athlete(None, "Bo", "Ek", height_cm=181, weight_kg=77.0, max_hr=188))
        nt = NewTestView(c)
        nt.athlete = a
        nt.go(1)
        nt.set_sport("bike")
        nt.kind_group.button(3).click()
        assert nt.kind == "bikefit" and nt.films() and 3 in nt.steps()
        nt.go(2)
        p = nt.protocol
        assert p.builder == "bike_fit" and p.kind == "bikefit" and nt.p_start.value() == 150
        assert nt.step_field.isHidden() and nt.n_field.isHidden() and nt.pause_field.isHidden()
        assert nt.start_field.findChild(type(nt.hint)).text() == "ARBETSEFFEKT"
        assert nt.len_field.findChild(type(nt.hint)).text() == "FILMNINGENS LÄNGD"
        assert not any(s.record_gait for s in p.stages) and p.name == "Bikefit landsväg"   # not renamed as gait
        nt.p_len.setValue(240)
        assert nt.protocol.stages[-1].duration_s == 240
        nt.go(3)
        assert _views(nt.view_group) == ["side_left", "side_right"] and _views(nt.view2_group) == ["front"]
        assert nt.view2 == "front" and not nt.cam2_box.isHidden()
        assert nt.view_hint.text() == t("ui.wiz.view_hint.bikefit")
        assert nt._ok_to_continue() == (False, t("ui.wiz.need_camera.bikefit"))
        assert nt._guide_view("side_left") == "bike_side" and nt._guide_view("front") == "front"
        # a 30 fps camera: the coach is told that 60 are needed
        cam = SimpleNamespace(id="usb:0", label="Webbkamera", fps=30.0, kind=None)
        nt.camera = cam
        nt._show_fps_hint()
        assert not nt.fps_hint.isHidden() and "30 bilder/s" in nt.fps_hint.text()
        nt.camera = None

        nt.go(1)
        nt.set_sport("skierg")
        nt.kind_group.button(2).click()
        assert nt.kind == "ski" and nt.films()
        nt.go(2)
        assert nt.protocol.builder == "skierg_technique" and nt.protocol.sport == "skierg"
        nt.go(3)
        assert _views(nt.view_group) == ["side_left", "side_right"] and nt.cam2_box.isHidden()
        assert nt._second() is None
        nt.go(1)
        nt.set_sport("run")                    # running keeps its three views and the rear runner
        nt.kind_group.button(0).click()
        nt.go(3)
        assert _views(nt.view_group) == ["side_left", "side_right", "rear"] and not nt.cam2_box.isHidden()
    finally:
        c.shutdown()


class _FakeAnalyzer:
    """latest_frame() gives a new frame every call (a camera that always has a newer picture)."""

    def __init__(self) -> None:
        self.i = 0
        self.source = SimpleNamespace(info=SimpleNamespace(fps=60.0))

    def latest_frame(self):
        self.i += 1
        return Frame(np.zeros((48, 64, 3), np.uint8), time.perf_counter(), self.i)

    def latest(self):
        return None


def test_the_film_of_a_technique_test_starts_when_it_is_armed(tmp_path, monkeypatch):
    from lopanalys.ui import live_controller as lc
    written: list[int] = []

    class Rec:
        def __init__(self, path, fps, size):
            self.path = path

        def write(self, fr):
            written.append(fr.index)

        def close(self):
            pass
    monkeypatch.setattr(services, "make_recorder", lambda path, fps, size: Rec(path, fps, size))
    r = lc._RecorderThread(_FakeAnalyzer(), tmp_path / "x.mp4", raw=True, gated=True, poll_s=1 / 500)
    r.start()
    time.sleep(0.15)
    assert not written and not r.ok and r.first_t is None          # the warm-up is not filmed
    r.arm()
    deadline = time.time() + 3
    while not written and time.time() < deadline:
        time.sleep(0.02)
    r.stop()
    assert written and r.ok and r.first_t is not None


def _start_demo(w, qtbot, sport: str, kind_index: int):
    nt = w.new_test
    w.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    if not nt.athlete.weight_kg:
        nt.athlete.weight_kg = 75.0
    nt._next()
    nt.set_sport(sport)
    nt.kind_group.button(kind_index).click()
    nt._next()
    assert nt.stack.currentIndex() == 2
    nt.p_warm.setValue(1)
    nt.p_len.setValue(60)
    nt._next()
    assert nt.stack.currentIndex() == 3                             # the camera step: these tests film
    qtbot.waitUntil(lambda: nt.camera is not None, timeout=8000)
    nt._next()
    qtbot.waitUntil(lambda: nt.tm_connected, timeout=8000)
    nt._next()
    assert nt.stack.currentIndex() == 5 and nt.framing_card.isVisibleTo(nt)
    return nt


def test_demo_bikefit_films_both_views_and_is_analysed_after_the_test(demo_window, qtbot):
    w = demo_window
    nt = _start_demo(w, qtbot, "bike", 3)
    assert nt.camera.id == "demo:synthetic-cyclist" and nt._second().id == "demo:synthetic-cyclist-front"
    assert nt.check_view.guide_view == "bike_side" and nt.check_view2.guide_view == "front"
    nt._next()
    op = w.operator
    board = op.tv.board
    assert board.tech and board.video2 is not None
    assert all(tile.isHidden() or not tile.isVisibleTo(board) for tile, _ in board.gait_tiles.values())
    op.start()
    qtbot.waitUntil(lambda: op.ctl.state is not None and op.ctl.state.phase == Phase.RUNNING, timeout=15000)
    rec = op.ctl._recorder
    assert rec is not None and not rec.armed.is_set()                # the warm-up is not filmed
    qtbot.waitUntil(lambda: rec.armed.is_set(), timeout=15000)
    assert op.ctl.state.stage_index == 1
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.results, timeout=30000)
    res = w.results
    s = res.session
    assert s.kind == TestKind.BIKEFIT and s.sport.value == "bike" and s.result.get("demo_camera")
    assert Path(s.video_path).is_file() and Path(s.result["second_video"]).is_file()
    assert s.result["view"] == "side_left" and s.result["second_view"] == "front"
    assert not res.tabs.isTabVisible(0) and not res.tabs.isTabVisible(1)
    assert res.tabs.currentWidget() is res.tech_tab and res.tabs.tabText(res.tabs.indexOf(res.tech_tab)) == "Bikefit"
    qtbot.waitUntil(lambda: res.technique is not None and res._analysis is None, timeout=90000)
    tech = w.ctx.store.technique(s.id)
    assert tech["kind"] == "bikefit" and [v["view"] for v in tech["views"]] == ["side_left", "front"]
    assert all(v["video_path"] in (s.video_path, s.result.get("second_video")) for v in tech["views"])
    side = tech["views"][0]
    assert side["summary"] and side["keyframes"] and side["quality"]["level"] != "poor" and side["advice"]
    assert w.ctx.store.gait(s.id) is None                            # never read as running
    html = res.html()
    assert "Bikefit" in html and "Laktatkurva" not in html and "Löpteknik" not in html


def test_demo_double_poling_films_from_the_side_and_is_analysed(demo_window, qtbot):
    w = demo_window
    nt = _start_demo(w, qtbot, "skierg", 2)
    assert nt.camera.id == "demo:synthetic-skier" and nt._second() is None
    nt._next()
    op = w.operator
    assert op.tv.board.tech and op.tv.board.video2 is None
    op.start()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.results, timeout=40000)
    res = w.results
    s = res.session
    assert s.kind == TestKind.SKI_TECHNIQUE and Path(s.video_path).is_file() and "second_video" not in s.result
    qtbot.waitUntil(lambda: res.technique is not None and res._analysis is None, timeout=90000)
    assert res.technique["kind"] == "ski" and len(res.technique["views"]) == 1
    assert res.tabs.tabText(res.tabs.indexOf(res.tech_tab)) == "Stakteknik"
    assert not res.tabs.isTabVisible(0) and not res.tabs.isTabVisible(1)


def test_technique_test_without_film_consent_is_not_started(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from lopanalys.ui.main_window import MainWindow
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    plan = SimpleNamespace(athlete=SimpleNamespace(name="Bo Ek", consents=lambda what: what == "test"),
                           kind="bikefit", camera=object())
    assert MainWindow._consent_ok(SimpleNamespace(), SimpleNamespace(plan=plan)) is False
    assert shown and "mäts bara ur filmen" in shown[0]
