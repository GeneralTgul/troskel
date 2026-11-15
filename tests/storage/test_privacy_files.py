"""Housekeeping on the data folder: how long logs and safety copies are kept."""
import os
import time

from lopanalys.storage import privacy


def _aged(p, days):
    p.write_text("x")
    t = time.time() - days * 86400
    os.utime(p, (t, t))
    return p


def test_logs_are_kept_30_days(tmp_path):
    keep = [_aged(tmp_path / "lopanalys.log", 40), _aged(tmp_path / "lopanalys.log.2026-11-01", 13),
            _aged(tmp_path / "crash.log", 2)]
    gone = [_aged(tmp_path / "lopanalys.log.2026-10-01", 44), _aged(tmp_path / "lopanalys.log.3", 31)]
    assert privacy.purge_old_logs(tmp_path) == 2
    assert all(p.exists() for p in keep) and not any(p.exists() for p in gone)
    assert privacy.purge_old_logs(tmp_path / "missing") == 0
