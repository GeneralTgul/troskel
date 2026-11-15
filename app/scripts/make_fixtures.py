"""Writes the app's files that come from the Python side: the example plan, the test fixtures and the plan texts.

    python app/scripts/make_fixtures.py            # write them
    python app/scripts/make_fixtures.py --check    # exit 1 when a file is out of date (tests/plan/test_link.py)

The example plan is the demo runner of ``lopanalys.plan.demo`` (12 weeks, a half marathon), never a real person.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lopanalys.core.i18n import set_language
from lopanalys.plan import demo, ics, link

APP = Path(__file__).resolve().parents[1]
I18N = APP.parents[0] / "src" / "lopanalys" / "i18n"
# The texts the app needs from the programme: the plan's own, and the zone names of the default system.
EXTRA_KEYS = tuple(f"assessments.zones.garmin.Z{i}.{f}" for i in range(1, 6) for f in ("name", "purpose"))


def _texts(lang: str) -> bytes:
    plan = json.loads((I18N / f"plan.{lang}.json").read_text(encoding="utf-8"))
    other = json.loads((I18N / f"assessments.{lang}.json").read_text(encoding="utf-8"))
    out = {**plan, **{k: other[k] for k in EXTRA_KEYS}}
    return (json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def outputs() -> dict[Path, bytes]:
    set_language("sv")
    plan = demo.demo_plan(12)
    stored = link.stored_plan(plan)
    out = {APP / "public" / "demo.troskel": link.plan_file(stored),
           APP / "test" / "fixtures" / "demo.link.txt": (link.plan_link(stored) + "\n").encode("ascii"),
           APP / "test" / "fixtures" / "demo.sv.ics": ics.to_ics(plan).encode("utf-8")}
    set_language("en")
    out[APP / "test" / "fixtures" / "demo.en.ics"] = ics.to_ics(plan).encode("utf-8")
    set_language("sv")
    for lang in ("sv", "en"):
        out[APP / "src" / "i18n" / f"plan.{lang}.json"] = _texts(lang)
    return out


def stale() -> list[Path]:
    return [p for p, data in outputs().items() if not p.exists() or p.read_bytes() != data]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        bad = stale()
        for p in bad:
            print(f"out of date: {p.relative_to(APP.parent)}")
        return 1 if bad else 0
    for p, data in outputs().items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        print(f"wrote {p.relative_to(APP.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
