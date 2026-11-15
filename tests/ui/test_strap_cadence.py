"""The live cadence tile: the strap's cadence (RSC) when it has one, else the camera's, titled by source."""
from __future__ import annotations

from types import SimpleNamespace

from lopanalys.ui.live_controller import STRAP_CADENCE_STALE_S, LiveController
from lopanalys.ui.views.live_board import LiveBoard
from lopanalys.ui.widgets.tiles import MetricTile


class _Ctl:
    def __init__(self) -> None:
        self.now = 100.0
        self.cadence_series: list[tuple[float, float]] = []

    def elapsed(self) -> float:
        return self.now

    def strap_cadence(self, window_s: float = 12.0):
        return LiveController.strap_cadence(self, window_s)   # the real logic on this fake


def test_strap_cadence_window_and_staleness():
    c = _Ctl()
    assert c.strap_cadence() is None
    c.cadence_series = [(80.0, 150.0), (95.0, 176.0), (98.0, 178.0), (99.5, 177.0)]
    assert c.strap_cadence() == 177.0                      # median of the last 12 s; 80 s is outside
    c.now = 99.5 + STRAP_CADENCE_STALE_S + 0.1
    assert c.strap_cadence() is None                       # the strap stopped sending


def test_tile_prefers_the_strap(qapp):
    board = LiveBoard.__new__(LiveBoard)                   # just the tile logic, no window
    board.t_cadence = MetricTile("Kadens", "steg/min", "steps")
    board.ctl = _Ctl()
    board._camera_cadence = 233.0
    LiveBoard._cadence_tile(board)
    assert board.t_cadence.title == "Kadens" and board.t_cadence.value == "233"
    board.ctl.cadence_series = [(99.0, 176.0)]
    LiveBoard._cadence_tile(board)
    assert board.t_cadence.title == "Kadens (pulsbandet)" and board.t_cadence.value == "176"
    board.ctl = SimpleNamespace()                          # a controller without strap support
    LiveBoard._cadence_tile(board)
    assert board.t_cadence.title == "Kadens"


def test_cadence_before_the_start_is_not_the_first_minutes(monkeypatch, caplog):
    """In a test on 2026-11-15 the tile showed "about 100" for the first 6 min while the saved values
    were 158-176: the walking before the start kept its times on the old clock, later than "now"."""
    import logging

    from lopanalys.ui import live_controller as lc
    clock = [1000.0]
    monkeypatch.setattr(lc.time, "perf_counter", lambda: clock[0])
    c = LiveController.__new__(LiveController)
    c._t0, c.hr_series, c.cadence_series, c.runner, c._cad_logged = 1000.0, [], [], None, {}
    for i in range(360):                                   # 6 min of walking while the camera is framed
        clock[0] = 1000.0 + i
        c.cadence_series.append((c.elapsed(), 100.0))
    c._restart_clock(clock[0])                             # Starta testet
    assert c.cadence_series[-1][0] == 0.0 and c.cadence_series[0][0] == -359.0
    for i in range(1, 16):
        clock[0] += 1
        c.cadence_series.append((c.elapsed(), 170.0))
    assert c.strap_cadence() == 170.0                      # after 15 s: no longer the median of the walking
    with caplog.at_level(logging.INFO, logger="lopanalys.ui.live_controller"):
        c.log_cadence_tile("tv", 170.0, "strap", "Kadens (pulsbandet)")
        c.log_cadence_tile("tv", 171.0, "strap", "Kadens (pulsbandet)")    # within the minute: not again
        c.log_cadence_tile("operator", 170.0, "strap", "Kadens (pulsbandet)")
    lines = [r.getMessage() for r in caplog.records if "cadence tile" in r.getMessage()]
    assert len(lines) == 2 and "cadence tile tv at 15 s: 170 steps/min, source strap, title 'Kadens (pulsbandet)'" in lines[0]
