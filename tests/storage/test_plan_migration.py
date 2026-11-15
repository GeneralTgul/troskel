"""A database in main's version (schema v8: v6 e-mail, v7 the watch's VO2max, v8 sport) gets the training
plan tables as v9 and keeps everything."""
import sqlite3
from datetime import datetime, timezone

from lopanalys.storage import db as dbmod
from lopanalys.storage.db import Database

T0 = datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc)
PLAN_VERSION = 9        # the migration that adds plans and plan_events, after main's v8 (sport)


def _main_db(path):
    con = sqlite3.connect(path)
    for v in range(PLAN_VERSION - 1):
        for stmt in dbmod.MIGRATIONS[v]:
            con.execute(stmt)
    con.execute(f"PRAGMA user_version = {PLAN_VERSION - 1}")
    con.execute("INSERT INTO athletes (id,first_name,last_name,notes,created,max_hr) VALUES (1,'Erik','Sandberg','',?,186)",
                (T0.isoformat(),))
    con.execute("INSERT INTO sessions (id,athlete_id,kind,started,result_json) VALUES (7,1,'lactate',?,?)",
                (T0.isoformat(), '{"coach_comment": "Bra test"}'))
    # what v6-v8 added: the e-mail, the watch's VO2max, a test in another sport and its max HR
    con.execute("UPDATE athletes SET email='erik@example.se', watch_vo2max=54.0, max_hr_source='test' WHERE id=1")
    con.execute("INSERT INTO sessions (id,athlete_id,kind,started,result_json,sport) VALUES (8,1,'max_hr',?,'{}','bike')",
                (T0.isoformat(),))
    con.execute("INSERT INTO athlete_sport (athlete_id,sport,max_hr,updated) VALUES (1,'run',186,?),(1,'bike',181,?)",
                (T0.isoformat(), T0.isoformat()))
    con.execute("INSERT INTO saved_files (athlete_id,session_id,kind,path,created) VALUES (1,7,'report','reports/rapport-1-x.pdf',?)",
                (T0.isoformat(),))
    con.execute("INSERT INTO settings (key,value_json) VALUES ('branding.coach','\"Coach Demo\"')")
    con.commit()
    con.close()


def test_the_plan_migration_only_adds(tmp_path):
    stmts = dbmod.MIGRATIONS[PLAN_VERSION - 1]
    assert all(s.lstrip().upper().startswith(("CREATE TABLE", "CREATE INDEX")) for s in stmts)
    assert any("CREATE TABLE plans" in s for s in stmts)


def test_main_database_migrates_and_keeps_everything(tmp_path):
    p = tmp_path / "lopanalys.sqlite"
    _main_db(p)
    with Database(p) as d:
        assert d.schema_version == dbmod.SCHEMA_VERSION >= PLAN_VERSION
        assert dbmod.SCHEMA_VERSION >= PLAN_VERSION                      # the plan, and the watch brand after it
        assert (tmp_path / "lopanalys.sqlite.v8.bak").is_file()        # the copy from before the upgrade
        (erik,) = d.list_athletes()
        assert erik.name == "Erik Sandberg" and erik.max_hr == 186 and erik.email == "erik@example.se"
        assert erik.watch_vo2max == 54.0 and erik.max_hr_for("bike") == 181
        s, bike = sorted(d.sessions(1), key=lambda x: x.id)
        assert s.id == 7 and s.result["coach_comment"] == "Bra test" and s.sport.value == "run"
        assert bike.id == 8 and bike.sport.value == "bike"
        assert len(d.saved_files(1)) == 1 and d.get_setting("branding.coach") == "Coach Demo"
        assert d.get_plan(1) is None
        # the new tables work, and follow the test and the athlete
        pid = d.save_plan(1, request={"a": 1}, plan={"b": 2}, health={"confirmed": []}, session_id=7)
        assert d.get_plan(1)["session_id"] == 7
        d.delete_session(7)
        d.delete_session(8)
        assert d.get_plan(1)["session_id"] is None and d.get_plan(1)["plan"] == {"b": 2}   # the plan survives its test
        d.delete_athlete(1)
        assert d._q("SELECT COUNT(*) FROM plans")[0][0] == 0
        assert d._q("SELECT COUNT(*) FROM plan_events WHERE plan_id=?", (pid,))[0][0] == 0


def test_the_backup_copy_is_still_mains_version(tmp_path):
    p = tmp_path / "lopanalys.sqlite"
    _main_db(p)
    Database(p).close()
    con = sqlite3.connect(tmp_path / "lopanalys.sqlite.v8.bak")
    assert con.execute("PRAGMA user_version").fetchone()[0] == PLAN_VERSION - 1
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='plans'").fetchone()[0] == 0
    con.close()
