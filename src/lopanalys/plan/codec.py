"""A plan and its request as JSON-ready dicts and back, for storage. No Qt, no database.

The stored plan is the plan as it was built: reading it back never runs the engine, so a saved
plan stays the same when the rules change (``Plan.engine_version`` says which rules made it).
The athlete's name is not part of the stored request: the caller passes it in when reading, so
the name lives in one place (the athlete's row).

``Anchors`` is stored field by field without its lactate curve (``source``); the plan uses the
default zone system, which needs the thresholds only.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, time
from typing import Any

from lopanalys.assessments.zone_systems import DEFAULT_SYSTEM, Anchors

from .models import Block, Capacity, Goal, Note, Part, Plan, PlanRequest, Slot, Week, Workout

FORMAT = 1
_ANCHOR_FIELDS = tuple(f.name for f in fields(Anchors) if f.name != "source")
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
    return {"sport": c.sport, "anchors": anchors, "tested": _d(c.tested), "source": c.source}


def capacity_from_dict(d: dict[str, Any]) -> Capacity:
    a = d.get("anchors")
    anchors = None if a is None else Anchors(**{k: _sport(a[k]) if k == "sport" else a.get(k)
                                                for k in _ANCHOR_FIELDS if k in a or k != "sport"})
    return Capacity(_sport(d["sport"]), anchors, _pd(d.get("tested")), d.get("source", ""))


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
    return {
        "format": FORMAT, "start": _d(r.start), "sports": list(r.sports),
        "availability": [{"weekday": s.weekday, "max_minutes": s.max_minutes, "sports": list(s.sports),
                          "start": s.start.isoformat() if s.start else None, "role": s.role} for s in r.availability],
        "capacity": [capacity_to_dict(c) for c in r.capacity], "goals": [_goal_to_dict(g) for g in r.goals],
        "duration_weeks": r.duration_weeks, "level": r.level, "recent_weekly_minutes": r.recent_weekly_minutes,
    }


def request_from_dict(d: dict[str, Any], athlete_name: str = "") -> PlanRequest:
    slots = tuple(Slot(s["weekday"], s["max_minutes"], tuple(_sport(x) for x in s["sports"]), _pt(s.get("start")),
                       s.get("role", "core"))
                  for s in d["availability"])
    return PlanRequest(athlete_name, date.fromisoformat(d["start"]), slots,
                       tuple(capacity_from_dict(c) for c in d.get("capacity", [])), tuple(_sport(x) for x in d["sports"]),
                       tuple(_goal_from_dict(g) for g in d.get("goals", [])), d.get("duration_weeks"),
                       d.get("level", "recreational"), d.get("recent_weekly_minutes"))


def _workout_to_dict(w: Workout) -> dict[str, Any]:
    return {"key": w.key, "date": _d(w.date), "sport": w.sport, "template_id": w.template_id, "family": w.family,
            "quality": w.quality, "start": w.start.isoformat() if w.start else None, "status": w.status,
            "goal": _goal_to_dict(w.goal) if w.goal else None,
            "parts": [{"kind": p.kind, "duration_s": p.duration_s, "zone": p.zone, "rpe": p.rpe, "repeat": p.repeat,
                       "rest_s": p.rest_s, "rest_zone": p.rest_zone} for p in w.parts]}


def _workout_from_dict(d: dict[str, Any]) -> Workout:
    parts = [Part(p["kind"], p["duration_s"], p.get("zone"), p["rpe"], p.get("repeat", 1), p.get("rest_s", 0),
                  p.get("rest_zone", "Z1")) for p in d["parts"]]
    return Workout(d["key"], date.fromisoformat(d["date"]), _sport(d["sport"]), d["template_id"], d["family"], d["quality"],
                   parts, _pt(d.get("start")), _goal_from_dict(d["goal"]) if d.get("goal") else None,
                   d.get("status", "planned"))


def plan_to_dict(p: Plan) -> dict[str, Any]:
    """The built plan without its request (stored beside it, see ``request_to_dict``)."""
    return {
        "format": FORMAT, "id": p.id, "created": p.created.isoformat(), "engine_version": p.engine_version,
        "zone_system": p.zone_system, "status": p.status, "based_on": {k: _d(v) for k, v in p.based_on.items()},
        "notes": [_note_to_dict(n) for n in p.notes],
        "capacity_log": [[_d(until), capacity_to_dict(c)] for until, c in p.capacity_log],
        "blocks": [{"kind": b.kind, "first_week": b.first_week, "weeks": b.weeks, "start": _d(b.start),
                    "reason": _note_to_dict(b.reason)} for b in p.blocks],
        "weeks": [{"index": w.index, "monday": _d(w.monday), "block": w.block, "kind": w.kind,
                   "target_minutes": w.target_minutes, "notes": [_note_to_dict(n) for n in w.notes],
                   "workouts": [_workout_to_dict(x) for x in w.workouts]} for w in p.weeks],
    }


def plan_from_dict(d: dict[str, Any], request: PlanRequest) -> Plan:
    blocks = [Block(b["kind"], b["first_week"], b["weeks"], date.fromisoformat(b["start"]), _note_from_dict(b["reason"]))
              for b in d["blocks"]]
    weeks = [Week(w["index"], date.fromisoformat(w["monday"]), w["block"], w["kind"], w["target_minutes"],
                  [_workout_from_dict(x) for x in w["workouts"]], [_note_from_dict(n) for n in w.get("notes", [])])
             for w in d["weeks"]]
    log = tuple((date.fromisoformat(until), capacity_from_dict(c)) for until, c in d.get("capacity_log", []))
    return Plan(d["id"], request, datetime.fromisoformat(d["created"]), d.get("engine_version", ""), blocks, weeks,
                [_note_from_dict(n) for n in d.get("notes", [])], d.get("zone_system", DEFAULT_SYSTEM),
                {_sport(k): _pd(v) for k, v in d.get("based_on", {}).items()}, d.get("status", "draft"), log)


__all__ = ["FORMAT", "capacity_from_dict", "capacity_to_dict", "plan_from_dict", "plan_to_dict", "request_from_dict",
           "request_to_dict"]
