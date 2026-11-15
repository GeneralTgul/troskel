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
