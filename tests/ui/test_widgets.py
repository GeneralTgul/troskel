import numpy as np

from lopanalys.ui.widgets.charts import LactateChart, TimeChart, nice_ticks
from lopanalys.ui.widgets.inputs import NumberPad, RpePicker
from lopanalys.ui.widgets.tiles import CountdownRing, GaitTile, MetricTile, PromptBanner
from lopanalys.ui.widgets.video import VideoView, pose_in_guide, to_qimage


def test_tiles_and_charts_paint(qtbot):
    ws = [MetricTile("Puls", "slag/min", "heart"), CountdownRing(), GaitTile("Översteg", "cm"),
          TimeChart("Puls"), LactateChart("Laktat", live=True)]
    ws[0].set_value("152", "Zon 3", "watch", 0.8)
    ws[1].set_state("STEG 3/7", "2:41", "till nästa steg", 0.4)
    ws[2].set_value("12,4", "watch", "Håll koll", "< 10 cm")
    for i in range(30):
        ws[3].add(float(i), 120 + i)
    ws[4].set_data([(8, 1.1), (9, 1.3), (10, 2.0)])
    for w in ws:
        qtbot.addWidget(w)
        w.resize(400, 260)
        assert not w.grab().isNull()


def test_time_chart_restarts_on_new_clock(qtbot):
    c = TimeChart()
    qtbot.addWidget(c)
    for t in (5.0, 6.0, 7.0):
        c.add(t, 100)
    c.add(0.5, 90)
    assert c.points == [(0.5, 90)]


def test_numpad_accepts_comma_and_clamps(qtbot):
    pad = NumberPad("mmol/L", 30.0)
    qtbot.addWidget(pad)
    got = []
    pad.submitted.connect(got.append)
    for k in "2,45":
        pad.press(k)
    assert pad.text == "2,45"
    pad.submit()
    assert got == [2.45]
    for k in "99":
        pad.press(k)
    pad.submit()                      # 99 mmol/L is not a lactate value
    assert got == [2.45]


def test_rpe_picker(qtbot):
    r = RpePicker()
    qtbot.addWidget(r)
    got = []
    r.picked.connect(got.append)
    r.group.button(15).click()
    assert got == [15]


def test_banner_and_video(qtbot):
    v = VideoView()
    qtbot.addWidget(v)
    v.resize(640, 360)
    img = np.zeros((720, 1280, 3), np.uint8)
    img[:, :, 2] = 200
    v.set_image(img)
    v.guide = True
    b = PromptBanner(v)
    b.show_prompt("Ta laktatprov", "Skriv in värdet", "watch", "droplet", flash=True)
    assert not v.grab().isNull()
    q = to_qimage(img)
    assert q.width() == 1280 and q.pixelColor(10, 10).red() == 200


def test_nice_ticks():
    assert nice_ticks(0, 6, 3) == [0, 2, 4, 6]


def test_pose_in_guide():
    class P:
        width, height = 1000, 1000
        visibility = np.ones(33)
        xy = np.column_stack([np.linspace(420, 580, 33), np.linspace(150, 900, 33)])
    assert pose_in_guide(P, "side_left") is True
    P.xy = P.xy.copy()
    P.xy[0, 0] = 50
    assert pose_in_guide(P, "side_left") is False
    assert pose_in_guide(None, "side_left") is None
