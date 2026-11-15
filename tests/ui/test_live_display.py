"""What the TV and the coach's mirror show: the prompt banner fits on a scaled TV, the pace comes
before km/h, the heart-rate scale ends at the max HR, and the distance on the belt."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtGui import QFontMetricsF
from PySide6.QtWidgets import QWidget

from lopanalys.assessments.protocol import Stage
from lopanalys.core.models import TimeSample, distance_m
from lopanalys.ui.fmt import speed, speed_range
from lopanalys.ui.views.live_board import LiveBoard
from lopanalys.ui.widgets.charts import TimeChart
from lopanalys.ui.widgets.tiles import MetricTile, PromptBanner

# The video area of the TV board at 1920x1080 and at Windows scaling 150 % and 200 % (the window is
# then 1280x720 and 960x540 logical pixels), and a small window.
VIDEO_SIZES = [(1145, 775), (763, 516), (572, 387), (480, 300)]
LONG = [
    ("Sätt bandet på 6:40 min/km (9,0 km/h) · 1 %", "Lutning 1 %. Tryck sedan på Bandet är inställt.", ""),
    ("Sätt bandet på 4:17 min/km (14,0 km/h) · 2,5 % (sikta på 14,0 km/h)", "", ""),
    ("4:37 min/km (13,0 km/h) · 1 %", "", "Nästa steg om 12 s"),
    ("Ta laktatprov", "Sedan: 4:37 min/km (13,0 km/h) · 1 %", ""),
    ("Bandet är på högsta lutningen (12 %): farten ökar 0,5 km/h per steg i stället.", "", ""),
]


def _banner_on(video_size: tuple[int, int]) -> SimpleNamespace:
    """A banner placed on a video area of this size the way the board places it (the board keeps
    the video alive: the banner is its child)."""
    video = QWidget()
    video.resize(*video_size)
    board = SimpleNamespace(video=video, banner=PromptBanner(video))
    LiveBoard._place_banner(board)
    return board


@pytest.mark.parametrize("size", VIDEO_SIZES)
@pytest.mark.parametrize("text,sub,kicker", LONG)
def test_the_banner_never_clips_its_text(qapp, size, text, sub, kicker):
    board = _banner_on(size)
    b = board.banner
    b.show_prompt(text, sub, "accent2", "gauge", kicker=kicker)
    lay = b.layout()
    box = lay["box"]
    lines = lay["lines"]
    assert lines
    for font, line, rect in lines:
        assert QFontMetricsF(font).horizontalAdvance(line) <= rect.width() + 0.5, (size, line)
        assert box.left() <= rect.left() and rect.right() <= box.right() + 0.5
        assert box.top() - 0.5 <= rect.top() and rect.bottom() <= box.bottom() + 0.5, (size, line, rect, box)
    # every word is drawn: nothing elided
    drawn = " ".join(ln for _, ln, _ in lines)
    for part in (kicker.upper(), text, sub):
        for word in part.split():
            assert word in drawn
    main = [f.pixelSize() for f, ln, _ in lines if ln in text]
    assert min(main) >= (16 if size[0] >= 560 else 13)        # still readable from the treadmill
    assert not b.grab().isNull()


def test_the_warning_counts_down_to_the_next_stage(qapp):
    board = LiveBoard.__new__(LiveBoard)
    board.banner = PromptBanner()
    board.sport = "run"
    st = SimpleNamespace(next_stage=Stage(13.0, 1.0, 240), stage=Stage(12.0, 1.0, 240), stage_remaining_s=11.4)
    LiveBoard._announce(board, st, "Om 15 s: 13,0 km/h · 1 %")
    assert board.banner.kicker == "Nästa steg om 12 s"
    assert board.banner.text == "4:37 min/km (13,0 km/h) · 1 %"
    st.stage = Stage(12.0, 1.0, 240, sample_lactate=True)     # a sample comes first: the runner's words
    LiveBoard._announce(board, st, "Om 15 s: laktatprov")
    assert board.banner.text == "Om 15 s: laktatprov" and board.banner.kicker == ""


def test_pace_first_and_km_h_after(qapp):
    assert speed(12.0) == "5:00 min/km (12,0 km/h)"
    assert speed(200, "bike") == "200 W"
    assert speed_range(11.0, 12.0) == "5:27–5:00 min/km (11,0–12,0 km/h)"
    assert speed_range(None, 12.0) == "> 5:00 min/km (< 12,0 km/h)"
    board = LiveBoard.__new__(LiveBoard)
    board.t_speed, board.t_incline = MetricTile("Fart", "min/km"), MetricTile("Lutning", "%")
    LiveBoard._speed_tiles(board, 12.0, 1.0, target=False)
    assert board.t_speed.value == "5:00" and board.t_speed.sub == "12,0 km/h"
    LiveBoard._speed_tiles(board, 12.0, 1.0, target=True)
    assert board.t_speed.sub == "12,0 km/h  ·  inställt"


def test_the_heart_rate_scale_ends_at_the_max(qtbot):
    c = TimeChart("Puls")
    qtbot.addWidget(c)
    c.resize(400, 260)
    c.y_top = 199
    for i in range(30):
        c.add(float(i), 120 + i)
    c.grab()
    assert c.yr[1] == 199
    c.add(31.0, 203)                       # above the saved max: the scale follows
    c.grab()
    assert c.yr[1] >= 203


def test_distance_on_the_belt():
    smp = [TimeSample(float(t), speed_kmh=12.0) for t in range(0, 61)]
    assert distance_m(smp) == pytest.approx(200.0)               # 12 km/h for a minute
    gap = smp[:31] + [TimeSample(60.0, speed_kmh=12.0)]          # a 30 s gap counts nothing
    assert distance_m(gap) == pytest.approx(100.0)
    from lopanalys.ui.live_controller import LiveController
    ctl = SimpleNamespace(runner=SimpleNamespace(samples=smp), resume=None)
    assert LiveController.distance_m(ctl) == pytest.approx(200.0)
    board = LiveBoard.__new__(LiveBoard)
    board.power, board.ctl = False, SimpleNamespace(distance_m=lambda: LiveController.distance_m(ctl))
    board.header = SimpleNamespace(distance="")
    board.t_distance = MetricTile("Sträcka", "km")
    LiveBoard._distance(board)
    assert board.header.distance == "0,20 km" and board.t_distance.value == "0,20"
