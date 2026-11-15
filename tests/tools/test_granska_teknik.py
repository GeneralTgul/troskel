"""The review tool on bike fit and SkiErg films (synthetic athletes: no camera, no pose model)."""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest
from PIL import Image

from lopanalys.core import i18n

TOOL = Path(__file__).resolve().parents[2] / "tools" / "granska_analys.py"


@pytest.fixture
def tool():
    i18n.set_language("sv")
    spec = importlib.util.spec_from_file_location("granska_analys", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("sport,sheets,column,rows", [("bike", ("bottoms.png", "tops.png"), "knee_flex_bottom_deg", 8),
                                                     ("ski", ("starts.png", "finishes.png"), "trunk_lean_end_deg", 4)])
def test_review_of_a_synthetic_film(tool, tmp_path, sport, sheets, column, rows):
    out = tmp_path / "granskning"
    tool.main(["--sport", sport, "--synthetic", "--height", "178", "--out", str(out), "--contacts", "3",
               "--around", "2", "--seconds", "16"])
    with (out / "cycles.csv").open(encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert len(got) >= rows and float(got[0][column]) > 0
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "quality: good" in summary and "no verdict" in summary and "+-5.0" in summary
    assert ("advice:\n  saddle_height keep" in summary) == (sport == "bike")
    for name in sheets:
        im = Image.open(out / name)
        assert im.height == 3 * tool.THUMB_H and im.width > 5 * 50      # three events, five frames each


def test_synthetic_needs_a_sport_and_a_folder(tool):
    with pytest.raises(SystemExit):
        tool.main(["--synthetic", "--height", "178"])
