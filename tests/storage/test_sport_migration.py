"""The sport migration (schema v8): a database from before sport existed opens, every test
becomes a running test with its values untouched, and a copy of the old file is kept.

Two old databases are tried: version 2, as the program wrote it at dev-003ea3d, and version 3, as
main writes it since tests are saved while they run (the ``status`` column). Both are built here
with the schema and the INSERTs written out, so that a later change to ``MIGRATIONS`` or to the
save code cannot hide a broken migration.
"""
import json
import sqlite3
import zlib
from datetime import datetime, timezone

import pytest

from lopanalys.core.models import Session, StepRecord, TestKind as Kind
from lopanalys.core.sport import Sport
from lopanalys.storage import db as dbmod
from lopanalys.storage.db import Database
from lopanalys.ui import services

V2_SCHEMA = [
    """CREATE TABLE athletes (id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL DEFAULT '',
        birth_date TEXT, sex TEXT, height_cm REAL, weight_kg REAL, max_hr INTEGER, resting_hr INTEGER,
        notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)""",
    """CREATE TABLE sessions (id INTEGER PRIMARY KEY,
        athlete_id INTEGER NOT NULL REFERENCES athletes(id) ON DELETE CASCADE, kind TEXT NOT NULL,
        started TEXT NOT NULL, ended TEXT, protocol_name TEXT NOT NULL DEFAULT '',
        protocol_json TEXT NOT NULL DEFAULT '{}', treadmill_mode TEXT NOT NULL DEFAULT 'manual',
        camera_source TEXT NOT NULL DEFAULT '', video_path TEXT, temperature_c REAL,
        notes TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL DEFAULT '{}')""",
    "CREATE INDEX ix_sessions_athlete ON sessions(athlete_id, kind, started)",
    """CREATE TABLE step_records (session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL, speed_kmh REAL NOT NULL, incline_pct REAL NOT NULL, duration_s REAL NOT NULL,
        hr_end INTEGER, hr_mean INTEGER, lactate_mmol REAL, rpe INTEGER, gait_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (session_id, idx))""",
    """CREATE TABLE time_samples (session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        n INTEGER NOT NULL, data BLOB NOT NULL)""",
    """CREATE TABLE lactate_entries (id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, stage_index INTEGER,
        mmol REAL NOT NULL, entered TEXT NOT NULL, note TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE gait_analyses (id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, stage_index INTEGER,
        view TEXT NOT NULL DEFAULT '', video_path TEXT, created TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}')""",
    """CREATE TABLE keyframes (id INTEGER PRIMARY KEY,
        gait_id INTEGER NOT NULL REFERENCES gait_analyses(id) ON DELETE CASCADE, label TEXT NOT NULL DEFAULT '',
        frame_index INTEGER, t_s REAL, path TEXT NOT NULL, findings_json TEXT NOT NULL DEFAULT '[]')""",
    "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)",
    "ALTER TABLE athletes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE athletes ADD COLUMN updated TEXT",
    "ALTER TABLE sessions ADD COLUMN updated TEXT",
    "CREATE INDEX ix_lactate_session ON lactate_entries(session_id)",
    "CREATE INDEX ix_gait_session ON gait_analyses(session_id)",
    "CREATE INDEX ix_keyframes_gait ON keyframes(gait_id)",
]

SPEEDS = [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
LACTATE = [1.1, 1.0, 1.2, 1.4, 1.9, 2.6, 3.9, 6.2]
HR = [131, 139, 147, 155, 163, 171, 179, 187]
OLD_PROTOCOL = {"name": "Laktattrappa", "kind": "lactate", "lactate_pause_s": 30.0, "open_ended": False,
                "increment_speed_kmh": 0.0, "increment_incline_pct": 0.0, "description": "",
                "final_sprint_s": 0.0, "final_sprint_kmh": 0.0, "max_open_ended_s": 1800.0,
                "builder": "lactate_step", "params": {"start_kmh": 9.0},
                "stages": [{"speed_kmh": v, "incline_pct": 1.0, "duration_s": 240.0, "label": f"Steg {i + 1}",
                            "sample_lactate": True, "record_gait": False, "is_warmup": False}
                           for i, v in enumerate(SPEEDS)]}
OLD_RESULT = {"lt1_method": "bsln_1_0", "lt2_method": "mod_dmax", "max_hr": 187, "zone_system": "garmin",
              "lt2": {"speed_kmh": 14.6, "hr": 176.0, "lactate_mmol": 3.4}}
V3_SCHEMA = V2_SCHEMA + [
    "ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'finished'",
    "CREATE INDEX ix_sessions_status ON sessions(status)",
]
T1 = "2026-10-01T09:00:00+00:00"
T2 = "2026-11-01T09:00:00+00:00"


def _lactate_session(con, athlete_id, started, shift=0.0):
    sid = con.execute(
        "INSERT INTO sessions (athlete_id,kind,started,ended,protocol_name,protocol_json,treadmill_mode,"
        "camera_source,video_path,temperature_c,notes,result_json,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (athlete_id, "lactate", started, started, "Laktattrappa", json.dumps(OLD_PROTOCOL), "ftms",
         "GoPro HERO8 (USB)", None, 19.5, "Varmt", json.dumps(OLD_RESULT), started)).lastrowid
    for i, (v, la, hr) in enumerate(zip(SPEEDS, LACTATE, HR)):
        con.execute("INSERT OR REPLACE INTO step_records (session_id,idx,speed_kmh,incline_pct,duration_s,hr_end,"
                    "hr_mean,lactate_mmol,rpe,gait_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sid, i, v + shift, 1.0, 240.0, hr, hr - 4, la, 10 + i, "{}"))
    cols = {"t": [0.0, 1.0], "hr": [130, 131], "v": [9.0 + shift, 9.0 + shift], "inc": [1.0, 1.0],
            "cad": [172.0, 173.0], "cs": ["strap", "strap"]}
    con.execute("INSERT INTO time_samples (session_id,n,data) VALUES (?,?,?)",
                (sid, 2, zlib.compress(json.dumps(cols, separators=(",", ":")).encode(), 6)))
    return sid


@pytest.fixture(params=[2, 3])
def v2_path(tmp_path, request):
    """A database from before sport (version 2 or 3): two athletes, two lactate tests, a technique test."""
    return _old_database(tmp_path / "lopanalys.sqlite", request.param)


def _old_database(p, version):
    con = sqlite3.connect(p)
    for stmt in (V2_SCHEMA if version == 2 else V3_SCHEMA):
        con.execute(stmt)
    con.execute(f"PRAGMA user_version = {version}")
    ins = ("INSERT INTO athletes (first_name,last_name,birth_date,sex,height_cm,weight_kg,max_hr,resting_hr,notes,"
           "created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)")
    a1 = con.execute(ins, ("Demo", "Löpare", "1988-04-12", "male", 181, 74.5, 192, 46, "", T1, T1)).lastrowid
    con.execute(ins, ("Ny", "Kund", None, None, None, None, None, None, "", T1, None))
    _lactate_session(con, a1, T1, shift=-0.4)
    _lactate_session(con, a1, T2)
    gid = con.execute(
        "INSERT INTO sessions (athlete_id,kind,started,protocol_name,protocol_json,result_json) VALUES (?,?,?,?,?,?)",
        (a1, "gait", T2, "Löpteknik", json.dumps({"name": "Löpteknik", "kind": "gait", "stages": [
            {"speed_kmh": 12.0, "incline_pct": 1.0, "duration_s": 60.0, "record_gait": True}]}), "{}")).lastrowid
    con.execute("INSERT INTO step_records (session_id,idx,speed_kmh,incline_pct,duration_s,gait_json) VALUES (?,?,?,?,?,?)",
                (gid, 0, 12.0, 1.0, 60.0, json.dumps({"cadence_spm": 172.0})))
    con.execute("INSERT INTO gait_analyses (session_id,view,created,data_json) VALUES (?,?,?,?)",
                (gid, "side_left", T2, json.dumps({"summary": {"cadence_spm": 172.0, "speed_kmh": 12.0}})))
    con.execute("INSERT INTO settings (key,value_json) VALUES ('devices.treadmill_mode', '\"qz\"')")
    con.commit()
    con.close()
    return p


def _dump(path):
    """Every row of the tables that existed in v2, with the columns they had then. Main's later
    versions added columns to ``athletes`` (consent, e-mail, the watch's VO2max) and write the
    setting ``privacy.neutral_names`` when a database is opened; neither is an old value."""
    con = sqlite3.connect(path)
    out = {}
    for table, cols in (("athletes", "id,first_name,last_name,birth_date,sex,height_cm,weight_kg,max_hr,resting_hr,"
                                     "notes,created,updated"), ("step_records", "*"), ("time_samples", "*"), ("gait_analyses", "*"),
                        ("settings", "*"),
                        ("sessions", "id,athlete_id,kind,started,ended,protocol_name,protocol_json,treadmill_mode,"
                                     "camera_source,video_path,temperature_c,notes,result_json,updated")):
        out[table] = con.execute(f"SELECT {cols} FROM {table} ORDER BY 1, 2").fetchall()
    out["settings"] = [r for r in out["settings"] if r[0] != "privacy.neutral_names"]
    con.close()
    return out


def test_v2_database_opens_and_every_test_becomes_running(v2_path):
    before = _dump(v2_path)
    with Database(v2_path) as d:
        assert d.schema_version == dbmod.SCHEMA_VERSION >= 8
        assert {s.status for s in d.sessions(next(iter(d.list_athletes())).id)} <= {"finished"}
        a = next(x for x in d.list_athletes() if x.first_name == "Demo")
        tests = d.sessions(a.id)
        assert len(tests) == 3 and {s.sport for s in tests} == {Sport.RUN}
        assert d.sessions(a.id, sport="run") == tests and d.sessions(a.id, sport="bike") == []
        new = d.sessions(a.id, Kind.LACTATE_STEP)[0]
        assert new.treadmill_mode == "ftms" and new.temperature_c == 19.5 and new.result == OLD_RESULT
        assert new.protocol == OLD_PROTOCOL
        recs = d.step_records(new.id)
        assert [r.speed_kmh for r in recs] == SPEEDS == [r.load for r in recs]
        assert [r.lactate_mmol for r in recs] == LACTATE and [r.hr_end for r in recs] == HR
        samples = d.time_samples(new.id)
        assert samples[0].speed_kmh == 9.0 and samples[0].cadence_source == "strap"
        gait = d.sessions(a.id, Kind.GAIT)[0]
        assert d.step_records(gait.id)[0].gait == {"cadence_spm": 172.0}
        assert d.gait_analysis(gait.id)["summary"]["speed_kmh"] == 12.0
        assert d.get_setting("devices.treadmill_mode") == "qz"
    # additions only: every row that was there reads the same, column for column
    assert _dump(v2_path) == before


def test_max_hr_becomes_running_max_hr(v2_path):
    with Database(v2_path) as d:
        demo, ny = sorted(d.list_athletes(), key=lambda x: x.first_name)
        assert demo.max_hr == 192 == demo.max_hr_for("run") == d.sport_max_hr(demo.id, Sport.RUN)
        assert demo.sport_max_hr == {} and d.sport_max_hr(demo.id, "bike") is None
        assert ny.max_hr is None and d.sport_max_hr(ny.id) is None
        # the other sports are stored per athlete and sport, and running's value is not touched
        d.set_sport_max_hr(demo.id, "bike", 184)
        demo = d.get_athlete(demo.id)
        assert demo.max_hr == 192 and demo.max_hr_for(Sport.BIKE) == 184 and demo.max_hr_for("skierg") is None
        demo.max_hr = 194
        d.save_athlete(demo)
        assert d.sport_max_hr(demo.id) == 194 and d.sport_max_hr(demo.id, "bike") == 184
        d.set_sport_max_hr(demo.id, "bike", None)
        assert d.get_athlete(demo.id).sport_max_hr == {} and d.sport_max_hr(demo.id) == 194
        d.delete_athlete(demo.id)
        assert d._q("SELECT COUNT(*) FROM athlete_sport")[0][0] == 0


def test_a_copy_of_the_old_database_is_kept_at_its_old_version(v2_path):
    before = _dump(v2_path)
    con = sqlite3.connect(v2_path)
    old = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    Database(v2_path).close()
    bak = v2_path.with_name(f"{v2_path.name}.v{old}.bak")
    assert bak.exists()
    con = sqlite3.connect(bak)
    assert con.execute("PRAGMA user_version").fetchone()[0] == old
    assert "sport" not in [r[1] for r in con.execute("PRAGMA table_info(sessions)")]
    con.close()
    assert _dump(bak) == before
    # opening again migrates nothing and writes no second copy
    bak.unlink()
    Database(v2_path).close()
    assert not bak.exists()


def test_results_and_report_read_the_same_after_migration(v2_path):
    """The thresholds, the comparison with the previous test and the report of an old test."""
    with Database(v2_path) as d:
        store = services.Store(d)
        a = next(x for x in d.list_athletes() if x.first_name == "Demo")
        new, old = d.sessions(a.id, Kind.LACTATE_STEP)
        assert d.previous_session(new).id == old.id and d.previous_session(old) is None
        lv = services.analyze_lactate(d.step_records(new.id), "bsln_1_0", "mod_dmax", a.max_hr_for(new.sport),
                                      new.sport)
        # the same numbers as straight from the values that were stored before the migration
        ref = services.analyze_lactate([StepRecord(i, v, 1.0, 240.0, hr_end=hr, lactate_mmol=la)
                                        for i, (v, la, hr) in enumerate(zip(SPEEDS, LACTATE, HR))],
                                       "bsln_1_0", "mod_dmax", 192)
        assert (lv.lt1.speed_kmh, lv.lt2.speed_kmh, lv.lt2.hr) == (ref.lt1.speed_kmh, ref.lt2.speed_kmh, ref.lt2.hr)
        assert lv.raw.sport == "run" and services.sport_thresholds(store, a).lt2.speed_kmh == lv.lt2.speed_kmh
        assert services.sport_thresholds(store, a, Sport.BIKE) is None
        zone_list = services.zones(lv, 192, a.resting_hr, "garmin", new.sport)
        html = services.render_report(a, new, d.step_records(new.id), lv, zone_list, None, None,
                                      {"session": old, "records": d.step_records(old.id),
                                       "lactate": services.analyze_lactate(d.step_records(old.id))}, {})
        assert "Laktattrappa" in html and "km/h" in html and "Jämfört med förra testet" in html


def test_previous_test_is_never_one_in_another_sport(tmp_path):
    with Database(tmp_path / "l.sqlite") as d:
        from lopanalys.core.models import Athlete
        aid = d.add_athlete(Athlete(None, "Demo", "Idrottare", max_hr=190))
        t = [datetime(2026, 11, day, 9, tzinfo=timezone.utc) for day in (1, 2, 3)]
        run1 = d.add_session(Session(None, aid, Kind.LACTATE_STEP, t[0]))
        d.add_session(Session(None, aid, Kind.LACTATE_STEP, t[1], sport=Sport.BIKE))
        run2 = Session(None, aid, Kind.LACTATE_STEP, t[2])
        d.add_session(run2)
        assert d.get_session(run2.id).sport is Sport.RUN and d.previous_session(run2).id == run1
        assert [s.sport for s in d.sessions(aid, sport="bike")] == [Sport.BIKE]
        assert len(d.history(aid, Kind.LACTATE_STEP)) == 3 and len(d.history(aid, Kind.LACTATE_STEP, "run")) == 2
        out = d.export_athlete(aid)
        assert [s["session"]["sport"] for s in out["sessions"]] == ["run", "bike", "run"]


def test_a_version_7_database_from_main_gets_sport(tmp_path):
    """Main at e7624f2 writes version 7 (data protection, consent, e-mail, the watch's VO2max). Sport
    is the step after it: the rows stay, every test becomes a running test, and the copy is version 7."""
    path = tmp_path / "main7.sqlite"
    con = sqlite3.connect(path)
    for step in dbmod.MIGRATIONS[:7]:
        for stmt in step:
            con.execute(stmt)
    con.execute("INSERT INTO athletes (first_name,last_name,max_hr,notes,created,updated,consent_json,email,"
                "max_hr_source,watch_vo2max) VALUES ('Demo','Idrottare',188,'','2026-11-01T09:00:00+00:00',"
                "'2026-11-02T09:00:00+00:00','{\"film\": true}','demo@example.se','entered',52.0)")
    con.execute("INSERT INTO sessions (athlete_id,kind,started,protocol_name,protocol_json,treadmill_mode,"
                "camera_source,notes,result_json,status,updated) VALUES (1,'lactate','2026-11-03T09:00:00+00:00',"
                "'Laktattrappa','{}','manual','','','{}','finished','2026-11-03T10:00:00+00:00')")
    con.execute("PRAGMA user_version = 7")
    con.commit()
    con.close()
    with Database(path) as d:
        assert d.schema_version == dbmod.SCHEMA_VERSION >= 8      # v9 (the training plan) follows sport
        a = d.list_athletes()[0]
        assert (a.max_hr, a.max_hr_source, a.watch_vo2max, a.email) == (188, "entered", 52.0, "demo@example.se")
        assert a.consents("film") and d.sport_max_hr(a.id, Sport.RUN) == 188 and a.sport_max_hr == {}
        assert [s.sport for s in d.sessions(a.id)] == [Sport.RUN]
    bak = path.with_name(f"{path.name}.v7.bak")
    con = sqlite3.connect(bak)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 7
    con.close()
