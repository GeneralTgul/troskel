"""A database written by main (schema v3) opens, migrates and keeps everything.

The data on the coach's PC must survive every update: these tests open a real v3 database with its
files, and check that nothing is lost while the files get neutral names."""
import json
import sqlite3
from pathlib import Path

from lopanalys.core.models import TestKind as Kind
from lopanalys.storage import db as dbmod
from lopanalys.storage.db import Database

from . import main_db


def _names(folder):
    return sorted(p.name for p in folder.iterdir())


def test_main_database_migrates_and_keeps_everything(tmp_path):
    root, _ = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        assert db.schema_version == dbmod.SCHEMA_VERSION
        assert (root / "lopanalys.sqlite.v3.bak").is_file()            # the copy from before the upgrade
        anna, erik = db.list_athletes()
        assert (anna.name, erik.name) == ("Anna Lindqvist Berg", "Erik Sandberg")
        assert anna.notes == "Ont i höger knä i våras" and erik.max_hr == 186
        (gait,) = db.sessions(anna.id)
        assert gait.kind == Kind.GAIT and gait.status == "finished" and len(db.time_samples(gait.id)) == 120
        lact = db.sessions(erik.id, Kind.LACTATE_STEP)
        done = next(s for s in lact if s.status == "finished")
        assert [r.lactate_mmol for r in db.step_records(done.id)] == [1.0, 1.3, 2.2, 3.7, 5.8]
        assert len(db.lactate_entries(done.id)) == 5 and done.result["coach_comment"] == "Bra test"
        assert db.get_setting("branding.coach") == "Coach Demo"
        assert db.saved_files(anna.id) and db._q("SELECT COUNT(*) FROM erased")[0][0] == 0
        assert anna.consent == {} and not anna.consent_registered        # nothing is assumed for people from before
        anna.consent = {"date": "2026-11-14", "test": True, "film": True, "store": False, "contact": False}
        db.save_athlete(anna)
        back = db.get_athlete(anna.id)
        assert back.consent == anna.consent and back.consents("film") and not back.consents("store")


def test_films_keyframes_and_reports_get_neutral_names(tmp_path):
    root, outside = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        anna, erik = db.list_athletes()
        (gait,) = db.sessions(anna.id)
        # the film, the film from before the cut, and their sidecars: named after the test
        assert _names(root / "recordings") == [
            "session-1-20261113-100000.mp4", "session-1-20261113-100000.mp4.json",
            "session-1-20261113-101500.mp4", "session-1-20261113-101500.mp4.json",
            "session-3-20261114-143200.mp4", "session-3-20261114-143200.mp4.json"]
        assert gait.video_path == str(root / "recordings" / "session-1-20261113-101500.mp4")
        assert gait.result["earlier_videos"] == [str(root / "recordings" / "session-1-20261113-100000.mp4")]
        assert json.loads((root / "recordings" / "session-1-20261113-101500.mp4.json").read_text())["video"] == \
            "session-1-20261113-101500.mp4"
        # stored relative to the data folder: a moved folder or a restored backup still finds them
        assert db._q("SELECT video_path FROM sessions WHERE id=1")[0][0] == "recordings/session-1-20261113-101500.mp4"
        # key frames
        g = db.gait_analysis(gait.id)
        assert _names(root / "keyframes" / "session-1") == ["session-1-g1_01_contact_left.png",
                                                            "session-1-g1_02_midstance_left.png"]
        assert [k["label"] for k in g["keyframes"]] == ["Fotisättning", "Mittstöd"]
        assert all((root / "keyframes" / "session-1").joinpath(k["image_path"]).is_file() for k in g["keyframes"])
        assert g["video_path"] == gait.video_path and g["findings"][0]["code"] == "overstride"
        # a film outside the data folder is the coach's own: untouched
        own = next(s for s in db.sessions(erik.id) if s.camera_source == "Videofil")
        assert Path(own.video_path) == outside / main_db.OUTSIDE_FILM and (outside / main_db.OUTSIDE_FILM).is_file()
        # reports: renamed and registered to the athlete, the sheet about the tests left alone
        assert _names(root / "reports") == ["Om_testerna.pdf", "rapport-1-Löpteknik_10–12_km-h_2026-11-13.pdf",
                                            "rapport-2-Laktattrappa_2026-11-14.pdf"]
        assert [f["kind"] for f in db.saved_files(erik.id)] == ["report"]
        assert db.saved_files(erik.id)[0]["path"] == str(root / "reports" / "rapport-2-Laktattrappa_2026-11-14.pdf")
    # no surname left in a file name, in the database's paths or in the log
    left = [p.name for p in root.rglob("*") if "Lindqvist" in p.name or "Sandberg" in p.name]
    assert left == []
    con = sqlite3.connect(root / "lopanalys.sqlite")
    rows = [str(r) for t in ("sessions", "gait_analyses", "keyframes", "saved_files")
            for r in con.execute(f"SELECT * FROM {t}")]   # noqa: S608 - fixed table names
    con.close()
    assert not [r for r in rows if "Lindqvist_Berg" in r or "-Sandberg" in r.replace("GX010042-Sandberg", "")]
    assert "Lindqvist" not in (root / "logs" / "lopanalys.log").read_text(encoding="utf-8")


def test_renaming_survives_an_interruption_and_runs_once(tmp_path, monkeypatch):
    root, _ = main_db.build(tmp_path)
    calls = {"n": 0}
    real = Database._neutral_keyframe

    def dies(self, *a):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("power cut")
        return real(self, *a)

    monkeypatch.setattr(Database, "_neutral_keyframe", dies)
    with Database(root / "lopanalys.sqlite") as db:                    # opens anyway
        assert db.get_setting("privacy.neutral_names") is None
        assert len(db.list_athletes()) == 2
    monkeypatch.setattr(Database, "_neutral_keyframe", real)
    with Database(root / "lopanalys.sqlite") as db:                    # the next start finishes the job
        assert db.get_setting("privacy.neutral_names") == 1
        g = db.gait_analysis(1)
        assert all((root / "keyframes" / "session-1").joinpath(k["image_path"]).is_file() for k in g["keyframes"])
        assert _names(root / "keyframes" / "session-1") == ["session-1-g1_01_contact_left.png",
                                                            "session-1-g1_02_midstance_left.png"]
        assert len(_names(root / "recordings")) == 6
        assert len(db.saved_files(1)) == 1                             # the report is registered once
    before = sorted(p.as_posix() for p in root.rglob("*") if p.is_file() and "sqlite" not in p.name)
    with Database(root / "lopanalys.sqlite"):
        pass
    assert before == sorted(p.as_posix() for p in root.rglob("*") if p.is_file() and "sqlite" not in p.name)


def test_cut_off_test_from_main_is_still_found_and_continued(tmp_path):
    """The test that was running when main was closed for the update is offered as before."""
    root, _ = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        (cut,) = db.running_sessions()
        assert cut.video_path == str(root / "recordings" / "session-3-20261114-143200.mp4")
        assert cut.result["live"] == {"lactate": {"1": 1.9}, "rpe": {}}
        db.mark_interrupted(cut.id)
        assert db.get_session(cut.id).status == "interrupted" and len(db.step_records(cut.id)) == 1


def test_new_paths_are_relative_and_read_back_absolute(tmp_path):
    from datetime import datetime, timezone

    from lopanalys.core.models import Athlete, Session
    root = tmp_path / "d"
    with Database(root / "l.sqlite") as db:
        a = db.save_athlete(Athlete(None, "Olle", "Löpare"))
        film = root / "recordings" / "session-1-20261114-090000.mp4"
        s = db.save_session(Session(None, a.id, Kind.GAIT, datetime(2026, 11, 14, 9, tzinfo=timezone.utc),
                                    video_path=str(film), result={"earlier_videos": [str(root / "recordings" / "x.mp4")]}))
        assert db._q("SELECT video_path FROM sessions")[0][0] == "recordings/session-1-20261114-090000.mp4"
        assert "recordings/x.mp4" in db._q("SELECT result_json FROM sessions")[0][0]
        back = db.get_session(s.id)
        assert back.video_path == str(film) and back.result["earlier_videos"] == [str(root / "recordings" / "x.mp4")]
        db.set_session_result(s.id, back.result)
        assert db.get_session(s.id).result["earlier_videos"] == [str(root / "recordings" / "x.mp4")]
