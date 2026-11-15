"""The manual part of the bike fit: what is stored, what the report shows and what it means for the fit."""
from __future__ import annotations

from lopanalys.pose.bike import checklist as ck


def test_clean_keeps_only_known_fields_with_values():
    v = ck.clean({"goal": "  Vätternrundan ", "hamstring.left": "limited", "hamstring.right": "nonsense",
                  "saddle_height_mm.before": "742", "saddle_height_mm.after": "748,5", "insoles": True,
                  "ball_over_axle.left": False, "unknown": "x", "hamstring": "good", "shoe_size": "abc"})
    assert v == {"goal": "Vätternrundan", "hamstring.left": "limited", "saddle_height_mm.before": 742.0,
                 "saddle_height_mm.after": 748.5, "insoles": True}


def test_rows_for_the_report():
    rows = ck.rows({"goal": "Vätternrundan", "hamstring.left": "limited", "hamstring.right": "good",
                    "saddle_height_mm.before": 742, "saddle_height_mm.after": 748, "leg_length_diff_mm": 6,
                    "ball_over_axle.left": True, "ball_over_axle.right": True})
    by = {s["key"]: {r["key"]: r["value"] for r in s["rows"]} for s in rows}
    assert [s["key"] for s in rows] == ["interview", "mobility", "legs", "cleats", "bike"]     # only filled sections
    assert by["mobility"]["hamstring"] == "vänster Begränsad · höger God"
    assert by["bike"]["saddle_height_mm"] == "742 mm → 748 mm (+6 mm)"
    assert by["cleats"]["ball_over_axle"] == "vänster Ja · höger Ja"
    assert ck.rows({}) == [] and ck.rows(None) == []


def test_progress_counts_items_not_fields():
    done, total = ck.progress({"hamstring.left": "good", "goal": "x"})
    assert (done, total) == (2, len(ck.ITEMS)) and total >= 25


def test_notes_read_the_checklist_together_with_the_film():
    notes = ck.notes({"leg_length_diff_mm": 7, "hamstring.left": "limited", "arch.left": "low",
                      "saddle_height_mm.before": 720, "saddle_height_mm.after": 738},
                     {"trunk_angle_deg": 48.0, "knee_direction": "in"})
    text = " ".join(notes)
    assert "Benlängdsskillnad 7 mm" in text and "lågt styre" in text and "inlägg" in text and "18 mm" in text
    assert ck.notes({"leg_length_diff_mm": 3}, {}) == []        # small differences are not commented


def test_every_item_and_option_has_a_text_in_both_languages():
    import json
    from pathlib import Path

    import lopanalys
    d = Path(lopanalys.__file__).parent / "i18n"
    sv = json.loads((d / "technique.sv.json").read_text(encoding="utf-8"))
    en = json.loads((d / "technique.en.json").read_text(encoding="utf-8"))
    assert set(sv) == set(en)
    for section, items in ck.SECTIONS:
        assert f"{ck.PREFIX}.section.{section}" in sv
        for item in items:
            assert f"{ck.PREFIX}.item.{item.key}" in sv, item.key
            for o in item.options:
                assert f"{ck.PREFIX}.opt.{o}" in sv, o
