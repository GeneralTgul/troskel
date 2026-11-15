import json
import sqlite3
import threading
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from lopanalys.core.models import Athlete, Session, Sex, StepRecord, TestKind as Kind, TimeSample
from lopanalys.storage import db as dbmod
from lopanalys.storage.db import Database, StorageError

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "data" / "l.sqlite")
    yield d
    d.close()


def athlete(**kw):
    base = dict(id=None, first_name="Olle", last_name="Löpare", birth_date=date(1990, 5, 1), sex=Sex.MALE,
                height_cm=182, weight_kg=74.5, max_hr=192, resting_hr=44, notes="Halvmara i vår")
    base.update(kw)
    return Athlete(**base)


def session(aid, started=T0, kind=Kind.LACTATE_STEP, **kw):
    return Session(id=None, athlete_id=aid, kind=kind, started=started, ended=started + timedelta(minutes=50),
                   protocol_name="Laktattrappa", protocol={"name": "Laktattrappa", "stages": [{"speed_kmh": 10}]},
                   treadmill_mode="ftms", camera_source="GoPro HERO8 (USB)", temperature_c=19.5,
                   result={"lt2": {"speed_kmh": 14.6}}, **kw)


def test_schema_pragmas(db):
    assert db.schema_version == dbmod.SCHEMA_VERSION
    assert db._q("PRAGMA foreign_keys")[0][0] == 1
    assert db._q("PRAGMA journal_mode")[0][0] == "wal"


def test_athlete_crud_and_search(db):
    a = athlete()
    aid = db.add_athlete(a)
    assert a.id == aid
    b = db.get_athlete(aid)
    assert b.name == "Olle Löpare" and b.birth_date == date(1990, 5, 1) and b.sex == Sex.MALE and b.weight_kg == 74.5
    assert b.created is not None
    b.max_hr = 195
    db.update_athlete(b)
    assert db.get_athlete(aid).max_hr == 195
    db.add_athlete(athlete(first_name="Anna", last_name="Berg", sex=None, birth_date=None))
    assert [x.first_name for x in db.list_athletes()] == ["Anna", "Olle"]
    assert [x.first_name for x in db.list_athletes("löp")] == ["Olle"]
    assert [x.first_name for x in db.list_athletes("BERG")] == ["Anna"]
    db.set_archived(aid)
    assert [x.first_name for x in db.list_athletes()] == ["Anna"]
    assert len(db.list_athletes(include_archived=True)) == 2 and db.is_archived(aid)
    with pytest.raises(StorageError):
        db.add_athlete(athlete(first_name="  "))
    with pytest.raises(StorageError):
        db.update_athlete(athlete(id=999))


def test_session_records_samples_roundtrip(db):
    aid = db.add_athlete(athlete())
    s = session(aid)
    recs = [StepRecord(i, 10 + i, 1.0, 240, 130 + 6 * i, 125 + 6 * i, 1.0 + 0.5 * i, 11 + i,
                       gait={"cadence_spm": 170 + i}) for i in range(1, 6)]
    warm = [StepRecord(0, 8, 1.0, 600, 120, 110, 1.2)]
    samples = [TimeSample(float(t), 120 + t // 10, 10.0 + (t // 240), 1.0, 170.5 if t % 2 else None) for t in range(3000)]
    sid = db.save_test(s, recs, samples, warmup_records=warm)
    assert s.id == sid
    g = db.get_session(sid)
    assert g.kind == Kind.LACTATE_STEP and g.started == T0 and g.protocol["stages"][0]["speed_kmh"] == 10
    assert g.result == {"lt2": {"speed_kmh": 14.6}} and g.camera_source.startswith("GoPro")
    back = db.step_records(sid)
    assert back == warm + recs
    assert db.time_samples(sid) == samples
    blob = db._q("SELECT length(data) FROM time_samples WHERE session_id=?", (sid,))[0][0]
    assert blob < 3000 * 6            # compact: a couple of bytes per value
    # update the result only, then the whole session
    db.set_session_result(sid, {"lt2": {"speed_kmh": 14.9}})
    assert db.get_session(sid).result["lt2"]["speed_kmh"] == 14.9
    g.notes = "Varmt i rummet"
    db.save_test(g, recs[:2], samples[:10])
    assert db.get_session(sid).notes == "Varmt i rummet" and len(db.step_records(sid)) == 2
    assert len(db.time_samples(sid)) == 10


def test_lactate_entries_update_stage(db):
    aid = db.add_athlete(athlete())
    sid = db.save_test(session(aid), [StepRecord(1, 10, 1, 240), StepRecord(2, 11, 1, 240)])
    db.add_lactate_entry(sid, 2, 2.4)
    db.add_lactate_entry(sid, 2, 2.1, note="omprov")
    db.add_lactate_entry(sid, None, 9.8, note="3 min efter")
    assert db.step_records(sid)[1].lactate_mmol == 2.1
    e = db.lactate_entries(sid)
    assert [x["mmol"] for x in e] == [2.4, 2.1, 9.8] and e[1]["note"] == "omprov"
    with pytest.raises(StorageError):
        db.add_lactate_entry(sid, 1, 55)


def test_history_and_previous(db):
    aid = db.add_athlete(athlete())
    other = db.add_athlete(athlete(first_name="Anna"))
    ids = [db.add_session(session(aid, T0 + timedelta(days=30 * i))) for i in range(3)]
    db.add_session(session(aid, T0 + timedelta(days=5), kind=Kind.GAIT))
    db.add_session(session(other, T0 + timedelta(days=90)))
    h = db.history(aid, Kind.LACTATE_STEP)
    assert [s.id for s in h] == ids[::-1]
    assert len(db.history(aid)) == 4 and len(db.history(aid, "gait")) == 1
    latest = db.get_session(ids[2])
    assert db.previous_session(latest).id == ids[1]
    assert db.previous_session(db.get_session(ids[0])) is None
    rec = db.recent_sessions(2)
    assert rec[0][1].first_name == "Anna" and len(rec) == 2


def test_gait_and_keyframes_and_relative_paths(db, tmp_path):
    aid = db.add_athlete(athlete())
    sid = db.add_session(session(aid, kind=Kind.GAIT))
    kdir = db.keyframes_root / f"s{sid}"
    kdir.mkdir(parents=True)
    kf = []
    for i, label in enumerate(["Isättning", "Mittstöd"]):
        p = kdir / f"k{i}.png"
        p.write_bytes(b"\x89PNG fake")
        kf.append({"label": label, "frame_index": 10 * i, "t": 0.5 * i, "image_path": str(p), "findings": ["overstride"]})
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"x")
    kf.append({"label": "Extern", "frame_index": 1, "t": 0.1, "image_path": str(outside), "findings": []})
    gid = db.add_gait_analysis(sid, {"summary": {"cadence_spm": 168}, "keyframes": kf}, stage_index=1, view="side")
    stored = [r[0] for r in db._q("SELECT path FROM keyframes WHERE gait_id=?", (gid,))]
    assert stored[0] == f"s{sid}/k0.png" and stored[2] == str(outside)
    g = db.gait_analyses(sid)
    assert g[0]["data"]["summary"]["cadence_spm"] == 168 and g[0]["view"] == "side"
    assert g[0]["keyframes"][1]["image_path"] == str(kdir / "k1.png")
    assert g[0]["keyframes"][0]["findings"] == ["overstride"]

    class FakeAnalysis:
        keyframes = [type("K", (), {"label": "Avfraspark", "frame_index": 3, "t": 1.0, "image_path": str(kdir / "k0.png"),
                                    "findings": []})()]

        def to_dict(self):
            return {"summary": {}}
    db.add_gait_analysis(sid, FakeAnalysis())
    assert db.gait_analyses(sid)[1]["keyframes"][0]["label"] == "Avfraspark"


def test_delete_athlete_erases_everything_and_owned_files_only(db, tmp_path):
    aid = db.add_athlete(athlete())
    keep = db.add_athlete(athlete(first_name="Anna"))
    rec_dir = db.data_root / "recordings"
    rec_dir.mkdir()
    video = rec_dir / "tom.mp4"
    video.write_bytes(b"v")
    own_video = tmp_path / "coach_videos" / "coach_original.mp4"
    own_video.parent.mkdir()
    own_video.write_bytes(b"v")
    sid = db.save_test(session(aid, video_path=str(video)), [StepRecord(1, 10, 1, 240)], [TimeSample(0.0, 100)])
    db.add_lactate_entry(sid, 1, 1.5)
    kf = db.keyframes_root / "tom.png"
    kf.parent.mkdir(parents=True, exist_ok=True)
    kf.write_bytes(b"k")
    db.add_gait_analysis(sid, {"x": 1}, keyframes=[{"label": "a", "image_path": str(kf)}], video_path=str(own_video))
    sid2 = db.save_test(session(keep), [StepRecord(1, 10, 1, 240)])

    res = db.delete_athlete(aid)
    assert res == {"sessions": 1, "files": 2}          # the film and the key frame (no time file here)
    assert not video.exists() and not kf.exists() and own_video.exists()
    for table in ("sessions", "step_records", "time_samples", "lactate_entries", "gait_analyses", "keyframes"):
        col = "athlete_id" if table == "sessions" else None
        n = db._q(f"SELECT COUNT(*) FROM {table}")[0][0]
        if table in ("sessions", "step_records"):
            assert n == 1, table          # Anna's data stays
        else:
            assert n == 0, table
    assert db.get_athlete(aid) is None and db.get_session(sid2) is not None
    with pytest.raises(StorageError):
        db.delete_athlete(aid)


def test_delete_session(db):
    aid = db.add_athlete(athlete())
    sid = db.save_test(session(aid), [StepRecord(1, 10, 1, 240)], [TimeSample(0.0, 100)])
    db.delete_session(sid)
    assert db.get_session(sid) is None and db.step_records(sid) == [] and db.time_samples(sid) == []


def test_settings(db):
    assert db.get_setting("branding", {"x": 1}) == {"x": 1}
    db.set_setting("branding", {"business": "Löpkliniken", "accent": "#22A699"})
    db.set_setting("language", "sv")
    assert db.get_setting("branding")["business"] == "Löpkliniken"
    assert db.all_settings()["language"] == "sv"
    db.delete_setting("language")
    assert db.get_setting("language") is None


def test_transaction_rolls_back(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.add_athlete(athlete())
            raise RuntimeError("boom")
    assert db.list_athletes() == []


def test_export_athlete(db, tmp_path):
    aid = db.add_athlete(athlete())
    sid = db.save_test(session(aid), [StepRecord(1, 10, 1, 240, lactate_mmol=1.1)], [TimeSample(0.0, 100, 10.0, 1.0)])
    out = db.export_athlete_json(aid, tmp_path / "tom.json")
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["athlete"]["first_name"] == "Olle" and d["athlete"]["sex"] == "male"
    assert d["sessions"][0]["session"]["id"] == sid
    assert d["sessions"][0]["step_records"][0]["lactate_mmol"] == 1.1
    assert d["sessions"][0]["time_samples"][0]["hr"] == 100


def test_backup_and_restore(tmp_path):
    root = tmp_path / "pc1"
    db = Database(root / "lopanalys.sqlite")
    aid = db.add_athlete(athlete())
    sid = db.add_session(session(aid, kind=Kind.GAIT))
    (db.keyframes_root / "s1").mkdir(parents=True)
    (db.keyframes_root / "s1" / "a.png").write_bytes(b"png")
    db.add_gait_analysis(sid, {"x": 1}, keyframes=[{"label": "a", "image_path": str(db.keyframes_root / "s1" / "a.png")}])
    db.set_setting("coach", "Anna")
    z = db.backup(tmp_path / "backup.zip")
    db.close()
    with zipfile.ZipFile(z) as zz:
        assert set(zz.namelist()) == {"manifest.json", "lopanalys.sqlite", "keyframes/s1/a.png"}

    # restore on another "PC": key frames are found under the new folder
    pc2 = tmp_path / "pc2" / "lopanalys.sqlite"
    assert Database.restore_backup(z, pc2) == pc2
    db2 = Database(pc2)
    assert db2.get_athlete(aid).first_name == "Olle" and db2.get_setting("coach") == "Anna"
    p = db2.gait_analyses(sid)[0]["keyframes"][0]["image_path"]
    assert p == str(pc2.parent / "keyframes" / "s1" / "a.png") and (pc2.parent / "keyframes" / "s1" / "a.png").exists()
    db2.add_athlete(athlete(first_name="Ny"))
    db2.close()
    # restoring over an existing database keeps a copy of it
    kept = Database.restore_backup(z, pc2)
    assert kept != pc2 and kept.exists() and "before-restore" in kept.name
    with Database(pc2) as db3:
        assert [a.first_name for a in db3.list_athletes()] == ["Olle"]


def test_restore_rejects_bad_files(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(StorageError):
        Database.restore_backup(bad, tmp_path / "d.sqlite")
    z = tmp_path / "y.zip"
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("manifest.json", json.dumps({"format": "lopanalys-backup", "schema_version": 1}))
        zz.writestr("lopanalys.sqlite", b"garbage" * 100)
    with pytest.raises(StorageError):
        Database.restore_backup(z, tmp_path / "d.sqlite")
    assert not (tmp_path / "d.sqlite").exists()
    # zip-slip entries are ignored
    good = Database(tmp_path / "g" / "lopanalys.sqlite")
    zp = good.backup(tmp_path / "g.zip")
    good.close()
    with zipfile.ZipFile(zp, "a") as zz:
        zz.writestr("keyframes/../../evil.txt", "x")
    Database.restore_backup(zp, tmp_path / "h" / "lopanalys.sqlite")
    assert not (tmp_path / "evil.txt").exists()


def test_migration_from_v1_keeps_data_and_backs_up(tmp_path):
    p = tmp_path / "old.sqlite"
    con = sqlite3.connect(p)
    for stmt in dbmod.MIGRATIONS[0]:
        con.execute(stmt)
    con.execute("PRAGMA user_version = 1")
    con.execute("INSERT INTO athletes (first_name,last_name,notes,created) VALUES ('Gammal','Kund','', ?)",
                (T0.isoformat(),))
    con.commit()
    con.close()
    with Database(p) as d:
        assert d.schema_version == dbmod.SCHEMA_VERSION
        a = d.list_athletes()[0]
        assert a.name == "Gammal Kund" and not d.is_archived(a.id)
        d.set_archived(a.id)
        assert d.list_athletes() == []
    assert (tmp_path / "old.sqlite.v1.bak").exists()


def test_newer_schema_is_refused_and_untouched(tmp_path):
    p = tmp_path / "new.sqlite"
    con = sqlite3.connect(p)
    con.execute(f"PRAGMA user_version = {dbmod.SCHEMA_VERSION + 1}")
    con.close()
    with pytest.raises(StorageError) as e:
        Database(p)
    assert "nyare version" in str(e.value)
    con = sqlite3.connect(p)
    assert con.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION + 1
    con.close()


def test_not_a_database(tmp_path):
    p = tmp_path / "junk.sqlite"
    p.write_bytes(b"this is not sqlite" * 200)
    with pytest.raises(StorageError):
        Database(p)


def test_default_path_uses_lopanalys_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOPANALYS_HOME", str(tmp_path / "home"))
    with Database() as d:
        assert d.path == tmp_path / "home" / "lopanalys.sqlite"
        assert d.keyframes_root == tmp_path / "home" / "keyframes"


def test_thread_safety(db):
    aid = db.add_athlete(athlete())
    errors = []

    def worker(n):
        try:
            for i in range(20):
                sid = db.save_test(session(aid, T0 + timedelta(minutes=n * 100 + i)),
                                   [StepRecord(1, 10, 1, 240)], [TimeSample(0.0, 100)])
                db.add_lactate_entry(sid, 1, 2.0)
                db.history(aid)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    assert errors == []
    assert len(db.history(aid)) == 120


def test_gait_aliases_for_ui(db):
    aid = db.add_athlete(athlete())
    sid = db.add_session(session(aid, kind=Kind.GAIT))
    assert db.gait_analysis(sid) is None
    db.save_gait_analysis(sid, {"summary": {"cadence_spm": 170}, "keyframes": [{"label": "a", "image_path": "s/a.png"}]})
    g = db.gait_analysis(sid)
    assert g["summary"]["cadence_spm"] == 170 and g["keyframes"][0]["image_path"] == str(db.keyframes_root / "s/a.png")


def test_save_upserts(db):
    a = db.save_athlete(athlete())
    a.notes = "ny"
    db.save_athlete(a)
    assert len(db.list_athletes()) == 1 and db.get_athlete(a.id).notes == "ny"
    s = db.save_session(session(a.id))
    s.notes = "x"
    db.save_session(s)
    assert len(db.history(a.id)) == 1 and db.get_session(s.id).notes == "x"


def test_v7_max_hr_source_and_watch_vo2max(tmp_path):
    """Where the max HR came from, and the VO2max from the watch (migration v7)."""
    from datetime import date

    from lopanalys.core.models import Athlete
    from lopanalys.storage import db as dbmod
    from lopanalys.storage.db import Database
    assert dbmod.SCHEMA_VERSION >= 7
    with Database(tmp_path / "x.sqlite") as d:
        a = d.save_athlete(Athlete(None, "Olle", "T", max_hr=188, max_hr_source="entered",
                                   max_hr_date=date(2026, 11, 14), watch_vo2max=51.0,
                                   watch_vo2max_date=date(2026, 11, 1)))
        b = d.get_athlete(a.id)
        assert (b.max_hr_source, b.max_hr_date, b.watch_vo2max, b.watch_vo2max_date) == (
            "entered", date(2026, 11, 14), 51.0, date(2026, 11, 1))
        b.max_hr = None
        d.save_athlete(b)
        c = d.get_athlete(a.id)
        assert (c.max_hr_source, c.max_hr_date) == ("", None)
