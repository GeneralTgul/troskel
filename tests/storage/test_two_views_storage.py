"""Two camera views in the database: the second film is one of the test's files (paths,
erasure, retention), and the second view's analysis is a row of its own under ``"second"``."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lopanalys.core.models import Athlete, Session, StepRecord, TestKind
from lopanalys.storage.db import Database

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "data" / "l.sqlite")
    yield d
    d.close()


def film(db: Database, name: str):
    p = db.data_root / "recordings" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"film")
    (p.parent / (name + ".json")).write_text("{}")
    return p


def two_view_test(db: Database, when: datetime = T0) -> tuple[int, object, object]:
    aid = db.add_athlete(Athlete(None, "Bo", "Ek"))
    a, b = film(db, "session-1-x.mp4"), film(db, "session-1-x-2.mp4")
    s = Session(None, aid, TestKind.GAIT, when, when + timedelta(minutes=5), video_path=str(a),
                result={"view": "side_left", "second_video": str(b), "second_view": "rear", "second_offset_s": 0.21})
    sid = db.save_test(s, [StepRecord(1, 10, 1, 60, gait={"cadence_spm": 170, "second": {"pelvic_drop_deg": 5.0}})], [])
    return sid, a, b


def test_second_film_path_is_stored_relative_and_comes_back(db):
    sid, a, b = two_view_test(db)
    raw = db._q("SELECT result_json FROM sessions WHERE id=?", (sid,))[0][0]
    assert str(db.data_root) not in raw and "session-1-x-2.mp4" in raw
    s = db.get_session(sid)
    assert s.result["second_video"] == str(b) and s.result["second_offset_s"] == 0.21
    assert db.step_records(sid)[0].gait["second"] == {"pelvic_drop_deg": 5.0}


def test_erasing_the_test_or_the_athlete_removes_both_films(db):
    sid, a, b = two_view_test(db)
    db.delete_session(sid)
    assert not a.exists() and not b.exists() and not (b.parent / (b.name + ".json")).exists()
    sid, a, b = two_view_test(db)
    db.delete_athlete(db.get_session(sid).athlete_id)
    assert not a.exists() and not b.exists()


def test_retention_removes_both_films_and_keeps_the_analyses(db):
    sid, a, b = two_view_test(db, T0 - timedelta(days=60))
    db.save_gait_analysis(sid, {"summary": {"cadence_spm": 170}, "view": "side_left", "video_path": str(a)})
    db.save_gait_analysis(sid, {"summary": {"pelvic_drop_deg": 6}, "view": "rear", "angle": "second",
                                "video_path": str(b)})
    assert db.purge_expired_videos(now=T0) == 1
    assert not a.exists() and not b.exists() and not (b.parent / (b.name + ".json")).exists()
    s = db.get_session(sid)
    assert s.video_path is None and "second_video" not in s.result and s.result["second_view"] == "rear"
    g = db.gait_analysis(sid)
    assert g["summary"]["cadence_spm"] == 170 and g["second"]["summary"]["pelvic_drop_deg"] == 6
    assert "video_path" not in g and "video_path" not in g["second"]


def test_only_a_second_film_left_is_still_found_by_the_retention(db):
    sid, a, b = two_view_test(db, T0 - timedelta(days=60))
    with db.transaction() as c:
        c.execute("UPDATE sessions SET video_path=NULL WHERE id=?", (sid,))
    a.unlink()
    assert db.purge_expired_videos(now=T0) == 1 and not b.exists()


def test_gait_analysis_joins_the_two_views(db, tmp_path):
    sid, a, b = two_view_test(db)
    assert db.gait_analysis(sid) is None
    kf = db.keyframes_root / f"session-{sid}" / "rear.png"
    kf.parent.mkdir(parents=True, exist_ok=True)
    kf.write_bytes(b"png")
    db.save_gait_analysis(sid, {"summary": {"pelvic_drop_deg": 6}, "view": "rear", "angle": "second",
                                "keyframes": [{"label": "Höftfall", "image_path": str(kf), "frame_index": 3, "t": 0.1}]})
    only = db.gait_analysis(sid)                      # only the second film could be analysed
    assert "summary" not in only and only["second"]["keyframes"][0]["image_path"] == str(kf)
    db.save_gait_analysis(sid, {"summary": {"cadence_spm": 170}, "view": "side_left"})
    db.save_gait_analysis(sid, {"summary": {"cadence_spm": 172}, "view": "side_left"})   # analysed again
    g = db.gait_analysis(sid)
    assert g["summary"]["cadence_spm"] == 172 and g["view"] == "side_left" and g["keyframes"] == []
    assert g["second"]["view"] == "rear" and g["second"]["keyframes"][0]["label"] == "Höftfall"
    db.delete_session(sid)
    assert not kf.exists()


def test_one_camera_tests_are_untouched(db):
    aid = db.add_athlete(Athlete(None, "Bo", "Ek"))
    a = film(db, "session-9-x.mp4")
    sid = db.save_test(Session(None, aid, TestKind.GAIT, T0, video_path=str(a), result={"plan_kind": "gait"}), [], [])
    db.save_gait_analysis(sid, {"summary": {"cadence_spm": 170}, "view": "side_left"})
    g = db.gait_analysis(sid)
    assert "second" not in g and g["summary"]["cadence_spm"] == 170
    assert db.get_session(sid).result == {"plan_kind": "gait"}


def test_extract_holds_both_films(db, tmp_path):
    import json
    import zipfile

    from lopanalys.storage import privacy
    sid, a, b = two_view_test(db)
    dest = privacy.export_athlete_zip(db, db.get_session(sid).athlete_id, tmp_path / "ut" / "utdrag.zip")
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        data = json.loads(z.read("data.json"))
    assert {f"film/{a.name}", f"film/{b.name}", f"film/{b.name}.json"} <= set(names)
    assert data["sessions"][0]["session"]["result"]["second_video"] == f"film/{b.name}"    # no path of the coach's disk
