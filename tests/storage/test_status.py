"""Session status: running while the test runs, interrupted when the program died."""
import sqlite3
from datetime import datetime, timedelta, timezone

from lopanalys.core.models import Athlete, Session, TestKind
from lopanalys.storage import db as dbmod
from lopanalys.storage.db import Database

T0 = datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc)


def test_status_round_trip_and_interrupted(tmp_path):
    with Database(tmp_path / "x.sqlite") as d:
        aid = d.add_athlete(Athlete(None, "Anna", "S"))
        s = Session(None, aid, TestKind.LACTATE_STEP, T0, status="running")
        d.add_session(s)
        assert d.get_session(s.id).status == "running"
        assert [x.id for x in d.running_sessions()] == [s.id]
        d.mark_interrupted(s.id, T0 + timedelta(minutes=14))
        got = d.get_session(s.id)
        assert got.status == "interrupted" and got.ended == T0 + timedelta(minutes=14)
        assert d.running_sessions() == []
        d.add_session(Session(None, aid, TestKind.GAIT, T0))
        assert d.sessions(aid)[0].status == "finished"


def test_v2_database_gets_status_finished(tmp_path):
    p = tmp_path / "v2.sqlite"
    con = sqlite3.connect(p)
    for v in (0, 1):
        for stmt in dbmod.MIGRATIONS[v]:
            con.execute(stmt)
    con.execute("PRAGMA user_version = 2")
    con.execute("INSERT INTO athletes (id,first_name,last_name,notes,created) VALUES (1,'A','B','',?)", (T0.isoformat(),))
    con.execute("INSERT INTO sessions (athlete_id,kind,started) VALUES (1,'lactate',?)", (T0.isoformat(),))
    con.commit()
    con.close()
    with Database(p) as d:
        assert d.schema_version == dbmod.SCHEMA_VERSION >= 3
        assert d.sessions(1)[0].status == "finished" and d.running_sessions() == []


def test_earlier_videos_are_deleted_with_the_session(tmp_path):
    with Database(tmp_path / "x.sqlite") as d:
        aid = d.add_athlete(Athlete(None, "Anna", "S"))
        old, new = tmp_path / "a.mp4", tmp_path / "b.mp4"
        old.write_bytes(b"x")
        new.write_bytes(b"y")
        s = Session(None, aid, TestKind.GAIT, T0, video_path=str(new), result={"earlier_videos": [str(old)]})
        d.add_session(s)
        d.delete_session(s.id)
        assert not old.exists() and not new.exists()
