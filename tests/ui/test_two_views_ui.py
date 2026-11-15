"""Two camera views at once: the wizard, the live pages, both films, the analysis of both
and the report; with simulated sources at different rates, one of which drops; and the dialog that
adds the phone by its address."""
from __future__ import annotations

import dataclasses
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from lopanalys.assessments.protocol import Phase
from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.camera.network_sim import FakePhoneCamera
from lopanalys.core.bus import TOPIC_GAIT_STEP_2, TOPIC_GAIT_SUMMARY_2
from lopanalys.pose.estimator import ReplayEstimator
from lopanalys.pose.live import LiveAnalyzer
from lopanalys.pose.offline import analyze_core
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import View
from lopanalys.ui import demo, services
from lopanalys.ui.context import AppContext, TestPlan
from lopanalys.ui.live_controller import LiveController, _RecorderThread
from lopanalys.ui.views.add_stream import AddStreamDialog
from lopanalys.ui.views.live_board import LiveBoard
from lopanalys.ui.views.new_test import KINDS


def canned(view: View, **over) -> dict:
    """An analysis as pose.offline gives it, from the synthetic runner in ``view``."""
    p = RunnerParams(fps=60, duration_s=8, width=640, height=360, noise_px=0.5, view=view, **over)
    poses, _ = generate(p)
    return analyze_core(poses, 178, view, 12.0, times=[q.t for q in poses]).analysis.to_dict()


def to_check_page(w, qtbot, kind: str = "combined"):
    nt = w.new_test
    w.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    nt._next()
    nt.kind_group.button([k for k, *_ in KINDS].index(kind)).click()
    nt._next()
    nt.p_warm.setValue(0)
    nt._next()
    qtbot.waitUntil(lambda: nt.cam_list.count() >= 2 and nt.cam2_box.count() >= 2, timeout=5000)
    return nt


def test_two_cameras_from_the_wizard_to_the_report(demo_window, qtbot, monkeypatch):
    w = demo_window
    nt = to_check_page(w, qtbot)
    assert nt.camera is not None and demo.is_demo_source(nt.camera)
    assert nt.cam2_box.currentIndex() == 0 and nt.camera2 is None and not nt.cam2_view.isVisible()
    assert all(nt.cam2_box.itemData(i) is None or nt.cam2_box.itemData(i).id != nt.camera.id
               for i in range(nt.cam2_box.count()))            # the first camera cannot also be the second
    nt.cam2_box.setCurrentIndex(nt.cam2_box.count() - 1)        # the demo runner from behind
    assert demo.is_rear_demo(nt.camera2) and nt.view2 == "rear" and nt.cam2_view.isVisible()
    qtbot.waitUntil(lambda: nt.cam_view.frame_count > 0 and nt.cam2_view.frame_count > 0, timeout=8000)
    nt._sources_found([])                                       # "Sök igen" keeps both previews
    assert demo.is_rear_demo(nt.camera2) and nt.preview2 is not None and nt.preview2.running
    nt._next()
    assert nt.preview is None and nt.preview2 is None
    assert nt.ctx.setting("camera.second") == demo.DEMO_REAR_ID and nt.ctx.setting("camera.view2") == "rear"
    qtbot.waitUntil(lambda: nt.tm_connected and nt.hr_connected, timeout=8000)
    nt._next()
    plan = nt.plan()
    assert plan.camera2 is not None and plan.view2 == "rear" and plan.view == nt.view
    qtbot.waitUntil(lambda: nt.check_view.frame_count > 0 and nt.check_view2.frame_count > 0, timeout=8000)
    assert nt.check_view2.isVisible() and nt.check_view2.guide_view == "rear"
    nt._next()                                                  # launch
    op = w.operator
    ctl = op.ctl
    assert ctl.analyzer is not None and ctl.analyzer2 is not None
    assert "+" in op._devices_line() and "+" in ctl.session.camera_source
    board, tv = op.board, op.tv.board
    for b in (board, tv):
        assert b.two and b.video2 is not None and set(b.gait2_tiles) == {
            "pelvic_drop_deg", "knee_valgus_deg", "step_width_cm", "arm_cross_cm"}
        assert b.video.caption and b.video2.caption == "Bakifrån"
    op.start()
    qtbot.waitUntil(lambda: ctl.state is not None and ctl.state.phase == Phase.RUNNING, timeout=15000)
    qtbot.waitUntil(lambda: ctl.gait is not None and ctl.gait2 is not None, timeout=8000)
    assert ctl.gait.overstride_cm is not None and ctl.gait.pelvic_drop_deg is None      # each view its own measures
    assert ctl.gait2.pelvic_drop_deg is not None and ctl.gait2.overstride_cm is None
    qtbot.waitUntil(lambda: tv.video.frame_count > 0 and tv.video2.frame_count > 0, timeout=5000)
    assert tv.gait2_tiles["pelvic_drop_deg"][0].value not in ("", "–")
    op.tv.resize(1920, 1080)
    assert op.tv.grab().width() == 1920
    qtbot.waitUntil(lambda: ctl._recorder.ok and ctl._recorder2.ok and bool(ctl.stage_gait2), timeout=8000)

    seen = []

    def fake(path, height, view, progress, kf, max_seconds=None):
        seen.append((Path(path).name, str(getattr(view, "value", view))))
        rear = str(getattr(view, "value", view)) == "rear"
        return dict(canned(View.REAR, pelvic_drop_deg=9.0, knee_valgus_deg=14.0) if rear else canned(View.SIDE_LEFT),
                    video_path=str(path))
    monkeypatch.setattr(services, "analyze_video", fake)
    op.stop()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.results, timeout=10000)
    rv = w.results
    s = rv.session
    res = s.result
    first, second = Path(s.video_path), Path(res["second_video"])
    assert first.is_file() and second.is_file() and second.name == first.stem + "-2.mp4"
    assert res["second_view"] == "rear" and res["view"] == plan.view
    assert abs(res["second_offset_s"]) < 3.0                    # both films on the same clock
    for f in (first, second):
        side = json.loads(Path(str(f) + ".json").read_text(encoding="utf-8"))
        assert side["clock"] == "monotonic" and side["frames"] > 3
    assert any(r.gait and r.gait.get("second", {}).get("view") == "rear" for r in rv.records) or rv.stage_gait
    qtbot.waitUntil(lambda: rv._analysis is None and rv.gait is not None, timeout=10000)
    assert seen == [(first.name, plan.view), (second.name, "rear")]
    g = rv.gait
    assert g["view"] == "side_left" and g["second"]["view"] == "rear" and g["second"]["angle"] == "second"
    assert g["second"]["sync"]["matched"] >= 8 and g["second"]["offset_s"] == res["second_offset_s"]
    codes = {f["code"] for f in g["second"]["findings"]}
    assert {"pelvic_drop", "knee_valgus"} & codes
    stored = rv.ctx.store.gait(s.id)
    assert stored["view"] == "side_left" and stored["second"]["view"] == "rear" and "second" not in stored["second"]
    html = rv.html()
    assert "Löpteknik · Från vänster" in html and "Löpteknik · Bakifrån" in html
    assert "samma steg i båda filmerna" in html
    # erasing the test takes both films with it
    rv.ctx.store.db.delete_session(s.id)
    assert not first.exists() and not second.exists() and not Path(str(second) + ".json").exists()


def test_one_camera_is_as_before(demo_window, qtbot, monkeypatch):
    seen = []

    def fake(path, height, view, progress, kf, max_seconds=None):
        seen.append(Path(path).name)
        return {"summary": {"cadence_spm": 172.0}, "findings": [], "keyframes": [], "view": "side_left"}
    monkeypatch.setattr(services, "analyze_video", fake)
    w = demo_window
    nt = to_check_page(w, qtbot, "gait")
    nt._next()
    qtbot.waitUntil(lambda: nt.tm_connected, timeout=8000)
    nt._next()
    assert nt.plan().camera2 is None and not nt.check_view2.isVisible()
    nt._next()
    op = w.operator
    ctl = op.ctl
    assert ctl.analyzer2 is None and ctl.latest2() is None and not ctl.camera2_lost()
    assert not ctl._balance_timer.isActive()
    for b in (op.board, op.tv.board):
        assert not b.two and b.video2 is None and not b.gait2_tiles and b.video.caption == ""
    assert "+" not in op._devices_line()
    op.start()
    qtbot.waitUntil(lambda: ctl.state is not None and ctl.state.phase == Phase.RUNNING, timeout=15000)
    qtbot.waitUntil(lambda: ctl._recorder is not None and ctl._recorder.ok, timeout=8000)
    assert ctl._recorder2 is None and not ctl._recorder.raw
    op.stop()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.results, timeout=10000)
    rv = w.results
    res = rv.session.result
    assert "second_video" not in res and "second_view" not in res and "second_offset_s" not in res
    qtbot.waitUntil(lambda: rv._analysis is None and rv.gait is not None, timeout=10000)
    assert seen == [Path(rv.session.video_path).name]            # one film, analysed once
    assert "second" not in rv.gait and "second" not in rv.ctx.store.gait(rv.session.id)
    assert "Löpteknik · Från" not in rv.html()


# ---------------------------------------------------------------- simulated sources
class ClockSource:
    """Small frames at ``fps`` on the shared clock; ``fail_after`` frames the camera is lost."""

    def __init__(self, sid: str, fps: float, fail_after: int | None = None) -> None:
        self.info = SourceInfo(SourceKind.SYSTEM, sid, f"sim {sid}", 320, 180, fps)
        self.fps, self.fail_after = fps, fail_after
        self.closed = threading.Event()

    def open(self) -> None:
        self.t0 = time.perf_counter()

    def frames(self):
        img = np.zeros((180, 320, 3), np.uint8)
        i = 0
        while not self.closed.is_set():
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("telefonen tappade nätet")
            wait = self.t0 + i / self.fps - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            yield Frame(img, time.perf_counter(), i)
            i += 1

    def close(self) -> None:
        self.closed.set()


def looping(view: View, fps: float):
    """An estimator that replays the synthetic runner for ever (by the frame's own time)."""
    p = RunnerParams(fps=fps, duration_s=4, width=320, height=180, noise_px=0.3, view=view, cadence_spm=180)
    poses, _ = generate(p)       # 180 steps/min and 4 s: the loop closes on a whole stride
    t0 = time.perf_counter()
    return ReplayEstimator(lambda i, t, img: poses[int(round((t - t0) * fps)) % len(poses)])


@pytest.fixture
def sim_ctl(qapp, monkeypatch):
    """A LiveController on two simulated cameras: 30 frames a second from the side, 20 from behind."""
    made: dict[str, ClockSource] = {}

    def start(fail_after=None):
        ctx = AppContext(demo=True, time_scale=40)
        a = ctx.store.athletes()[0] if ctx.store.athletes() else None
        if a is None:
            demo.seed_demo_data(ctx.store)
            a = ctx.store.athletes()[0]
        made["a"], made["b"] = ClockSource("a", 30), ClockSource("b", 20, fail_after)
        monkeypatch.setattr(services, "make_source", lambda info: made[info.id])

        def analyzer(source, bus, height, view, second=False, name=""):
            v = View(view)
            if second:
                return LiveAnalyzer(source, bus, height, v, estimator=looping(v, 20), publish_every_s=0.5, name=name,
                                    topics=(TOPIC_GAIT_STEP_2, TOPIC_GAIT_SUMMARY_2), max_fps=10)
            return LiveAnalyzer(source, bus, height, v, estimator=looping(v, 30), publish_every_s=0.5, name=name)
        monkeypatch.setattr(services, "make_live_analyzer", analyzer)
        ctx.devices.use_simulated_treadmill() if hasattr(ctx.devices, "use_simulated_treadmill") else None
        plan = TestPlan(a, "gait", services.gait_check([10.0, 12.0], 60), made["a"].info, "side_left",
                        camera2=made["b"].info, view2="rear")
        ctl = LiveController(ctx, plan)
        ctl.open_camera()
        return ctx, ctl, made
    return start


def test_two_simulated_sources_at_different_rates(sim_ctl, qtbot, caplog):
    import logging
    caplog.set_level(logging.INFO)
    ctx, ctl, made = sim_ctl()
    try:
        assert ctl.analyzer.name == "side_left" and ctl.analyzer2.name == "rear" and ctl.analyzer2.max_fps == 10
        qtbot.waitUntil(lambda: ctl.latest() is not None and ctl.latest2() is not None, timeout=5000)
        r1 = _RecorderThread(ctl.analyzer, Path(ctx.store.db.data_root) / "a.mp4")
        r2 = _RecorderThread(ctl.analyzer2, Path(ctx.store.db.data_root) / "a-2.mp4", raw=True)
        r1.start()
        r2.start()
        qtbot.wait(3000)
        analysed = ctl.analyzer2._estimator_arg.calls
        r1.stop()
        r2.stop()
        s1 = json.loads(Path(str(r1.path) + ".json").read_text())
        s2 = json.loads(Path(str(r2.path) + ".json").read_text())
        assert 25 < s1["measured_fps"] < 33
        assert 17 < s2["measured_fps"] < 22          # the second film has every frame of its camera,
        assert analysed < 0.7 * (s2["frames"] + 20)    # though the live analysis took about half of them
        assert abs((r2.first_t - r1.first_t) - (s2["t0"] - s1["t0"])) < 1e-6      # one clock for both films
        qtbot.waitUntil(lambda: ctl.analyzer.summary is not None and ctl.analyzer2.summary is not None, timeout=8000)
        assert ctl.analyzer.summary.view == "side_left" and ctl.analyzer2.summary.view == "rear"
        ctl._balance()
        lines = [r.getMessage() for r in caplog.records]
        assert any(x.startswith("two views: side_left camera") and "rear camera" in x for x in lines)
        assert any(x.startswith("live[rear]: camera") for x in lines)
        assert any(x.startswith("live[side_left]: camera") for x in lines)
    finally:
        ctl.abort()
        ctx.shutdown()


def test_the_second_camera_drops_and_the_test_goes_on(sim_ctl, qtbot):
    ctx, ctl, made = sim_ctl(fail_after=30)
    errors = []
    ctl.error.connect(errors.append)
    try:
        board = LiveBoard(ctl)
        assert board.two and board.video2.caption == "Bakifrån"
        qtbot.waitUntil(ctl.camera2_lost, timeout=8000)
        board._check_camera2()
        assert board.video2.caption == "Bilden försvann"
        before = ctl.latest().frame.index
        qtbot.wait(500)
        assert ctl.latest().frame.index > before and not ctl.analyzer.source_lost     # the first camera goes on
        assert ctl.analyzer.error is None and ctl.analyzer2.error == "telefonen tappade nätet"
        board.detach_video()
    finally:
        ctl.abort()
        ctx.shutdown()


def test_second_camera_that_cannot_start_leaves_one_camera(qapp, monkeypatch):
    ctx = AppContext(demo=True, time_scale=40)
    demo.seed_demo_data(ctx.store) if not ctx.store.athletes() else None
    a = ctx.store.athletes()[0]
    first = demo.demo_source_info("demo")
    phone = services.stream_source_info("http://127.0.0.1:9/video")
    real = services.make_live_analyzer

    def analyzer(source, bus, height, view, second=False, name=""):
        if second:
            raise RuntimeError("ingen ström")
        return real(source, bus, height, view, second=second, name=name)
    monkeypatch.setattr(services, "make_live_analyzer", analyzer)
    ctl = LiveController(ctx, TestPlan(a, "gait", services.gait_check([10.0], 60), first, "side_left",
                                       camera2=phone, view2="rear"))
    errors = []
    ctl.error.connect(errors.append)
    try:
        ctl.open_camera()
        assert ctl.analyzer is not None and ctl.analyzer2 is None
        assert errors and "testet körs med en kamera" in errors[0]
        assert not LiveBoard(ctl).two
    finally:
        ctl.abort()
        ctx.shutdown()


def test_balance_lowers_the_second_views_rate_when_the_main_view_lags(qapp):
    ctx = AppContext(demo=True)
    demo.seed_demo_data(ctx.store) if not ctx.store.athletes() else None
    ctl = LiveController(ctx, TestPlan(ctx.store.athletes()[0], "gait", services.gait_check([10.0], 60)))

    class A:
        def __init__(self, cam, ana, max_fps=None):
            self.cam, self.ana, self.max_fps = cam, ana, max_fps

        def rates(self):
            return self.cam, self.ana

        def stop(self):
            pass
    try:
        ctl.analyzer, ctl.analyzer2 = A(30, 18), A(30, 15, 15.0)
        ctl._balance()
        assert ctl.analyzer2.max_fps == pytest.approx(11.2, abs=0.1)
        for _ in range(8):
            ctl._balance()
        assert ctl.analyzer2.max_fps == 6.0
        ctl.analyzer.ana = 29.5
        ctl._balance()
        assert ctl.analyzer2.max_fps == 7.0
        ctl.analyzer2 = A(30, 30, None)            # the demo analyzer has no ceiling: left alone
        ctl._balance()
    finally:
        ctl.analyzer = ctl.analyzer2 = None
        ctl.abort()
        ctx.shutdown()


# ---------------------------------------------------------------- the phone by its address
def test_add_stream_dialog_finds_the_phone_by_its_address(qapp, qtbot):
    with FakePhoneCamera(fps=10) as cam:
        dlg = AddStreamDialog()
        assert not dlg.ok.isEnabled()
        dlg.address.setText(f"127.0.0.1:{cam.port}")
        dlg.probe()
        qtbot.waitUntil(lambda: not dlg._busy, timeout=8000)
        assert dlg.found.count() == 1 and "MJPEG" in dlg.found.item(0).text() and dlg.ok.isEnabled()
        dlg.accept()
        assert dlg.url == cam.url
        dlg = AddStreamDialog()
        dlg.address.setText("127.0.0.1:9")
        dlg.probe()
        qtbot.waitUntil(lambda: not dlg._busy, timeout=8000)
        assert dlg.found.count() == 0 and not dlg.ok.isEnabled()
        dlg.address.setText("rtsp://user:hemligt@127.0.0.1:9/live")     # a full address may be added anyway
        dlg.probe()
        qtbot.waitUntil(lambda: not dlg._busy, timeout=8000)
        assert dlg.found.count() == 1 and "hemligt" not in dlg.found.item(0).text()
        dlg.accept()
        assert dlg.url == "rtsp://user:hemligt@127.0.0.1:9/live"


def test_the_phone_becomes_the_second_camera_and_shows_its_picture(demo_window, qtbot, monkeypatch):
    with FakePhoneCamera(fps=15) as cam:
        w = demo_window
        nt = to_check_page(w, qtbot, "gait")

        class Dlg:
            url = cam.url

            def __init__(self, parent=None):
                pass

            def exec(self):
                return 1
        import lopanalys.ui.views.add_stream as add_stream
        monkeypatch.setattr(add_stream, "AddStreamDialog", Dlg)
        nt._add_stream()                      # only the demo runner is chosen: the phone becomes the camera
        qtbot.waitUntil(lambda: nt.camera is not None and nt.camera.id == cam.url, timeout=5000)
        assert cam.url in nt.ctx.setting("camera.recent")
        qtbot.waitUntil(lambda: nt.preview is not None and nt.preview.key == cam.url
                        and nt.preview.latest() is not None, timeout=8000)
        assert nt.preview.latest().shape == (180, 320, 3)
        nt._stop_preview()


def test_passwords_never_reach_the_log_key():
    info = services.stream_source_info("rtsp://admin:hemligt@10.0.0.5:8554/live")
    assert "hemligt" not in services._source_key(info) and "hemligt" not in services.safe_source_id(info)
    assert "hemligt" not in info.label


# ---------------------------------------------------------------- only when the test films
def test_second_camera_only_when_the_test_is_filmed(demo_window, qtbot):
    w = demo_window
    nt = to_check_page(w, qtbot, "combined")
    nt.cam2_box.setCurrentIndex(nt.cam2_box.count() - 1)
    assert nt.films() and nt._second() is not None
    nt._stop_preview()
    nt.go(1)
    nt.kind_group.button([k for k, *_ in KINDS].index("lactate")).click()      # not filmed: no camera step
    assert not nt.films() and nt.camera is None and nt._second() is None
    from lopanalys.ui.views.new_test import CAMERA_STEP
    assert CAMERA_STEP not in nt.steps()
    nt._next()
    nt._next()
    assert nt.stack.currentIndex() != CAMERA_STEP                              # the camera page is skipped
    assert nt.plan().camera is None and nt.plan().camera2 is None


def test_the_60_s_rule_stops_a_filmed_test_with_two_cameras_too(demo_window, qtbot):
    """The rule (every stage at least 60 s, one incline) is about the test, so it holds for both
    views: a protocol that breaks it never reaches the camera step, with one camera or two."""
    from lopanalys.assessments.protocol import gait_compatible
    w = demo_window
    nt = to_check_page(w, qtbot, "combined")
    nt.cam2_box.setCurrentIndex(nt.cam2_box.count() - 1)
    assert nt._second() is not None and nt.gait_ok()
    nt._stop_preview()
    nt.go(2)
    p = nt.protocol
    short = dataclasses.replace(p, stages=[dataclasses.replace(s, duration_s=30.0) for s in p.stages])
    assert not gait_compatible(short)
    nt.protocol = short
    assert not nt.gait_ok()
    ok, why = nt._ok_to_continue()
    assert not ok and why
