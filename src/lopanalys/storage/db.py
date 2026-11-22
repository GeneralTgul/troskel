"""SQLite storage for athletes, test sessions and their raw data. No ORM.

One file per coach (``core.paths.db_path()``), WAL journal, foreign keys with ON DELETE CASCADE so
deleting an athlete removes all their data (GDPR: a coach must be able to erase a client), and
their recordings and key frames inside the app's data folder.

Schema versioning: ``PRAGMA user_version`` holds the version; ``MIGRATIONS[i]`` upgrades version i
to i+1 inside one transaction. Before an existing database is migrated a copy is saved next to it
(``lopanalys.sqlite.v<old>.bak``). A database from a newer program version is refused, never
touched.

Sport (version 8): every session has a ``sport`` and ``athlete_sport`` holds the max HR per athlete
and sport. The migration only adds (a column with the default 'run', an index, a table), so every
test from before is a running test with its values untouched. ``athletes.max_hr`` stays and is
running's max HR; the row for 'run' in ``athlete_sport`` mirrors it. ``step_records.speed_kmh`` is
the load in the unit of the session's sport (km/h for running).

Thread safety: one connection shared under a re-entrant lock (``check_same_thread=False``). Each
public method is atomic; ``transaction()`` groups several calls.

Time samples are stored compactly: one row per session, columnar JSON compressed with zlib.
Key frame paths inside the key-frame folder are stored relative to it, and films, reports and other
files inside the data folder relative to the data folder, so a backup restored on another PC (or a
data folder that was moved) still finds them. In memory every path is absolute.

Privacy: file names never carry a person's name. Films are ``session-<id>-<time>.mp4``,
key frames ``session-<id>-g<analysis>_…png``, reports ``rapport-<athlete id>-…pdf``. Databases
written before that are renamed once, file by file (``_neutralize_files``). ``saved_files`` keeps
the path of every report, extract, backup and export the program wrote, so erasing a person finds
their files and can list the copies it cannot reach.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import threading
import zipfile
import zlib
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from lopanalys.core import paths
from lopanalys.core.i18n import t
from lopanalys.core.models import Athlete, Session, Sex, StepRecord, TestKind, TimeSample
from lopanalys.core.sport import DEFAULT_SPORT, Sport, sport_of

log = logging.getLogger(__name__)

DB_FILENAME = "lopanalys.sqlite"
BACKUP_FORMAT = 1

MIGRATIONS: list[list[str]] = [
    # v0 -> v1: initial schema
    [
        """CREATE TABLE athletes (
            id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL DEFAULT '',
            birth_date TEXT,
            sex TEXT,
            height_cm REAL,
            weight_kg REAL,
            max_hr INTEGER,
            resting_hr INTEGER,
            notes TEXT NOT NULL DEFAULT '',
            created TEXT NOT NULL)""",
        """CREATE TABLE sessions (
            id INTEGER PRIMARY KEY,
            athlete_id INTEGER NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            started TEXT NOT NULL,
            ended TEXT,
            protocol_name TEXT NOT NULL DEFAULT '',
            protocol_json TEXT NOT NULL DEFAULT '{}',
            treadmill_mode TEXT NOT NULL DEFAULT 'manual',
            camera_source TEXT NOT NULL DEFAULT '',
            video_path TEXT,
            temperature_c REAL,
            notes TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}')""",
        "CREATE INDEX ix_sessions_athlete ON sessions(athlete_id, kind, started)",
        """CREATE TABLE step_records (
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            idx INTEGER NOT NULL,
            speed_kmh REAL NOT NULL,
            incline_pct REAL NOT NULL,
            duration_s REAL NOT NULL,
            hr_end INTEGER,
            hr_mean INTEGER,
            lactate_mmol REAL,
            rpe INTEGER,
            gait_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (session_id, idx))""",
        """CREATE TABLE time_samples (
            session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            n INTEGER NOT NULL,
            data BLOB NOT NULL)""",
        """CREATE TABLE lactate_entries (
            id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            stage_index INTEGER,
            mmol REAL NOT NULL,
            entered TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE gait_analyses (
            id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            stage_index INTEGER,
            view TEXT NOT NULL DEFAULT '',
            video_path TEXT,
            created TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}')""",
        """CREATE TABLE keyframes (
            id INTEGER PRIMARY KEY,
            gait_id INTEGER NOT NULL REFERENCES gait_analyses(id) ON DELETE CASCADE,
            label TEXT NOT NULL DEFAULT '',
            frame_index INTEGER,
            t_s REAL,
            path TEXT NOT NULL,
            findings_json TEXT NOT NULL DEFAULT '[]')""",
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)",
    ],
    # v1 -> v2: archived athletes (hidden from the list, data kept), change times, lookup indexes
    [
        "ALTER TABLE athletes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE athletes ADD COLUMN updated TEXT",
        "ALTER TABLE sessions ADD COLUMN updated TEXT",
        "CREATE INDEX ix_lactate_session ON lactate_entries(session_id)",
        "CREATE INDEX ix_gait_session ON gait_analyses(session_id)",
        "CREATE INDEX ix_keyframes_gait ON keyframes(gait_id)",
    ],
    # v2 -> v3: a test is saved while it runs; its status tells a finished test from one cut off
    [
        "ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'finished'",
        "CREATE INDEX ix_sessions_status ON sessions(status)",
    ],
    # v3 -> v4: the files the program wrote (reports and extracts per athlete; backups and exports of
    # the whole register), and the ids that were erased, without names
    [
        """CREATE TABLE saved_files (
            id INTEGER PRIMARY KEY,
            athlete_id INTEGER REFERENCES athletes(id) ON DELETE CASCADE,
            session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            created TEXT NOT NULL)""",
        "CREATE INDEX ix_saved_files_athlete ON saved_files(athlete_id)",
        """CREATE TABLE erased (
            kind TEXT NOT NULL,
            row_id INTEGER NOT NULL,
            created TEXT NOT NULL DEFAULT '',
            erased TEXT NOT NULL,
            PRIMARY KEY (kind, row_id, created))""",
    ],
    # v4 -> v5: the consent per athlete (date and the four boxes), as JSON
    [
        "ALTER TABLE athletes ADD COLUMN consent_json TEXT NOT NULL DEFAULT '{}'",
    ],
    # v5 -> v6: the athlete's e-mail address, optional
    [
        "ALTER TABLE athletes ADD COLUMN email TEXT NOT NULL DEFAULT ''",
    ],
    # v6 -> v7: where the max HR came from and when, and the VO2max from the athlete's watch
    [
        "ALTER TABLE athletes ADD COLUMN max_hr_source TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE athletes ADD COLUMN max_hr_date TEXT",
        "ALTER TABLE athletes ADD COLUMN watch_vo2max REAL",
        "ALTER TABLE athletes ADD COLUMN watch_vo2max_date TEXT",
    ],
    # v7 -> v8: sport. Additions only: every existing session becomes a running test, and the max HR
    # of each athlete becomes running's max HR. Nothing is renamed, moved or rewritten.
    [
        "ALTER TABLE sessions ADD COLUMN sport TEXT NOT NULL DEFAULT 'run'",
        "CREATE INDEX ix_sessions_athlete_sport ON sessions(athlete_id, sport, kind, started)",
        """CREATE TABLE athlete_sport (
            athlete_id INTEGER NOT NULL REFERENCES athletes(id) ON DELETE CASCADE,
            sport TEXT NOT NULL,
            max_hr INTEGER,
            updated TEXT NOT NULL,
            PRIMARY KEY (athlete_id, sport))""",
        """INSERT INTO athlete_sport (athlete_id, sport, max_hr, updated)
           SELECT id, 'run', max_hr, COALESCE(updated, created) FROM athletes WHERE max_hr IS NOT NULL""",
    ],
    # v8 -> v9: the training plan, one per athlete: what was stated (request), the plan as it was built,
    # the health declaration, and the log of what happened to it. Additions only.
    [
        """CREATE TABLE plans (
            id INTEGER PRIMARY KEY,
            athlete_id INTEGER NOT NULL UNIQUE REFERENCES athletes(id) ON DELETE CASCADE,
            session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
            engine_version TEXT NOT NULL DEFAULT '',
            request_json TEXT NOT NULL DEFAULT '{}',
            plan_json TEXT NOT NULL DEFAULT '{}',
            health_json TEXT NOT NULL DEFAULT '{}',
            created TEXT NOT NULL,
            updated TEXT NOT NULL)""",
        """CREATE TABLE plan_events (
            id INTEGER PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}')""",
        "CREATE INDEX ix_plan_events_plan ON plan_events(plan_id)",
    ],
    # v9 -> v10: the athlete's watch brand ("garmin", "huawei" ...), chosen in the result's box
    # "Fyll i klockan". Written only by set_watch_brand(), so the athlete form leaves it as it is.
    [
        "ALTER TABLE athletes ADD COLUMN watch_brand TEXT NOT NULL DEFAULT ''",
    ],
]
SAVED_FILE_KINDS = ("report", "extract", "backup", "export", "fit")
PENDING_ERASED = "erased-pending.json"     # ids erased before a backup was restored, see restore_backup()
SAFETY_COPY_DAYS = 30                      # .bak and before-restore copies are removed after this
TECHNIQUE_KINDS = ("bikefit", "ski")    # pose.technique.KINDS: analyses that are not running gait
VIDEO_RETENTION = ("after_analysis", "30d", "keep")   # the setting privacy.video_retention; "30d" is the default
VIDEO_DAYS = 30
RETENTION_MONTHS = 24                      # test data: a reminder this long after the person's latest test
SESSION_STATUSES = ("running", "finished", "stopped", "interrupted")
SCHEMA_VERSION = len(MIGRATIONS)


class StorageError(Exception):
    """A storage problem with a message the coach can read (Swedish via i18n)."""


# ---------------------------------------------------------------------------- conversions
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dt(v: datetime | None) -> str | None:
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.isoformat()


def _pdt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _pdate(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _jd(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=_json_default)


def _json_default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if hasattr(o, "value"):
        return o.value
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def _row_athlete(r: sqlite3.Row) -> Athlete:
    return Athlete(id=r["id"], first_name=r["first_name"], last_name=r["last_name"],
                   birth_date=_pdate(r["birth_date"]), sex=Sex(r["sex"]) if r["sex"] else None,
                   height_cm=r["height_cm"], weight_kg=r["weight_kg"], max_hr=r["max_hr"],
                   resting_hr=r["resting_hr"], notes=r["notes"], created=_pdt(r["created"]),
                   consent=json.loads(r["consent_json"] or "{}") if "consent_json" in r.keys() else {},
                   email=(r["email"] or "") if "email" in r.keys() else "",
                   watch_brand=(r["watch_brand"] or "") if "watch_brand" in r.keys() else "",
                   **({"max_hr_source": r["max_hr_source"] or "", "max_hr_date": _pdate(r["max_hr_date"]),
                       "watch_vo2max": r["watch_vo2max"], "watch_vo2max_date": _pdate(r["watch_vo2max_date"])}
                      if "watch_vo2max" in r.keys() else {}))


def _athlete_v7(a: Athlete) -> tuple[Any, ...]:
    """The v7 columns: max_hr_source, max_hr_date, watch_vo2max, watch_vo2max_date."""
    def iso(d: Any) -> str | None:
        return d.isoformat() if hasattr(d, "isoformat") else (str(d) if d else None)
    return ((a.max_hr_source or "") if a.max_hr else "", iso(a.max_hr_date) if a.max_hr else None,
            a.watch_vo2max, iso(a.watch_vo2max_date) if a.watch_vo2max else None)


def _row_step(r: sqlite3.Row) -> StepRecord:
    return StepRecord(index=r["idx"], speed_kmh=r["speed_kmh"], incline_pct=r["incline_pct"],
                      duration_s=r["duration_s"], hr_end=r["hr_end"], hr_mean=r["hr_mean"],
                      lactate_mmol=r["lactate_mmol"], rpe=r["rpe"], gait=json.loads(r["gait_json"]))


_STAMP = re.compile(r"(\d{8}-\d{6})")
_KEYFRAME_TAIL = re.compile(r"_(\d{2}_[a-z0-9_]+\.png)$", re.IGNORECASE)


def pack_samples(samples: Iterable[TimeSample]) -> tuple[int, bytes]:
    """Columnar JSON + zlib: about 2-4 bytes per sample and channel."""
    s = list(samples)
    cols = {"t": [x.t_s for x in s], "hr": [x.hr for x in s], "v": [x.speed_kmh for x in s],
            "inc": [x.incline_pct for x in s], "cad": [x.cadence_spm for x in s],
            "cs": [x.cadence_source for x in s]}
    return len(s), zlib.compress(json.dumps(cols, separators=(",", ":")).encode(), 6)


def unpack_samples(blob: bytes) -> list[TimeSample]:
    c = json.loads(zlib.decompress(blob))
    src = c.get("cs") or [None] * len(c["t"])     # sessions saved before 2026-11-14 have no source
    return [TimeSample(*row) for row in zip(c["t"], c["hr"], c["v"], c["inc"], c["cad"], src)]


def _carry_erased(db: Path) -> None:
    """Before a restore replaces ``db``: save its list of erased ids next to it, so that the people
    erased since the backup was made are erased again when the restored database opens."""
    try:
        con = sqlite3.connect(str(db))
        try:
            rows = [{"kind": r[0], "row_id": r[1], "created": r[2], "erased": r[3]}
                    for r in con.execute("SELECT kind,row_id,created,erased FROM erased")]
        finally:
            con.close()
    except sqlite3.Error:      # a database from before v4 has no such list
        return
    pending = db.with_name(PENDING_ERASED)
    try:
        if pending.is_file():
            rows += json.loads(pending.read_text(encoding="utf-8"))
        if rows:
            pending.write_text(json.dumps(rows), encoding="utf-8")
    except (OSError, ValueError):
        log.exception("saving the erased ids before the restore failed")


def _carry_saved_files(staged: Path, old: Path | None, backup: Path, created: str) -> None:
    """Before a restore replaces ``old`` with ``staged``: the restored database must still know the
    files the program wrote, or an erasure after the restore leaves them behind unlisted. The rows of
    ``saved_files`` in the replaced database are copied for the whole register and for every person who
    is the same in both (id and creation time), and the backup itself is registered (its own row is
    written after its snapshot, so it never holds it)."""
    def has_table(con: sqlite3.Connection, schema: str) -> bool:
        return bool(con.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name='saved_files'").fetchone())

    try:
        con = sqlite3.connect(str(staged))
    except sqlite3.Error:
        return
    try:
        if not has_table(con, "main"):      # a backup from before v4 has no such register
            return
        if old is not None:
            try:
                con.execute("ATTACH DATABASE ? AS old", (str(old),))
                if has_table(con, "old"):
                    con.execute(
                        "INSERT INTO main.saved_files (athlete_id,session_id,kind,path,created) "
                        "SELECT o.athlete_id, (SELECT s.id FROM main.sessions s JOIN old.sessions os ON os.id=s.id "
                        "AND os.started=s.started WHERE s.id=o.session_id), o.kind, o.path, o.created "
                        "FROM old.saved_files o WHERE (o.athlete_id IS NULL OR EXISTS (SELECT 1 FROM main.athletes a "
                        "JOIN old.athletes oa ON oa.id=a.id AND oa.created=a.created WHERE a.id=o.athlete_id)) "
                        "AND NOT EXISTS (SELECT 1 FROM main.saved_files m WHERE m.kind=o.kind AND m.path=o.path "
                        "AND m.athlete_id IS o.athlete_id) ORDER BY o.id")
                    con.commit()
            except sqlite3.Error:           # the replaced database is damaged: nothing to carry over
                con.rollback()
                log.warning("the register of saved files could not be carried over the restore")
        try:
            stored = backup.resolve().relative_to(staged.parent.resolve()).as_posix()
        except (ValueError, OSError):
            stored = str(backup.absolute())
        if not con.execute("SELECT 1 FROM main.saved_files WHERE kind='backup' AND path=? AND athlete_id IS NULL",
                           (stored,)).fetchone():
            con.execute("INSERT INTO main.saved_files (kind,path,created) VALUES ('backup',?,?)", (stored, created or _now()))
        con.commit()
    except sqlite3.Error:
        log.exception("registering the saved files in the restored database failed")
    finally:
        con.close()


# ---------------------------------------------------------------------------- database
class Database:
    """The app's SQLite database. ``Database()`` opens (and creates/migrates) ``core.paths.db_path()``."""

    def __init__(self, path: str | Path | None = None, *, keyframes_root: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else paths.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if keyframes_root is not None:
            self.keyframes_root = Path(keyframes_root)
        elif path is None:
            self.keyframes_root = paths.keyframes_dir()
        else:
            self.keyframes_root = self.path.parent / "keyframes"
        self.data_root = self.path.parent
        self._lock = threading.RLock()
        try:
            self._con = sqlite3.connect(str(self.path), timeout=15, check_same_thread=False, isolation_level=None)
        except sqlite3.Error as e:
            raise StorageError(t("storage.error.open", path=str(self.path), err=str(e))) from e
        self._con.row_factory = sqlite3.Row
        self._tx_depth = 0
        try:
            self._con.execute("PRAGMA foreign_keys = ON")
            self._con.execute("PRAGMA journal_mode = WAL")
            self._con.execute("PRAGMA synchronous = NORMAL")
            self._con.execute("PRAGMA secure_delete = ON")   # erased rows are overwritten, not just unlinked
            self._migrate()
            self._neutralize_files()
            self._apply_pending_erased()
        except sqlite3.DatabaseError as e:
            self._con.close()
            raise StorageError(t("storage.error.corrupt", path=str(self.path), err=str(e))) from e
        except Exception:
            self._con.close()
            raise

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        with self._lock:
            try:
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._con.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._con.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Group several calls atomically (nests: only the outermost commits)."""
        with self._lock:
            outer = self._tx_depth == 0
            if outer:
                self._con.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
            try:
                yield self._con
            except BaseException:
                self._tx_depth -= 1
                if outer:
                    self._con.execute("ROLLBACK")
                raise
            self._tx_depth -= 1
            if outer:
                self._con.execute("COMMIT")

    def _migrate(self) -> None:
        cur = self.schema_version
        if cur > SCHEMA_VERSION:
            raise StorageError(t("storage.error.newer_schema", found=cur, supported=SCHEMA_VERSION))
        if cur == SCHEMA_VERSION:
            return
        if cur > 0:
            bak = self.path.with_name(f"{self.path.name}.v{cur}.bak")
            dst = sqlite3.connect(str(bak))
            try:
                self._con.backup(dst)
            finally:
                dst.close()
            log.info("database v%s backed up to %s before migration", cur, bak.name)
        for v in range(cur, SCHEMA_VERSION):
            with self.transaction() as c:
                for stmt in MIGRATIONS[v]:
                    c.execute(stmt)
                c.execute(f"PRAGMA user_version = {v + 1}")
            log.info("database migrated to v%s", v + 1)

    def _q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, tuple(args)).fetchall()

    # ------------------------------------------------------------------ paths
    def _rel(self, p: str | Path | None) -> str | None:
        """How a path is stored: relative to the data folder when it lies inside it."""
        if not p:
            return None
        pp = Path(p)
        if not pp.is_absolute():
            return pp.as_posix()
        try:
            return pp.resolve().relative_to(self.data_root.resolve()).as_posix()
        except (ValueError, OSError):
            return str(pp)

    def _abs(self, stored: str | None) -> str | None:
        if not stored:
            return None
        p = Path(stored)
        return str(p if p.is_absolute() else self.data_root / p)

    def _owned(self, p: str | Path) -> bool:
        """True for a path inside the data folder (the only files the program renames by itself)."""
        try:
            return Path(p).resolve().is_relative_to(self.data_root.resolve())
        except OSError:
            return False

    def _result_out(self, result: dict[str, Any] | None) -> str:
        res = dict(result or {})
        if res.get("earlier_videos"):
            res["earlier_videos"] = [self._rel(p) for p in res["earlier_videos"] if p]
        if res.get("second_video"):      # the second camera's film of a test with two views
            res["second_video"] = self._rel(res["second_video"])
        return _jd(res)

    def _result_in(self, result_json: str | None) -> dict[str, Any]:
        res = json.loads(result_json or "{}")
        if res.get("earlier_videos"):
            res["earlier_videos"] = [self._abs(p) for p in res["earlier_videos"] if p]
        if res.get("second_video"):
            res["second_video"] = self._abs(res["second_video"])
        return res

    def _earlier_videos(self, result_json: str) -> list[Path]:
        """The films of a test besides ``video_path``: recordings from before a cut that was continued
        (``result["earlier_videos"]``) and the second camera's film (``result["second_video"]``)."""
        try:
            res = self._result_in(result_json)
            return [Path(p) for p in [*(res.get("earlier_videos") or []), res.get("second_video")] if p]
        except (ValueError, AttributeError):
            return []

    def _row_session(self, r: sqlite3.Row) -> Session:
        return Session(id=r["id"], athlete_id=r["athlete_id"], kind=TestKind(r["kind"]), started=_pdt(r["started"]),
                       ended=_pdt(r["ended"]), protocol_name=r["protocol_name"], protocol=json.loads(r["protocol_json"]),
                       treadmill_mode=r["treadmill_mode"], camera_source=r["camera_source"],
                       video_path=self._abs(r["video_path"]), temperature_c=r["temperature_c"], notes=r["notes"],
                       result=self._result_in(r["result_json"]),
                       status=r["status"] if "status" in r.keys() else "finished",
                       sport=sport_of(r["sport"]) if "sport" in r.keys() else DEFAULT_SPORT)

    # ------------------------------------------------------------------ neutral file names
    def _neutralize_files(self) -> None:
        """Once per database: films, key frames and reports named after a person get neutral names,
        and paths inside the data folder become relative.

        Safe to interrupt and to run again: each file is renamed and its row updated on its own, a
        file that is already renamed is only looked up, and nothing outside the data folder is touched.
        The setting ``privacy.neutral_names`` is written last."""
        if self.get_setting("privacy.neutral_names") == 1:
            return
        try:
            stems: dict[str, str] = {}
            for r in self._q("SELECT id, video_path, result_json FROM sessions ORDER BY id"):
                sid = int(r["id"])
                video = self._neutral_video(sid, r["video_path"], stems)
                res = json.loads(r["result_json"] or "{}")
                if res.get("earlier_videos"):
                    res["earlier_videos"] = [self._neutral_video(sid, p, stems) for p in res["earlier_videos"] if p]
                res_json = _jd(res)
                if video != r["video_path"] or res_json != r["result_json"]:
                    with self.transaction() as c:
                        c.execute("UPDATE sessions SET video_path=?, result_json=? WHERE id=?", (video, res_json, sid))
            for r in self._q("SELECT id, session_id, video_path, data_json FROM gait_analyses ORDER BY id"):
                sid, gid = int(r["session_id"]), int(r["id"])
                video = self._neutral_video(sid, r["video_path"], stems)
                data = json.loads(r["data_json"] or "{}")
                data.pop("keyframes", None)            # they are rows of their own, with relative paths
                if data.get("video_path"):
                    data["video_path"] = self._neutral_video(sid, data["video_path"], stems)
                data_json = _jd(data)
                if video != r["video_path"] or data_json != r["data_json"]:
                    with self.transaction() as c:
                        c.execute("UPDATE gait_analyses SET video_path=?, data_json=? WHERE id=?", (video, data_json, gid))
                for k in self._q("SELECT id, path FROM keyframes WHERE gait_id=? ORDER BY id", (gid,)):
                    new = self._neutral_keyframe(sid, gid, int(k["id"]), k["path"])
                    if new != k["path"]:
                        with self.transaction() as c:
                            c.execute("UPDATE keyframes SET path=? WHERE id=?", (new, k["id"]))
            self._neutral_reports()
            self._scrub_logs(stems)
            self.set_setting("privacy.neutral_names", 1)
        except Exception:   # the database must open anyway; the next start tries again
            log.exception("renaming files to neutral names failed")

    def _neutral_video(self, sid: int, stored: str | None, stems: dict[str, str]) -> str | None:
        """The stored (relative) path of a film under its neutral name; renames the file and its sidecar."""
        if not stored:
            return stored
        old = Path(self._abs(stored) or "")
        if not self._owned(old):
            return stored
        if old.name.startswith(f"session-{sid}-"):
            return self._rel(old)
        if old.stem in stems:                              # the same film, met again in another row
            return self._rel(old.with_name(stems[old.stem] + old.suffix))
        m = _STAMP.search(old.name)
        base = f"session-{sid}-{m.group(1) if m else 'film'}"
        new, n = old.with_name(base + old.suffix), 1
        while new.exists() and old.exists():               # never overwrite another film
            n += 1
            new = old.with_name(f"{base}-{n}{old.suffix}")
        for a, b in ((old, new), (old.with_name(old.name + ".json"), new.with_name(new.name + ".json"))):
            if a.is_file() and not b.exists():
                a.rename(b)
        side = new.with_name(new.name + ".json")
        if side.is_file():
            try:
                d = json.loads(side.read_text(encoding="utf-8"))
                if d.get("video") != new.name:
                    d["video"] = new.name
                    side.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            except (ValueError, OSError):
                pass
        stems[old.stem] = new.stem
        return self._rel(new)

    def _neutral_keyframe(self, sid: int, gid: int, kid: int, stored: str) -> str:
        old = self._abs_keyframe(stored)
        if not stored or not self._owned(old):
            return stored
        if old.name.startswith(f"session-{sid}-g{gid}") and old.parent.name == f"session-{sid}":
            return self._rel_keyframe(old)
        m = _KEYFRAME_TAIL.search(old.name)
        name = f"session-{sid}-g{gid}_{m.group(1)}" if m else f"session-{sid}-g{gid}-k{kid}{old.suffix or '.png'}"
        new = self.keyframes_root / f"session-{sid}" / name
        if old.is_file():
            new.parent.mkdir(parents=True, exist_ok=True)
            if new.exists():
                old.unlink()                               # a backup put the old name back beside the new one
            else:
                old.rename(new)
            try:
                if old.parent != new.parent and not any(old.parent.iterdir()):
                    old.parent.rmdir()
            except OSError:
                pass
        return self._rel_keyframe(new)

    def _neutral_reports(self) -> None:
        """PDFs in ``reports/`` that start with an athlete's name: renamed and registered to the athlete."""
        folder = self.data_root / "reports"
        if not folder.is_dir():
            return
        people = [(int(r["id"]), f"{r['first_name']} {r['last_name']}".strip().replace(" ", "_") + "_")
                  for r in self._q("SELECT id, first_name, last_name FROM athletes ORDER BY id")]
        for f in sorted(folder.glob("*.pdf")):
            owners = [aid for aid, prefix in people if len(prefix) > 1 and f.name.startswith(prefix)]
            if not owners:
                continue
            prefix = next(p for aid, p in people if aid == owners[0])
            new, n = f.with_name(f"rapport-{owners[0]}-{f.name[len(prefix):]}"), 1
            while new.exists():
                n += 1
                new = f.with_name(f"rapport-{owners[0]}-{n}-{f.name[len(prefix):]}")
            f.rename(new)
            for aid in owners:                              # two athletes with the same name: both own it
                self.add_saved_file("report", new, athlete_id=aid)

    def _scrub_logs(self, stems: dict[str, str]) -> None:
        """Old log lines name films by their old file names: write the new names there too."""
        if not stems:
            return
        for f in (self.data_root / "logs").glob("*.log*") if (self.data_root / "logs").is_dir() else ():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                out = text
                for old, new in stems.items():
                    out = out.replace(old, new)
                if out != text:
                    with open(f, "r+", encoding="utf-8") as fh:   # in place: the log handler has the file open
                        fh.seek(0)
                        fh.write(out)
                        fh.truncate()
            except OSError:
                log.warning("could not rewrite a log file")

    # ------------------------------------------------------------------ files the program wrote
    def add_saved_file(self, kind: str, path: str | Path, *, athlete_id: int | None = None,
                       session_id: int | None = None) -> int:
        """Remember a report, FIT file or extract (per athlete), or a backup or export of the whole register."""
        if kind not in SAVED_FILE_KINDS:
            raise ValueError(kind)
        stored = self._rel(Path(path).absolute())
        with self.transaction() as c:
            c.execute("DELETE FROM saved_files WHERE kind=? AND path=? AND athlete_id IS ?", (kind, stored, athlete_id))
            return int(c.execute("INSERT INTO saved_files (athlete_id,session_id,kind,path,created) VALUES (?,?,?,?,?)",
                                 (athlete_id, session_id, kind, stored, _now())).lastrowid)

    def saved_files(self, athlete_id: int | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        """[{"id", "athlete_id", "session_id", "kind", "path" (absolute), "created"}], oldest first.

        ``athlete_id`` None: the files of the whole register (backups, exports)."""
        sql, args = "SELECT * FROM saved_files WHERE athlete_id IS ?", [athlete_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return [dict(r, path=self._abs(r["path"])) for r in self._q(sql + " ORDER BY id", args)]

    def forget_saved_file(self, file_id: int) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM saved_files WHERE id=?", (file_id,))

    # ------------------------------------------------------------------ athletes
    def add_athlete(self, a: Athlete) -> int:
        """Insert; sets and returns ``a.id``."""
        if not (a.first_name or "").strip():
            raise StorageError(t("storage.error.name"))
        a.created = a.created or datetime.now(timezone.utc)
        with self.transaction() as c:
            cur = c.execute(
                "INSERT INTO athletes (first_name,last_name,birth_date,sex,height_cm,weight_kg,max_hr,resting_hr,"
                "notes,created,updated,consent_json,email,max_hr_source,max_hr_date,watch_vo2max,watch_vo2max_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (a.first_name.strip(), (a.last_name or "").strip(), a.birth_date.isoformat() if a.birth_date else None,
                 Sex(a.sex).value if a.sex else None, a.height_cm, a.weight_kg, a.max_hr, a.resting_hr, a.notes or "",
                 _dt(a.created), _now(), _jd(a.consent or {}), (a.email or "").strip(), *_athlete_v7(a)))
            a.id = int(cur.lastrowid)
            self._put_sport_max_hr(c, a)
        return a.id

    def update_athlete(self, a: Athlete) -> None:
        if a.id is None:
            raise StorageError(t("storage.error.no_id"))
        if not (a.first_name or "").strip():
            raise StorageError(t("storage.error.name"))
        with self.transaction() as c:
            n = c.execute(
                "UPDATE athletes SET first_name=?,last_name=?,birth_date=?,sex=?,height_cm=?,weight_kg=?,max_hr=?,"
                "resting_hr=?,notes=?,updated=?,consent_json=?,email=?,max_hr_source=?,max_hr_date=?,watch_vo2max=?,"
                "watch_vo2max_date=? WHERE id=?",
                (a.first_name.strip(), (a.last_name or "").strip(), a.birth_date.isoformat() if a.birth_date else None,
                 Sex(a.sex).value if a.sex else None, a.height_cm, a.weight_kg, a.max_hr, a.resting_hr, a.notes or "",
                 _now(), _jd(a.consent or {}), (a.email or "").strip(), *_athlete_v7(a), a.id)).rowcount
            if n:
                self._put_sport_max_hr(c, a)
        if n == 0:
            raise StorageError(t("storage.error.not_found"))

    @staticmethod
    def _put_sport_max_hr(c: sqlite3.Connection, a: Athlete) -> None:
        """Make ``athlete_sport`` say what the athlete says: ``max_hr`` for running and
        ``sport_max_hr`` for the other sports. Rows that did not change keep their time."""
        want = {sport_of(k).value: int(v) for k, v in (a.sport_max_hr or {}).items() if v}
        want.pop(DEFAULT_SPORT.value, None)
        if a.max_hr:
            want[DEFAULT_SPORT.value] = int(a.max_hr)
        have = {r["sport"]: r["max_hr"] for r in
                c.execute("SELECT sport, max_hr FROM athlete_sport WHERE athlete_id=?", (a.id,))}
        for sport in have.keys() - want.keys():
            c.execute("DELETE FROM athlete_sport WHERE athlete_id=? AND sport=?", (a.id, sport))
        for sport, v in want.items():
            if have.get(sport) != v:
                c.execute("INSERT OR REPLACE INTO athlete_sport (athlete_id,sport,max_hr,updated) VALUES (?,?,?,?)",
                          (a.id, sport, v, _now()))

    def _with_sports(self, athletes: list[Athlete]) -> list[Athlete]:
        """Fill in ``sport_max_hr`` (the max HR of the sports other than running)."""
        by_id = {a.id: a for a in athletes}
        for r in self._q("SELECT athlete_id, sport, max_hr FROM athlete_sport WHERE sport<>? AND max_hr IS NOT NULL",
                         (DEFAULT_SPORT.value,)):
            if r["athlete_id"] in by_id:
                by_id[r["athlete_id"]].sport_max_hr[r["sport"]] = r["max_hr"]
        return athletes

    def sport_max_hr(self, athlete_id: int, sport: Sport | str | None = None) -> int | None:
        """The athlete's max HR in one sport (None: not known in that sport)."""
        rows = self._q("SELECT max_hr FROM athlete_sport WHERE athlete_id=? AND sport=?",
                       (athlete_id, sport_of(sport).value))
        return rows[0][0] if rows else None

    def set_sport_max_hr(self, athlete_id: int, sport: Sport | str | None, max_hr: int | None) -> None:
        """Set (or with None clear) the athlete's max HR in one sport."""
        a = self.get_athlete(athlete_id)
        if a is None:
            raise StorageError(t("storage.error.not_found"))
        a.set_max_hr(sport, max_hr)
        self.update_athlete(a)

    def set_watch_brand(self, athlete_id: int, brand: str) -> None:
        """Remember which watch the athlete has (zone_systems.WATCH_BRANDS key; "" forgets it)."""
        with self.transaction() as c:
            c.execute("UPDATE athletes SET watch_brand=?, updated=? WHERE id=?", (brand or "", _now(), athlete_id))

    def save_athlete(self, a: Athlete) -> Athlete:
        """Insert or update (by ``a.id``); returns the athlete with its id set."""
        if a.id is None:
            self.add_athlete(a)
        else:
            self.update_athlete(a)
        return a

    def get_athlete(self, athlete_id: int) -> Athlete | None:
        rows = self._q("SELECT * FROM athletes WHERE id=?", (athlete_id,))
        return self._with_sports([_row_athlete(rows[0])])[0] if rows else None

    def list_athletes(self, search: str = "", include_archived: bool = False) -> list[Athlete]:
        """Athletes sorted by name; `search` matches first or last name (case-insensitive)."""
        sql = "SELECT * FROM athletes WHERE 1=1"
        args: list[Any] = []
        if not include_archived:
            sql += " AND archived=0"
        if search.strip():
            sql += " AND (first_name || ' ' || last_name) LIKE ? COLLATE NOCASE"
            args.append(f"%{search.strip()}%")
        sql += " ORDER BY first_name COLLATE NOCASE, last_name COLLATE NOCASE"
        return self._with_sports([_row_athlete(r) for r in self._q(sql, args)])

    def set_archived(self, athlete_id: int, archived: bool = True) -> None:
        with self.transaction() as c:
            c.execute("UPDATE athletes SET archived=?, updated=? WHERE id=?", (int(archived), _now(), athlete_id))

    def is_archived(self, athlete_id: int) -> bool:
        rows = self._q("SELECT archived FROM athletes WHERE id=?", (athlete_id,))
        return bool(rows and rows[0][0])

    def delete_athlete(self, athlete_id: int, delete_files: bool = True) -> dict[str, int]:
        """Erase an athlete; see erase_athlete(). Returns counts: {"sessions": n, "files": n}.

        ``delete_files`` False removes only the rows (the files stay; used by tests and tools)."""
        if delete_files:
            res = self.erase_athlete(athlete_id)
            return {"sessions": res["sessions"], "files": res["files"]}
        with self.transaction() as c:
            n_sessions = c.execute("SELECT COUNT(*) FROM sessions WHERE athlete_id=?", (athlete_id,)).fetchone()[0]
            if c.execute("DELETE FROM athletes WHERE id=?", (athlete_id,)).rowcount == 0:
                raise StorageError(t("storage.error.not_found"))
        self._scrub()
        return {"sessions": int(n_sessions), "files": 0}

    def erase_athlete(self, athlete_id: int, keep: Iterable[str | Path] = ()) -> dict[str, Any]:
        """Erase a person completely (the right to erasure).

        Removed: the athlete and every test (rows are overwritten: ``secure_delete`` and ``VACUUM``);
        inside the data folder the films with their time files, and each test's key-frame folder;
        and the reports and extracts whose path was saved, wherever they lie, except those in ``keep``
        (the report the coach just saved to hand over).

        The ids are remembered without names (table ``erased``), so a backup restored later does not
        bring the person back. Returns ``{"sessions": n, "files": n, "left": [...]}`` where ``left``
        lists what the program cannot reach: ``{"kind", "path", "until"}`` with kind "report" or
        "extract" (not found where it was saved, or kept on purpose), "film" (outside the data folder),
        "backup" and "export" (copies of the whole register made while the person was in it) and
        "safety" (the program's own copies, removed by themselves on the date ``until``).
        """
        rows = self._q("SELECT created FROM athletes WHERE id=?", (athlete_id,))
        if not rows:
            raise StorageError(t("storage.error.not_found"))
        created = rows[0][0] or ""
        keep_set = {Path(p).absolute() for p in keep}
        sessions = self._q("SELECT id, started FROM sessions WHERE athlete_id=?", (athlete_id,))
        files = self._athlete_files(athlete_id)
        files += [f.with_name(f.name + ".json") for f in files if f.suffix.lower() in (".mp4", ".mov", ".m4v")]
        folders = [self.keyframes_root / f"session-{r['id']}" for r in sessions]
        saved = self.saved_files(athlete_id)
        left: list[dict[str, Any]] = []
        with self.transaction() as c:
            now = _now()
            c.execute("INSERT OR REPLACE INTO erased (kind,row_id,created,erased) VALUES ('athlete',?,?,?)",
                      (athlete_id, created, now))
            for r in sessions:
                c.execute("INSERT OR REPLACE INTO erased (kind,row_id,created,erased) VALUES ('session',?,?,?)",
                          (r["id"], r["started"] or "", now))
            c.execute("DELETE FROM athletes WHERE id=?", (athlete_id,))
        removed = 0
        for f in dict.fromkeys(files):
            if self._remove_owned(f):
                removed += 1
            elif f.suffix.lower() != ".json" and f.is_file() and not self._owned(f):
                left.append({"kind": "film", "path": str(f), "until": None})
        for d in folders:
            removed += self._remove_owned_tree(d)
        for sf in saved:
            p = Path(sf["path"])
            if p.absolute() in keep_set or not self._remove_saved(p):
                left.append({"kind": sf["kind"], "path": str(p), "until": None})
            else:
                removed += 1
        self._scrub()
        for sf in self.saved_files(None):
            if sf["created"][:19] >= created[:19] and Path(sf["path"]).is_file():   # made while the person was in it
                left.append({"kind": sf["kind"], "path": sf["path"], "until": None})
        for p, until in self.safety_copies():
            left.append({"kind": "safety", "path": str(p), "until": until})
        log.info("athlete %s erased: %d tests, %d files, %d copies out of reach", athlete_id, len(sessions), removed,
                 len(left))
        return {"sessions": len(sessions), "files": removed, "left": left}

    def _scrub(self) -> None:
        """After an erasure: nothing of the rows stays in the WAL or in free pages of the file."""
        with self._lock:
            if self._tx_depth:
                return
            try:
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._con.execute("VACUUM")
                self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                log.exception("scrubbing the database after an erasure failed")

    def _remove_owned_tree(self, folder: Path) -> int:
        """Remove a key-frame folder inside the data folder with everything in it. Returns the files removed."""
        if not folder.is_dir() or not self._owned(folder) or folder.resolve() == self.keyframes_root.resolve():
            return 0
        n = sum(1 for f in folder.rglob("*") if f.is_file())
        shutil.rmtree(folder, ignore_errors=True)
        return n

    @staticmethod
    def _remove_saved(p: Path) -> bool:
        """A report or extract the program wrote itself: removed where it was saved (also outside the data folder)."""
        try:
            if p.is_file() and p.suffix.lower() in (".pdf", ".zip", ".csv", ".fit"):
                p.unlink()
                return True
        except OSError:
            log.warning("could not delete a saved %s file", p.suffix)
        return False

    def safety_copies(self) -> list[tuple[Path, str]]:
        """The program's own full copies in the data folder (before an upgrade, before a restore), each
        with the date it is removed: ``[(path, "YYYY-MM-DD")]``."""
        out = []
        for f in sorted(self.data_root.glob(f"{self.path.name}.v*.bak")) + \
                sorted(self.data_root.glob(f"{self.path.stem}.before-restore-*.sqlite")):
            try:
                until = datetime.fromtimestamp(f.stat().st_mtime) + timedelta(days=SAFETY_COPY_DAYS)
            except OSError:
                continue
            out.append((f, until.strftime("%Y-%m-%d")))
        return out

    def apply_erased(self) -> int:
        """Erase again what the list of erased ids names (after a backup from before the erasure was
        restored). A row counts as the erased one when both its id and its creation time match, so a
        new person who got a reused id is never touched. Returns the number of athletes and tests erased."""
        n = 0
        for r in self._q("SELECT row_id, created FROM erased WHERE kind='athlete'"):
            if self._q("SELECT 1 FROM athletes WHERE id=? AND created=?", (r["row_id"], r["created"])):
                self.erase_athlete(int(r["row_id"]))
                n += 1
        for r in self._q("SELECT row_id, created FROM erased WHERE kind='session'"):
            if self._q("SELECT 1 FROM sessions WHERE id=? AND started=?", (r["row_id"], r["created"])):
                self.delete_session(int(r["row_id"]))
                n += 1
        if n:
            log.warning("%d erased athletes or tests came back with a restored backup and were erased again", n)
        return n

    def _apply_pending_erased(self) -> None:
        """restore_backup() left the ids erased in the replaced database: merge and apply them."""
        pending = self.data_root / PENDING_ERASED
        if not pending.is_file():
            return
        try:
            rows = json.loads(pending.read_text(encoding="utf-8"))
            with self.transaction() as c:
                for r in rows:
                    c.execute("INSERT OR IGNORE INTO erased (kind,row_id,created,erased) VALUES (?,?,?,?)",
                              (r["kind"], int(r["row_id"]), r.get("created") or "", r.get("erased") or _now()))
            self.apply_erased()
            pending.unlink()
        except Exception:   # kept for the next start
            log.exception("applying the erased ids after a restore failed")

    def _athlete_files(self, athlete_id: int) -> list[Path]:
        out: list[Path] = []
        for r in self._q("SELECT video_path FROM sessions WHERE athlete_id=? AND video_path IS NOT NULL", (athlete_id,)):
            out.append(Path(self._abs(r[0]) or ""))
        for r in self._q("SELECT result_json FROM sessions WHERE athlete_id=?", (athlete_id,)):
            out += self._earlier_videos(r[0])
        for r in self._q("SELECT g.video_path FROM gait_analyses g JOIN sessions s ON s.id=g.session_id "
                         "WHERE s.athlete_id=? AND g.video_path IS NOT NULL", (athlete_id,)):
            out.append(Path(self._abs(r[0]) or ""))
        for r in self._q("SELECT k.path FROM keyframes k JOIN gait_analyses g ON g.id=k.gait_id "
                         "JOIN sessions s ON s.id=g.session_id WHERE s.athlete_id=?", (athlete_id,)):
            out.append(self._abs_keyframe(r[0]))
        return out

    def _remove_owned(self, p: Path) -> bool:
        try:
            rp = p.resolve()
            if not rp.is_relative_to(self.data_root.resolve()) or not rp.is_file():
                return False
            rp.unlink()
            return True
        except OSError:
            log.warning("could not delete %s", p.name)
            return False

    # ------------------------------------------------------------------ sessions
    def add_session(self, s: Session) -> int:
        with self.transaction() as c:
            cur = c.execute(
                "INSERT INTO sessions (athlete_id,kind,started,ended,protocol_name,protocol_json,treadmill_mode,"
                "camera_source,video_path,temperature_c,notes,result_json,status,sport,updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._session_args(s) + (_now(),))
            s.id = int(cur.lastrowid)
        return s.id

    def update_session(self, s: Session) -> None:
        if s.id is None:
            raise StorageError(t("storage.error.no_id"))
        with self.transaction() as c:
            n = c.execute(
                "UPDATE sessions SET athlete_id=?,kind=?,started=?,ended=?,protocol_name=?,protocol_json=?,"
                "treadmill_mode=?,camera_source=?,video_path=?,temperature_c=?,notes=?,result_json=?,status=?,sport=?,"
                "updated=? WHERE id=?", self._session_args(s) + (_now(), s.id)).rowcount
        if n == 0:
            raise StorageError(t("storage.error.not_found"))

    def save_session(self, s: Session) -> Session:
        """Insert or update (by ``s.id``); returns the session with its id set."""
        if s.id is None:
            self.add_session(s)
        else:
            self.update_session(s)
        return s

    def set_session_result(self, session_id: int, result: dict[str, Any]) -> None:
        """Replace only the computed result (e.g. after the coach picks another threshold method)."""
        with self.transaction() as c:
            c.execute("UPDATE sessions SET result_json=?, updated=? WHERE id=?",
                      (self._result_out(result), _now(), session_id))

    def _session_args(self, s: Session) -> tuple[Any, ...]:
        kind = s.kind.value if isinstance(s.kind, TestKind) else TestKind(s.kind).value
        return (s.athlete_id, kind, _dt(s.started), _dt(s.ended), s.protocol_name or "", _jd(s.protocol or {}),
                s.treadmill_mode or "manual", s.camera_source or "", self._rel(s.video_path), s.temperature_c,
                s.notes or "", self._result_out(s.result), s.status if s.status in SESSION_STATUSES else "finished",
                sport_of(s.sport).value)

    def running_sessions(self) -> list[Session]:
        """Sessions still marked "running", oldest first. At program start these were cut off."""
        return [self._row_session(r) for r in self._q("SELECT * FROM sessions WHERE status='running' ORDER BY started, id")]

    def mark_interrupted(self, session_id: int, ended: datetime | None = None) -> None:
        """A "running" session that no program is running any more: keep everything, mark it interrupted."""
        with self.transaction() as c:
            c.execute("UPDATE sessions SET status='interrupted', ended=COALESCE(ended, ?), updated=? "
                      "WHERE id=? AND status='running'", (_dt(ended), _now(), session_id))

    def get_session(self, session_id: int) -> Session | None:
        rows = self._q("SELECT * FROM sessions WHERE id=?", (session_id,))
        return self._row_session(rows[0]) if rows else None

    def delete_session(self, session_id: int, delete_files: bool = True) -> None:
        """Remove one test. With ``delete_files`` it is an erasure: the film with its time file and the
        key-frame folder go too, the id is remembered as erased, and the rows are overwritten."""
        files: list[Path] = []
        started = self._q("SELECT started FROM sessions WHERE id=?", (session_id,))
        if delete_files:
            files += [Path(self._abs(r[0]) or "") for r in self._q(
                "SELECT video_path FROM sessions WHERE id=? AND video_path IS NOT NULL", (session_id,))]
            for r in self._q("SELECT result_json FROM sessions WHERE id=?", (session_id,)):
                files += self._earlier_videos(r[0])
            files += [Path(self._abs(r[0]) or "") for r in self._q(      # as erase_athlete: the analyses' films too
                "SELECT video_path FROM gait_analyses WHERE session_id=? AND video_path IS NOT NULL", (session_id,))]
            files += [self._abs_keyframe(r[0]) for r in self._q(
                "SELECT k.path FROM keyframes k JOIN gait_analyses g ON g.id=k.gait_id WHERE g.session_id=?", (session_id,))]
        with self.transaction() as c:
            if delete_files and started:
                c.execute("INSERT OR REPLACE INTO erased (kind,row_id,created,erased) VALUES ('session',?,?,?)",
                          (session_id, started[0][0] or "", _now()))
            c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        if not delete_files:
            return
        for f in files + [f.with_name(f.name + ".json") for f in files if f.suffix.lower() in (".mp4", ".mov", ".m4v")]:
            self._remove_owned(f)
        self._remove_owned_tree(self.keyframes_root / f"session-{session_id}")
        self._scrub()

    # ------------------------------------------------------------------ retention of test data
    def retention_due(self, months: int = RETENTION_MONTHS, now: datetime | None = None) -> list[dict[str, Any]]:
        """The athletes the coach should review, never erased by the program itself:
        ``[{"athlete": Athlete, "reason": "old" | "no_store", "last": "YYYY-MM-DD"}]``.

        "old": the latest test (or, without tests, the registration) is more than ``months`` months
        back. "no_store": a consent is registered without "keep for follow-up", and there are tests
        from before today (the report should have been handed over and the person erased)."""
        now = now or datetime.now(timezone.utc)
        limit = now - timedelta(days=round(months * 365.25 / 12))
        last = {r["athlete_id"]: r["last"] for r in self._q(
            "SELECT athlete_id, MAX(started) AS last FROM sessions WHERE status != 'running' GROUP BY athlete_id")}
        out = []
        for a in self.list_athletes():
            when = _pdt(last.get(a.id)) or a.created
            if when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            day = when.astimezone().date()
            if when < limit:
                out.append({"athlete": a, "reason": "old", "last": day.isoformat()})
            elif a.id in last and a.consent_registered and not a.consents("store") and day < now.astimezone().date():
                out.append({"athlete": a, "reason": "no_store", "last": day.isoformat()})
        return out

    # ------------------------------------------------------------------ retention of films
    def remove_session_video(self, session_id: int) -> int:
        """Remove a test's films (also those from before a cut) with their time files, and forget the
        paths. Key frames, measurements and the analysis stay. Films outside the data folder are the
        coach's own files: they stay, and so does their path. Returns the number of files removed."""
        rows = self._q("SELECT video_path, result_json FROM sessions WHERE id=?", (session_id,))
        if not rows:
            return 0
        res = self._result_in(rows[0]["result_json"])
        main = self._abs(rows[0]["video_path"])
        films = [Path(p) for p in [main, *(res.get("earlier_videos") or []), res.get("second_video")] if p]
        if not films or any(f.is_file() and not self._owned(f) for f in films):
            return 0
        n = 0
        for f in films:
            n += sum(1 for x in (f, f.with_name(f.name + ".json")) if self._remove_owned(x))
        res.pop("earlier_videos", None)
        res.pop("second_video", None)
        res.pop("video_unreadable", None)
        res["video_removed"] = date.today().isoformat()
        with self.transaction() as c:
            c.execute("UPDATE sessions SET video_path=NULL, result_json=?, updated=? WHERE id=?",
                      (self._result_out(res), _now(), session_id))
            for g in c.execute("SELECT id, data_json FROM gait_analyses WHERE session_id=?", (session_id,)).fetchall():
                data = json.loads(g["data_json"] or "{}")
                data.pop("video_path", None)
                c.execute("UPDATE gait_analyses SET video_path=NULL, data_json=? WHERE id=?", (_jd(data), g["id"]))
        return n

    def purge_expired_videos(self, days: int = VIDEO_DAYS, now: datetime | None = None) -> int:
        """Remove the films of tests that ended more than ``days`` days ago (never a running test).
        Returns the number of tests whose film was removed. Called at program start."""
        limit = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        n = 0
        for r in self._q("SELECT id, started, ended FROM sessions WHERE status != 'running' AND "
                         "(video_path IS NOT NULL OR result_json LIKE '%earlier_videos%' OR result_json LIKE '%second_video%')"):
            when = _pdt(r["ended"]) or _pdt(r["started"])
            if when is None:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < limit and (self.remove_session_video(int(r["id"])) or
                                 self._q("SELECT 1 FROM sessions WHERE id=? AND video_path IS NULL", (r["id"],))):
                n += 1
        if n:
            log.info("films of %d tests older than %d days removed", n, days)
        return n

    def sessions(self, athlete_id: int, kind: TestKind | str | None = None,
                 sport: Sport | str | None = None) -> list[Session]:
        """An athlete's sessions, newest first, optionally of one kind and of one sport."""
        sql, args = "SELECT * FROM sessions WHERE athlete_id=?", [athlete_id]
        if kind is not None:
            sql += " AND kind=?"
            args.append(TestKind(kind).value)
        if sport is not None:
            sql += " AND sport=?"
            args.append(sport_of(sport).value)
        return [self._row_session(r) for r in self._q(sql + " ORDER BY started DESC, id DESC", args)]

    def history(self, athlete_id: int, kind: TestKind | str | None = None,
                sport: Sport | str | None = None) -> list[Session]:
        """Same as sessions(): newest first. Used for trend charts and the session list."""
        return self.sessions(athlete_id, kind, sport)

    def previous_session(self, session: Session) -> Session | None:
        """The same athlete's latest earlier session of the same kind in the same sport (for "compared
        with last time": a cycling test is never compared with a running test)."""
        rows = self._q("SELECT * FROM sessions WHERE athlete_id=? AND kind=? AND sport=? "
                       "AND (started < ? OR (started = ? AND id < ?)) ORDER BY started DESC, id DESC LIMIT 1",
                       (session.athlete_id, TestKind(session.kind).value, sport_of(session.sport).value,
                        _dt(session.started), _dt(session.started), session.id or 1 << 62))
        return self._row_session(rows[0]) if rows else None

    def recent_sessions(self, limit: int = 20) -> list[tuple[Session, Athlete]]:
        """Latest sessions across all athletes (start page)."""
        rows = self._q("SELECT s.*, a.id AS a_id FROM sessions s JOIN athletes a ON a.id=s.athlete_id "
                       "ORDER BY s.started DESC, s.id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            a = self.get_athlete(r["a_id"])
            if a:
                out.append((self._row_session(r), a))
        return out

    # ------------------------------------------------------------------ step records and samples
    def save_step_records(self, session_id: int, records: Iterable[StepRecord]) -> None:
        """Replace all step records of a session."""
        with self.transaction() as c:
            c.execute("DELETE FROM step_records WHERE session_id=?", (session_id,))
            for r in records:
                self._put_step(c, session_id, r)

    def upsert_step_record(self, session_id: int, r: StepRecord) -> None:
        with self.transaction() as c:
            self._put_step(c, session_id, r)

    @staticmethod
    def _put_step(c: sqlite3.Connection, session_id: int, r: StepRecord) -> None:
        c.execute("INSERT OR REPLACE INTO step_records (session_id,idx,speed_kmh,incline_pct,duration_s,hr_end,hr_mean,"
                  "lactate_mmol,rpe,gait_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (session_id, r.index, r.speed_kmh, r.incline_pct, r.duration_s, r.hr_end, r.hr_mean, r.lactate_mmol,
                   r.rpe, _jd(r.gait or {})))

    def step_records(self, session_id: int) -> list[StepRecord]:
        return [_row_step(r) for r in self._q("SELECT * FROM step_records WHERE session_id=? ORDER BY idx", (session_id,))]

    def save_time_samples(self, session_id: int, samples: Iterable[TimeSample]) -> None:
        n, blob = pack_samples(samples)
        with self.transaction() as c:
            c.execute("INSERT OR REPLACE INTO time_samples (session_id,n,data) VALUES (?,?,?)", (session_id, n, blob))

    def time_samples(self, session_id: int) -> list[TimeSample]:
        rows = self._q("SELECT data FROM time_samples WHERE session_id=?", (session_id,))
        return unpack_samples(rows[0][0]) if rows else []

    def save_test(self, session: Session, records: Iterable[StepRecord] = (), samples: Iterable[TimeSample] | None = None,
                  warmup_records: Iterable[StepRecord] = ()) -> int:
        """Store a finished test in one transaction (new or existing session). Returns the session id.

        Step records are replaced; time samples are replaced only when `samples` is given."""
        with self.transaction():
            if session.id is None:
                self.add_session(session)
            else:
                self.update_session(session)
            assert session.id is not None
            self.save_step_records(session.id, list(warmup_records) + list(records))
            if samples is not None:
                self.save_time_samples(session.id, samples)
        return session.id

    def log_mailed(self, session_ids: Iterable[int], what: Iterable[str]) -> dict[str, Any]:
        """Note on each test that it was mailed to the athlete: the time and what was attached
        ("report", "fit"), never the address. Returns the entry added to ``result["mailed"]``."""
        entry = {"at": _now(), "what": list(what)}
        with self.transaction() as c:
            for sid in session_ids:
                row = c.execute("SELECT result_json FROM sessions WHERE id=?", (sid,)).fetchone()
                if row is None:
                    continue
                res = json.loads(row[0] or "{}")
                res["mailed"] = list(res.get("mailed") or []) + [entry]
                c.execute("UPDATE sessions SET result_json=?, updated=? WHERE id=?", (_jd(res), _now(), sid))
        return entry

    # ------------------------------------------------------------------ lactate entries
    def add_lactate_entry(self, session_id: int, stage_index: int | None, mmol: float, note: str = "") -> int:
        """Log a lactate value as typed (keeps corrections and repeat samples); also sets the stage's value."""
        if not (0.0 <= float(mmol) <= 30.0):
            raise StorageError(t("storage.error.lactate"))
        with self.transaction() as c:
            cur = c.execute("INSERT INTO lactate_entries (session_id,stage_index,mmol,entered,note) VALUES (?,?,?,?,?)",
                            (session_id, stage_index, float(mmol), _now(), note))
            if stage_index is not None:
                c.execute("UPDATE step_records SET lactate_mmol=? WHERE session_id=? AND idx=?",
                          (float(mmol), session_id, stage_index))
            return int(cur.lastrowid)

    def lactate_entries(self, session_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self._q("SELECT * FROM lactate_entries WHERE session_id=? ORDER BY id", (session_id,))]

    # ------------------------------------------------------------------ gait analyses
    def _rel_keyframe(self, p: str | Path) -> str:
        pp = Path(p)
        try:
            return pp.resolve().relative_to(self.keyframes_root.resolve()).as_posix()
        except (ValueError, OSError):
            return str(pp)

    def _abs_keyframe(self, stored: str) -> Path:
        p = Path(stored)
        return p if p.is_absolute() else self.keyframes_root / p

    def add_gait_analysis(self, session_id: int, analysis: Any, *, keyframes: Iterable[Any] | None = None,
                          stage_index: int | None = None, view: str = "", video_path: str | None = None) -> int:
        """Store a pose.offline GaitAnalysis (or its dict). Key frames default to ``analysis.keyframes``."""
        data = analysis.to_dict() if hasattr(analysis, "to_dict") else dict(analysis)
        kfs = list(keyframes) if keyframes is not None else list(getattr(analysis, "keyframes", None) or data.get("keyframes", []))
        data.pop("keyframes", None)       # stored as rows, with paths relative to the key-frame folder
        if data.get("video_path"):
            data["video_path"] = self._rel(data["video_path"])
        with self.transaction() as c:
            gid = int(c.execute("INSERT INTO gait_analyses (session_id,stage_index,view,video_path,created,data_json) "
                                "VALUES (?,?,?,?,?,?)",
                                (session_id, stage_index, getattr(view, "value", view) or "", self._rel(video_path), _now(),
                                 _jd(data))).lastrowid)
            for k in kfs:
                g = (lambda key, k=k: k.get(key) if isinstance(k, dict) else getattr(k, key, None))
                c.execute("INSERT INTO keyframes (gait_id,label,frame_index,t_s,path,findings_json) VALUES (?,?,?,?,?,?)",
                          (gid, g("label") or "", g("frame_index"), g("t"), self._rel_keyframe(g("image_path") or ""),
                           _jd(g("findings") or [])))
        return gid

    def gait_analyses(self, session_id: int) -> list[dict[str, Any]]:
        """[{"id", "stage_index", "view", "video_path", "created", "data", "keyframes": [{..., "image_path"}]}]"""
        out = []
        for r in self._q("SELECT * FROM gait_analyses WHERE session_id=? ORDER BY id", (session_id,)):
            kf = [{"label": k["label"], "frame_index": k["frame_index"], "t": k["t_s"],
                   "image_path": str(self._abs_keyframe(k["path"])), "findings": json.loads(k["findings_json"])}
                  for k in self._q("SELECT * FROM keyframes WHERE gait_id=? ORDER BY id", (r["id"],))]
            data = json.loads(r["data_json"])
            if data.get("video_path"):
                data["video_path"] = self._abs(data["video_path"])
            out.append({"id": r["id"], "stage_index": r["stage_index"], "view": r["view"],
                        "video_path": self._abs(r["video_path"]), "created": r["created"], "data": data, "keyframes": kf})
        return out

    def save_gait_analysis(self, session_id: int, analysis: Any, **kw: Any) -> int:
        """Alias of add_gait_analysis() (name used by the UI)."""
        return self.add_gait_analysis(session_id, analysis, **kw)

    def gait_analysis(self, session_id: int) -> dict[str, Any] | None:
        """The latest gait analysis of a session as its data dict, with key-frame paths resolved.

        A test with two camera views has a row per view: the second view's row carries
        ``"angle": "second"`` in its data and comes back under the key ``"second"`` of the main one
        (or alone, as ``{"second": ...}``, when only the second film could be analysed)."""
        def out(g: dict[str, Any]) -> dict[str, Any]:
            data = dict(g["data"])
            data["keyframes"] = g["keyframes"]
            return data

        # bike fit and SkiErg analyses share the table but are another kind of result (technique_analysis)
        rows = [g for g in self.gait_analyses(session_id) if g["data"].get("kind") not in TECHNIQUE_KINDS]
        if not rows:
            return None
        main = [g for g in rows if g["data"].get("angle") != "second"]
        second = [g for g in rows if g["data"].get("angle") == "second"]
        data = out(main[-1]) if main else {}
        if second:
            data["second"] = out(second[-1])
        return data

    def technique_analysis(self, session_id: int) -> dict[str, Any] | None:
        """The bike fit or SkiErg analysis of a session: ``{"kind", "views": [analysis, ...]}``.

        Each film (bike fit: from the side and from the front) is a row in ``gait_analyses`` whose
        data carries ``"kind"`` (pose.technique.KINDS). The latest row per camera view counts, the
        side view first, and each comes back with its key-frame paths resolved."""
        latest: dict[str, dict[str, Any]] = {}
        for g in self.gait_analyses(session_id):
            if g["data"].get("kind") in TECHNIQUE_KINDS:
                data = dict(g["data"])
                data["keyframes"] = [dict(k, kind=k0.get("kind", ""), caption=k0.get("caption", ""))
                                     for k, k0 in zip(g["keyframes"], data.get("keyframe_meta") or [{}] * len(g["keyframes"]))]
                latest[str(data.get("view") or g["view"])] = data
        if not latest:
            return None
        views = sorted(latest.values(), key=lambda d: not str(d.get("view", "")).startswith("side"))
        return {"kind": views[0]["kind"], "views": views}

    def add_technique_analysis(self, session_id: int, analysis: Any) -> int:
        """Store one film's bike fit or SkiErg analysis (pose.technique.TechniqueAnalysis or its dict)."""
        data = analysis.to_dict() if hasattr(analysis, "to_dict") else dict(analysis)
        # the key-frame rows have no column for kind and caption: they ride along in the data
        data["keyframe_meta"] = [{"kind": k.get("kind", ""), "caption": k.get("caption", "")}
                                 for k in data.get("keyframes") or []]
        return self.add_gait_analysis(session_id, data, view=data.get("view", ""), video_path=data.get("video_path"))

    def delete_gait_analysis(self, gait_id: int, delete_files: bool = True) -> None:
        files = [self._abs_keyframe(r[0]) for r in self._q("SELECT path FROM keyframes WHERE gait_id=?", (gait_id,))]
        with self.transaction() as c:
            c.execute("DELETE FROM gait_analyses WHERE id=?", (gait_id,))
        if delete_files:
            for f in files:
                self._remove_owned(f)

    # ------------------------------------------------------------------ training plan
    def save_plan(self, athlete_id: int, *, request: dict[str, Any], plan: dict[str, Any],
                  health: dict[str, Any] | None = None, session_id: int | None = None, engine_version: str = "",
                  event: str = "saved", detail: dict[str, Any] | None = None) -> int:
        """Save the athlete's training plan (one per athlete; a new one replaces the old) and log ``event``.

        ``request`` and ``plan`` are JSON-ready dicts (plan.codec), ``session_id`` the test the plan is
        built on. ``health`` None keeps the stored health declaration. Returns the plan's row id."""
        now = _now()
        with self.transaction() as c:
            row = c.execute("SELECT id, health_json FROM plans WHERE athlete_id=?", (athlete_id,)).fetchone()
            hj = _jd(health) if health is not None else (row["health_json"] if row else "{}")
            if row:
                pid = int(row["id"])
                c.execute("UPDATE plans SET session_id=?, engine_version=?, request_json=?, plan_json=?, health_json=?, "
                          "updated=? WHERE id=?", (session_id, engine_version, _jd(request), _jd(plan), hj, now, pid))
            else:
                pid = int(c.execute(
                    "INSERT INTO plans (athlete_id,session_id,engine_version,request_json,plan_json,health_json,created,"
                    "updated) VALUES (?,?,?,?,?,?,?,?)",
                    (athlete_id, session_id, engine_version, _jd(request), _jd(plan), hj, now, now)).lastrowid)
            c.execute("INSERT INTO plan_events (plan_id,at,kind,detail_json) VALUES (?,?,?,?)",
                      (pid, now, event, _jd(detail or {})))
        return pid

    def get_plan(self, athlete_id: int) -> dict[str, Any] | None:
        """{"id", "athlete_id", "session_id", "engine_version", "request", "plan", "health", "created", "updated"}."""
        rows = self._q("SELECT * FROM plans WHERE athlete_id=?", (athlete_id,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r["id"], "athlete_id": r["athlete_id"], "session_id": r["session_id"],
                "engine_version": r["engine_version"], "request": json.loads(r["request_json"]),
                "plan": json.loads(r["plan_json"]), "health": json.loads(r["health_json"]),
                "created": r["created"], "updated": r["updated"]}

    def log_plan_event(self, plan_id: int, kind: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add ``kind`` to the plan's log without changing the plan (e.g. "mailed": what was attached,
        never the address). Returns the entry as plan_events() gives it."""
        entry = {"at": _now(), "kind": kind, "detail": dict(detail or {})}
        with self.transaction() as c:
            c.execute("INSERT INTO plan_events (plan_id,at,kind,detail_json) VALUES (?,?,?,?)",
                      (plan_id, entry["at"], kind, _jd(entry["detail"])))
        return entry

    def plan_events(self, plan_id: int) -> list[dict[str, Any]]:
        """The plan's log, oldest first: [{"at", "kind", "detail"}]."""
        return [{"at": r["at"], "kind": r["kind"], "detail": json.loads(r["detail_json"])}
                for r in self._q("SELECT * FROM plan_events WHERE plan_id=? ORDER BY id", (plan_id,))]

    def delete_plan(self, athlete_id: int) -> bool:
        """Remove the athlete's plan and its log; the rows are overwritten. False when there was none."""
        with self.transaction() as c:
            n = c.execute("DELETE FROM plans WHERE athlete_id=?", (athlete_id,)).rowcount
        if n:
            self._scrub()
        return bool(n)

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._q("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.transaction() as c:
            c.execute("INSERT OR REPLACE INTO settings (key,value_json) VALUES (?,?)", (key, _jd(value)))

    def delete_setting(self, key: str) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM settings WHERE key=?", (key,))

    def all_settings(self) -> dict[str, Any]:
        return {r[0]: json.loads(r[1]) for r in self._q("SELECT key, value_json FROM settings")}

    # ------------------------------------------------------------------ export, backup, restore
    def export_athlete(self, athlete_id: int) -> dict[str, Any]:
        """Everything stored about one athlete as JSON-ready data (GDPR right of access/portability)."""
        a = self.get_athlete(athlete_id)
        if a is None:
            raise StorageError(t("storage.error.not_found"))
        out: dict[str, Any] = {"format": "lopanalys-athlete", "version": 1, "exported": _now(),
                               "athlete": json.loads(_jd(a.__dict__)), "sessions": []}
        for s in self.sessions(athlete_id):
            assert s.id is not None
            out["sessions"].append({
                "session": json.loads(_jd(s.__dict__)),
                "step_records": [json.loads(_jd(r.__dict__)) for r in self.step_records(s.id)],
                "time_samples": [json.loads(_jd(x.__dict__)) for x in self.time_samples(s.id)],
                "lactate_entries": self.lactate_entries(s.id),
                "gait_analyses": self.gait_analyses(s.id),
            })
        plan = self.get_plan(athlete_id)
        if plan is not None:
            out["training_plan"] = dict(plan, events=self.plan_events(plan["id"]))
        return out

    def export_athlete_json(self, athlete_id: int, dest: str | Path) -> Path:
        dest = Path(dest)
        dest.write_text(json.dumps(self.export_athlete(athlete_id), ensure_ascii=False, indent=1), encoding="utf-8")
        return dest

    def backup(self, dest_zip: str | Path, password: str | None = None) -> Path:
        """Zip with a consistent copy of the database (SQLite backup API) and the key-frame folder.

        With ``password`` the zip is encrypted (``storage.privacy.encrypt_file``: AES-256-GCM, key from
        scrypt) and no plain copy is left behind: the plain zip is made in the data folder and removed. The program always gives a password; a plain zip is
        only made by tests and tools. The backup is registered, so an erasure can list it."""
        dest_zip = Path(dest_zip)
        dest_zip.parent.mkdir(parents=True, exist_ok=True)
        if password is not None:
            from lopanalys.storage import privacy
            if len(password) < privacy.MIN_PASSWORD:
                raise StorageError(t("storage.error.password_short", n=privacy.MIN_PASSWORD))
            # in the data folder, never where the backup goes: a plain copy removed from a USB stick can
            # be read back, and one in a folder synced to the cloud is already uploaded
            plain = self.data_root / f"{self.path.name}.backup.plain.zip"
            # encrypted beside the destination and moved into place when whole: a failure (a full stick)
            # leaves neither a cut-off backup nor a damaged one over the good backup of the same name
            part = dest_zip.with_name(dest_zip.name + ".part")
            try:
                self.backup(plain)
                privacy.encrypt_file(plain, part, password)
                part.replace(dest_zip)
            except OSError as e:
                raise StorageError(t("storage.error.backup", err=str(e))) from e
            finally:
                plain.unlink(missing_ok=True)
                part.unlink(missing_ok=True)
                self._forget_path("backup", plain)
            self.add_saved_file("backup", dest_zip)
            return dest_zip
        tmp = dest_zip.with_name(dest_zip.name + ".tmp.sqlite")
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                with self._lock:
                    self._con.backup(dst)
            finally:
                dst.close()
            manifest = {"format": "lopanalys-backup", "version": BACKUP_FORMAT, "schema_version": self.schema_version,
                        "created": _now()}
            part = dest_zip.with_name(dest_zip.name + ".part")
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("manifest.json", json.dumps(manifest))
                z.write(tmp, DB_FILENAME)
                if self.keyframes_root.is_dir():
                    for f in sorted(self.keyframes_root.rglob("*")):
                        if f.is_file():
                            z.write(f, "keyframes/" + f.relative_to(self.keyframes_root).as_posix())
            part.replace(dest_zip)
        except OSError as e:
            raise StorageError(t("storage.error.backup", err=str(e))) from e
        finally:
            tmp.unlink(missing_ok=True)
        self.add_saved_file("backup", dest_zip)
        return dest_zip

    def _forget_path(self, kind: str, path: Path) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM saved_files WHERE kind=? AND path=?", (kind, self._rel(Path(path).absolute())))

    def purge_safety_copies(self, days: int = SAFETY_COPY_DAYS) -> int:
        """Remove the program's own full copies (before an upgrade, before a restore) older than ``days``."""
        n = 0
        limit = datetime.now() - timedelta(days=days)
        for f, _ in self.safety_copies():
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < limit:
                    f.unlink()
                    n += 1
            except OSError:
                log.warning("could not remove an old safety copy")
        if n:
            log.info("%d safety copies older than %d days removed", n, days)
        return n

    @staticmethod
    def restore_backup(zip_path: str | Path, db_path: str | Path | None = None,
                       keyframes_root: str | Path | None = None, password: str | None = None, *,
                       _source: Path | None = None) -> Path:
        """Replace the database (and key frames) with a backup. Close every open Database first.

        An encrypted backup needs its ``password``. The current database is kept as
        ``<name>.before-restore-<time>.sqlite`` (removed after 30 days), and the ids erased in it are
        carried over, so people erased since the backup was made are erased again when the restored
        database opens. Returns the kept copy's path (or the db path if there was no database before).
        """
        zip_path = Path(zip_path)
        db = Path(db_path) if db_path is not None else paths.db_path()
        from lopanalys.storage import privacy
        if privacy.is_encrypted(zip_path):
            if not password:
                raise StorageError(t("storage.error.password_needed"))
            db.parent.mkdir(parents=True, exist_ok=True)
            plain = db.with_name(db.name + ".restore.zip")
            try:
                privacy.decrypt_file(zip_path, plain, password)
                return Database.restore_backup(plain, db_path, keyframes_root, _source=zip_path)
            finally:
                plain.unlink(missing_ok=True)
        kroot = Path(keyframes_root) if keyframes_root is not None else (
            paths.keyframes_dir() if db_path is None else db.parent / "keyframes")
        try:
            z = zipfile.ZipFile(zip_path)
        except (OSError, zipfile.BadZipFile) as e:
            raise StorageError(t("storage.error.bad_backup", err=str(e))) from e
        with z:
            names = set(z.namelist())
            if "manifest.json" not in names or DB_FILENAME not in names:
                raise StorageError(t("storage.error.bad_backup", err="manifest"))
            try:
                m = json.loads(z.read("manifest.json"))
                if not isinstance(m, dict) or m.get("format") != "lopanalys-backup":
                    raise StorageError(t("storage.error.bad_backup", err="format"))
                newer = int(m.get("schema_version", 0)) > SCHEMA_VERSION
            except (ValueError, TypeError, zipfile.BadZipFile, zlib.error, EOFError) as e:   # a damaged zip
                raise StorageError(t("storage.error.bad_backup", err="manifest")) from e
            if newer:
                raise StorageError(t("storage.error.newer_schema", found=m.get("schema_version"), supported=SCHEMA_VERSION))
            db.parent.mkdir(parents=True, exist_ok=True)
            staged = db.with_name(db.name + ".restore")
            try:
                with z.open(DB_FILENAME) as src, open(staged, "wb") as out:
                    shutil.copyfileobj(src, out)
            except (zipfile.BadZipFile, zlib.error, EOFError) as e:      # damaged: nothing is left behind
                staged.unlink(missing_ok=True)
                raise StorageError(t("storage.error.bad_backup", err=str(e))) from e
            try:
                con = sqlite3.connect(str(staged))
                try:
                    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    con.close()
            except sqlite3.DatabaseError as e:
                staged.unlink(missing_ok=True)
                raise StorageError(t("storage.error.bad_backup", err=str(e))) from e
            if ok != "ok":
                staged.unlink(missing_ok=True)
                raise StorageError(t("storage.error.bad_backup", err=ok))
            _carry_saved_files(staged, db if db.exists() else None, _source or zip_path, str(m.get("created") or ""))
            kept = db
            if db.exists():
                kept = db.with_name(f"{db.stem}.before-restore-{datetime.now():%Y%m%d-%H%M%S}.sqlite")
                shutil.copy2(db, kept)
                _carry_erased(db)
            for ext in ("-wal", "-shm"):
                Path(str(db) + ext).unlink(missing_ok=True)
            staged.replace(db)
            kroot.mkdir(parents=True, exist_ok=True)
            root = kroot.resolve()
            for name in names:
                if not name.startswith("keyframes/") or name.endswith("/"):
                    continue
                target = (kroot / name[len("keyframes/"):]).resolve()
                if not target.is_relative_to(root):     # zip-slip guard
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with z.open(name) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                except (zipfile.BadZipFile, zlib.error, EOFError):   # the database is in place: go on
                    target.unlink(missing_ok=True)
                    log.warning("a damaged key frame in the backup was left out")
        return kept
