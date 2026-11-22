"""The right to erasure: everything about a person goes, the file holds no trace of the rows,
what the program cannot reach is listed, and a restored backup does not bring the person back."""
import os
import time
from pathlib import Path
from datetime import datetime, timezone

import pytest

from lopanalys.core.models import Athlete, Session, StepRecord, TimeSample
from lopanalys.core.models import TestKind as Kind
from lopanalys.storage.db import Database, StorageError

from . import main_db

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
SECRET = "Zebraknä-anteckning-4711"


def _person(db, root, first="Olle", last="Löpare"):
    a = db.save_athlete(Athlete(None, first, last, notes=SECRET if first == "Olle" else ""))
    s = Session(None, a.id, Kind.GAIT, T0, protocol_name="Löpteknik", result={})
    db.save_session(s)
    film = root / "recordings" / f"session-{s.id}-20261101-090000.mp4"
    earlier = root / "recordings" / f"session-{s.id}-20261101-085000.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    for f in (film, earlier):
        f.write_bytes(b"v")
        f.with_name(f.name + ".json").write_text("{}")
    s.video_path, s.result = str(film), {"earlier_videos": [str(earlier)]}
    db.save_test(s, [StepRecord(0, 10, 1, 60, lactate_mmol=1.5)], [TimeSample(0.0, 100)])
    kdir = db.keyframes_root / f"session-{s.id}"
    kdir.mkdir(parents=True, exist_ok=True)
    kf = kdir / f"session-{s.id}-g1_01_contact_left.png"
    kf.write_bytes(b"k")
    (kdir / "stray.png").write_bytes(b"k")            # a file the database does not know: the folder goes anyway
    db.add_gait_analysis(s.id, {"x": 1}, keyframes=[{"label": "a", "image_path": str(kf)}])
    return a, s


def test_erase_removes_rows_files_reports_and_lists_the_rest(tmp_path):
    root = tmp_path / "data"
    with Database(root / "lopanalys.sqlite") as db:
        olle, s = _person(db, root)
        anna, s2 = _person(db, root, "Anna", "Berg")
        inside = root / "reports" / f"rapport-{olle.id}-x.pdf"
        inside.parent.mkdir()
        outside = tmp_path / "usb" / "Olle_Löpare_x.pdf"
        outside.parent.mkdir()
        handed = tmp_path / "usb" / "just-saved.pdf"
        moved = tmp_path / "usb" / "moved-away.pdf"
        extract = tmp_path / "usb" / "utdrag.zip"
        for f in (inside, outside, handed, extract):
            f.write_bytes(b"x")
        for f in (inside, outside, handed, moved):
            db.add_saved_file("report", f, athlete_id=olle.id, session_id=s.id)
        db.add_saved_file("extract", extract, athlete_id=olle.id)
        backup = tmp_path / "usb" / "kopia.lopbak"
        backup.write_bytes(b"b")
        db.add_saved_file("backup", backup)
        bak = root / "lopanalys.sqlite.v3.bak"
        bak.write_bytes(b"old")

        res = db.erase_athlete(olle.id, keep=[handed])

        assert res["sessions"] == 1 and res["files"] == 9          # 2 films, 2 time files, 2 key frames, 2 reports, extract
        assert db.get_athlete(olle.id) is None and db.sessions(olle.id) == []
        assert sorted(p.name for p in (root / "recordings").iterdir()) == [
            f"session-{s2.id}-20261101-085000.mp4", f"session-{s2.id}-20261101-085000.mp4.json",
            f"session-{s2.id}-20261101-090000.mp4", f"session-{s2.id}-20261101-090000.mp4.json"]
        assert not (db.keyframes_root / f"session-{s.id}").exists() and (db.keyframes_root / f"session-{s2.id}").is_dir()
        assert not inside.exists() and not outside.exists() and not extract.exists() and handed.exists()
        left = {(x["kind"], os.path.basename(x["path"])) for x in res["left"]}
        assert left == {("report", "just-saved.pdf"), ("report", "moved-away.pdf"), ("backup", "kopia.lopbak"),
                        ("safety", "lopanalys.sqlite.v3.bak")}
        assert next(x for x in res["left"] if x["kind"] == "safety")["until"] >= time.strftime("%Y-%m-%d")
        assert db.saved_files(olle.id) == [] and len(db.sessions(anna.id)) == 1     # Anna is untouched
        assert [tuple(r) for r in db._q("SELECT kind,row_id FROM erased ORDER BY kind")] == [("athlete", olle.id), ("session", s.id)]
        with pytest.raises(StorageError):
            db.erase_athlete(olle.id)
    # nothing of the person is left in the database file or beside it (secure_delete + VACUUM, WAL emptied)
    for f in root.glob("lopanalys.sqlite*"):
        if f.name.endswith(".bak"):
            continue
        assert SECRET.encode() not in f.read_bytes() and "Löpare".encode() not in f.read_bytes(), f.name


def test_without_scrub_the_rows_would_still_be_in_the_file(tmp_path):
    """The reason for secure_delete and VACUUM: a plain DELETE leaves the text in the file's free pages."""
    import sqlite3
    p = tmp_path / "plain.sqlite"
    con = sqlite3.connect(p)
    con.execute("PRAGMA secure_delete = OFF")
    con.execute("CREATE TABLE a (id INTEGER PRIMARY KEY, notes TEXT)")
    con.executemany("INSERT INTO a (notes) VALUES (?)", [(f"{SECRET}-{i}" * 20,) for i in range(200)])
    con.commit()
    con.execute("DELETE FROM a")
    con.commit()
    con.close()
    assert SECRET.encode() in p.read_bytes()


def test_film_outside_the_data_folder_is_listed_not_removed(tmp_path):
    root, outside = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        erik = db.list_athletes()[1]
        res = db.erase_athlete(erik.id)
        assert (outside / main_db.OUTSIDE_FILM).is_file()
        assert ("film", (outside / main_db.OUTSIDE_FILM).as_posix()) in {(x["kind"], Path(x["path"]).as_posix()) for x in res["left"]}
        assert not (root / "reports" / "rapport-2-Laktattrappa_2026-11-14.pdf").exists()
        assert sorted(p.name for p in (root / "recordings").iterdir()) == [
            "session-1-20261113-100000.mp4", "session-1-20261113-100000.mp4.json",
            "session-1-20261113-101500.mp4", "session-1-20261113-101500.mp4.json"]


def test_restored_backup_does_not_bring_the_erased_back(tmp_path):
    root = tmp_path / "data"
    db = Database(root / "lopanalys.sqlite")
    olle, s = _person(db, root)
    anna, _ = _person(db, root, "Anna", "Berg")
    zip_path = db.backup(tmp_path / "kopia.zip")                    # Olle is in this backup
    db.erase_athlete(olle.id)
    new = db.save_athlete(Athlete(None, "Nils", "Ny"))               # may get Olle's old id back
    db.close()

    Database.restore_backup(zip_path, root / "lopanalys.sqlite")
    assert (root / "erased-pending.json").is_file()
    with Database(root / "lopanalys.sqlite") as db2:
        names = [a.name for a in db2.list_athletes()]
        assert names == ["Anna Berg"]                                # Olle came back with the backup and went again
        assert db2.get_session(s.id) is None
        assert not (db2.keyframes_root / f"session-{s.id}").exists()  # the backup's key frames went too
        assert not (root / "erased-pending.json").exists()
        assert [tuple(r) for r in db2._q("SELECT kind,row_id FROM erased WHERE kind='athlete'")] == [("athlete", olle.id)]
        # a new person who gets the erased id is not the erased person
        nils = db2.save_athlete(Athlete(None, "Nils", "Ny"))
        assert db2.apply_erased() == 0 and db2.get_athlete(nils.id) is not None
    assert new.id is not None


def test_delete_session_is_an_erasure_too(tmp_path):
    root = tmp_path / "data"
    with Database(root / "lopanalys.sqlite") as db:
        olle, s = _person(db, root)
        db.delete_session(s.id)
        assert list((root / "recordings").iterdir()) == [] and not (db.keyframes_root / f"session-{s.id}").exists()
        assert [tuple(r) for r in db._q("SELECT kind,row_id FROM erased")] == [("session", s.id)]
        assert db.get_athlete(olle.id) is not None


def test_delete_session_removes_the_film_an_analysis_points_to(tmp_path):
    """A film known only from the analysis row (gait_analyses.video_path) went with the person in
    erase_athlete() but stayed on the disk when the one test was removed."""
    root = tmp_path / "data"
    with Database(root / "lopanalys.sqlite") as db:
        olle, s = _person(db, root)
        own = root / "recordings" / f"session-{s.id}-20261101-091500-2.mp4"
        own.write_bytes(b"v")
        own.with_name(own.name + ".json").write_text("{}")
        db.add_gait_analysis(s.id, {"x": 2}, video_path=str(own))
        db.delete_session(s.id)
        assert list((root / "recordings").iterdir()) == []


# ------------------------------------------------------------------ retention of films
def test_films_are_removed_30_days_after_the_test(tmp_path):
    from datetime import timedelta
    root = tmp_path / "data"
    with Database(root / "lopanalys.sqlite") as db:
        olle, old = _person(db, root)                                   # T0, ended None
        anna, new = _person(db, root, "Anna", "Berg")
        new.started = T0 + timedelta(days=5)
        db.save_session(new)
        own = tmp_path / "card" / "GX01.mp4"
        own.parent.mkdir()
        own.write_bytes(b"v")
        mine = db.save_session(Session(None, olle.id, Kind.GAIT, T0, video_path=str(own)))
        running = db.save_session(Session(None, olle.id, Kind.GAIT, T0, status="running",
                                          video_path=str(root / "recordings" / "session-9-x.mp4")))
        assert db.purge_expired_videos(now=T0 + timedelta(days=29)) == 0
        assert db.purge_expired_videos(now=T0 + timedelta(days=31)) == 1
        s = db.get_session(old.id)
        assert s.video_path is None and "earlier_videos" not in s.result and s.result["video_removed"]
        assert sorted(p.name for p in (root / "recordings").iterdir()) == [
            f"session-{new.id}-20261101-085000.mp4", f"session-{new.id}-20261101-085000.mp4.json",
            f"session-{new.id}-20261101-090000.mp4", f"session-{new.id}-20261101-090000.mp4.json"]
        g = db.gait_analysis(old.id)                                   # key frames and measurements stay
        assert g["x"] == 1 and len(g["keyframes"]) == 1 and (db.keyframes_root / f"session-{old.id}").is_dir()
        assert db.get_session(new.id).video_path and db.get_session(running.id).video_path
        assert db.get_session(mine.id).video_path == str(own) and own.is_file()   # the coach's own file stays
        assert db.purge_expired_videos(now=T0 + timedelta(days=31)) == 0


# ------------------------------------------------------------------ the extract
def test_extract_has_everything_about_one_person_and_nobody_else(tmp_path):
    import json
    import zipfile

    from lopanalys.storage import privacy
    root, _ = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        anna, erik = db.list_athletes()
        dest = privacy.export_athlete_zip(db, anna.id, tmp_path / "ut" / "utdrag.zip")
        with zipfile.ZipFile(dest) as z:
            names = sorted(z.namelist())
            page = z.read("sammanstallning.html").decode("utf-8")
            data = json.loads(z.read("data.json"))
            everything = b"".join(z.read(n) for n in names)
        assert names == ["bilder/session-1/session-1-g1_01_contact_left.png", "bilder/session-1/session-1-g1_02_midstance_left.png",
                         "data.json", "film/session-1-20261113-100000.mp4", "film/session-1-20261113-100000.mp4.json",
                         "film/session-1-20261113-101500.mp4", "film/session-1-20261113-101500.mp4.json",
                         "rapporter/rapport-1-Löpteknik_10–12_km-h_2026-11-13.pdf", "sammanstallning.html"]
        assert "Anna Lindqvist Berg" in page and "Ont i höger knä i våras" in page and "Löpteknik 10–12 km/h" in page
        assert "<td>10,0</td>" in page and "Personuppgifter" in page
        assert data["athlete"]["first_name"] == "Anna" and len(data["sessions"]) == 1
        s = data["sessions"][0]
        assert s["session"]["video_path"] == "film/session-1-20261113-101500.mp4"       # paths point into the zip
        assert s["gait_analyses"][0]["keyframes"][0]["image_path"].startswith("bilder/session-1/")
        assert len(s["time_samples"]) == 120
        assert b"Sandberg" not in everything and str(root).encode() not in everything     # nobody else, no local paths
        assert [f["kind"] for f in db.saved_files(anna.id)] == ["report", "extract"]
        db.erase_athlete(anna.id)                                                        # the extract goes with the person
        assert not dest.exists()
        with pytest.raises(StorageError):
            privacy.export_athlete_zip(db, anna.id, tmp_path / "x.zip")


# ------------------------------------------------------------------ the 24-month reminder
def test_reminder_24_months_after_the_latest_test(tmp_path):
    from datetime import timedelta
    now = datetime(2029, 1, 10, 12, 0, tzinfo=timezone.utc)
    with Database(tmp_path / "d" / "lopanalys.sqlite") as db:
        def person(first, last_test=None, created=None, consent=None):
            a = Athlete(None, first, "Test", created=created or now - timedelta(days=10), consent=consent or {})
            db.save_athlete(a)
            if last_test is not None:
                db.save_session(Session(None, a.id, Kind.LACTATE_STEP, last_test - timedelta(days=400)))
                db.save_session(Session(None, a.id, Kind.LACTATE_STEP, last_test))
            return a

        old = person("Gammal", now - timedelta(days=735))
        person("Nyss", now - timedelta(days=700))
        person("Ny", None)                                                    # registered ten days ago, no test
        never = person("Aldrig", None, created=now - timedelta(days=800))      # registered long ago, never tested
        keep = {"date": "2028-12-01", "test": True, "film": False, "store": False, "contact": False}
        nostore = person("Ejspara", now - timedelta(days=3), consent=keep)
        person("Idag", now, consent=keep)                                      # tested today: the report is not out yet
        due = {d["athlete"].first_name: d for d in db.retention_due(now=now)}
        assert sorted(due) == ["Aldrig", "Ejspara", "Gammal"]
        assert due["Gammal"]["reason"] == "old" and due["Gammal"]["last"] == "2027-01-06"
        assert due["Aldrig"]["reason"] == "old" and due["Ejspara"]["reason"] == "no_store"
        assert db.get_athlete(old.id) is not None and db.get_athlete(never.id) is not None   # a reminder, never an erasure
        db.erase_athlete(nostore.id)
        assert "Ejspara" not in [d["athlete"].first_name for d in db.retention_due(now=now)]


def test_restore_keeps_the_register_of_saved_files_so_an_erasure_still_finds_them(tmp_path):
    """A restored backup knew nothing of the files written after it was made (nor of itself: its own
    row is written after the snapshot). Erasing a person after a restore then left their extract and
    reports behind without listing them, and did not list the backup the person is in."""
    root = tmp_path / "data"
    db = Database(root / "lopanalys.sqlite")
    olle, s = _person(db, root)
    anna, _ = _person(db, root, "Anna", "Berg")
    backup = db.backup(tmp_path / "usb" / "kopia.lopbak", "ett-långt-lösenord")
    report = tmp_path / "usb" / "rapport.pdf"
    extract = tmp_path / "usb" / "utdrag.zip"
    gone = tmp_path / "usb" / "nils.pdf"
    for f in (report, extract, gone):
        f.write_bytes(b"x")
    db.add_saved_file("report", report, athlete_id=olle.id, session_id=s.id)
    db.add_saved_file("extract", extract, athlete_id=olle.id)
    nils = db.save_athlete(Athlete(None, "Nils", "Ny"))               # not in the backup
    db.add_saved_file("report", gone, athlete_id=nils.id)
    db.close()

    Database.restore_backup(backup, root / "lopanalys.sqlite", password="ett-långt-lösenord")
    with Database(root / "lopanalys.sqlite") as db2:
        assert {(f["kind"], os.path.basename(f["path"])) for f in db2.saved_files(olle.id)} == {
            ("report", "rapport.pdf"), ("extract", "utdrag.zip")}
        assert db2.saved_files(olle.id, "report")[0]["session_id"] == s.id
        assert db2.get_athlete(nils.id) is None and db2.saved_files(nils.id) == []   # no row for a person who is not there
        res = db2.erase_athlete(olle.id)
        assert not report.exists() and not extract.exists() and gone.exists()
        assert ("backup", "kopia.lopbak") in {(x["kind"], os.path.basename(x["path"])) for x in res["left"]}

    # on another PC there is no register to carry over: the backup itself is still known
    other = tmp_path / "pc2" / "lopanalys.sqlite"
    Database.restore_backup(backup, other, password="ett-långt-lösenord")
    with Database(other) as db3:
        assert [(f["kind"], os.path.basename(f["path"])) for f in db3.saved_files(None)] == [("backup", "kopia.lopbak")]
        res = db3.erase_athlete(olle.id)
        assert ("backup", "kopia.lopbak") in {(x["kind"], os.path.basename(x["path"])) for x in res["left"]}
