"""The workout library for running, as data. Every workout in a plan comes from a template here.

A template refers to zones, never to heart rates or paces. Quality templates carry their progression
as ``steps`` (total work time grows step by step); the engine picks the step, it never invents a
workout. Texts live in i18n: plan.workout.<id>.title / .purpose / .how.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import RUNNING, Part

MIN = 60


@dataclass(frozen=True)
class Template:
    id: str
    family: str                       # "easy" | "long" | "recovery" | "threshold" | "vo2max" | "fartlek" | "hills" | "test" | "race"
    quality: bool
    top_zone: str | None              # the hardest zone of the workout
    steps: tuple[tuple[Part, ...], ...] = ()   # fixed structures, easiest first; () = length set by the engine
    min_minutes: int = 20
    max_minutes: int = 240
    sport: str = RUNNING


def _set(warm_min: int, reps: int, work_s: int, rest_s: int, zone: str, rpe: int, cool_min: int = 10,
         rest_zone: str = "Z1") -> tuple[Part, ...]:
    return (Part("warmup", warm_min * MIN, "Z2", 3),
            Part("work", work_s, zone, rpe, repeat=reps, rest_s=rest_s, rest_zone=rest_zone),
            Part("cooldown", cool_min * MIN, "Z2", 2))


TEMPLATES: dict[str, Template] = {tpl.id: tpl for tpl in (
    Template("easy", "easy", False, "Z2", min_minutes=25, max_minutes=75),
    Template("recovery", "recovery", False, "Z2", min_minutes=20, max_minutes=45),
    Template("long", "long", False, "Z2", min_minutes=30),
    # The long run of the peak block for long races: the last 20 minutes just above LT1.
    Template("long_progressive", "long", False, "Z3", min_minutes=70),
    # Threshold intervals just below LT2. Progression: total work 20 -> 40 min.
    Template("threshold", "threshold", True, "Z4", steps=(
        _set(15, 4, 5 * MIN, 60, "Z4", 7), _set(15, 4, 6 * MIN, 60, "Z4", 7), _set(15, 5, 6 * MIN, 60, "Z4", 7),
        _set(15, 4, 8 * MIN, 120, "Z4", 7), _set(15, 3, 12 * MIN, 120, "Z4", 7), _set(15, 4, 10 * MIN, 120, "Z4", 7))),
    # Hard intervals above LT2. Progression: number first, then length (12 -> 24 min of work).
    Template("vo2max", "vo2max", True, "Z5", steps=(
        _set(15, 4, 3 * MIN, 120, "Z5", 9), _set(15, 5, 3 * MIN, 120, "Z5", 9), _set(15, 4, 4 * MIN, 180, "Z5", 9),
        _set(15, 6, 3 * MIN, 120, "Z5", 9), _set(15, 5, 4 * MIN, 180, "Z5", 9), _set(15, 6, 4 * MIN, 180, "Z5", 9))),
    # Fartlek: surges at threshold effort with easy running between, never above LT2.
    Template("fartlek", "fartlek", True, "Z4", steps=(
        _set(10, 5, 2 * MIN, 120, "Z4", 6, rest_zone="Z2"), _set(10, 6, 2 * MIN, 120, "Z4", 6, rest_zone="Z2"),
        _set(10, 8, 2 * MIN, 120, "Z4", 6, rest_zone="Z2"), _set(10, 6, 3 * MIN, 120, "Z4", 6, rest_zone="Z2"),
        _set(10, 8, 3 * MIN, 120, "Z4", 6, rest_zone="Z2"))),
    # Short hills: strength and running economy, jog back down as rest.
    Template("hills", "hills", True, "Z5", steps=(
        _set(15, 6, 45, 90, "Z5", 8), _set(15, 8, 45, 90, "Z5", 8), _set(15, 8, 60, 120, "Z5", 8),
        _set(15, 10, 60, 120, "Z5", 8), _set(15, 12, 60, 120, "Z5", 8))),
    # Race week: a short session that keeps the speed without tiring.
    Template("opener", "opener", True, "Z4", steps=(_set(10, 4, 1 * MIN, 120, "Z4", 6),)),
    # The retest at the coach: the lactate step test of the programme.
    Template("test", "test", True, None, steps=(
        (Part("warmup", 15 * MIN, "Z2", 3), Part("test", 35 * MIN, None, 10), Part("cooldown", 10 * MIN, "Z2", 2)),)),
    # A race has no planned parts: the goal itself.
    Template("race", "race", True, None, steps=((Part("race", 0, None, 10),),)),
)}


def get(template_id: str) -> Template:
    return TEMPLATES[template_id]


def last_step(template_id: str) -> int:
    return max(0, len(TEMPLATES[template_id].steps) - 1)


def minutes_of(template_id: str, step: int = 0) -> int:
    """Total length of a fixed template at a step, in whole minutes (rounded up)."""
    parts = TEMPLATES[template_id].steps[min(step, last_step(template_id))]
    return -(-sum(p.total_s for p in parts) // 60)


def build(template_id: str, *, minutes: int | None = None, step: int = 0) -> list[Part]:
    """The parts of a workout: a fixed template at ``step``, or an easy/long run of ``minutes``."""
    tpl = TEMPLATES[template_id]
    if tpl.steps:
        return list(tpl.steps[max(0, min(step, len(tpl.steps) - 1))])
    if minutes is None:
        raise ValueError(f"{template_id} needs a length")
    if template_id == "long_progressive":
        return [Part("steady", (minutes - 20) * MIN, "Z2", 4), Part("steady", 20 * MIN, "Z3", 6)]
    rpe = {"recovery": 2, "long": 4}.get(template_id, 3)
    return [Part("steady", minutes * MIN, "Z2", rpe)]
