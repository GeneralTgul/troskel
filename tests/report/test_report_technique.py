"""Bike fit and SkiErg technique from film to report: stored with the test, shown with how each range is
founded and how uncertain the measurement is, the bike's suggestions, the checklist and the comparison."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from lopanalys.core.models import Athlete, Session, TestKind
from lopanalys.pose import technique_synth as ts
from lopanalys.pose.bike import synthetic as bs
from lopanalys.pose.ski import synthetic as ss
from lopanalys.pose.types import View
from lopanalys.report import builder
from lopanalys.storage.db import Database
from lopanalys.ui import services

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPANALYS_HOME", str(tmp_path / "home"))
    d = Database(tmp_path / "data" / "l.sqlite")
    yield services.Store(d)
    d.close()


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<(style|svg).*?</\1>", " ", html, flags=re.S)))


def film(tmp_path, name, module, params):
    video = module.write_video(str(tmp_path / f"{name}.mp4"), params)
    return str(video), ts.replay(module.generate(params)[0])


def analyse(store, tmp_path, session, kind, module, params, name):
    from lopanalys.pose.bike import analysis as ba
    from lopanalys.pose.ski import analysis as sa
    video, est = film(tmp_path, name, module, params)
    prev = store.previous_technique(session, kind, View(params.view).value)
    a = (ba if kind == "bikefit" else sa).analyze_video(
        video, params.height_cm, params.view, keyframes_dir=store.db.keyframes_root / name, estimator=est,
        previous=prev[0] if prev else None)
    store.save_technique(session.id, a.to_dict())
    return a


def new_session(store, aid, when, name):
    return store.save_session(Session(None, aid, TestKind.GAIT, when, when + timedelta(minutes=20), protocol_name=name))


def render(store, athlete, session):
    return builder.render_html(services.report_data(athlete, session, [], None, [], None, store.gait(session.id), None, {},
                                                    technique=services.technique_record(store, session)))


def test_bike_fit_from_two_films_to_the_report(store, tmp_path):
    athlete = Athlete(None, "Cyklande", "Exempel", height_cm=178)
    athlete.id = store.db.add_athlete(athlete)
    s = new_session(store, athlete.id, T0, "Bikefit landsväg")
    small = dict(duration_s=9.0, fps=30.0)
    side = bs.with_knee(bs.CyclistParams(width=320, height=180, elbow_flex_deg=2.0, shoulder_angle_deg=104.0, **small), 53.0)
    analyse(store, tmp_path, s, "bikefit", bs, side, "side")
    analyse(store, tmp_path, s, "bikefit", bs,
            bs.CyclistParams(view=View.FRONT, width=180, height=320, knee_lateral_cm=6.0, **small), "front")
    services.save_checklist(store, s, {"hamstring.left": "limited", "leg_length_diff_mm": "6", "nonsense": 1,
                                       "saddle_height_mm.before": 720, "saddle_height_mm.after": 728,
                                       "ball_over_axle.left": True, "ball_over_axle.right": True})
    assert store.gait(s.id) is None                       # a bike fit is not a running analysis
    rec = services.technique_record(store, store.db.get_session(s.id))
    assert rec["kind"] == "bikefit" and [v["view"] for v in rec["views"]] == ["side_left", "front"]
    assert rec["checklist"]["leg_length_diff_mm"] == 6.0 and "nonsense" not in rec["checklist"]
    assert [k["kind"] for k in rec["views"][0]["keyframes"]] == ["bottom", "top", "forward", "posture", "sequence"]
    assert all(k["caption"] and k["image_path"] for k in rec["views"][0]["keyframes"])

    html = render(store, athlete, store.db.get_session(s.id))
    body = text(html)
    assert "<h1>Bikefit</h1>" in html
    for want in ("Från vänster sida", "Framifrån", "Knäflexion i nedersta läget", "33–43°", "forskningsbaserat",
                 "erfarenhetsvärde", "visas utan bedömning", "±5°", "Förslag på cykeln", "Höj sadeln", "Ställs på plats",
                 "Rekommendation", "kortare styrstam", "faller inåt i nedtrampet", "Manuell genomgång",
                 "720 mm → 728 mm (+8 mm)", "Benlängdsskillnad 6 mm", "Holmes et al. 1994", "inte en diagnos",
                 "Filmen har 30 bilder/s", "Osäker mätning", "4 av"):
        assert want in body, want
    assert html.count("data:image/") >= 6                 # the key frames are in the report
    assert "Fotledens omfång" in body
    # the uncertain film gives a tentative saddle advice, never a number presented as exact
    assert "Mät med goniometer först" in body and "Osäkert: kontrollera först" in body
    assert "Jämfört med förra testet" not in body


def test_ski_technique_first_test_then_compared_with_it(store, tmp_path):
    athlete = Athlete(None, "Stakande", "Exempel", height_cm=178)
    athlete.id = store.db.add_athlete(athlete)
    small = dict(duration_s=14.0, width=180, height=320)
    s1 = new_session(store, athlete.id, T0 - timedelta(days=30), "Stakteknik")
    a1 = analyse(store, tmp_path, s1, "ski", ss,
                 ss.SkierParams(trunk_lean_end_deg=22.0, trunk_lead_ms=-150.0, **small), "ski1")
    body1 = text(render(store, athlete, s1))
    # feedback at the very first test, with what to think of and an exercise, every range marked
    assert a1.comparison == []
    for want in ("Stakteknik", "Bålens fällning", "armdrag", "Tredelat drag", "Börja draget med magen", "erfarenhetsvärde",
                 "Det här är första testet", "Startläget", "Ett drag i fyra lägen", "Tänk på", "Övning", "Mätvärden"):
        assert want in body1, want
    assert "forskningsbaserat" not in body1               # nothing here has a research-based range
    assert "Förslag på cykeln" not in body1 and "Manuell genomgång" not in body1

    s2 = new_session(store, athlete.id, T0, "Stakteknik")
    a2 = analyse(store, tmp_path, s2, "ski", ss, ss.SkierParams(trunk_lean_end_deg=44.0, **small), "ski2")
    assert {c["metric"]: c["trend"] for c in a2.comparison}["trunk_lean_end_deg"] == "better"
    body2 = text(render(store, athlete, s2))
    for want in ("Jämfört med förra testet", "15 oktober 2026", "Närmare riktvärdet", "Oförändrat"):
        assert want in body2, want
    assert "Det här är första testet" not in body2


def test_running_reports_are_unchanged_by_the_new_field():
    from . import sample_data as sd
    html = builder.render_html(builder.ReportData(athlete=sd.athlete(), session=sd.session(TestKind.MAX_HR)))
    assert "tech-metrics" not in html and "Bikefit" not in html
