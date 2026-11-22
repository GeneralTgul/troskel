"""Writes app/test/fixtures/choices.json.gz: the coach's choices (PlanRequest.quality_sessions, easy_days,
second_long, easy_weeks, max_weekly_minutes and a fixed day's minutes) for the app's engine, checked by app/test/engine_choices.test.ts.

    python -m tests.plan.choices_fixtures            # write it
    python -m tests.plan.choices_fixtures --check    # exit 1 when it is out of date (tests/plan/test_choices.py)

``build`` is requests with choices and the plans ``build_plan`` makes of them, ``roll`` rolling plans detailed later
(``roll_forward``), ``rebuild`` plans with new times (``phone.rebuild``), and ``short`` the short link's payload of
every build case (``link._pack``, format 3; ``link.short_plan`` gives it once the guard ``link.APP_LACKS`` is off), which expands to the whole payload under the same name in
``plans``: for the app's reader of the short link (app/test/short_choices.test.ts). The requests are the made-up
ones of tests/plan/cases.py.
"""
from __future__ import annotations

import gzip
import json
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from lopanalys.core.i18n import set_language
from lopanalys.plan import HardDay, build_plan, codec, link, phone
from lopanalys.plan.engine import ENGINE_VERSION
from lopanalys.plan.rolling import roll_forward

from . import cases as c

PATH = Path(__file__).resolve().parents[2] / "app" / "test" / "fixtures" / "choices.json.gz"
ROLL_DAYS = (c.START + timedelta(weeks=3), c.START + timedelta(weeks=7, days=2), date(2027, 6, 1))


def requests() -> list[tuple[str, Any, date | None]]:
    """(name, request, today): the cases with every choice, and each choice alone on simpler requests."""
    half = c.CASES["half_12"]
    ski = c.CASES["high_ski"]
    out = [(name, c.CASES[name], None) for name in ("high_ski_choices", "high_ski_choices_low", "run_bike_choices",
                                                    "three_quality_cycle")]
    out += [
        ("ski-rolling", c.CASES["high_ski_choices"], c.START + timedelta(days=9)),
        ("run-bike-rolling", c.CASES["run_bike_choices"], c.START),
        ("quality-two-recreational", replace(half, quality_sessions=2), None),
        ("quality-three-beginner", c.request(c.SEVEN, weeks=12, level="beginner", recent=None, quality=3), None),
        ("quality-over-the-most", replace(c.CASES["marathon_trained"], quality_sessions=7), None),
        ("group-length-only", replace(ski, hard_days=(HardDay(3, "ski", 105), HardDay(0, c.RUNNING, 90))), None),
        ("group-longer-than-the-slot", replace(half, hard_days=(HardDay(1, minutes=120),)), None),
        ("easy-day-only", replace(ski, easy_days=(HardDay(1, "ski", 105), HardDay(4, "", 0))), None),
        ("easy-day-other-sport", replace(c.CASES["run_bike_12"], easy_days=(HardDay(3, "swim", 60), HardDay(0, "bike"))), None),
        ("easy-day-on-a-hard-day", replace(c.CASES["half_hard_tuesday"], easy_days=(HardDay(1, minutes=75), HardDay(9))), None),
        ("second-long-only", replace(ski, second_long=True), None),
        ("second-long-one-sport", replace(half, second_long=True), None),
        ("second-long-triathlon", replace(c.CASES["triathlon_swim"], second_long=True), None),
        ("second-long-on-a-hard-day", replace(c.CASES["run_bike_choices"], hard_days=(HardDay(0, c.RUNNING),)), None),
        ("second-long-on-an-easy-day", replace(c.CASES["run_bike_choices"], easy_days=(HardDay(0, "bike", 45),)), None),
        ("easy-weeks-goal", replace(half, easy_weeks=(date(2026, 12, 23), date(2026, 11, 1), date(2027, 2, 1))), None),
        ("easy-weeks-beginner", replace(c.CASES["beginner_26"], easy_weeks=(date(2026, 12, 21), date(2027, 2, 15))), None),
        ("easy-weeks-first-and-last", c.request(weeks=12, easy_weeks=(c.START, date(2027, 2, 1), date(2028, 1, 1))), None),
        ("easy-weeks-milestone", replace(c.CASES["half_b_recovery"], easy_weeks=(date(2027, 1, 11),)), None),
        ("most-minutes-only", replace(c.CASES["marathon_trained"], max_weekly_minutes=420), None),
        ("most-minutes-below-now", replace(half, max_weekly_minutes=150), None),
        ("most-minutes-tiny", replace(half, max_weekly_minutes=10, recent_weekly_minutes=None), None),
        ("most-minutes-above-the-slots", replace(half, max_weekly_minutes=5000), None),
    ]
    return out


def data() -> dict[str, Any]:
    set_language("sv")
    build, short, roll, rebuilds = [], {}, [], []
    stored: dict[str, Any] = {}
    for name, request, today in requests():
        plan = build_plan(request, plan_id="choice", today=today)
        whole = link.stored_plan(plan)
        stored[name] = whole
        build.append({"name": name, "request": codec.request_to_dict(request), "plan_id": "choice",
                      "today": today.isoformat() if today else None, "plan": codec.plan_to_dict(plan)})
        assert link.short_plan(whole) is None or not link.APP_LACKS, name
        packed = link._pack(whole)
        assert packed["format"] == link.CHOICES_FORMAT, name
        assert link.expand(json.loads(json.dumps(packed))) == json.loads(json.dumps(whole)), name
        short[name] = packed
        if today is not None:
            for day in ROLL_DAYS:
                rolled = roll_forward(plan, day)
                roll.append({"name": f"{name}-{day.isoformat()}", "request": codec.request_to_dict(request),
                             "plan": codec.plan_to_dict(plan), "today": day.isoformat(),
                             "result": codec.plan_to_dict(rolled)})
    slot = lambda d, m, sports, start=None: {"weekday": d, "max_minutes": m, "sports": list(sports), "start": start,
                                             "role": "core"}
    changes = [
        ("ski-fewer-days", "high_ski_choices", [slot(d, 270 if d == 5 else 100, ("ski", "run"), "17:00:00")
                                                for d in (0, 1, 3, 5, 6)] + [slot(2, 45, ("bike", "swim"), "12:00:00")],
         ["2026-12-10"], "2026-11-30"),
        ("ski-rolling-no-tuesday", "ski-rolling", [slot(d, 270 if d == 5 else 105, ("ski", "run"), "17:00:00")
                                                   for d in (0, 2, 3, 4, 5, 6)], [], "2026-11-26"),
        ("run-bike-long-on-saturday", "run_bike_choices", [slot(d, 180 if d == 5 else 90, ("run", "bike"))
                                                           for d in range(7)], [], "2026-12-01"),
    ]
    for name, plan, slots, off, from_day in changes:
        rebuilds.append({"name": name, "plan": plan, "slots": slots, "unavailable": off, "from": from_day,
                         "result": phone.rebuild(stored[plan], slots, off, from_day)})
    return {"engine_version": ENGINE_VERSION, "build": build, "roll": roll, "plans": stored, "rebuild": rebuilds,
            "short": short}


def content() -> bytes:
    raw = json.dumps(data(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0)


def stale() -> bool:
    return not PATH.exists() or PATH.read_bytes() != content()


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        sys.exit(1 if stale() else 0)
    PATH.write_bytes(content())
    print(f"wrote {PATH}")
