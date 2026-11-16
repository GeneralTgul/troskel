"""Writes the app's files that come from the Python side: the example plan, the test fixtures and the plan texts.

    python app/scripts/make_fixtures.py            # write them
    python app/scripts/make_fixtures.py --check    # exit 1 when a file is out of date (tests/plan/test_link.py)

The example plan is the demo runner of ``lopanalys.plan.demo`` (12 weeks, a half marathon), never a real person.

``engine.json.gz`` is what the app's TypeScript engine (app/src/engine) is checked against: plans that
``build_plan`` and ``roll_forward`` make from demo requests, and ``rebuild`` on the demo plans with new times.
``rebuild`` here is the rule the app's ``rebuild`` follows, written with the Python engine's own functions.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language, t
from lopanalys.plan import codec, demo, ics, link
from lopanalys.plan.engine import ENGINE_VERSION, build_plan, sketch_of
from lopanalys.plan.models import Capacity, Goal, PlanRequest, SportRole
from lopanalys.plan.rolling import roll_forward

APP = Path(__file__).resolve().parents[1]
I18N = APP.parents[0] / "src" / "lopanalys" / "i18n"
# The texts the app needs from the programme: the plan's own, and the zone names of the default system.
EXTRA_KEYS = tuple(f"assessments.zones.garmin.Z{i}.{f}" for i in range(1, 6) for f in ("name", "purpose"))


def _texts(lang: str) -> bytes:
    plan = json.loads((I18N / f"plan.{lang}.json").read_text(encoding="utf-8"))
    other = json.loads((I18N / f"assessments.{lang}.json").read_text(encoding="utf-8"))
    out = {**plan, **{k: other[k] for k in EXTRA_KEYS}}
    return (json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def rebuild(stored: dict[str, Any], slots: list[dict[str, Any]], unavailable: list[str], from_day: str) -> dict[str, Any]:
    """The stored plan with new times from ``from_day`` on: what ``rebuild`` in app/src/engine/index.ts does.

    The request gets the new slots and the engine builds the whole plan again (same id, same ``created``),
    so the blocks stay and the weekly time follows what the new slots hold. A week that ends before
    ``from_day`` is kept as it was saved; in the week of ``from_day`` the workouts before it are kept (with
    their status) and the rest are new. The horizon of a rolling plan stays: the weeks after it are sketches
    of the new plan. On the dates in ``unavailable`` the workouts are dropped (races stay). Raises ValueError
    when the engine cannot make a plan."""
    day = date.fromisoformat(from_day)
    off = {date.fromisoformat(d) for d in unavailable}
    request = codec.request_from_dict({**stored["request"], "availability": slots}, stored["athlete"])
    old = codec.plan_from_dict(stored["plan"], codec.request_from_dict(stored["request"], stored["athlete"]))
    fresh = build_plan(request, plan_id=old.id, created=old.created)
    assert len(fresh.weeks) == len(old.weeks)
    weeks = []
    for was, new in zip(old.weeks, fresh.weeks):
        if was.sunday < day:
            weeks.append(was)
        elif old.horizon is not None and new.index > old.horizon:
            weeks.append(sketch_of(new))
        else:
            kept = [w for w in was.workouts if w.date < day]
            made = [w for w in new.workouts if w.date >= day and (w.goal is not None or w.date not in off)]
            weeks.append(replace(new, workouts=sorted(kept + made, key=lambda w: (w.date, w.key))))
    plan = replace(old, request=request, blocks=fresh.blocks, notes=fresh.notes, weeks=weeks,
                   engine_version=ENGINE_VERSION)
    return {**stored, "request": codec.request_to_dict(request), "plan": codec.plan_to_dict(plan)}


def _slot(weekday: int, minutes: int, start: str | None = "18:00:00", role: str = "core",
          sports: tuple[str, ...] = ("run",)) -> dict[str, Any]:
    return {"weekday": weekday, "max_minutes": minutes, "sports": list(sports), "start": start, "role": role}


def _slots(*rows: tuple) -> list[dict[str, Any]]:
    return [_slot(*r) for r in rows]


DEMO_SLOTS = ((0, 45), (1, 75), (3, 75), (5, 60, "09:00:00"), (6, 120, "09:00:00"))


def _requests() -> list[tuple[str, PlanRequest, date | None]]:
    """The requests the build fixtures are made from: (name, request, today)."""
    out: list[tuple[str, PlanRequest, date | None]] = []
    for level in ("beginner", "recreational", "trained"):
        for weeks in (5, 8, 12, 20):
            for goal in (True, False):
                out.append((f"{level}-{weeks}-{'goal' if goal else 'cycle'}", demo.demo_request(weeks, goal=goal, level=level), None))
    base = demo.demo_request(12)
    sl = lambda rows: tuple(codec.request_from_dict({**codec.request_to_dict(base), "availability": _slots(*rows)}).availability)
    out += [
        ("three-slots", replace(base, availability=sl(((1, 60), (3, 60), (6, 100)))), None),
        ("seven-slots", replace(base, availability=sl(tuple((d, 70) for d in range(7)))), None),
        ("short-slots", replace(base, availability=sl(tuple((d, 30) for d in (0, 2, 4, 6)))), None),
        ("two-slots", replace(base, availability=sl(((2, 50), (5, 90)))), None),
        ("extra-and-bike", replace(base, availability=sl(DEMO_SLOTS + ((2, 40, None, "extra"), (4, 60, None, "core", ("bike",)),
                                                                        (4, 30, "07:00:00"), (4, 50, "12:00:00")))), None),
        ("no-test", replace(base, capacity=()), None),
        ("max-hr-only", replace(base, capacity=(Capacity("run", Anchors(max_hr=190), date(2026, 11, 1), "Maxpuls"),)), None),
        ("no-recent", replace(base, recent_weekly_minutes=None), None),
        ("recent-above-cap", replace(base, recent_weekly_minutes=900), None),
        ("mid-week-start", replace(base, start=date(2026, 11, 19)), None),
        ("long-goal", replace(base, goals=(Goal("Marathon", date(2027, 3, 28), distance_km=42.2),)), None),
        ("c-and-b-races", replace(base, goals=base.goals + (Goal("Parkrun", date(2026, 12, 12), distance_km=5, priority="C"),
                                                             Goal("Utanför", date(2027, 6, 1), distance_km=5, priority="B"))), None),
        ("rolling-start", base, date(2026, 11, 16)),
        ("rolling-week-6", demo.demo_request(20), date(2026, 12, 23)),
        ("rolling-before", demo.demo_request(20), date(2026, 10, 1)),
        ("rolling-late", demo.demo_request(20), date(2027, 6, 1)),
    ]
    # Several sports (#666): the roles, the shares, a slot for one sport only, a borrowed test and a bike race.
    rb = ("run", "bike")
    four = ("run", "bike", "ski", "swim")
    roles4 = (SportRole("run", "main"), SportRole("bike", "support"), SportRole("ski", "support", 20),
              SportRole("swim", "maintenance"))
    skierg = Capacity("skierg", Anchors(188, 48, 150, 168, 160.0, 210.0, sport="skierg"), date(2026, 11, 1), "Laktattrappa")
    multi = sl(tuple((*r, None, "core", rb) for r in ((0, 45), (1, 75), (3, 75), (5, 60), (6, 120))))
    out += [
        ("run-bike", replace(base, availability=multi, roles=(SportRole("run", "main"), SportRole("bike", "support"))), None),
        ("sports-without-roles", replace(base, availability=multi, sports=rb), None),
        ("bike-main", replace(base, availability=multi, roles=(SportRole("bike", "main", 70), SportRole("run", "support", 30))),
         None),
        ("bike-only-wednesday", replace(base, availability=sl(DEMO_SLOTS + ((2, 60, None, "core", ("bike",)),)),
                                        roles=(SportRole("run", "main"), SportRole("bike", "support"))), None),
        ("four-sports", replace(base, availability=sl(tuple((d, 70 if d < 5 else 150, None, "core", four) for d in range(7))),
                                level="trained", recent_weekly_minutes=None, roles=roles4, capacity=base.capacity + (skierg,)), None),
        ("bike-race", replace(base, availability=multi, roles=(SportRole("run", "main"), SportRole("bike", "support")),
                              goals=base.goals + (Goal("Cykellopp", date(2026, 12, 12), sport="bike", priority="B"),)), None),
        ("four-sports-rolling", replace(base, availability=sl(tuple((d, 60, None, "core", four) for d in range(7))),
                                        roles=roles4), date(2026, 12, 2)),
    ]
    return out


ERRORS = (  # (name, request, the text key of the error and its values)
    ("no-slots", lambda b: replace(b, availability=()), "plan.error.no_slots", {}),
    ("goal-too-close", lambda b: replace(b, goals=(Goal("Snart", date(2026, 11, 20)),)), "plan.error.goal_too_close",
     {"name": "Snart"}),
    ("too-short", lambda b: replace(b, goals=(), duration_weeks=3), "plan.error.too_short", {"weeks": 4}),
    ("unknown-level", lambda b: replace(b, level="elite"), "plan.error.level", {"level": "elite"}),
)


def _error_case(name: str, request: PlanRequest, key: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        build_plan(request, plan_id="demo")
    except ValueError as e:
        assert str(e) == t(key, **args), (name, str(e))
        return {"name": name, "request": codec.request_to_dict(request), "plan_id": "demo", "today": None,
                "error": {"key": key, "args": args}}
    raise AssertionError(f"{name}: no error")


def engine_fixtures() -> dict[str, Any]:
    build = []
    for name, request, today in _requests():
        plan = build_plan(request, plan_id="demo", today=today)
        build.append({"name": name, "request": codec.request_to_dict(request), "plan_id": "demo",
                      "today": today.isoformat() if today else None, "plan": codec.plan_to_dict(plan)})
    base = demo.demo_request(12)
    build += [_error_case(n, f(base), k, a) for n, f, k, a in ERRORS]

    roll = []
    rolling = build_plan(demo.demo_request(20), plan_id="demo", today=date(2026, 11, 18))
    for today in ("2026-11-10", "2026-11-25", "2026-12-08", "2027-01-20", "2027-05-01"):
        out = roll_forward(rolling, date.fromisoformat(today))
        roll.append({"name": f"roll-{today}", "today": today, "plan": codec.plan_to_dict(out)})

    plans = {"demo": link.stored_plan(demo.demo_plan(12)),
             "rolling": link.stored_plan(rolling),
             "beginner": link.stored_plan(demo.demo_plan(8, level="beginner")),
             "trained-cycle": link.stored_plan(demo.demo_plan(12, goal=False, level="trained"))}
    done = json.loads(json.dumps(plans["demo"]))
    for wk in done["plan"]["weeks"]:
        for w in wk["workouts"]:
            if w["date"] < "2026-11-26":
                w["status"] = "done" if w["date"] != "2026-11-19" else "missed"
    plans["demo-done"] = done
    run_bike = next(r for n, r, _ in _requests() if n == "run-bike")
    plans["run-bike"] = link.stored_plan(build_plan(run_bike, plan_id="demo"))
    demo_slots = _slots(*DEMO_SLOTS)
    changes = [  # (name, plan, slots, unavailable, from)
        ("shorter-thursday", "demo", _slots(*DEMO_SLOTS[:2], (3, 45), *DEMO_SLOTS[3:]), [], "2026-11-26"),
        ("no-tuesday", "demo", _slots(DEMO_SLOTS[0], *DEMO_SLOTS[2:]), [], "2026-11-24"),
        ("add-wednesday", "demo", _slots(*DEMO_SLOTS, (2, 60)), [], "2026-11-17"),
        ("long-on-saturday", "demo", _slots(*DEMO_SLOTS[:3], (5, 120, "09:00:00"), (6, 60, "09:00:00")), [], "2026-11-30"),
        ("new-clock-time", "demo", _slots((0, 45, "06:30:00"), *DEMO_SLOTS[1:]), [], "2026-11-17"),
        ("any-time", "demo", _slots(*((d, m, None) for d, m, *_ in DEMO_SLOTS)), [], "2026-12-01"),
        ("days-off", "demo", demo_slots, ["2026-11-26", "2026-12-06", "2027-02-06", "2026-11-20"], "2026-11-24"),
        ("days-off-and-fewer", "demo", _slots(*DEMO_SLOTS[1:]), ["2026-12-15", "2026-12-17"], "2026-12-08"),
        ("from-before-start", "demo", _slots(*DEMO_SLOTS[:4], (6, 90, "09:00:00")), [], "2026-11-10"),
        ("from-after-end", "demo", _slots(*DEMO_SLOTS[1:]), [], "2027-03-01"),
        ("from-sunday", "demo", _slots(*DEMO_SLOTS[:4], (6, 100, "09:00:00")), [], "2026-12-20"),
        ("more-time", "demo", _slots(*((d, m + 30, *r) for d, m, *r in DEMO_SLOTS)), [], "2026-11-19"),
        ("with-extra", "demo", demo_slots + [_slot(2, 40, None, "extra")], [], "2026-11-17"),
        ("done-kept", "demo-done", _slots(*DEMO_SLOTS[:3], (4, 60), *DEMO_SLOTS[3:]), [], "2026-11-26"),
        ("rolling-shorter", "rolling", _slots(*DEMO_SLOTS[:4], (6, 80, "09:00:00")), ["2026-12-05"], "2026-11-21"),
        ("rolling-after-horizon", "rolling", _slots(*DEMO_SLOTS[1:]), [], "2027-01-11"),
        ("rolling-more", "rolling", _slots(*DEMO_SLOTS, (2, 45), (4, 45)), [], "2026-11-19"),
        ("beginner-add-day", "beginner", _slots(*DEMO_SLOTS, (2, 30)), [], "2026-11-25"),
        ("beginner-two-days", "beginner", _slots((1, 40), (5, 60)), [], "2026-11-17"),
        ("run-bike-fewer", "run-bike", _slots((1, 75, None, "core", ("run", "bike")), (3, 75, None, "core", ("bike",)),
                                              (6, 120, None, "core", ("run", "bike"))), ["2026-12-10"], "2026-11-30"),
        ("trained-cycle-fewer", "trained-cycle", _slots(*DEMO_SLOTS[:1], *DEMO_SLOTS[2:4]), ["2026-12-03"], "2026-11-30"),
    ]
    rebuilds = []
    for name, plan, slots, off, from_day in changes:
        rebuilds.append({"name": name, "plan": plan, "slots": slots, "unavailable": off, "from": from_day,
                         "result": rebuild(plans[plan], slots, off, from_day)})
    for name, slots in (("rebuild-no-slots", []), ("rebuild-only-extra", [_slot(1, 60, None, "extra")]),
                        ("rebuild-only-bike", [_slot(1, 60, None, "core", ("bike",))])):
        try:
            rebuild(plans["demo"], slots, [], "2026-11-24")
        except ValueError:
            rebuilds.append({"name": name, "plan": "demo", "slots": slots, "unavailable": [], "from": "2026-11-24",
                             "error": {"key": "plan.error.no_slots", "args": {}}})
        else:
            raise AssertionError(name)
    return {"engine_version": ENGINE_VERSION, "build": build, "roll": {"plan": codec.plan_to_dict(rolling), "request":
            codec.request_to_dict(rolling.request), "cases": roll}, "plans": plans, "rebuild": rebuilds}


def _gz(data: Any) -> bytes:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0)


def outputs() -> dict[Path, bytes]:
    set_language("sv")
    plan = demo.demo_plan(12)
    stored = link.stored_plan(plan)
    out = {APP / "public" / "demo.troskel": link.plan_file(stored),
           APP / "test" / "fixtures" / "demo.link.txt": (link.plan_link(stored) + "\n").encode("ascii"),
           APP / "test" / "fixtures" / "demo.sv.ics": ics.to_ics(plan).encode("utf-8")}
    set_language("en")
    out[APP / "test" / "fixtures" / "demo.en.ics"] = ics.to_ics(plan).encode("utf-8")
    out[APP / "test" / "fixtures" / "engine.json.gz"] = _gz(engine_fixtures())
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
