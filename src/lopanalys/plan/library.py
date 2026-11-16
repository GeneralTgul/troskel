"""The workout library, as data: running; the bike, the SkiErg and swimming in the places of the running
templates; and strength, which has no zones. Every workout in a plan comes from a template here.

A template refers to zones, never to heart rates or paces. Quality templates carry their progression
as ``steps`` (total work time grows step by step); the engine picks the step, it never invents a
workout. Texts live in i18n: plan.workout.<id>.title / .purpose / .how.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .models import RUNNING, STRENGTH, SWIM, Part

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
    base: str = ""                    # the running template this one takes the place of in another sport


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


def _fit(like: tuple[Part, ...], warm_min: int, reps: int, work_s: int, rest_s: int, zone: str, rpe: int,
         rest_zone: str = "Z1") -> tuple[Part, ...]:
    """An interval set as long as the running step ``like``: the cool-down takes what the set leaves."""
    total = sum(p.total_s for p in like)
    cool = total - warm_min * MIN - reps * work_s - (reps - 1) * rest_s
    if cool < 5 * MIN:
        raise ValueError("the cool-down would be shorter than five minutes")
    return (Part("warmup", warm_min * MIN, "Z2", 3),
            Part("work", work_s, zone, rpe, repeat=reps, rest_s=rest_s, rest_zone=rest_zone),
            Part("cooldown", cool, "Z2", 2))


def _steps(run_id: str, *sets: tuple) -> tuple[tuple[Part, ...], ...]:
    """One step per running step, each as long as the running step (``_fit``)."""
    run = TEMPLATES[run_id].steps
    if len(sets) != len(run):
        raise ValueError(f"{run_id}: {len(run)} steps")
    return tuple(_fit(like, *s) for like, s in zip(run, sets))


def _band(steps: tuple[tuple[Part, ...], ...], band: str) -> tuple[tuple[Part, ...], ...]:
    """The steps with the work parts in a band of their zone (resolve.BANDS)."""
    return tuple(tuple(replace(p, band=band) if p.kind == "work" else p for p in st) for st in steps)


def _test(new_id: str, warm_min: int, test_min: int, cool_min: int) -> Template:
    """A sport's own test in the place of running's: its own warm-up and length, the whole session as long."""
    parts = (Part("warmup", warm_min * MIN, "Z2", 3), Part("test", test_min * MIN, None, 10),
             Part("cooldown", cool_min * MIN, "Z2", 2))
    run = TEMPLATES["test"]
    if sum(p.total_s for p in parts) != sum(p.total_s for p in run.steps[0]):
        raise ValueError(f"{new_id}: as long as running's test")
    return Template(new_id, run.family, run.quality, None, (parts,), run.min_minutes, run.max_minutes,
                    new_id.split("_")[0], base="test")


def _like(run_id: str, new_id: str, steps: tuple[tuple[Part, ...], ...] | None = None) -> Template:
    """A template of another sport in the place of a running template: the same family, effort and limits."""
    tpl = TEMPLATES[run_id]
    top = tpl.top_zone if steps is None else max(p.zone for st in steps for p in st if p.zone)
    return Template(new_id, tpl.family, tpl.quality, top, tpl.steps if steps is None else steps,
                    tpl.min_minutes, tpl.max_minutes, new_id.split("_")[0], base=run_id)


# ---- the other sports (#666, step 2)
# The engine fills the week with the running templates above; when a workout gets its sport (engine._sports) it
# takes the sport's template in the same place (``in_sport``): the same family and the same number of steps,
# each step exactly as long as the running step, so the week keeps its minutes. The hard minutes may differ:
# the engine checks the week again with the sport's templates. The test is the sport's own (``_test``): the
# lactate step test on the bike and the SkiErg (skis test on the SkiErg), the CSS test in the pool. A race stays.
# Bike: Z2 rides, sweet spot (88-94 % of the threshold power), threshold and VO2 intervals.
# SkiErg: easy double poling and intervals in the running structures. Skis and roller skis use the SkiErg's.
_BIKE = (
    _like("easy", "bike_easy"), _like("recovery", "bike_recovery"), _like("long", "bike_long"),
    _like("long_progressive", "bike_long_progressive"),
    # Sweet spot: long blocks just below the threshold, easy spinning between. Total work 20 -> 36 min.
    # The blocks carry the band "sweetspot", 88-94 % of the threshold power, and count as Z4: the zones split the
    # span between the thresholds in two, and the band lies in the upper half (resolve.BANDS).
    _like("fartlek", "bike_sweetspot", _band(_steps(
        "fartlek", (10, 2, 10 * MIN, 3 * MIN, "Z4", 6, "Z2"), (10, 2, 12 * MIN, 3 * MIN, "Z4", 6, "Z2"),
        (10, 3, 10 * MIN, 2 * MIN, "Z4", 6, "Z2"), (10, 2, 15 * MIN, 3 * MIN, "Z4", 6, "Z2"),
        (10, 3, 12 * MIN, 3 * MIN, "Z4", 6, "Z2")), "sweetspot")),
    # Threshold: longer intervals than running, the legs carry no impact. Total work 24 -> 45 min.
    _like("threshold", "bike_threshold", _steps(
        "threshold", (12, 3, 8 * MIN, 3 * MIN, "Z4", 7), (10, 3, 10 * MIN, 3 * MIN, "Z4", 7),
        (12, 3, 12 * MIN, 3 * MIN, "Z4", 7), (12, 2, 20 * MIN, 5 * MIN, "Z4", 7),
        (10, 4, 10 * MIN, 3 * MIN, "Z4", 7), (12, 3, 15 * MIN, 4 * MIN, "Z4", 7))),
    # VO2: three to four minutes hard with as long an easy spin between. Total work 12 -> 24 min.
    _like("vo2max", "bike_vo2max", _steps(
        "vo2max", (15, 4, 3 * MIN, 3 * MIN, "Z5", 9), (15, 5, 3 * MIN, 3 * MIN, "Z5", 9),
        (15, 4, 4 * MIN, 4 * MIN, "Z5", 9), (15, 5, 4 * MIN, 3 * MIN, "Z5", 9),
        (15, 5, 4 * MIN, 4 * MIN, "Z5", 9), (15, 6, 4 * MIN, 4 * MIN, "Z5", 9))),
    # Short VO2 intervals, one minute on and one off, in the place of the hills.
    _like("hills", "bike_vo2_short", _steps(
        "hills", (15, 8, MIN, MIN, "Z5", 8), (15, 10, MIN, MIN, "Z5", 8), (15, 12, MIN, MIN, "Z5", 8),
        (15, 14, MIN, MIN, "Z5", 8), (15, 16, MIN, MIN, "Z5", 8))),
    _like("opener", "bike_opener", _steps("opener", (10, 3, 2 * MIN, 2 * MIN, "Z4", 6))),
    # The lactate step test on the trainer: 6-8 steps of 5 minutes (assessments.library_power).
    _test("bike_test", 10, 40, 10),
)
_SKIERG = tuple(_like(run_id, f"skierg_{run_id}") for run_id in (
    "easy", "recovery", "long", "long_progressive", "threshold", "vo2max", "fartlek", "hills", "opener")) + (
    # The lactate step test on the SkiErg: 6-8 steps of 4 minutes with half a minute's pause for the sample.
    _test("skierg_test", 10, 35, 15),)
# Skis have no test of their own: the athlete tests on the SkiErg, whose zones the skis borrow (models.BORROWS).
_SKI = (_test("ski_test", 10, 35, 15),)
# Swimming (#666, step 3): on effort (RPE) without numbers, or with the pace per 100 m of a CSS test. Easy swimming,
# technique (drills at an easy effort, in the place of the recovery run) and intervals with short rests.
_SWIM = (
    _like("easy", "swim_easy"), _like("recovery", "swim_technique"), _like("long", "swim_long"),
    _like("long_progressive", "swim_long_progressive"),
    # Intervals just below the CSS pace, 30-60 s rest at the wall. Total work 24 -> 48 min.
    _like("threshold", "swim_intervals", _steps(
        "threshold", (10, 8, 3 * MIN, 30, "Z4", 7), (10, 10, 3 * MIN, 30, "Z4", 7), (10, 9, 4 * MIN, 30, "Z4", 7),
        (10, 6, 6 * MIN, 45, "Z4", 7), (10, 5, 8 * MIN, MIN, "Z4", 7), (10, 6, 8 * MIN, MIN, "Z4", 7))),
    # Hard intervals faster than CSS, a minute's rest. Number first, then length (10 -> 30 min of work).
    _like("vo2max", "swim_hard", _steps(
        "vo2max", (15, 10, MIN, MIN, "Z5", 9), (15, 12, MIN, MIN, "Z5", 9), (15, 8, 2 * MIN, MIN, "Z5", 9),
        (15, 10, 2 * MIN, MIN, "Z5", 9), (15, 8, 3 * MIN, MIN, "Z5", 9), (15, 10, 3 * MIN, MIN, "Z5", 9))),
    # Mixed pace: longer blocks between the thresholds with easy swimming between, in the place of the fartlek.
    _like("fartlek", "swim_mixed", _steps(
        "fartlek", (10, 4, 4 * MIN, MIN, "Z3", 6, "Z2"), (10, 5, 4 * MIN, MIN, "Z3", 6, "Z2"),
        (10, 6, 4 * MIN, MIN, "Z3", 6, "Z2"), (10, 4, 6 * MIN, MIN, "Z3", 6, "Z2"), (10, 5, 6 * MIN, MIN, "Z3", 6, "Z2"))),
    # Short sprints, 30 s on and 30 s off, in the place of the hills.
    _like("hills", "swim_sprints", _steps(
        "hills", (15, 8, 30, 30, "Z5", 8), (15, 12, 30, 30, "Z5", 8), (15, 16, 30, 30, "Z5", 8),
        (15, 20, 30, 30, "Z5", 8), (15, 24, 30, 30, "Z5", 8))),
    _like("opener", "swim_opener", _steps("opener", (10, 4, MIN, MIN, "Z4", 6))),
    # The CSS test (assessments.css): 400 m and, after a full rest, 200 m, each as fast as an even pace allows.
    _test("swim_test", 20, 25, 15),
)
TEMPLATES.update({tpl.id: tpl for tpl in _BIKE + _SKIERG + _SKI + _SWIM})
# Sport -> running template -> the sport's template. Every endurance sport has its own, the test too; a race
# stays as it is.
SPORT_TEMPLATES: dict[str, dict[str, str]] = {
    "bike": {tpl.base: tpl.id for tpl in _BIKE},
    "skierg": {tpl.base: tpl.id for tpl in _SKIERG},
    SWIM: {tpl.base: tpl.id for tpl in _SWIM},
}
SPORT_TEMPLATES["ski"] = {**SPORT_TEMPLATES["skierg"], **{tpl.base: tpl.id for tpl in _SKI}}


# ---- strength (#666, step 3): no zones, a fixed length, effort only. Never in the place of an endurance workout:
# the engine adds the sessions to the week (engine._Filler._strength).
def _strength(warm_min: int, main_min: int, rpe: int, mobility_min: int) -> tuple[tuple[Part, ...], ...]:
    return ((Part("warmup", warm_min * MIN, None, 3), Part("strength", main_min * MIN, None, rpe),
             Part("mobility", mobility_min * MIN, None, 2)),)


_STRENGTH = (
    Template("strength_full", "strength", False, None, _strength(10, 30, 7, 5), sport=STRENGTH),
    Template("strength_legs", "strength", False, None, _strength(10, 25, 7, 5), sport=STRENGTH),
    Template("strength_upper", "strength", False, None, _strength(8, 20, 6, 7), sport=STRENGTH),
    # The taper and the race week: shorter and lighter, no heavy loads.
    Template("strength_maintenance", "strength", False, None, _strength(8, 15, 5, 7), sport=STRENGTH),
)
TEMPLATES.update({tpl.id: tpl for tpl in _STRENGTH})
# The week's strength sessions by their number: the legs in the day with most room before a hard day (the engine).
STRENGTH_WEEK = {1: ("strength_full",), 2: ("strength_legs", "strength_upper"),
                 3: ("strength_legs", "strength_full", "strength_upper")}
STRENGTH_TAPER = "strength_maintenance"


def for_sport(template_id: str, sport: str) -> str:
    """The sport's template in the place of a running template (the same id when the sport has none)."""
    return SPORT_TEMPLATES.get(sport, {}).get(template_id, template_id)


def in_sport(parts: list[Part], template_id: str, sport: str) -> tuple[str, list[Part]]:
    """A running workout's template and parts moved to ``sport``: a fixed template at the same step, a
    workout of a free length with the same minutes. Unchanged when the sport has no template of its own."""
    new = for_sport(template_id, sport)
    if new == template_id:
        return template_id, parts
    run = TEMPLATES[template_id]
    if run.steps:
        step = next(i for i, st in enumerate(run.steps) if list(st) == list(parts))
        return new, build(new, step=step)
    return new, build(new, minutes=sum(p.total_s for p in parts) // MIN)


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
    kind = tpl.base or template_id
    if kind == "long_progressive":
        return [Part("steady", (minutes - 20) * MIN, "Z2", 4), Part("steady", 20 * MIN, "Z3", 6)]
    if template_id == "swim_technique":   # drills, easy all through
        return [Part("technique", minutes * MIN, "Z2", 3)]
    rpe = {"recovery": 2, "long": 4}.get(kind, 3)
    return [Part("steady", minutes * MIN, "Z2", rpe)]


# ---- the guide to a workout: how it should feel, tips, and drills for the technique swim
# The texts are plan.tip.<id> and plan.drill.<id>; a tip can serve several templates. The app's box for the day and
# the athlete's printout read them from here (the app's port: app/src/engine/library.ts, checked with the fixtures).
TIPS: dict[str, tuple[str, ...]] = {
    "easy": ("middle_zone", "too_easy"),
    "recovery": ("too_easy",),
    "long": ("carbs", "race_kit"),
    "long_progressive": ("carbs", "race_kit"),
    "threshold": ("even", "even_start", "jog_rest", "tired_z3"),
    "vo2max": ("even", "jog_rest", "relaxed"),
    "hills": ("treadmill_hills", "arms"),
    "opener": ("relaxed",),
    "race": ("nothing_new", "race_energy"),
    "bike_easy": ("cadence_drills",),
    "bike_recovery": ("too_easy",),
    "bike_long": ("carbs", "race_kit"),
    "bike_long_progressive": ("carbs",),
    "bike_sweetspot": ("seated",),
    "bike_threshold": ("even", "seated"),
    "bike_vo2max": ("even",),
    "bike_vo2_short": ("even",),
    "skierg_easy": ("pole_hips",),
    "skierg_recovery": ("pole_hips",),
    "skierg_long": ("pole_hips", "carbs", "race_kit"),
    "skierg_long_progressive": ("carbs",),
    "skierg_threshold": ("even_split",),
    "skierg_vo2max": ("even_split",),
    "skierg_fartlek": ("pole_hips",),
    "skierg_hills": ("pole_hips", "full_rest"),
    "swim_easy": ("breathe_three",),
    "swim_long": ("breathe_three",),
    "swim_intervals": ("even",),
    "swim_hard": ("even",),
    "swim_test": ("css_formula",),
    "strength_full": ("heavy_before",),
    "strength_legs": ("heavy_before",),
    "strength_maintenance": ("no_max",),
}
DRILLS: dict[str, tuple[str, ...]] = {
    "swim_technique": ("warmup", "catch_up", "fingertip", "single_arm", "six_kick", "fists", "sculling",
                       "breathe_three", "finish", "kit"),
}
# The parts that say how a workout feels: not the warm-up, the cool-down or the mobility after strength.
_EASING = ("warmup", "cooldown", "mobility")


def feel(parts: list[Part] | tuple[Part, ...]) -> tuple[str | None, str | None, int, int] | None:
    """How a workout should feel: its lowest and highest zone (None without a zone) and its lowest and highest
    effort (RPE of 10), over the parts that carry the workout, not the warm-up or the cool-down. None for a race."""
    main = [p for p in parts if p.kind not in _EASING and p.kind != "race"] or [p for p in parts if p.kind != "race"]
    if not main:
        return None
    zones = sorted(p.zone for p in main if p.zone)
    rpes = [p.rpe for p in main]
    return (zones[0] if zones else None, zones[-1] if zones else None, min(rpes), max(rpes))
