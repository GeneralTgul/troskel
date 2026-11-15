"""Data protection chores on the data folder. No Qt, no database schema.

What the program keeps is described in docs/DATASKYDD.md. This module holds the parts that work on
files: how long logs and the program's own safety copies are kept. ``storage.db`` holds the parts
that work on rows (erasure, the retention of films, consent).
"""
from __future__ import annotations

import html
import json
import logging
import os
import struct
import time
import zipfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LOG_DAYS = 30


def _older_than(p: Path, days: float, now: float | None = None) -> bool:
    try:
        return (now if now is not None else time.time()) - p.stat().st_mtime > days * 86400
    except OSError:
        return False


def purge_old_logs(folder: str | Path, days: int = LOG_DAYS, now: float | None = None) -> int:
    """Remove rotated log files (``*.log.<something>``) not written for ``days`` days. Returns how many.

    The log of today is never removed. ``crash.log`` is appended to on every native crash and has no
    rotation of its own, so it is removed too once nothing was written to it for ``days`` days."""
    folder, n = Path(folder), 0
    if not folder.is_dir():
        return 0
    for f in folder.iterdir():
        rotated = ".log." in f.name or f.name == "crash.log"
        if f.is_file() and rotated and _older_than(f, days, now):
            try:
                f.unlink()
                n += 1
            except OSError:      # crash.log is held open by this process
                pass
    return n


# ---------------------------------------------------------------------------- the extract (right of access)
def export_athlete_zip(db: Any, athlete_id: int, dest: str | Path) -> Path:
    """Everything stored about one person in one zip: ``sammanstallning.html`` to read,
    ``data.json`` (``Database.export_athlete``), the films with their time files in ``film/``, the key
    frames in ``bilder/`` and the saved reports in ``rapporter/``.

    Paths inside ``data.json`` point into the zip, not to the coach's disk. The zip is not encrypted:
    it is the person's own data, handed to them. It is registered, so erasing the person removes it."""
    from lopanalys.core.i18n import t
    from lopanalys.storage.db import StorageError
    dest = Path(dest)
    data = db.export_athlete(athlete_id)
    arc: dict[str, str] = {}                        # absolute path on disk -> name in the zip

    def take(path: str | None, folder: str) -> None:
        if not path or path in arc or not Path(path).is_file():
            return
        name, n = f"{folder}/{Path(path).name}", 1
        while name in arc.values():
            n += 1
            name = f"{folder}/{Path(path).stem}-{n}{Path(path).suffix}"
        arc[path] = name

    for item in data["sessions"]:
        s = item["session"]
        res = s.get("result") or {}
        # the film, the films from before a cut, and the second camera's film (two views)
        for film in [s.get("video_path"), *(res.get("earlier_videos") or []), res.get("second_video")]:
            take(film, "film")
            take(f"{film}.json" if film else None, "film")
        for g in item["gait_analyses"]:
            take(g.get("video_path"), "film")
            for k in g["keyframes"]:
                take(k.get("image_path"), f"bilder/session-{s['id']}")
    for sf in db.saved_files(athlete_id, "report") + db.saved_files(athlete_id, "fit"):
        take(sf["path"], "rapporter")
    data["saved_files"] = [{"kind": sf["kind"], "path": sf["path"], "created": sf["created"]}
                           for sf in db.saved_files(athlete_id)]
    data = _repath(data, arc)
    part = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("sammanstallning.html", _summary_html(data, sorted(arc.values()), t))
            z.writestr("data.json", json.dumps(data, ensure_ascii=False, indent=1))
            for src, name in arc.items():
                z.write(src, name, zipfile.ZIP_STORED if name.endswith((".mp4", ".mov", ".m4v", ".png", ".pdf"))
                        else zipfile.ZIP_DEFLATED)
        part.replace(dest)
    except OSError as e:
        raise StorageError(t("storage.error.extract", err=str(e))) from e
    finally:
        part.unlink(missing_ok=True)
    db.add_saved_file("extract", dest, athlete_id=athlete_id)
    log.info("extract made for athlete %s: %d files", athlete_id, len(arc))
    return dest


def _repath(v: Any, arc: dict[str, str]) -> Any:
    """Every string that is a file put in the zip becomes its name there; other local paths become file names."""
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k, x in v.items():
            if k in ("video_path", "image_path", "path", "second_video") and isinstance(x, str):
                out[k] = _local(x, arc)
            elif k == "earlier_videos" and isinstance(x, list):
                out[k] = [_local(str(p), arc) for p in x]
            else:
                out[k] = _repath(x, arc)
        return out
    if isinstance(v, list):
        return [_repath(x, arc) for x in v]
    return arc.get(v, v) if isinstance(v, str) else v


def _local(path: str, arc: dict[str, str]) -> str:
    return arc.get(path) or Path(path).name


def _summary_html(data: dict[str, Any], files: list[str], t: Any) -> str:
    e = html.escape
    a = data["athlete"]
    name = f"{a.get('first_name', '')} {a.get('last_name', '')}".strip()

    def rows(pairs: list[tuple[str, Any]]) -> str:
        return "".join(f"<tr><th>{e(k)}</th><td>{e(_txt(v, t))}</td></tr>" for k, v in pairs if v not in (None, "", {}))

    out = ["<!doctype html><html><head><meta charset='utf-8'>", f"<title>{e(t('storage.extract.title', name=name))}</title>",
           "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:900px;margin:2em auto;color:#222}"
           "table{border-collapse:collapse;margin:.5em 0 1.5em}th,td{border:1px solid #ccc;padding:4px 10px;text-align:left}"
           "th{background:#f3f3f3}h2{margin-top:2em}</style></head><body>",
           f"<h1>{e(t('storage.extract.title', name=name))}</h1>",
           f"<p>{e(t('storage.extract.intro', date=str(data.get('exported', ''))[:10]))}</p>",
           f"<h2>{e(t('storage.extract.person'))}</h2><table>",
           rows([(t(f"storage.extract.{k}"), a.get(k)) for k in
                 ("first_name", "last_name", "email", "birth_date", "sex", "height_cm", "weight_kg", "max_hr", "resting_hr",
                  "watch_vo2max", "watch_vo2max_date", "notes", "created")]), "</table>"]
    consent = a.get("consent") or {}
    if consent:
        out += [f"<h2>{e(t('storage.extract.consent'))}</h2><table>",
                rows([(t(f"storage.extract.consent.{k}"), consent.get(k)) for k in
                      ("date", "test", "film", "store", "contact", "withdrawn") if k in consent]), "</table>"]
    out.append(f"<h2>{e(t('storage.extract.tests'))}</h2>")
    if not data["sessions"]:
        out.append(f"<p>{e(t('storage.extract.no_tests'))}</p>")
    for item in data["sessions"]:
        s, res = item["session"], item["session"].get("result") or {}
        out.append(f"<h3>{e(t('storage.extract.test', date=str(s.get('started', ''))[:16].replace('T', ' '), protocol=s.get('protocol_name') or s.get('kind', '')))}</h3><table>")
        pairs: list[tuple[str, Any]] = [(t("storage.extract.status"), s.get("status")),
                                        (t("storage.extract.treadmill"), s.get("treadmill_mode")),
                                        (t("storage.extract.camera"), s.get("camera_source")),
                                        (t("storage.extract.notes"), s.get("notes")),
                                        (t("storage.extract.comment"), res.get("coach_comment")),
                                        (t("storage.extract.max_hr"), res.get("max_hr"))]
        for key in ("lt1", "lt2"):
            thr = res.get(key)
            if isinstance(thr, dict) and thr.get("speed_kmh"):
                pairs.append((t(f"storage.extract.{key}"), t("storage.extract.threshold", speed=_num(thr.get("speed_kmh")),
                                                             hr=_num(thr.get("hr"), 0), lactate=_num(thr.get("lactate_mmol")))))
        vo2 = res.get("vo2max")
        if isinstance(vo2, dict) and vo2.get("value") is not None:
            pairs.append((t("storage.extract.vo2max"), _num(vo2.get("value"))))
        pairs.append((t("storage.extract.samples"), len(item["time_samples"]) or None))
        out += [rows(pairs), "</table>"]
        if res.get("video_removed"):
            out.append(f"<p>{e(t('storage.extract.film_removed', date=res['video_removed']))}</p>")
        if item["step_records"]:
            cols = ("stage", "speed", "incline", "duration", "hr", "lactate", "rpe")
            out.append("<table><tr>" + "".join(f"<th>{e(t('storage.extract.col.' + c))}</th>" for c in cols) + "</tr>")
            for r in item["step_records"]:
                vals = [r.get("index", 0) + 1, _num(r.get("speed_kmh")), _num(r.get("incline_pct")), _num(r.get("duration_s"), 0),
                        r.get("hr_end"), _num(r.get("lactate_mmol")), r.get("rpe")]
                out.append("<tr>" + "".join(f"<td>{e('' if v is None else str(v))}</td>" for v in vals) + "</tr>")
            out.append("</table>")
        if item["gait_analyses"]:
            out.append(f"<p>{e(t('storage.extract.gait', n=len(item['gait_analyses']), k=sum(len(g['keyframes']) for g in item['gait_analyses'])))}</p>")
    if files:
        out.append(f"<h2>{e(t('storage.extract.files'))}</h2><ul>" + "".join(f"<li>{e(f)}</li>" for f in files) + "</ul>")
    out.append("</body></html>")
    return "".join(out)


def _num(v: Any, decimals: int = 1) -> str:
    return "" if v is None else f"{float(v):.{decimals}f}".replace(".", ",")


def _txt(v: Any, t: Any) -> str:
    if isinstance(v, bool):
        return t("storage.extract.yes") if v else t("storage.extract.no")
    return str(v)


# ---------------------------------------------------------------------------- encrypted backup
# File format (version 1): MAGIC, 16 bytes salt, 8 bytes nonce prefix, then chunks. A chunk is a 4-byte
# big-endian length and that many bytes of AES-256-GCM output (the 16-byte tag included). The key is
# scrypt(password, salt); the nonce is the prefix plus a 4-byte chunk counter; the last chunk is
# authenticated with b"L" and the others with b"M", so a file cut short does not decrypt.
MAGIC = b"LOPBAK1\n"
BACKUP_SUFFIX = ".lopbak"
MIN_PASSWORD = 8
_CHUNK = 1 << 20
_SCRYPT = dict(length=32, n=1 << 15, r=8, p=1)


def _key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, **_SCRYPT).derive(password.encode("utf-8"))


def is_encrypted(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt_file(src: str | Path, dest: str | Path, password: str) -> None:
    """Write ``src`` to ``dest`` encrypted with a password (AES-256-GCM, key from scrypt)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, prefix = os.urandom(16), os.urandom(8)
    aead = AESGCM(_key(password, salt))
    with open(src, "rb") as fin, open(dest, "wb") as out:
        out.write(MAGIC + salt + prefix)
        i, chunk = 0, fin.read(_CHUNK)
        while True:
            nxt = fin.read(_CHUNK)
            ct = aead.encrypt(prefix + struct.pack(">I", i), chunk, b"M" if nxt else b"L")
            out.write(struct.pack(">I", len(ct)) + ct)
            if not nxt:
                break
            i, chunk = i + 1, nxt


def decrypt_file(src: str | Path, dest: str | Path, password: str) -> None:
    """The reverse of encrypt_file(). Raises StorageError for a wrong password or a damaged file."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from lopanalys.core.i18n import t
    from lopanalys.storage.db import StorageError
    ok = False
    try:
        with open(src, "rb") as fin, open(dest, "wb") as out:
            if fin.read(len(MAGIC)) != MAGIC:
                raise StorageError(t("storage.error.bad_backup", err="format"))
            salt, prefix = fin.read(16), fin.read(8)
            aead = AESGCM(_key(password, salt))
            i, head = 0, fin.read(4)
            while True:
                if len(head) != 4:
                    raise StorageError(t("storage.error.bad_backup", err="truncated"))
                ct = fin.read(struct.unpack(">I", head)[0])
                head = fin.read(4)
                try:
                    out.write(aead.decrypt(prefix + struct.pack(">I", i), ct, b"M" if head else b"L"))
                except InvalidTag:
                    raise StorageError(t("storage.error.bad_password")) from None
                if not head:
                    break
                i += 1
        ok = True
    finally:
        if not ok:
            Path(dest).unlink(missing_ok=True)


# ---------------------------------------------------------------------------- moving the data folder
PROGRAM_ITEMS = ("recordings", "keyframes", "reports", "logs", "branding", "models", "cache", "demo")


def _is_program_file(name: str) -> bool:
    return name.startswith("lopanalys.") or name == "erased-pending.json"


def move_data_folder(db: Any, dest: str | Path) -> Path:
    """Copy the whole data folder of the open database ``db`` to ``dest`` and check the copy.

    The database is copied with SQLite's backup API (consistent while it is open), everything else
    file by file. Paths inside the data folder are stored relative to it, so nothing in the database
    has to change, except the logo setting. The old folder is removed by ``finish_move()`` at the next
    program start, when the program has opened the new one. Raises StorageError with the reason."""
    import shutil
    import sqlite3

    from lopanalys.core.i18n import t
    from lopanalys.storage.db import Database, StorageError
    src, dest = Path(db.data_root).resolve(), Path(dest).resolve()
    if dest == src or dest.is_relative_to(src) or src.is_relative_to(dest):
        raise StorageError(t("storage.error.move_inside"))
    if (dest / db.path.name).exists():
        raise StorageError(t("storage.error.move_has_data"))
    try:
        dest.mkdir(parents=True, exist_ok=True)
        out = sqlite3.connect(str(dest / db.path.name))
        try:
            with db._lock:
                db._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db._con.backup(out)
        finally:
            out.close()
        want: list[tuple[Path, int]] = []
        for item in sorted(src.iterdir()):
            if item.name in (db.path.name, db.path.name + "-wal", db.path.name + "-shm"):
                continue
            if item.is_dir() and item.name in PROGRAM_ITEMS:
                shutil.copytree(item, dest / item.name, dirs_exist_ok=True)
                want += [(dest / f.relative_to(src), f.stat().st_size) for f in item.rglob("*") if f.is_file()]
            elif item.is_file() and _is_program_file(item.name):
                shutil.copy2(item, dest / item.name)
                want.append((dest / item.name, item.stat().st_size))
        # the active log grows while we copy: every other file must have arrived whole
        bad = [p for p, size in want if p.parent.name != "logs" and (not p.is_file() or p.stat().st_size != size)]
        if bad:
            raise StorageError(t("storage.error.move_incomplete", n=len(bad)))
        with Database(dest / db.path.name) as moved:
            if moved._q("PRAGMA integrity_check")[0][0] != "ok":
                raise StorageError(t("storage.error.move_incomplete", n=1))
            logo = moved.get_setting("branding.logo")
            if logo and Path(logo).is_absolute() and Path(logo).resolve().is_relative_to(src):
                moved.set_setting("branding.logo", str(dest / Path(logo).resolve().relative_to(src)))
    except (OSError, sqlite3.Error) as e:
        raise StorageError(t("storage.error.move_failed", err=str(e))) from e
    log.info("data folder copied to its new place; the old one is removed at the next start")
    return dest


def finish_move(old: str | Path, new: str | Path, db_name: str = "lopanalys.sqlite") -> int:
    """After a move, at the next start: remove the program's files from the old folder (only what the
    program made; a folder that then is empty goes too). Does nothing unless the new folder holds a
    database that opens. Returns the number of files removed."""
    import shutil
    import sqlite3
    old, new = Path(old), Path(new)
    if not old.is_dir() or not (new / db_name).is_file() or old.resolve() == new.resolve() \
            or new.resolve().is_relative_to(old.resolve()):
        return 0
    try:
        con = sqlite3.connect(f"file:{(new / db_name).as_posix()}?mode=ro", uri=True)
        try:
            if con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='athletes'").fetchone()[0] != 1:
                return 0
        finally:
            con.close()
    except sqlite3.Error:
        return 0
    n = 0
    for item in list(old.iterdir()):
        try:
            if item.is_dir() and item.name in PROGRAM_ITEMS:
                n += sum(1 for f in item.rglob("*") if f.is_file())
                shutil.rmtree(item, ignore_errors=True)
            elif item.is_file() and _is_program_file(item.name):
                item.unlink()
                n += 1
        except OSError:
            log.warning("could not remove an item of the old data folder")
    try:
        old.rmdir()
    except OSError:
        pass
    log.info("old data folder cleared after the move: %d files", n)
    return n
