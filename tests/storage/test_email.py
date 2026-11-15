"""The athlete's e-mail address: the migration, the extract, the erasure and the note
on a test that it was mailed."""
import json
import sqlite3
import zipfile
from datetime import datetime, timezone

from lopanalys.core.models import Athlete, Session, StepRecord, TimeSample, valid_email
from lopanalys.core.models import TestKind as Kind
from lopanalys.storage import db as dbmod
from lopanalys.storage import privacy
from lopanalys.storage.db import Database

from . import main_db

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
ADDRESS = "anna.unik-adress@example.test"


def _v5(path):
    """A database as the version before this one left it (schema v5), with one athlete."""
    con = sqlite3.connect(path)
    for stmts in dbmod.MIGRATIONS[:5]:
        for stmt in stmts:
            con.execute(stmt)
    con.execute("INSERT INTO athletes (first_name,last_name,created,consent_json) VALUES ('Anna','Berg',?,?)",
                (T0.isoformat(), json.dumps({"date": "2026-11-14", "contact": True})))
    con.execute("PRAGMA user_version = 5")
    con.commit()
    con.close()


def test_migration_adds_the_column_and_keeps_everything(tmp_path):
    path = tmp_path / "lopanalys.sqlite"
    _v5(path)
    with Database(path) as db:
        assert db.schema_version == dbmod.SCHEMA_VERSION >= 6
        assert (tmp_path / "lopanalys.sqlite.v5.bak").is_file()              # the copy from before the upgrade
        (anna,) = db.list_athletes()
        assert anna.name == "Anna Berg" and anna.email == "" and anna.consents("contact")
        anna.email = f"  {ADDRESS} "
        db.save_athlete(anna)
        assert db.get_athlete(anna.id).email == ADDRESS
        new = db.save_athlete(Athlete(None, "Erik", "", email="erik@example.test"))
        assert db.get_athlete(new.id).email == "erik@example.test"
    # the migration is one added column with a default: nothing else in the table changed
    assert dbmod.MIGRATIONS[5] == ["ALTER TABLE athletes ADD COLUMN email TEXT NOT NULL DEFAULT ''"]


def test_a_database_from_main_v3_gets_the_column(tmp_path):
    root, _ = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        assert [a.email for a in db.list_athletes()] == ["", ""]
        cols = [r[1] for r in db._q("PRAGMA table_info(athletes)")]
        i = cols.index("consent_json")
        assert cols[i:i + 2] == ["consent_json", "email"]          # later versions add after them


def test_the_address_is_in_the_extract(tmp_path):
    with Database(tmp_path / "data" / "lopanalys.sqlite") as db:
        a = db.save_athlete(Athlete(None, "Anna", "Berg", email=ADDRESS))
        assert db.export_athlete(a.id)["athlete"]["email"] == ADDRESS
        z = privacy.export_athlete_zip(db, a.id, tmp_path / "utdrag.zip")
        with zipfile.ZipFile(z) as zf:
            assert json.loads(zf.read("data.json"))["athlete"]["email"] == ADDRESS
            html = zf.read("sammanstallning.html").decode("utf-8")
        assert f"<th>E-post</th><td>{ADDRESS}</td>" in html


def test_erasure_removes_the_address_and_the_saved_fit_file(tmp_path):
    root = tmp_path / "data"
    with Database(root / "lopanalys.sqlite") as db:
        a = db.save_athlete(Athlete(None, "Anna", "Berg", email=ADDRESS))
        s = Session(None, a.id, Kind.LACTATE_STEP, T0)
        db.save_test(s, [StepRecord(0, 10, 1, 60)], [TimeSample(0.0, 100, 10.0)])
        fit_inside = root / "reports" / f"test-{s.id}-2026-11-14.fit"
        fit_inside.parent.mkdir()
        fit_outside = tmp_path / "usb" / f"test-{s.id}-2026-11-14.fit"
        fit_outside.parent.mkdir()
        for f in (fit_inside, fit_outside):
            f.write_bytes(b"fit")
            db.add_saved_file("fit", f, athlete_id=a.id, session_id=s.id)
        gone = tmp_path / "usb" / "moved.fit"
        db.add_saved_file("fit", gone, athlete_id=a.id, session_id=s.id)
        db.log_mailed([s.id], ["report", "fit"])
        res = db.erase_athlete(a.id)
        assert not fit_inside.exists() and not fit_outside.exists()
        assert [(x["kind"], x["path"]) for x in res["left"]] == [("fit", str(gone))]
        assert db.list_athletes() == []
    blob = b"".join(p.read_bytes() for p in root.glob("lopanalys.sqlite*"))
    assert ADDRESS.encode() not in blob and b"unik-adress" not in blob      # not left in the file's free pages


def test_a_mailed_test_notes_time_and_content_but_no_address(tmp_path):
    with Database(tmp_path / "lopanalys.sqlite") as db:
        a = db.save_athlete(Athlete(None, "Anna", "Berg", email=ADDRESS))
        s = db.save_session(Session(None, a.id, Kind.LACTATE_STEP, T0, result={"coach_comment": "Bra"}))
        entry = db.log_mailed([s.id, 999], ["report", "fit"])
        db.log_mailed([s.id], ["report"])
        res = db.get_session(s.id).result
        assert res["coach_comment"] == "Bra" and res["mailed"][0] == entry
        assert [m["what"] for m in res["mailed"]] == [["report", "fit"], ["report"]]
        assert set(entry) == {"at", "what"} and datetime.fromisoformat(entry["at"]).tzinfo is not None
        assert ADDRESS not in db._q("SELECT result_json FROM sessions")[0][0]


def test_what_counts_as_an_address():
    for ok in ("a@b.se", "anna.berg+test@example.com", "åsa@exempel.se", "x_y-z@sub.domain.org"):
        assert valid_email(ok), ok
    for bad in ("", "a", "a@b", "@b.se", "a@.se", "a b@c.se", "a@b.se ", "a@b..se", "a@b.se\nBcc: x@y.zz",
                "Anna <a@b.se>", "a@b.se,c@d.se", "a@b@c.se", "x" * 250 + "@b.se"):
        assert not valid_email(bad), bad
