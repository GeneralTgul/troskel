"""Training plans built from the tests: fixed, explainable rules, no randomness and no AI.

    request = PlanRequest(...)            # athlete, capacity per sport, availability, goals or a length
    plan = build_plan(request)            # blocks, weeks and workouts; the same input gives the same plan
    resolve.target(capacity, "Z4")        # heart rate and pace of a zone, from the latest test
    ics.to_ics(plan)                      # calendar file (RFC 5545)
    report.plan_builder.render_plan_html  # the printout

The plan stores zones, never heart rates or paces: the numbers are looked up in the athlete's
``Capacity`` when the plan is shown, printed or exported, so a retest moves every target without
rewriting the plan. No Qt and no network in this package (docs/TRANINGSPLAN.md).
"""
from __future__ import annotations

from .engine import ENGINE_VERSION, build_plan
from .models import RUNNING, Block, Capacity, Goal, HardDay, Note, Part, Plan, PlanRequest, Slot, SportRole, Week, Workout

__all__ = ["ENGINE_VERSION", "RUNNING", "Block", "Capacity", "Goal", "HardDay", "Note", "Part", "Plan", "PlanRequest",
           "Slot", "SportRole", "Week", "Workout", "build_plan"]
