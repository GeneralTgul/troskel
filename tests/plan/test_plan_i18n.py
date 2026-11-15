"""Every text of the plan exists in Swedish and English."""
from __future__ import annotations

import json
import re
from pathlib import Path

from lopanalys.plan import library
from lopanalys.plan.models import BLOCK_KINDS, LEVELS, PRIORITIES, WEEK_KINDS, WEEKDAY_KEYS

ROOT = Path(__file__).resolve().parents[2] / "src/lopanalys"
SV = json.loads((ROOT / "i18n/plan.sv.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "i18n/plan.en.json").read_text(encoding="utf-8"))


def test_files_have_the_same_keys_and_placeholders():
    assert SV.keys() == EN.keys()
    for k in SV:
        assert SV[k].strip() and EN[k].strip(), k
        assert set(re.findall(r"{(\w+)}", SV[k])) == set(re.findall(r"{(\w+)}", EN[k])), k


def test_every_key_in_code_and_templates_exists():
    files = [*(ROOT / "plan").glob("*.py"), ROOT / "report/plan_builder.py", *(ROOT / "report/templates").glob("plan*")]
    used = set()
    for f in files:
        used |= set(re.findall(r"""["'](plan\.[a-z0-9_.]+[a-z0-9_])["']""", f.read_text(encoding="utf-8")))
    used = {k for k in used if not k.endswith((".html", ".css", ".j2", ".ics", ".pdf", ".demo"))}
    missing = sorted(k for k in used if k not in SV)
    assert not missing, missing


def test_keys_built_at_run_time_exist():
    keys = []
    for tid in library.TEMPLATES:
        keys += [f"plan.workout.{tid}.{x}" for x in ("title", "purpose", "how")]
    for b in BLOCK_KINDS:
        keys += [f"plan.block.{b}", f"plan.block_short.{b}", f"plan.reason.{b}"]
    keys += ["plan.reason.base_cycle", "plan.reason.build_cycle"]
    keys += [f"plan.week.kind.{k}" for k in WEEK_KINDS]
    keys += [f"plan.week.focus.{k}" for k in ("base", "build", "peak", "easy", "test", "taper", "race")]
    keys += [f"plan.week.race_{p.lower()}" for p in PRIORITIES] + [f"plan.pdf.fact.goal_{p.lower()}" for p in PRIORITIES]
    keys += [f"plan.level.{x}" for x in LEVELS] + [f"plan.weekday.{d}" for d in WEEKDAY_KEYS]
    keys += [f"plan.month.{m}" for m in range(1, 13)] + [f"plan.pdf.rule.{i}" for i in range(1, 7)]
    keys += [f"plan.part.{k}" for k in ("warmup", "steady", "work", "cooldown", "test", "race")]
    missing = sorted(k for k in keys if k not in SV)
    assert not missing, missing


def test_no_unused_keys():
    text = "".join(f.read_text(encoding="utf-8") for f in [*(ROOT / "plan").glob("*.py"), ROOT / "report/plan_builder.py",
                                                            *(ROOT / "report/templates").glob("plan*")])
    prefixes = ("plan.workout.", "plan.block", "plan.reason.", "plan.week.", "plan.level.", "plan.weekday.", "plan.month.",
                "plan.pdf.rule.", "plan.part.", "plan.pdf.fact.goal_")
    unused = sorted(k for k in SV if k not in text and not k.startswith(prefixes))
    assert not unused, unused
