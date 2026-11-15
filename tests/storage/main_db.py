"""A data folder as the program on main (schema v3, before the data protection work) left it: the database from
fixtures/main_v3.sql and the files it points to, named after the people."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "main_v3.sql"
FILM = "20261113-101500-Lindqvist_Berg.mp4"
EARLIER = "20261113-100000-Lindqvist_Berg.mp4"
CUT_FILM = "20261114-143200-Sandberg.mp4"
KEYFRAMES = ("20261113-101500-Lindqvist_Berg_01_contact_left.png", "20261113-101500-Lindqvist_Berg_02_midstance_left.png")
REPORTS = ("Anna_Lindqvist_Berg_Löpteknik_10–12_km-h_2026-11-13.pdf", "Erik_Sandberg_Laktattrappa_2026-11-14.pdf",
           "Om_testerna.pdf")
OUTSIDE_FILM = "sd-kort/GX010042-Sandberg.mp4"


def build(tmp_path: Path) -> tuple[Path, Path]:
    """Returns (data folder, a folder outside it). Open ``data / "lopanalys.sqlite"`` to migrate."""
    root, outside = tmp_path / "data", tmp_path / "outside"
    for d in (root / "recordings", root / "keyframes" / "session-1", root / "reports", root / "logs", outside / "sd-kort"):
        d.mkdir(parents=True)
    sql = FIXTURE.read_text(encoding="utf-8").replace("/__ROOT__", root.as_posix()).replace("/__OUTSIDE__", outside.as_posix())
    con = sqlite3.connect(root / "lopanalys.sqlite")
    con.executescript(sql)
    con.close()
    for name in (FILM, EARLIER, CUT_FILM):
        (root / "recordings" / name).write_bytes(b"film " + name.encode())
        (root / "recordings" / (name + ".json")).write_text(json.dumps({"version": 1, "video": name, "t": [0.0, 0.033]}),
                                                            encoding="utf-8")
    for name in KEYFRAMES:
        (root / "keyframes" / "session-1" / name).write_bytes(b"png " + name.encode())
    for name in REPORTS:
        (root / "reports" / name).write_bytes(b"%PDF " + name.encode())
    (outside / OUTSIDE_FILM).write_bytes(b"film from the card")
    (root / "logs" / "lopanalys.log").write_text(
        f"2026-11-13 10:19:00 ERROR rec lopanalys.camera.recorder: recording {root.as_posix()}/recordings/{FILM} failed\n",
        encoding="utf-8")
    return root, outside
