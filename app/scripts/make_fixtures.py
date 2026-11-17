"""Writes the app's files that come from the Python side: the example plan, the test fixtures and the plan texts.

    python app/scripts/make_fixtures.py            # write them
    python app/scripts/make_fixtures.py --check    # exit 1 when a file is out of date (tests/plan/test_link.py)

The example plan is the demo runner of ``lopanalys.plan.demo`` (12 weeks, a half marathon), never a real person.

``engine.json.gz`` is what the app's TypeScript engine (app/src/engine) is checked against: plans that
``build_plan`` and ``roll_forward`` make from demo requests, and ``rebuild`` on the demo plans with new times.
``rebuild`` (``lopanalys.plan.phone.rebuild``) is the rule the app's ``rebuild`` follows, written with the Python engine's own functions.
``texts`` is what the app shows of a workout in another sport (title, parts and their watt, time per 500 m or per
100 m, how it should feel, tips and drills), written by text.py and resolve.py; ``guide`` is library.TIPS and
library.DRILLS, which app/src/engine/library.ts has too.
``links`` is the short link's data (link.short_plan, format 2) of every plan above that has one: the app builds each
of them with its engine and gets the stored plan back. ``demo.link.txt`` is the demo plan's link as Tröskel sends
it (the short one) and ``demo.full.link.txt`` the link with the whole plan (format 1).
``request.json.gz`` is the athlete's own plan (#777): forms of the app's *Gör ett schema själv* and the plans Tröskel builds
from the same answers (``form_request``), checked by app/test/request.test.tsx.
``zones.json.gz`` is the zones' targets the app works out itself for the athlete's own values (#777, app/src/engine/zones.ts):
capacities and the targets ``link.zone_targets`` gives them, the targets per sport of requests (``link._targets``), the
capacity of typed-in values (``values_capacity``) and a plan laid on new own values (``with_values``), checked by
app/test/values.test.ts.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from dataclasses import replace
from datetime import date, time
from pathlib import Path
from typing import Any

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language, t
from lopanalys.plan import codec, demo, ics, library, link, phone, text
from lopanalys.plan.engine import ENGINE_VERSION, build_plan, sketch_of
from lopanalys.plan.models import (BORROWS, LEVELS, PLAN_SPORTS, RUNNING, SELF, Capacity, Goal, HardDay, PlanRequest,
                                   Slot, SportRole)
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


rebuild = phone.rebuild        # the rule the app's rebuild follows, in lopanalys.plan.phone (Tröskel uses it too)


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
    # The bike's and the SkiErg's own workouts (#666, step 2): with and without their tests, one sport alone, and skis.
    erg = ("run", "bike", "skierg")
    out += [
        ("bike-skierg-tested", replace(base, availability=sl(tuple((r[0], r[1], None, "core", erg) for r in DEMO_SLOTS)),
                                       capacity=base.capacity + (BIKE_TEST, skierg),
                                       roles=(SportRole("run", "main"), SportRole("bike", "support"),
                                              SportRole("skierg", "support", 20))), None),
        ("bike-alone", replace(base, availability=sl(tuple((r[0], r[1], None, "core", ("bike",)) for r in DEMO_SLOTS)),
                               capacity=(BIKE_TEST,), roles=(SportRole("bike"),), level="trained",
                               goals=(Goal("Cykellopp", date(2027, 2, 13), sport="bike", distance_km=90),)), None),
        ("ski-main", replace(base, availability=sl(tuple((d, 60 if d < 5 else 150, None, "core", ("ski", "skierg", "run"))
                                                         for d in range(7))),
                             capacity=base.capacity + (skierg,), level="trained", recent_weekly_minutes=None,
                             roles=(SportRole("ski", "main"), SportRole("skierg", "support"), SportRole("run", "maintenance")),
                             goals=(Goal("Skidlopp", date(2027, 3, 7), sport="ski", distance_km=45),)), None),
    ]
    # Strength and swimming (#666, step 3): strength on the running days and in slots of its own, a race week, and a
    # triathlon with swimming on a CSS test.
    tri = ("run", "bike", "swim")
    strength_slots = sl(DEMO_SLOTS + tuple((d, 45, "07:00:00", "core", ("strength",)) for d in (0, 2, 4)))
    out += [
        ("run-strength", replace(base, strength_sessions=2), None),
        ("strength-slots", replace(base, availability=strength_slots, strength_sessions=3), None),
        ("strength-one-cycle", replace(demo.demo_request(12, goal=False), strength_sessions=1), None),
        ("strength-rolling", replace(demo.demo_request(20), strength_sessions=2), date(2026, 12, 23)),
        ("triathlon-css", replace(base, availability=sl(tuple((d, 70 if d < 5 else 150, None, "core", tri) for d in range(7))),
                                  capacity=base.capacity + (BIKE_TEST, SWIM_TEST), level="trained", recent_weekly_minutes=None,
                                  roles=(SportRole("run", "main", 40), SportRole("bike", "support", 35),
                                         SportRole("swim", "support", 25)), strength_sessions=1), None),
        ("swim-without-test", replace(base, availability=sl(tuple((d, 60, None, "core", ("run", "swim")) for d in range(7))),
                                      roles=(SportRole("run", "main"), SportRole("swim", "support"))), None),
    ]
    # Each sport's own test (#666, the rest of step 2): plans without a goal end with it. The bike alone (with the
    # sweet spot as a band of its threshold power), swimming alone on its CSS test, and skis, which test on the SkiErg.
    one = lambda sport: sl(tuple((r[0], r[1], None, "core", (sport,)) for r in DEMO_SLOTS))
    cycle = replace(base, goals=(), duration_weeks=12)
    out += [
        ("bike-alone-cycle", replace(cycle, availability=one("bike"), capacity=(BIKE_TEST,), roles=(SportRole("bike"),)), None),
        ("swim-alone-cycle", replace(cycle, availability=one("swim"), capacity=(SWIM_TEST,), roles=(SportRole("swim"),)), None),
        ("ski-cycle", replace(cycle, availability=sl(tuple((r[0], r[1], None, "core", ("ski", "skierg")) for r in DEMO_SLOTS)),
                              capacity=(skierg,), roles=(SportRole("ski", "main"), SportRole("skierg", "support"))), None),
        ("skierg-alone-cycle", replace(cycle, availability=one("skierg"), capacity=(skierg,), roles=(SportRole("skierg"),)),
         None),
    ]
    # Two workouts a day (#666): a morning on the bike beside the evening run, more sessions than days, and strength.
    out += [
        ("two-a-day", replace(base, availability=sl(DEMO_SLOTS + MORNING_BIKE), level="trained", recent_weekly_minutes=330,
                              roles=(SportRole("run", "main"), SportRole("bike", "support")), strength_sessions=1), None),
        ("two-a-day-rolling", replace(demo.demo_request(20), availability=sl(DEMO_SLOTS + MORNING_BIKE), level="trained",
                                      roles=(SportRole("run", "main"), SportRole("bike", "support"))), date(2026, 12, 2)),
    ]
    # A smaller long workout in a supporting sport (#666): a longer bike ride every other week for a runner.
    out += [
        ("support-long", replace(base, availability=sl(tuple((d, 90 if d < 5 else 150, None, "core", rb) for d in range(7))),
                                 level="trained", recent_weekly_minutes=360,
                                 roles=(SportRole("run", "main"), SportRole("bike", "support"))), None),
    ]
    # Recovery after a race (#666): a long milestone, an earlier A race, a race just before the plan, and a bike race.
    out += [
        ("recovery-milestone", replace(base, goals=base.goals + (Goal("Halvmaran i vinter", date(2027, 1, 2), distance_km=21.1,
                                                                       priority="B"),), strength_sessions=2), None),
        ("recovery-two-a-goals", replace(demo.demo_request(20), goals=demo.demo_request(20).goals
                                         + (Goal("Vintermaran", date(2027, 1, 9), distance_km=42.2),)), None),
        ("recovery-before-start", replace(base, goals=(Goal("Förra maran", date(2026, 11, 14), distance_km=42.2,
                                                            priority="B"),), duration_weeks=12), None),
        ("recovery-bike-race", replace(base, availability=sl(tuple((r[0], r[1], None, "core", rb) for r in DEMO_SLOTS)),
                                       roles=(SportRole("run", "main"), SportRole("bike", "support")),
                                       goals=base.goals + (Goal("Cykelloppet", date(2026, 12, 12), sport="bike",
                                                                distance_km=150, priority="B"),)), date(2026, 12, 10)),
    ]
    # The high level, the long run after the goal's length and fixed hard days (engine 9).
    ski4 = ("ski", "run", "bike", "swim")
    high_slots = sl(tuple((d, 270 if d == 5 else 105, "17:00:00", "core", ("ski", "run")) for d in range(7))
                    + tuple((d, 45, "12:00:00", "core", ("bike", "swim")) for d in (1, 2, 3)))
    high_roles = (SportRole("ski", "main", 58), SportRole("run", "support", 28), SportRole("bike", "maintenance", 7),
                  SportRole("swim", "maintenance", 7))
    long_ski = Goal("Långloppet", date(2027, 3, 7), sport="ski", distance_km=90)
    out += [
        ("high-volume", replace(base, availability=high_slots, sports=ski4, roles=high_roles, level="high",
                                recent_weekly_minutes=600, capacity=base.capacity + (skierg,), goals=(long_ski,),
                                strength_sessions=3, hard_days=(HardDay(3, "ski"), HardDay(0, "run"))), None),
        ("high-run", replace(base, availability=sl(tuple((d, 180 if d == 6 else 105) for d in range(7))), level="high",
                             recent_weekly_minutes=None), None),
        ("high-rolling", replace(demo.demo_request(20), availability=high_slots, sports=ski4, roles=high_roles,
                                 level="high", goals=(long_ski,), hard_days=(HardDay(3, "ski"),)), date(2026, 12, 23)),
        ("long-ski-race", replace(base, availability=sl(tuple((d, 60 if d < 5 else 300, None, "core", ("ski", "run"))
                                                              for d in range(7))), level="trained", recent_weekly_minutes=420,
                                  roles=(SportRole("ski", "main"), SportRole("run", "support")), goals=(long_ski,),
                                  capacity=base.capacity + (skierg,)), None),
        ("ultra-recreational", replace(base, availability=sl(DEMO_SLOTS[:4] + ((6, 300, "09:00:00"),)),
                                       goals=(Goal("Ultra", date(2027, 5, 1), distance_km=90),)), None),
        ("hard-day", replace(base, hard_days=(HardDay(1),)), None),
        ("hard-days-two", replace(base, hard_days=(HardDay(3), HardDay(0))), None),
        ("hard-day-before-long", replace(base, hard_days=(HardDay(5),)), None),
        ("hard-day-sport", replace(base, availability=multi, roles=(SportRole("run", "main"), SportRole("bike", "support")),
                                   hard_days=(HardDay(3, "bike"),)), None),
        ("hard-day-maintenance", replace(base, availability=sl(tuple((d, 70 if d < 5 else 150, None, "core", four)
                                                                     for d in range(7))), level="trained",
                                         recent_weekly_minutes=None, roles=roles4, capacity=base.capacity + (skierg,),
                                         hard_days=(HardDay(2, "swim"), HardDay(4))), None),
    ]
    return out


MORNING_BIKE = tuple((d, 45, "07:00:00", "core", ("bike",)) for d in (1, 3, 5))
BIKE_TEST = Capacity("bike", Anchors(185, 48, 140, 160, 180.0, 240.0, sport="bike"), date(2026, 11, 1), "Laktattrappa")
SWIM_TEST = Capacity("swim", None, date(2026, 11, 10), "CSS-test", css_s100=115.0)    # 400 m 7:20, 200 m 3:30


TEXT_PLANS = ("bike-skierg-tested", "ski-main", "triathlon-css", "swim-without-test", "run-strength", "bike-alone-cycle",
              "swim-alone-cycle", "ski-cycle", "skierg-alone-cycle")


def text_fixtures(plans: dict[str, Any]) -> list[dict[str, Any]]:
    """What the app shows of a workout, written by the Python side (text.py, resolve.py): the title and every part
    as one line, how it should feel, its tips and drills, in both languages, for the first workouts of each sport and
    template of a plan with several sports."""
    out = []
    for name in TEXT_PLANS:
        stored = plans[name]
        plan = codec.plan_from_dict(stored["plan"], codec.request_from_dict(stored["request"], stored["athlete"]))
        seen: set[tuple[str, str]] = set()
        for w in plan.workouts:
            if w.goal is not None or (w.sport, w.template_id) in seen:
                continue
            seen.add((w.sport, w.template_id))
            for lang in ("sv", "en"):
                set_language(lang)
                lines = [text.plain(x) for x in text.workout_lines(w, plan.capacity_on(w.sport, w.date), plan.zone_system)]
                out.append({"plan": name, "key": w.key, "lang": lang, "title": plan.title(w), "lines": lines,
                            "feel": text.feel_line(w), "tips": text.tips(w), "drills": text.drills(w)})
    set_language("sv")
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
    # The demo requests carry texts (the name of the test, the goal): always the Swedish ones, whatever the caller
    # last wrote in.
    set_language("sv")
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
    plans["two-a-day"] = link.stored_plan(build_plan(next(r for n, r, _ in _requests() if n == "two-a-day"), plan_id="demo"))
    plans["high-volume"] = link.stored_plan(build_plan(next(r for n, r, _ in _requests() if n == "high-volume"),
                                                       plan_id="demo"))
    for name in TEXT_PLANS:
        plans[name] = link.stored_plan(build_plan(next(r for n, r, _ in _requests() if n == name), plan_id="demo"))
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
        ("two-a-day-no-mornings", "two-a-day", demo_slots, [], "2026-12-01"),
        ("two-a-day-more-mornings", "two-a-day", _slots(*DEMO_SLOTS, *MORNING_BIKE, (0, 40, "06:30:00", "core", ("bike",))),
         ["2026-12-03"], "2026-11-24"),
        ("high-volume-fewer", "high-volume", _slots(*((d, 270 if d == 5 else 90, "17:00:00", "core", ("ski", "run"))
                                                      for d in (0, 1, 3, 5, 6)), (2, 45, "12:00:00", "core", ("bike",))),
         ["2026-12-10"], "2026-11-30"),
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
            codec.request_to_dict(rolling.request), "cases": roll}, "plans": plans, "rebuild": rebuilds,
            "texts": text_fixtures(plans), "links": short_links(build, roll, rolling, plans),
            "guide": {"tips": library.TIPS, "drills": library.DRILLS}}


def short_links(build: list[dict[str, Any]], roll: list[dict[str, Any]], rolling: Any,
                plans: dict[str, Any]) -> dict[str, str]:
    """The short link's data of the build cases, the roll cases and the stored plans ("build:<name>", "roll:<name>",
    "plan:<name>"); a plan the short payload cannot give back (demo-done, with its statuses) has none."""
    out: dict[str, str] = {}
    req = {c["name"]: r for c, (_, r, _) in zip(build, _requests())}
    whole: dict[str, dict[str, Any]] = {}
    for c in build:
        if "plan" in c:
            whole[f"build:{c['name']}"] = link.stored_plan(codec.plan_from_dict(c["plan"], req[c["name"]]))
    for c in roll:
        whole[f"roll:{c['name']}"] = link.stored_plan(codec.plan_from_dict(c["plan"], rolling.request))
    whole.update({f"plan:{n}": p for n, p in plans.items()})
    for name, stored in whole.items():
        short = link.short_plan(stored)
        if short is not None:
            out[name] = link.encode(short)
    return out


def athlete_fixture(stored: dict[str, Any]) -> dict[str, Any]:
    """What the app sends the coach (app/src/model/share.ts, read by plan.phone): the demo plan without Mondays from
    26 November (the app's *Spara* on the 25th), the first workout done, the second partly done, the third ticked off
    by the watch, and a session outside the plan. app/test/share.test.ts builds the same with the app's engine."""
    slots = [s for s in stored["request"]["availability"] if s["weekday"] != 0]
    plan = phone.rebuild(stored, slots, [], "2026-11-26")
    w = [x for wk in plan["plan"]["weeks"] for x in wk["workouts"]]
    return {"kind": phone.KIND, "format": phone.FORMAT, "exported": "2026-11-25", "plan": plan,
            "marks": {w[0]["key"]: {"status": "done"}, w[1]["key"]: {"status": "partial", "minutes": 20},
                      w[2]["key"]: {"status": "done", "auto": True}},
            "extras": [{"id": "2026-11-23-1", "date": "2026-11-23", "sport": "other", "minutes": 30, "name": "Yoga"}]}


# The new test after the coach read the athlete's plan in (app/test/share.test.tsx): the day after the export.
RETEST = Capacity("run", Anchors(192, 45, 168, 177, 13.6, 14.8), date(2026, 11, 26), "Laktattrappa")


def athlete_back(athlete: dict[str, Any]) -> dict[str, Any]:
    """The way back to the phone: Tröskel reads ``athlete`` in (plan.phone, as services.import_phone_plan), a new
    test lays its targets on it (resolve.retest and roll_forward, as services.apply_retest), and the plan goes to the
    app again. It has the athlete's times, so the app keeps nothing waiting (keptTimes)."""
    from lopanalys.plan import resolve
    day = RETEST.tested
    got = phone.read(json.dumps(athlete))
    plan = roll_forward(resolve.retest(phone.to_plan(got, got.athlete, day), RETEST, from_date=day), day)
    return link.stored_plan(plan)


# ---- the athlete's own plan (#777): the form "Gör ett schema själv" in the app (app/src/model/request.ts) ----------------

def form_request(f: dict[str, Any]) -> PlanRequest:
    """The request of the app's form, by the rules of the coach's form (views/plan.py, ``collect``): the slots of the days
    that are on (a day's own sports, or all of them), the first goal the A goal and the others milestones, a length in
    weeks without a goal, and the roles only with more than one sport or another sport than running. The tests are the
    ones of the plan the form was filled in from, for the plan's sports (skis: the SkiErg's, without the SkiErg)."""
    sports: list[str] = []
    for x in f["sports"]:
        if x["sport"] in PLAN_SPORTS and x["sport"] not in sports:
            sports.append(x["sport"])
    slots = tuple(Slot(wd, round(d["minutes"]), tuple(s for s in d["sports"] if s in sports) or tuple(sports),
                       start=time.fromisoformat(d["start"]) if d["start"] else None)
                  for wd, d in enumerate(f["days"]) if d["on"])
    goals = tuple(Goal(g["name"].strip(), date.fromisoformat(g["date"]), g["sport"] if g["sport"] in sports else sports[0],
                       distance_km=g["distance_km"] or None, priority="A" if i == 0 else g["priority"],
                       target=g["target"].strip() if i == 0 else "")
                  for i, g in enumerate(f["goals"])) if f["mode"] == "goal" else ()
    tests = [c for c in f["capacity"] if c.get("source") != SELF and (
        c["sport"] in sports or any(src == c["sport"] and to in sports and src not in sports for to, src in BORROWS.items()))]
    caps = tuple(codec.capacity_from_dict(c) for c in with_values(tests, f.get("values") or {}, sports))
    roles = tuple(SportRole(x["sport"], x["role"], x["share"]) for x in f["sports"])
    return PlanRequest(f["athlete"].strip(), date.fromisoformat(f["start"]), slots, caps, tuple(sports), goals=goals,
                       duration_weeks=None if goals else f["weeks"], level=f["level"],
                       recent_weekly_minutes=f["recent_weekly_minutes"] or None,
                       roles=roles if len(roles) > 1 or roles[0].sport != RUNNING else (),
                       strength_sessions=f["strength_sessions"])


def form_from_plan(stored: dict[str, Any], today: str) -> dict[str, Any]:
    """The app's form filled in from a plan the phone has (request.ts, ``formFromPlan``): its answers and tests, a start
    on ``today``, the goals still ahead with the A goal first, and a day's own sports only when they are not all."""
    r = codec.request_from_dict(stored["request"], stored.get("athlete") or "")
    roles = r.plan_roles()
    every = [x.sport for x in roles]
    days = []
    for wd in range(7):
        s = next((x for x in r.availability if x.weekday == wd and x.role != "extra"), None)
        own = [x for x in s.sports if x in every] if s else []
        days.append({"on": s is not None, "minutes": s.max_minutes if s else 45,
                     "start": s.start.strftime("%H:%M") if s and s.start else "",
                     "sports": own if own and len(own) < len(every) else []})
    goals = [{"name": g.name, "date": g.date.isoformat(), "sport": g.sport, "distance_km": g.distance_km,
              "priority": g.priority, "target": g.target} for g in r.goals if g.date.isoformat() >= today]
    if goals:
        a = next((i for i, g in enumerate(goals) if g["priority"] == "A"), 0)
        goals.insert(0, goals.pop(a))
        goals[0]["priority"] = "A"
    empty = {"name": "", "date": "", "sport": "", "distance_km": None, "priority": "A", "target": ""}
    return {"athlete": stored.get("athlete") or "", "start": today,
            "sports": [{"sport": x.sport, "role": x.role, "share": x.share} for x in roles],
            "level": r.level if r.level in LEVELS else "", "recent_weekly_minutes": r.recent_weekly_minutes, "days": days,
            "mode": "goal" if goals else "weeks", "goals": goals or [empty], "weeks": r.duration_weeks or 8,
            "strength_sessions": r.strength_sessions,
            "capacity": [codec.capacity_to_dict(c) for c in r.capacity if not c.own_values],
            "values": {c.sport: values_of(codec.capacity_to_dict(c)) for c in r.capacity if c.own_values}}


def _day(on: bool = False, minutes: int = 45, start: str = "", sports: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"on": on, "minutes": minutes, "start": start, "sports": list(sports)}


def _goal(name: str, day: str, km: float | None = None, priority: str = "A", target: str = "",
          sport: str = "") -> dict[str, Any]:
    return {"name": name, "date": day, "sport": sport, "distance_km": km, "priority": priority, "target": target}


def _form(**kw: Any) -> dict[str, Any]:
    """A form as the app keeps it (request.ts, ``RequestForm``); the defaults are an empty form's."""
    out = {"athlete": "", "start": "2026-11-18", "sports": [{"sport": "run", "role": "main", "share": 0}], "level": "",
           "recent_weekly_minutes": None, "days": [_day() for _ in range(7)], "mode": "weeks", "goals": [_goal("", "")],
           "weeks": 8, "strength_sessions": 0, "capacity": [], "values": {}}
    return {**out, **kw}


def request_fixtures(demo_stored: dict[str, Any]) -> dict[str, Any]:
    """The app's own plans (test/request.test.ts): forms, the plan Tröskel builds from each of them (``form_request``,
    ``build_plan`` with every week detailed, ``link.stored_plan``), and the demo plan's form (``form_from_plan``)."""
    three = [_day(), _day(True, 45), _day(), _day(True, 50, "18:00"), _day(), _day(), _day(True, 80, "09:30")]
    five = [_day(True, 45, "18:00"), _day(True, 70, "18:00"), _day(), _day(True, 60, "06:30"), _day(),
            _day(True, 60, "10:00"), _day(True, 110, "09:00")]
    multi = [_day(True, 60, "", ("bike",)), _day(True, 60), _day(), _day(True, 45, "07:00", ("swim",)), _day(True, 60),
             _day(), _day(True, 150, "09:00", ("bike",))]
    forms = [
        ("run-weeks", _form(level="recreational", days=three, weeks=10)),
        ("run-goal-milestones", _form(athlete=" Exempel Löpare ", level="trained", recent_weekly_minutes=240, days=five,
                                      mode="goal", strength_sessions=1,
                                      goals=[_goal("Halvmaran", "2027-03-13", 21.1, target=" 1:45 "),
                                             _goal("Milen", "2027-01-16", 10, "B", "ska bort"),
                                             _goal("Parkrun", "2026-12-12", 5, "C")])),
        ("beginner-weeks", _form(level="beginner", days=three, weeks=12, start="2026-11-23")),
        ("multi-sport", _form(level="recreational", recent_weekly_minutes=300, days=multi, mode="goal",
                              sports=[{"sport": "bike", "role": "main", "share": 0}, {"sport": "run", "role": "support", "share": 0},
                                      {"sport": "swim", "role": "maintenance", "share": 0}],
                              goals=[_goal("Cykelloppet", "2027-05-15", 90, sport="bike")])),
        ("high-ski", _form(level="high", recent_weekly_minutes=480, days=[_day(True, 90) for _ in range(6)] + [_day(True, 240)],
                           mode="goal", sports=[{"sport": "ski", "role": "main", "share": 0},
                                                {"sport": "run", "role": "support", "share": 0}],
                           goals=[_goal("Långloppet", "2027-03-07", 90, sport="ski")], strength_sessions=2)),
        ("own-values", _form(level="recreational", days=multi, weeks=10,
                             sports=[{"sport": "run", "role": "main", "share": 0}, {"sport": "bike", "role": "support", "share": 0},
                                     {"sport": "swim", "role": "maintenance", "share": 0}],
                             values={"run": _v(max_hr="191", resting_hr="48", lt1_hr="158", lt2_hr="176", lt1="5:20",
                                               lt2="4:25"),
                                     "bike": _v(max_hr="182", lt2="245"), "swim": _v(css="1:58"),
                                     "skierg": _v(max_hr="185")})),
    ]
    cases = []
    for name, f in forms:
        plan_id = f"app-{name[:8]}"
        cases.append({"name": name, "form": f, "plan_id": plan_id,
                      "stored": link.stored_plan(build_plan(form_request(f), plan_id=plan_id))})
    f = form_from_plan(demo_stored, "2026-11-25")
    cases.append({"name": "from-demo", "from": "demo", "today": "2026-11-25", "form": f, "plan_id": "app-fromdemo",
                  "stored": link.stored_plan(build_plan(form_request(f), plan_id="app-fromdemo"))})
    return {"cases": cases}


# ---- the athlete's own values (#777): app/src/model/values.ts and app/src/engine/zones.ts ----------------------------

VALUE_SPORTS = ("run", "bike", "skierg", "ski", "swim")
LIMITS = {"max_hr": (100, 240), "resting_hr": (25, 110), "hr": (80, 235), "pace_s": (120, 720), "watt": (30, 900),
          "css_s": (50, 240)}


def _load_of(sport: str) -> str:
    return {"run": "pace", "bike": "watt", "skierg": "watt", "swim": "css"}.get(sport, "none")


def _int(v: str, field: str, limits: tuple[int, int]) -> int | None:
    s = v.strip()
    if not s:
        return None
    if not re.fullmatch(r"[0-9]+", s) or not limits[0] <= int(s) <= limits[1]:
        raise ValueError(f"ui.values.err.{field}")
    return int(s)


def _seconds(v: str, field: str, limits: tuple[int, int]) -> int | None:
    s = v.strip()
    if not s:
        return None
    m = re.fullmatch(r"([0-9]{1,2})[:.]([0-9]{2})", s)
    n = int(m[1]) * 60 + int(m[2]) if m and int(m[2]) < 60 else None
    if n is None or not limits[0] <= n <= limits[1]:
        raise ValueError(f"ui.values.err.{field}")
    return n


def values_capacity(sport: str, v: dict[str, str]) -> dict[str, Any] | None:
    """The capacity of one sport's typed-in values (values.ts, ``valuesCapacity``): own values, ``source`` SELF and no
    test day; None when every field is empty. ValueError with the text key for a field that cannot be read."""
    if _load_of(sport) == "css":
        css = _seconds(v["css"], "css", LIMITS["css_s"])
        return None if css is None else codec.capacity_to_dict(Capacity(sport, None, None, SELF, css_s100=css))
    mx, rest = _int(v["max_hr"], "max_hr", LIMITS["max_hr"]), _int(v["resting_hr"], "resting_hr", LIMITS["resting_hr"])
    lt1, lt2 = _int(v["lt1_hr"], "hr", LIMITS["hr"]), _int(v["lt2_hr"], "hr", LIMITS["hr"])
    if rest is not None and mx is not None and rest >= mx:
        raise ValueError("ui.values.err.order")
    if ((lt1 is not None and lt2 is not None and lt1 >= lt2) or (lt2 is not None and mx is not None and lt2 > mx)
            or (lt1 is not None and mx is not None and lt1 >= mx)):
        raise ValueError("ui.values.err.order")
    s1 = s2 = None
    if _load_of(sport) == "pace":
        p1, p2 = _seconds(v["lt1"], "pace", LIMITS["pace_s"]), _seconds(v["lt2"], "pace", LIMITS["pace_s"])
        if p1 is not None and p2 is not None and p1 <= p2:
            raise ValueError("ui.values.err.load_order")
        s1, s2 = (None if p1 is None else 3600 / p1), (None if p2 is None else 3600 / p2)
    elif _load_of(sport) == "watt":
        s1, s2 = _int(v["lt1"], "watt", LIMITS["watt"]), _int(v["lt2"], "watt", LIMITS["watt"])
        if s1 is not None and s2 is not None and s1 >= s2:
            raise ValueError("ui.values.err.load_order")
    if all(x is None for x in (mx, rest, lt1, lt2, s1, s2)):
        return None
    return codec.capacity_to_dict(Capacity(sport, Anchors(mx, rest, lt1, lt2, s1, s2, sport=sport), None, SELF))


def _js_round(x: float) -> int:
    """JavaScript's Math.round, which the app's fields use: halves up."""
    return int(math.floor(x + 0.5))


def values_of(c: dict[str, Any]) -> dict[str, str]:
    """The fields of a capacity (values.ts, ``valuesOf``)."""
    a = c.get("anchors") or {}
    load = _load_of(c["sport"])

    def num(v: Any) -> str:
        return "" if v is None else str(_js_round(v))

    def pace(kmh: Any) -> str:
        if not kmh:
            return ""
        s = _js_round(3600 / kmh)
        return f"{s // 60}:{s % 60:02d}"

    def load_text(v: Any) -> str:
        return pace(v) if load == "pace" else num(v) if load == "watt" else ""

    css = c.get("css_s100")
    return {"max_hr": num(a.get("max_hr")), "resting_hr": num(a.get("resting_hr")), "lt1_hr": num(a.get("lt1_hr")),
            "lt2_hr": num(a.get("lt2_hr")), "lt1": load_text(a.get("lt1_speed_kmh")), "lt2": load_text(a.get("lt2_speed_kmh")),
            "css": f"{_js_round(css) // 60}:{_js_round(css) % 60:02d}" if css else ""}


def with_values(capacity: list[dict[str, Any]], values: dict[str, Any], sports: Any) -> list[dict[str, Any]]:
    """The capacity with own values laid on it (values.ts, ``withValues``): a sport with a row takes its own values in
    place of what it had, or nothing when the row is empty; the others keep theirs."""
    own = [c for sp in VALUE_SPORTS if sp in values and sp in sports for c in [values_capacity(sp, values[sp])] if c]
    return [c for c in capacity if not (c["sport"] in values and c["sport"] in sports)] + own


def plan_with_values(stored: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """The stored plan on new own values (values.ts, ``planWithValues``): the same weeks, the capacity with the values,
    the targets from it, and the notes and based_on build_plan gives."""
    req0 = codec.request_from_dict(stored["request"], stored.get("athlete") or "")
    caps = with_values(stored["request"]["capacity"], values, req0.plan_sports)
    req = codec.request_from_dict({**stored["request"], "capacity": caps}, stored.get("athlete") or "")
    old = codec.plan_from_dict(stored["plan"], req)
    fresh = build_plan(req, plan_id=old.id, created=old.created)
    return link.stored_plan(replace(old, notes=fresh.notes, based_on=fresh.based_on), stored.get("athlete") or "")


def _cap(sport: str, mx=None, rest=None, lt1=None, lt2=None, s1=None, s2=None, source: str = SELF, tested=None,
         css=None, asport: str | None = None) -> Capacity:
    anchors = None if css is not None and mx is None else Anchors(mx, rest, lt1, lt2, s1, s2, sport=asport or sport)
    return Capacity(sport, anchors, tested, source, css_s100=css)


def _v(**kw: str) -> dict[str, str]:
    return {**{k: "" for k in ("max_hr", "resting_hr", "lt1_hr", "lt2_hr", "lt1", "lt2", "css")}, **kw}


def zones_fixtures(demo_stored: dict[str, Any]) -> dict[str, Any]:
    """The app's own zones against resolve.py (test/values.test.ts)."""
    caps = [
        ("run-full", _cap("run", 190, 50, 160, 175, 11.5, 13.5), None),
        ("run-ties", _cap("run", 185, None, 151, 170, 3600 / 330, 3600 / 265), None),
        ("run-odd-speeds", _cap("run", 197, 44, 163, 181, 3600 / 287, 3600 / 251), None),
        ("run-max-only", _cap("run", 185), None),
        ("run-max-rest", _cap("run", 193, 47), None),
        ("run-lt2-only", _cap("run", 188, None, None, 172, None, 3600 / 260), None),
        ("run-no-max", _cap("run", None, None, 160, 175, 11.5, 13.5), None),
        ("run-max-too-high", _cap("run", 250, None, 160, 175), None),
        ("run-crossed", _cap("run", 190, None, 176, 168), None),
        ("run-low-lt1", _cap("run", 190, None, 105, 170, 9.0, 13.0), None),
        ("run-lt2-at-max", _cap("run", 180, None, 150, 180), None),
        ("run-for-ski", _cap("run", 190, 50, 160, 175, 11.5, 13.5), "ski"),
        ("bike-ftp-max", _cap("bike", 180, None, None, None, None, 250.0), None),
        ("bike-ftp-only", _cap("bike", None, None, None, None, None, 250.0), None),
        ("bike-full", _cap("bike", 185, 48, 140, 160, 181.0, 243.0), None),
        ("bike-odd-ftp", _cap("bike", 179, None, 131, 158, 167.0, 263.0), None),
        ("skierg-full", _cap("skierg", 188, 48, 150, 168, 160.0, 210.0), None),
        ("skierg-for-ski", _cap("skierg", 188, 48, 150, 168, 160.0, 210.0), "ski"),
        ("ski-own", _cap("ski", 186, None, 148, 171), None),
        ("swim-css", _cap("swim", css=110.0), None),
        ("swim-css-odd", _cap("swim", css=97.3), None),
        ("swim-for-run", _cap("swim", css=110.0), "run"),
        ("nothing", Capacity("run"), None),
        ("measured", _cap("run", 192, 46, 165, 174, 13.2, 14.4, "Laktattrappa", date(2026, 11, 13)), None),
    ]
    targets = [{"name": n, "capacity": codec.capacity_to_dict(c), "sport": sp,
                "targets": link.zone_targets(c, "garmin", sp)} for n, c, sp in caps]
    by = {n: c for n, c, _ in caps}
    reqs = [
        ("run-bike-swim", (by["run-full"], by["bike-ftp-max"], by["swim-css"]),
         (SportRole("run", "main"), SportRole("bike", "support"), SportRole("swim", "maintenance"))),
        ("ski-borrows-skierg", (by["skierg-full"],), (SportRole("ski", "main"), SportRole("run", "support"))),
        ("ski-own-and-skierg", (by["ski-own"], by["skierg-full"]),
         (SportRole("ski", "main"), SportRole("skierg", "support"))),
        ("nothing-known", (Capacity("bike"),), (SportRole("run", "main"), SportRole("bike", "support"))),
    ]
    plans = []
    for n, cs, roles in reqs:
        r = PlanRequest("", date(2026, 11, 23), (Slot(1, 60),), cs, tuple(x.sport for x in roles), roles=roles)
        plans.append({"name": n, "request": codec.request_to_dict(r),
                      "targets": link._targets(type("P", (), {"request": r, "zone_system": "garmin"})())})
    plans.append({"name": "demo", "request": demo_stored["request"], "targets": demo_stored["targets"]})
    typed = [
        ("run", _v(max_hr="191", resting_hr="48", lt1_hr="158", lt2_hr="176", lt1="5:20", lt2="4:25")),
        ("run", _v(max_hr=" 185 ")),
        ("run", _v(lt2="4.30")),
        ("bike", _v(max_hr="180", lt2="250")),
        ("skierg", _v(lt1_hr="150", lt2_hr="168", lt1="160", lt2="210")),
        ("ski", _v(max_hr="186", lt1_hr="148", lt2_hr="171", lt1="ignored")),
        ("swim", _v(css="1:52")),
        ("swim", _v(max_hr="180")),
        ("run", _v()),
        ("run", _v(max_hr="90")), ("run", _v(max_hr="19O")), ("run", _v(resting_hr="190", max_hr="185")),
        ("run", _v(lt1_hr="170", lt2_hr="165")), ("run", _v(lt2_hr="190", max_hr="185")),
        ("run", _v(lt1="4:20", lt2="4:40")), ("run", _v(lt1="4:75")), ("run", _v(lt1="1:30")),
        ("bike", _v(lt1="260", lt2="250")), ("bike", _v(lt2="5000")), ("swim", _v(css="0:40")), ("swim", _v(css="abc")),
    ]
    values = []
    for sp, v in typed:
        try:
            values.append({"sport": sp, "values": v, "capacity": values_capacity(sp, v)})
        except ValueError as e:
            values.append({"sport": sp, "values": v, "error": str(e)})
    own = {"run": _v(max_hr="191", resting_hr="48", lt1_hr="158", lt2_hr="176", lt1="5:20", lt2="4:25")}
    laid = [
        {"name": "demo-run-own", "values": own, "stored": plan_with_values(demo_stored, own)},
    ]
    back = plan_with_values(demo_stored, own)
    laid.append({"name": "own-run-cleared", "from": "demo-run-own", "values": {"run": _v()},
                 "stored": plan_with_values(back, {"run": _v()})})
    return {"targets": targets, "plans": plans, "values": values, "laid": laid,
            "values_of": [{"capacity": x["capacity"], "values": values_of(x["capacity"])}
                          for x in values if x.get("capacity")] + [
                {"capacity": demo_stored["request"]["capacity"][0],
                 "values": values_of(demo_stored["request"]["capacity"][0])}]}


def _gz(data: Any) -> bytes:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0)


def outputs() -> dict[Path, bytes]:
    set_language("sv")
    plan = demo.demo_plan(12)
    stored = link.stored_plan(plan)
    out = {APP / "public" / "demo.troskel": link.plan_file(stored),
           APP / "test" / "fixtures" / "demo.link.txt": (link.plan_link(link.link_payload(stored)) + "\n").encode("ascii"),
           APP / "test" / "fixtures" / "demo.full.link.txt": (link.plan_link(stored) + "\n").encode("ascii"),
           APP / "test" / "fixtures" / "demo.sv.ics": ics.to_ics(plan).encode("utf-8"),
           APP / "test" / "fixtures" / "athlete.troskel":
               (json.dumps(athlete_fixture(stored), ensure_ascii=False, indent=1) + "\n").encode("utf-8"),
           APP / "test" / "fixtures" / "athlete.link.txt": (phone.athlete_link(athlete_fixture(stored)) + "\n").encode("ascii"),
           APP / "test" / "fixtures" / "athlete.back.link.txt":
               (link.plan_link(link.link_payload(athlete_back(athlete_fixture(stored)))) + "\n").encode("ascii")}
    out[APP / "test" / "fixtures" / "request.json.gz"] = _gz(request_fixtures(stored))
    out[APP / "test" / "fixtures" / "zones.json.gz"] = _gz(zones_fixtures(stored))
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
