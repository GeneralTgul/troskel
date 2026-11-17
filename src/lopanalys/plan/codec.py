"""A plan and its request as JSON-ready dicts and back, for storage. No Qt, no database.

The stored plan is the plan as it was built: reading it back never runs the engine, so a saved
plan stays the same when the rules change (``Plan.engine_version`` says which rules made it).
The athlete's name is not part of the stored request: the caller passes it in when reading, so
the name lives in one place (the athlete's row).

The rolling plan (plan.rolling) stores its horizon, the last week with workouts, and the weeks after
it as sketches (sums without workouts). A plan saved before that has neither and reads back as a
plan where every week is detailed. ``engine_version`` says which rules built it (or last rolled it on).

The sports' roles and shares (``PlanRequest.roles``, several sports) came after format 2 and are stored
under ``roles``; a request saved without them reads back with none, which means the sports in ``sports``.

Step 3 of several sports (#666) added ``strength_sessions`` to the request, ``css_s100`` (a swimming test) to a
capacity and ``strength`` to a sketched week. Each is written only when it is set, so a plan without strength or
a swimming test is stored as before, and a plan saved before reads back with none.

The fixed hard days (``PlanRequest.hard_days``, engine version 9) are stored the same way, under ``hard_days`` as
``{"weekday": 3, "sport": "ski"}`` (the sport left out when the engine picks it), only when there are any.

A plan written outside the engine (``Plan.source``, the weeks as a schedule gives them) is stored with ``source`` and
each workout with its own title under ``name``; both only when set, so every other plan is stored as before.

The athlete's changes of single workouts (``Plan.edits``, #777) are stored under ``edits`` as
``{"key": ..., "date": ..., "minutes": ...}`` (a field only when set), only when there are any.

``Anchors`` is stored field by field without its lactate curve (``source``); the plan uses the
default zone system, which needs the thresholds only. A zones test's ``SpeedLine`` is stored with it.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, time
from typing import Any

from lopanalys.assessments.zone_systems import DEFAULT_SYSTEM, Anchors

from .models import (Block, Capacity, Edit, Goal, HardDay, Note, Part, Plan, PlanRequest, Slot, SpeedLine, SportRole, Week, WeekSketch,
                     Workout)

FORMAT = 2                    # 2: the rolling plan (horizon and sketched weeks); 1 reads as fully detailed
_ANCHOR_FIELDS = tuple(f.name for f in fields(Anchors) if f.name != "source")
_LINE_FIELDS = tuple(f.name for f in fields(SpeedLine))
# Plans saved before the sport keys came from core.sport called running "running".
_OLD_SPORT = {"running": "run"}


def _sport(v: str) -> str:
    return _OLD_SPORT.get(v, v)


def _d(v: date | None) -> str | None:
    return v.isoformat() if v else None


def _pd(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _pt(v: str | None) -> time | None:
    return time.fromisoformat(v) if v else None


def capacity_to_dict(c: Capacity) -> dict[str, Any]:
    anchors = None if c.anchors is None else {k: getattr(c.anchors, k) for k in _ANCHOR_FIELDS}
    d = {"sport": c.sport, "anchors": anchors, "tested": _d(c.tested), "source": c.source}
    if c.speed_line is not None:       # only a zones test has one: older plans are stored without the key
        d["speed_line"] = {k: getattr(c.speed_line, k) for k in _LINE_FIELDS}
    if c.css_s100 is not None:
        d["css_s100"] = c.css_s100
    return d


def capacity_from_dict(d: dict[str, Any]) -> Capacity:
    a = d.get("anchors")
    anchors = None if a is None else Anchors(**{k: _sport(a[k]) if k == "sport" else a.get(k)
                                                for k in _ANCHOR_FIELDS if k in a or k != "sport"})
    ln = d.get("speed_line")
    line = None if not ln else SpeedLine(**{k: ln[k] for k in _LINE_FIELDS if k in ln})
    return Capacity(_sport(d["sport"]), anchors, _pd(d.get("tested")), d.get("source", ""), line, d.get("css_s100"))


def _goal_to_dict(g: Goal) -> dict[str, Any]:
    return {"name": g.name, "date": _d(g.date), "sport": g.sport, "distance_km": g.distance_km,
            "priority": g.priority, "target": g.target}


def _goal_from_dict(d: dict[str, Any]) -> Goal:
    return Goal(d["name"], date.fromisoformat(d["date"]), _sport(d["sport"]), d.get("distance_km"), d.get("priority", "A"),
                d.get("target", ""))


def _note_to_dict(n: Note) -> dict[str, Any]:
    return {"key": n.key, "args": [[k, v] for k, v in n.args]}


def _note_from_dict(d: dict[str, Any]) -> Note:
    return Note(d["key"], tuple((k, v) for k, v in d.get("args", [])))


def request_to_dict(r: PlanRequest) -> dict[str, Any]:
    """The request without the athlete's name."""
    d = {
        "format": FORMAT, "start": _d(r.start), "sports": list(r.sports),
        "availability": [{"weekday": s.weekday, "max_minutes": s.max_minutes, "sports": list(s.sports),
                          "start": s.start.isoformat() if s.start else None, "role": s.role} for s in r.availability],
        "capacity": [capacity_to_dict(c) for c in r.capacity], "goals": [_goal_to_dict(g) for g in r.goals],
        "duration_weeks": r.duration_weeks, "level": r.level, "recent_weekly_minutes": r.recent_weekly_minutes,
        "roles": [{"sport": x.sport, "role": x.role, "share": x.share} for x in r.roles],
    }
    if r.strength_sessions:
        d["strength_sessions"] = r.strength_sessions
    if r.hard_days:
        d["hard_days"] = [{"weekday": h.weekday, **({"sport": h.sport} if h.sport else {})} for h in r.hard_days]
    return d


def request_from_dict(d: dict[str, Any], athlete_name: str = "") -> PlanRequest:
    slots = tuple(Slot(s["weekday"], s["max_minutes"], tuple(_sport(x) for x in s["sports"]), _pt(s.get("start")),
                       s.get("role", "core"))
                  for s in d["availability"])
    return PlanRequest(athlete_name, date.fromisoformat(d["start"]), slots,
                       tuple(capacity_from_dict(c) for c in d.get("capacity", [])), tuple(_sport(x) for x in d["sports"]),
                       tuple(_goal_from_dict(g) for g in d.get("goals", [])), d.get("duration_weeks"),
                       d.get("level", "recreational"), d.get("recent_weekly_minutes"),
                       tuple(SportRole(_sport(x["sport"]), x.get("role", "main"), x.get("share", 0))
                             for x in d.get("roles", [])), d.get("strength_sessions", 0),
                       tuple(HardDay(h["weekday"], _sport(h.get("sport", ""))) for h in d.get("hard_days", [])))


def _workout_to_dict(w: Workout) -> dict[str, Any]:
    return {"key": w.key, "date": _d(w.date), "sport": w.sport, "template_id": w.template_id, "family": w.family,
            "quality": w.quality, "start": w.start.isoformat() if w.start else None, "status": w.status,
            "goal": _goal_to_dict(w.goal) if w.goal else None,
            "parts": [_part_to_dict(p) for p in w.parts], **({"name": w.name} if w.name else {})}


def _part_to_dict(p: Part) -> dict[str, Any]:
    """``band`` only on a part that has one, so every other part is stored as before."""
    d = {"kind": p.kind, "duration_s": p.duration_s, "zone": p.zone, "rpe": p.rpe, "repeat": p.repeat,
         "rest_s": p.rest_s, "rest_zone": p.rest_zone}
    if p.band:
        d["band"] = p.band
    return d


def _workout_from_dict(d: dict[str, Any]) -> Workout:
    parts = [Part(p["kind"], p["duration_s"], p.get("zone"), p["rpe"], p.get("repeat", 1), p.get("rest_s", 0),
                  p.get("rest_zone", "Z1"), p.get("band", "")) for p in d["parts"]]
    return Workout(d["key"], date.fromisoformat(d["date"]), _sport(d["sport"]), d["template_id"], d["family"], d["quality"],
                   parts, _pt(d.get("start")), _goal_from_dict(d["goal"]) if d.get("goal") else None,
                   d.get("status", "planned"), d.get("name", ""))


def plan_to_dict(p: Plan) -> dict[str, Any]:
    """The built plan without its request (stored beside it, see ``request_to_dict``)."""
    return {
        "format": FORMAT, "id": p.id, "created": p.created.isoformat(), "engine_version": p.engine_version,
        "zone_system": p.zone_system, "status": p.status, "based_on": {k: _d(v) for k, v in p.based_on.items()},
        "notes": [_note_to_dict(n) for n in p.notes],
        "capacity_log": [[_d(until), capacity_to_dict(c)] for until, c in p.capacity_log],
        "blocks": [{"kind": b.kind, "first_week": b.first_week, "weeks": b.weeks, "start": _d(b.start),
                    "reason": _note_to_dict(b.reason)} for b in p.blocks],
        "weeks": [_week_to_dict(w) for w in p.weeks],
        "horizon": p.horizon, **({"source": p.source} if p.source else {}),
        **({"edits": [edit_to_dict(e) for e in p.edits]} if p.edits else {}),
    }


def edit_to_dict(e: Edit) -> dict[str, Any]:
    return {"key": e.key, **({"date": _d(e.date)} if e.date else {}),
            **({"minutes": e.minutes} if e.minutes is not None else {})}


def edit_from_dict(d: dict[str, Any]) -> Edit:
    return Edit(str(d["key"]), _pd(d.get("date")), d.get("minutes"))


def _week_to_dict(w: Week) -> dict[str, Any]:
    d = {"index": w.index, "monday": _d(w.monday), "block": w.block, "kind": w.kind,
         "target_minutes": w.target_minutes, "notes": [_note_to_dict(n) for n in w.notes],
         "workouts": [_workout_to_dict(x) for x in w.workouts]}
    if w.sketch is not None:
        k = w.sketch
        d["sketch"] = {"minutes": k.minutes, "sessions": k.sessions, "quality": k.quality, "longest": k.longest,
                       "zones": [[z, m] for z, m in k.zones]}
        if k.strength:
            d["sketch"]["strength"] = k.strength
    return d


def _week_from_dict(w: dict[str, Any]) -> Week:
    k = w.get("sketch")
    sketch = WeekSketch(k["minutes"], k["sessions"], k["quality"], k["longest"],
                        tuple((z, float(m)) for z, m in k.get("zones", [])), k.get("strength", 0)) if k else None
    return Week(w["index"], date.fromisoformat(w["monday"]), w["block"], w["kind"], w["target_minutes"],
                [_workout_from_dict(x) for x in w["workouts"]], [_note_from_dict(n) for n in w.get("notes", [])], sketch)


def plan_from_dict(d: dict[str, Any], request: PlanRequest) -> Plan:
    blocks = [Block(b["kind"], b["first_week"], b["weeks"], date.fromisoformat(b["start"]), _note_from_dict(b["reason"]))
              for b in d["blocks"]]
    weeks = [_week_from_dict(w) for w in d["weeks"]]
    log = tuple((date.fromisoformat(until), capacity_from_dict(c)) for until, c in d.get("capacity_log", []))
    return Plan(d["id"], request, datetime.fromisoformat(d["created"]), d.get("engine_version", ""), blocks, weeks,
                [_note_from_dict(n) for n in d.get("notes", [])], d.get("zone_system", DEFAULT_SYSTEM),
                {_sport(k): _pd(v) for k, v in d.get("based_on", {}).items()}, d.get("status", "draft"), log,
                d.get("horizon"), d.get("source", ""), tuple(edit_from_dict(e) for e in d.get("edits", [])))


__all__ = ["FORMAT", "capacity_from_dict", "capacity_to_dict", "edit_from_dict", "edit_to_dict", "plan_from_dict", "plan_to_dict", "request_from_dict",
           "request_to_dict"]
