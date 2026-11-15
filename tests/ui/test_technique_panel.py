"""The result page of a bike fit and of a SkiErg technique test: the tab, the checklist and the report."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit

from lopanalys.core.models import Athlete, Session, TestKind
from lopanalys.pose.bike import analysis as ba
from lopanalys.pose.bike import synthetic as bs
from lopanalys.pose.ski import analysis as sa
from lopanalys.pose.ski import synthetic as ss
from lopanalys.pose.types import View

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)


def texts(w) -> str:
    return " | ".join(x.text() for x in w.findChildren(QLabel))


def make(win, name, analyses, when=T0):
    store = win.ctx.store
    a = store.save_athlete(Athlete(None, name, "Exempel", height_cm=178))
    s = store.save_session(Session(None, a.id, TestKind.GAIT, when, when + timedelta(minutes=15), protocol_name=name))
    for x in analyses:
        store.save_technique(s.id, x.to_dict())
    return a, s


def test_bike_fit_tab_with_advice_and_checklist(demo_window, qtbot):
    win = demo_window
    side = bs.with_knee(bs.CyclistParams(duration_s=12), 52.0)
    front = bs.CyclistParams(view=View.FRONT, duration_s=12, knee_lateral_cm=6.0)
    analyses = [ba.analyze_poses(*bs.generate(p)[:1], p.height_cm, p.view, times=bs.generate(p)[1]) for p in (side, front)]
    _, s = make(win, "Bikefit", analyses)
    win.open_session(s.id)
    res = win.results
    i = res.tabs.indexOf(res.tech_tab)
    assert res.tabs.isTabVisible(i) and res.tabs.tabText(i) == "Bikefit" and res.tabs.currentIndex() == i
    shown = texts(res.tech_panel)
    for want in ("Från vänster sida", "Framifrån", "Sadelhöjd", "Höj sadeln",
                 "forskningsbaserat", "erfarenhetsvärde", "visas utan bedömning", "±5°",
                 "Manuell genomgång", "Baksida lår (rakt benlyft)", "faller inåt i nedtrampet"):
        assert want in shown, want
    # the coach fills in the checklist; it is saved with the test and printed in the report
    panel = res.tech_panel
    panel._inputs["leg_length_diff_mm"].setText("7")
    panel._inputs["saddle_height_mm.before"].setText("720")
    panel._inputs["saddle_height_mm.after"].setText("726")
    cb = panel._inputs["hamstring.left"]
    assert isinstance(cb, QComboBox)
    cb.setCurrentIndex(cb.findData("limited"))
    panel.save_button.click()
    stored = win.ctx.store.session(s.id).result["bikefit_checklist"]
    assert stored == {"hamstring.left": "limited", "leg_length_diff_mm": 7.0, "saddle_height_mm.before": 720.0,
                      "saddle_height_mm.after": 726.0}
    again = res.tech_panel._inputs["saddle_height_mm.after"]
    assert isinstance(again, QLineEdit) and again.text() == "726"
    assert "Benlängdsskillnad 7 mm" in texts(res.tech_panel)
    html = res.html()
    assert "720 mm → 726 mm (+6 mm)" in html and "Förslag på cykeln" in html and "<h1>Bikefit</h1>" in html


def test_ski_tab_compares_with_the_previous_test(demo_window, qtbot):
    win = demo_window
    p1, p2 = ss.SkierParams(trunk_lean_end_deg=22.0), ss.SkierParams(trunk_lean_end_deg=45.0)
    first = sa.analyze_poses(ss.generate(p1)[0], 178, p1.view, times=ss.generate(p1)[1])
    a, s1 = make(win, "Stak", [first], T0 - timedelta(days=21))
    win.open_session(s1.id)
    assert "Det här är första testet" in texts(win.results.tech_panel)
    assert win.results.tabs.tabText(win.results.tabs.indexOf(win.results.tech_tab)) == "Stakteknik"
    store = win.ctx.store
    s2 = store.save_session(Session(None, a.id, TestKind.GAIT, T0, T0 + timedelta(minutes=15), protocol_name="Stak"))
    prev = store.previous_technique(s2, "ski", "side_left")
    assert prev is not None
    store.save_technique(s2.id, sa.analyze_poses(ss.generate(p2)[0], 178, p2.view, times=ss.generate(p2)[1],
                                                 previous=prev[0]).to_dict())
    win.open_session(s2.id)
    shown = texts(win.results.tech_panel)
    assert "Jämfört med förra testet" in shown and "Närmare riktvärdet" in shown
    assert "Manuell genomgång" not in shown


def test_other_tests_have_no_technique_tab(demo_window, qtbot):
    win = demo_window
    store = win.ctx.store
    a = store.save_athlete(Athlete(None, "Löpande", "Exempel"))
    s = store.save_session(Session(None, a.id, TestKind.MAX_HR, T0, T0 + timedelta(minutes=15)))
    win.open_session(s.id)
    assert not win.results.tabs.isTabVisible(win.results.tabs.indexOf(win.results.tech_tab))
