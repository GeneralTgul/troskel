"""The guide to a workout (library.TIPS, library.DRILLS, library.feel): how it should feel, tips and drills."""
from __future__ import annotations

import json
from pathlib import Path

from lopanalys.core.i18n import set_language
from lopanalys.plan import library, text
from lopanalys.plan.models import Part

SV = json.loads((Path(__file__).resolve().parents[2] / "src/lopanalys/i18n/plan.sv.json").read_text(encoding="utf-8"))


def test_tips_and_drills_belong_to_templates_and_every_text_is_used():
    assert set(library.TIPS) <= set(library.TEMPLATES)
    assert set(library.DRILLS) <= set(library.TEMPLATES)
    used_tips = {k for v in library.TIPS.values() for k in v}
    used_drills = {k for v in library.DRILLS.values() for k in v}
    assert {k.removeprefix("plan.tip.") for k in SV if k.startswith("plan.tip.")} == used_tips
    assert {k.removeprefix("plan.drill.") for k in SV if k.startswith("plan.drill.")} == used_drills
    assert len(library.DRILLS["swim_technique"]) == 10


def test_feel_is_the_work_not_the_warm_up():
    threshold = library.build("threshold", step=0)
    assert library.feel(threshold) == ("Z4", "Z4", 7, 7)
    assert library.feel(library.build("easy", minutes=40)) == ("Z2", "Z2", 3, 3)
    assert library.feel(library.build("long_progressive", minutes=90)) == ("Z2", "Z3", 4, 6)
    assert library.feel(library.build("strength_full")) == (None, None, 7, 7)
    assert library.feel(library.build("race")) is None
    assert library.feel((Part("warmup", 600, "Z2", 3),)) == ("Z2", "Z2", 3, 3)


def test_the_texts():
    from lopanalys.plan import demo
    plan = demo.demo_plan(12)
    by = {w.template_id: w for w in plan.workouts}
    set_language("sv")
    try:
        assert text.feel_line(by["easy"]) == "Z2, ansträngning 3 av 10"
        assert text.tips(by["easy"])[1] == "Hellre för lugnt än för fort."
        assert text.feel_line(next(w for w in plan.workouts if w.goal)) == ""
        set_language("en")
        assert text.feel_line(by["easy"]) == "Z2, effort 3 of 10"
    finally:
        set_language("sv")
