"""The encrypted backup, the safety copies and the move of the data folder."""
import os
import time
import zipfile

import pytest

from lopanalys.storage import privacy
from lopanalys.storage.db import DB_FILENAME, Database, StorageError

from . import main_db

PW = "korrekt häst batteri"


def test_encrypt_roundtrip_wrong_password_and_damage(tmp_path):
    src = tmp_path / "plain.bin"
    data = os.urandom(2_500_000) + b"Sandberg"                      # more than two chunks
    src.write_bytes(data)
    enc, out = tmp_path / "x.lopbak", tmp_path / "out.bin"
    privacy.encrypt_file(src, enc, PW)
    assert privacy.is_encrypted(enc) and not privacy.is_encrypted(src)
    assert b"Sandberg" not in enc.read_bytes()
    privacy.decrypt_file(enc, out, PW)
    assert out.read_bytes() == data
    with pytest.raises(StorageError, match="Fel lösenord"):
        privacy.decrypt_file(enc, out, "fel lösenord")
    assert not out.exists()                                          # nothing half-written is left
    raw = enc.read_bytes()
    (tmp_path / "cut.lopbak").write_bytes(raw[: len(raw) // 2])      # cut short
    with pytest.raises(StorageError):
        privacy.decrypt_file(tmp_path / "cut.lopbak", out, PW)
    flipped = bytearray(raw)
    flipped[100] ^= 1
    (tmp_path / "bad.lopbak").write_bytes(bytes(flipped))
    with pytest.raises(StorageError):
        privacy.decrypt_file(tmp_path / "bad.lopbak", out, PW)
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    privacy.encrypt_file(empty, enc, PW)
    privacy.decrypt_file(enc, out, PW)
    assert out.read_bytes() == b""


def test_backup_is_encrypted_and_restores_with_the_password(tmp_path):
    root, _ = main_db.build(tmp_path)
    db = Database(root / "lopanalys.sqlite")
    dest = db.backup(tmp_path / "usb" / "kopia.lopbak", PW)
    raw = dest.read_bytes()
    assert raw.startswith(privacy.MAGIC) and not zipfile.is_zipfile(dest)
    for word in (b"Lindqvist", b"Sandberg", b"SQLite format", b"manifest.json"):
        assert word not in raw
    assert sorted(p.name for p in dest.parent.iterdir()) == ["kopia.lopbak"]          # no plain copy left beside it
    assert [(f["kind"], os.path.basename(f["path"])) for f in db.saved_files(None)] == [("backup", "kopia.lopbak")]
    with pytest.raises(StorageError, match="minst 8"):
        db.backup(tmp_path / "usb" / "kort.lopbak", "kort")
    db.close()

    new = tmp_path / "pc2" / "lopanalys.sqlite"
    with pytest.raises(StorageError, match="lösenordsskyddad"):
        Database.restore_backup(dest, new)
    with pytest.raises(StorageError, match="Fel lösenord"):
        Database.restore_backup(dest, new, password="något annat")
    assert not new.exists() and not list(new.parent.glob("*.zip"))
    Database.restore_backup(dest, new, password=PW)
    with Database(new) as d2:
        assert [a.name for a in d2.list_athletes()] == ["Anna Lindqvist Berg", "Erik Sandberg"]
        g = d2.gait_analysis(1)
        assert all(os.path.isfile(k["image_path"]) for k in g["keyframes"])            # the key frames came along
    assert sorted(p.name for p in new.parent.iterdir() if p.is_file()) == ["lopanalys.sqlite"]


def test_old_plain_zip_backup_still_restores(tmp_path):
    root, _ = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        plain = db.backup(tmp_path / "gammal.zip")                   # what main wrote before the encrypted backup
    assert zipfile.is_zipfile(plain)
    Database.restore_backup(plain, tmp_path / "pc2" / "lopanalys.sqlite")
    with Database(tmp_path / "pc2" / "lopanalys.sqlite") as d2:
        assert len(d2.list_athletes()) == 2


def test_safety_copies_go_after_30_days(tmp_path):
    root, _ = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        bak = root / "lopanalys.sqlite.v3.bak"
        old_restore = root / "lopanalys.before-restore-20261001-120000.sqlite"
        old_restore.write_bytes(b"x")
        assert bak.is_file() and db.purge_safety_copies() == 0       # fresh: both stay
        t = time.time() - 31 * 86400
        os.utime(old_restore, (t, t))
        assert db.purge_safety_copies() == 1 and not old_restore.exists() and bak.is_file()
        os.utime(bak, (t, t))
        assert db.purge_safety_copies() == 1 and not bak.exists()
        assert (root / "lopanalys.sqlite").is_file()


def test_move_data_folder_is_a_real_move(tmp_path):
    root, _ = main_db.build(tmp_path)
    (root / "branding").mkdir()
    (root / "branding" / "logo.png").write_bytes(b"logo")
    (root / "egen-fil.txt").write_text("coachens egen fil")           # not the program's: stays
    dest = tmp_path / "ny plats" / "Tröskel"
    db = Database(root / "lopanalys.sqlite")
    db.set_setting("branding.logo", str(root / "branding" / "logo.png"))
    with pytest.raises(StorageError):
        privacy.move_data_folder(db, root / "inuti")
    with pytest.raises(StorageError):
        privacy.move_data_folder(db, tmp_path)
    privacy.move_data_folder(db, dest)
    db.close()
    assert privacy.finish_move(root, tmp_path / "finns inte") == 0 and (root / "recordings").is_dir()   # no new database: nothing removed
    with pytest.raises(StorageError):
        with Database(root / "lopanalys.sqlite") as again:
            privacy.move_data_folder(again, dest)                      # the target already holds a database
    removed = privacy.finish_move(root, dest)
    assert removed >= 12
    assert sorted(p.name for p in root.iterdir()) == ["egen-fil.txt"]  # the old folder holds no copy any more
    with Database(dest / "lopanalys.sqlite") as moved:
        assert [a.name for a in moved.list_athletes()] == ["Anna Lindqvist Berg", "Erik Sandberg"]
        gait = moved.sessions(1)[0]
        assert gait.video_path == str(dest / "recordings" / "session-1-20261113-101500.mp4") and os.path.isfile(gait.video_path)
        assert all(os.path.isfile(k["image_path"]) for k in moved.gait_analysis(1)["keyframes"])
        assert os.path.isfile(moved.saved_files(2)[0]["path"])
        assert moved.get_setting("branding.logo") == str(dest / "branding" / "logo.png")
    assert privacy.finish_move(root, dest) == 0


def test_a_damaged_plain_backup_is_refused_with_a_message_and_leaves_nothing_behind(tmp_path):
    """A plain zip (an old backup) with a damaged database, manifest or key frame raised zlib.error or
    JSONDecodeError past StorageError, and left ``lopanalys.sqlite.restore`` in the data folder."""
    root, _ = main_db.build(tmp_path)
    with Database(root / "lopanalys.sqlite") as db:
        good = db.backup(tmp_path / "kopia.zip")
        names = [a.name for a in db.list_athletes()]
    raw = bytearray(good.read_bytes())
    raw[raw.find(DB_FILENAME.encode()) + 200] ^= 0xFF           # inside the compressed database
    bad_db = tmp_path / "skadad.zip"
    bad_db.write_bytes(raw)
    bad_manifest = tmp_path / "manifest.zip"
    with zipfile.ZipFile(good) as zi, zipfile.ZipFile(bad_manifest, "w") as zo:
        zo.writestr("manifest.json", "{not json")
        zo.writestr(DB_FILENAME, zi.read(DB_FILENAME))
    for bad in (bad_db, bad_manifest):
        with pytest.raises(StorageError, match="säkerhetskopia"):
            Database.restore_backup(bad, root / "lopanalys.sqlite")
        assert not list(root.glob("*.restore*")) and not list(root.glob("*before-restore*"))
        with Database(root / "lopanalys.sqlite") as db:
            assert [a.name for a in db.list_athletes()] == names      # the data in use is untouched
    # a damaged key frame does not stop the restore: the database is what matters
    kf = next(n for n in zipfile.ZipFile(good).namelist() if n.startswith("keyframes/"))
    raw = bytearray(good.read_bytes())
    at = raw.find(kf.encode())
    raw[at + len(kf) + 20] ^= 0xFF
    bad_kf = tmp_path / "bild.zip"
    bad_kf.write_bytes(raw)
    other = tmp_path / "pc2" / "lopanalys.sqlite"
    Database.restore_backup(bad_kf, other)
    with Database(other) as db:
        assert [a.name for a in db.list_athletes()] == names
