"""The TV's live values in a bike fit and double poling, with the demo cyclist and skier: the
tiles of the test's own values in place of the running tiles, "–" until enough revolutions or
strokes, then the median with the report's range and colour."""
from __future__ import annotations

from lopanalys.assessments import library_power
from lopanalys.pose.technique_live import MIN_CYCLES
from lopanalys.ui import demo
from lopanalys.ui.context import AppContext, TestPlan
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.views.live_board import LiveBoard
from lopanalys.ui.widgets.tiles import GaitTile


def _ctl(kind: str, two: bool):
    ctx = AppContext(demo=True, time_scale=40)
    if not ctx.store.athletes():
        demo.seed_demo_data(ctx.store)
    a = ctx.store.athletes()[0]
    proto = library_power.bike_fit() if kind == "bikefit" else library_power.skierg_technique(110, 90, warmup_s=0)
    plan = TestPlan(a, kind, proto, demo.demo_info_for(kind), "side_left",
                    camera2=demo.demo_info_for(kind, second=True) if two else None, view2="front",
                    sport="bike" if kind == "bikefit" else "skierg")
    ctl = LiveController(ctx, plan)
    ctl.open_camera()
    return ctx, ctl


def _shown(tiles) -> bool:
    return all(tile.value != "–" for tile, _ in tiles.values())


def test_bikefit_tv_shows_the_knee_hip_and_trunk_live_from_both_views(qapp, qtbot):
    ctx, ctl = _ctl("bikefit", two=True)
    try:
        board = LiveBoard(ctl)
        board.resize(1920, 1080)
        board.show()
        assert list(board.tech_tiles) == ["knee_flex_bottom_deg", "hip_angle_top_deg", "trunk_angle_deg",
                                          "elbow_flex_deg"]
        assert list(board.tech2_tiles) == ["knee_lateral_left_cm", "knee_lateral_right_cm"]
        knee = board.tech_tiles["knee_flex_bottom_deg"][0]
        assert isinstance(knee, GaitTile) and knee.unit == "°" and knee.reference == "33–43°"
        assert knee.value == "–" and knee.status_text == f"Väntar på {MIN_CYCLES} varv"
        assert "erfarenhetsvärde" in board.tech_tiles["trunk_angle_deg"][0].reference
        assert knee.isVisibleTo(board) and not any(t.isVisibleTo(board) for t, _ in board.gait_tiles.values())
        qtbot.waitUntil(lambda: _shown(board.tech_tiles) and _shown(board.tech2_tiles), timeout=20000)
        # the demo cyclist's saddle is low: 50 deg at the bottom, on the edge of amber and red as in the report
        assert 47 <= float(knee.value.replace(",", ".")) <= 53 and knee.status in ("watch", "fix")
        assert knee.status_text == {"watch": "Håll koll", "fix": "Åtgärda"}[knee.status]
        assert board.tech_tiles["trunk_angle_deg"][0].status == "ok"
        assert all(t.unit == "cm" and t.status == "watch" for t, _ in board.tech2_tiles.values())
        assert ctl.tech is not None and ctl.tech2 is not None and ctl.gait is None
        board.detach_video()
    finally:
        ctl.abort()
        ctx.shutdown()


def test_double_poling_tv_shows_the_stroke_angles_live(qapp, qtbot):
    ctx, ctl = _ctl("ski", two=False)
    try:
        board = LiveBoard(ctl)
        board.resize(1920, 1080)
        board.show()
        assert list(board.tech_tiles) == ["elbow_angle_start_deg", "trunk_lean_end_deg", "hip_flex_end_deg",
                                          "knee_flex_end_deg"] and not board.tech2_tiles
        elbow = board.tech_tiles["elbow_angle_start_deg"][0]
        assert elbow.value == "–" and elbow.status_text == f"Väntar på {MIN_CYCLES} drag"
        qtbot.waitUntil(lambda: _shown(board.tech_tiles), timeout=25000)
        assert 90 <= float(elbow.value.replace(",", ".")) <= 100 and elbow.status == "ok"
        assert board.tech_tiles["trunk_lean_end_deg"][0].status == "watch"     # 38 deg: a shallow hinge
        board.detach_video()
    finally:
        ctl.abort()
        ctx.shutdown()
