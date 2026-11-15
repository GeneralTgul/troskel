"""Workouts as display texts in the current language, shared by the printout and the calendar file."""
from __future__ import annotations

from typing import Any

from lopanalys.core.i18n import t

from .models import Capacity, Part, Plan, Workout
from .periodize import first_week_minutes
from .resolve import Target, targets


def duration(seconds: int) -> str:
    """'45 s', '6 min', '1 h 30 min'."""
    if seconds < 60 or (seconds < 120 and seconds % 60):
        return t("plan.fmt.seconds", s=seconds)
    m = round(seconds / 60)
    if m < 60:
        return t("plan.fmt.minutes", m=m)
    return t("plan.fmt.hours", h=m // 60) if m % 60 == 0 else t("plan.fmt.hours_minutes", h=m // 60, m=m % 60)


def hours(minutes: float) -> str:
    """Weekly volume as 'h:mm'."""
    m = round(minutes)
    return f"{m // 60}:{m % 60:02d}"


def part_line(part: Part, zone_targets: dict[str, Target]) -> dict[str, Any]:
    """One part as pieces: {"kind", "what", "zone", "zone_name", "target", "rest", "rpe"}."""
    tg = zone_targets.get(part.zone) if part.zone else None
    what = duration(part.duration_s) if part.duration_s else ""
    if part.repeat > 1:
        what = t("plan.fmt.repeat", n=part.repeat, each=what)
    rest = ""
    if part.repeat > 1 and part.rest_s:
        rest = t("plan.part.rest_jog" if part.rest_zone == "Z2" else "plan.part.rest", d=duration(part.rest_s))
    return {"kind": t(f"plan.part.{part.kind}"), "what": what, "zone": part.zone or "",
            "zone_name": tg.name if tg else (t(f"assessments.zones.garmin.{part.zone}.name") if part.zone else ""),
            "target": tg.text if tg else "", "rest": rest, "rpe": t("plan.part.rpe", v=part.rpe)}


def workout_lines(workout: Workout, capacity: Capacity | None, system: str) -> list[dict[str, Any]]:
    zt = targets(capacity, system)
    return [part_line(p, zt) for p in workout.parts]


def plain(line: dict[str, Any]) -> str:
    """A part as one line of text: 'Arbete: 4 × 6 min i Z4 (170–174 slag/min · 4:21–4:10 min/km), vila 1 min'."""
    s = f"{line['kind']}: {line['what']}".rstrip(": ").rstrip()
    if line["zone"]:
        s += " " + t("plan.fmt.in_zone", zone=line["zone"])
        if line["target"]:
            s += f" ({line['target']})"
    if line["rest"]:
        s += ", " + line["rest"]
    return s


def volume_start(plan: Plan) -> tuple[int, str]:
    """The weekly time the plan started from and why: "recent", "default" or "capped" (periodize).

    Read from the plan's first ordinary load week, so a saved plan says what it was built from."""
    _, source = first_week_minutes(plan.request)
    first = next((wk for wk in plan.weeks if wk.kind == "load" and not any(w.goal for w in wk.workouts)), plan.weeks[0])
    return first.target_minutes, source


def start_line(plan: Plan) -> str:
    """'Börjar på 3 h 20 min i veckan, det du tränar nu. Tiden växer till 5 h i vecka 29.'"""
    if not plan.weeks:
        return ""
    start, source = volume_start(plan)
    text = t(f"plan.start.{source}", start=duration(start * 60))
    loads = [wk for wk in plan.weeks if wk.kind == "load"]
    top = max((wk.target_minutes for wk in loads), default=start)
    if top > start:
        week = next(wk.index for wk in loads if wk.target_minutes == top)
        return f"{text} {t('plan.start.grows', top=duration(top * 60), week=week)}"
    return f"{text} {t('plan.start.flat')}"


def progress_axis(values: list[float]) -> tuple[int, int]:
    """The minute axis of the overview's progression panel (longest run, hard minutes): (top, step).
    The top leaves room for the value labels above the highest point."""
    peak = max(values + [30])
    step = 30 if peak <= 90 else 60
    top = -(-peak // step) * step
    if top - peak < 0.12 * top:          # room for the value labels above the highest point
        top += step // 2
    return int(top), step
