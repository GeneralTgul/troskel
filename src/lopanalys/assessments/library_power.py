"""Ready-made test protocols in watt: cycling on a trainer and SkiErg.

The load of a stage is its power in watt (``Stage.speed_kmh`` holds it; read it as ``Stage.load``),
and the incline is 0. The builders mirror the treadmill library:

  - ``bike_lactate_step``: lactate steps of 5 min, +25 W, the sample taken while
    the rider keeps pedalling (no pause). Stage length matters for where the thresholds land
    (Jamnick et al. 2020, Sports Med 50:1729); 3-8 min are in use and 5 min is a common advice.
  - ``bike_ramp``: +20 W per minute to exhaustion. Gives the highest heart rate, the best
    one-minute power (MAP) and an FTP estimate (75 % of MAP).
  - ``bike_ftp_20``: warm-up in ERG, then 20 minutes as hard as the rider can hold, without a
    power target (``Stage.free``). FTP is 95 % of the mean power of the 20 minutes.
  - ``skierg_lactate_step``: steps of 4 min, +15 W, 30 s pause for the sample (the hands are busy).
    The athlete holds the watt; the screen shows the target and the pace per 500 m.
  - ``skierg_ramp``: +15 W per minute to exhaustion.
  - ``bike_fit`` and ``skierg_technique``: the filmed technique tests (bike fit and double poling).
    A warm-up that is not filmed, then one stage at an even working load that is filmed and analysed
    by ``pose.bike`` or ``pose.ski``. Nothing else is measured in them: the tests
    should be as good as possible, so they are never combined with a threshold test.

The step sizes, start powers and levels are the program's own proposals (docs/sporter/
tester-och-zoner.md): no published SkiErg protocol was found, and the cycling numbers are common
practice, not one standard. They are to be tried on real athletes before they are called standard.
"""
from __future__ import annotations

from typing import Any

from lopanalys.assessments.library import TreadmillLimits, fmt_pct
from lopanalys.assessments.protocol import Protocol, Stage
from lopanalys.core.i18n import t
from lopanalys.core.models import TestKind
from lopanalys.core.sport import Sport, profile, sport_of

# The load range of a power ergometer as TreadmillLimits (the "speed" is the watt, no incline).
POWER_LIMITS = TreadmillLimits(min_speed_kmh=0.0, max_speed_kmh=2000.0, max_incline_pct=0.0, min_incline_pct=0.0)

# Estimated power at LT2 per level, in watt. Proposals, not from a source.
LEVELS_BIKE: dict[str, float] = {"beginner": 150.0, "recreational": 200.0, "trained": 260.0, "elite": 330.0}
LEVELS_SKIERG: dict[str, float] = {"beginner": 110.0, "recreational": 140.0, "trained": 190.0, "elite": 250.0}
LEVELS = {Sport.BIKE: LEVELS_BIKE, Sport.SKIERG: LEVELS_SKIERG}


def power_limits(ergometer: Any = None) -> TreadmillLimits:
    """The watt range of a connected ergometer (its ``min_speed_kmh``/``max_speed_kmh`` are the load
    limits), or the defaults."""
    if ergometer is None:
        return POWER_LIMITS
    hi = float(getattr(ergometer, "max_speed_kmh", 0) or POWER_LIMITS.max_speed_kmh)
    lo = float(getattr(ergometer, "min_speed_kmh", 0) or 0.0)
    return TreadmillLimits(min_speed_kmh=max(lo, 0.0), max_speed_kmh=hi, max_incline_pct=0.0, min_incline_pct=0.0)


def fmt_w(v: float) -> str:
    return f"{v:.0f}"


def _round5(v: float) -> float:
    return float(round(v / 5.0) * 5)


def start_power_for(sport: Any, level: str | None = None, estimated_lt2_w: float | None = None,
                    step_w: float = 25.0, stages_below: int = 4) -> float:
    """Start power so that ``stages_below`` stages fall below the estimated threshold."""
    levels = LEVELS[sport_of(sport)]
    lt2 = estimated_lt2_w if estimated_lt2_w else levels.get(level or "recreational", levels["recreational"])
    return max(40.0, _round5(lt2 - stages_below * step_w))


def _pace_note(sport: Sport, first_w: float, last_w: float) -> str:
    p = profile(sport)
    if not p.pace_unit:
        return ""
    return " " + t("assessments.desc.pace_range", first=p.pace_str(first_w), last=p.pace_str(last_w), unit=p.pace_unit)


def _lactate_step(sport: Sport, builder: str, start_w: float, step_w: float, stages: int, stage_s: float,
                  pause_s: float, warmup_s: float, warmup_w: float | None, warmup_lactate: bool,
                  limits: TreadmillLimits) -> Protocol:
    if stages < 1:
        raise ValueError(t("assessments.error.stages"))
    if step_w <= 0 or stage_s <= 0:
        raise ValueError(t("assessments.error.step"))
    st: list[Stage] = []
    if warmup_s > 0:
        wk = limits.speed(warmup_w if warmup_w else max(_round5(start_w * 0.8), 30.0))
        st.append(Stage(wk, 0.0, warmup_s, t("assessments.stage.warmup"), sample_lactate=warmup_lactate,
                        is_warmup=True))
    for i in range(stages):
        st.append(Stage(limits.speed(start_w + i * step_w), 0.0, stage_s, t("assessments.stage.n", n=i + 1),
                        sample_lactate=True))
    work = [s for s in st if not s.is_warmup]
    sampling = (t(f"assessments.desc.pause_{sport.value}", s=int(pause_s)) if pause_s > 0
                else t(f"assessments.desc.on_the_move_{sport.value}"))
    return Protocol(
        name=t(f"assessments.protocol.{sport.value}.lactate.name"),
        kind=TestKind.LACTATE_STEP.value,
        stages=st,
        lactate_pause_s=float(max(pause_s, 0)),
        description=t(f"assessments.protocol.{sport.value}.lactate.desc", start=fmt_w(work[0].load), step=fmt_w(step_w),
                      min=fmt_pct(stage_s / 60), stages=stages, sampling=sampling)
        + _pace_note(sport, work[0].load, work[-1].load) + " " + t(f"assessments.protocol.{sport.value}.lactate.end"),
        builder=builder,
        params=dict(start_w=start_w, step_w=step_w, stages=stages, stage_s=stage_s, pause_s=pause_s,
                    warmup_s=warmup_s, warmup_w=warmup_w, warmup_lactate=warmup_lactate),
        sport=sport.value,
        overflow_speed_kmh=0.0, overflow_incline_pct=0.0,
    )


def _ramp(sport: Sport, builder: str, start_w: float, step_w: float, step_s: float, warmup_s: float,
          warmup_w: float | None, limits: TreadmillLimits) -> Protocol:
    if step_w <= 0 or step_s <= 0:
        raise ValueError(t("assessments.error.step"))
    st: list[Stage] = []
    if warmup_s > 0:
        wk = limits.speed(warmup_w if warmup_w else max(_round5(start_w * 0.8), 30.0))
        st.append(Stage(wk, 0.0, warmup_s, t("assessments.stage.warmup"), is_warmup=True))
    st.append(Stage(limits.speed(start_w), 0.0, step_s, t("assessments.stage.ramp", n=1)))
    return Protocol(
        name=t(f"assessments.protocol.{sport.value}.ramp.name"),
        kind=TestKind.MAX_HR.value,
        stages=st,
        lactate_pause_s=0.0,
        open_ended=True,
        increment_speed_kmh=float(step_w),
        increment_incline_pct=0.0,
        description=t(f"assessments.protocol.{sport.value}.ramp.desc", warmup=int(round(warmup_s / 60)),
                      start=fmt_w(st[-1].load), step=fmt_w(step_w), s=int(step_s))
        + _pace_note(sport, st[-1].load, st[-1].load + 8 * step_w),
        builder=builder,
        params=dict(start_w=start_w, step_w=step_w, step_s=step_s, warmup_s=warmup_s, warmup_w=warmup_w),
        sport=sport.value,
        overflow_speed_kmh=0.0, overflow_incline_pct=0.0,
        max_open_ended_s=2400.0,
    )


# ------------------------------------------------------------------------------------- cycling
def bike_lactate_step(start_w: float, step_w: float = 25.0, stages: int = 7, stage_s: float = 300,
                      pause_s: float = 0, *, warmup_s: float = 600, warmup_w: float | None = None,
                      warmup_lactate: bool = True, limits: TreadmillLimits = POWER_LIMITS) -> Protocol:
    """Lactate steps on the trainer in ERG mode: 5 min per step, the sample taken on the move."""
    return _lactate_step(Sport.BIKE, "bike_lactate_step", start_w, step_w, stages, stage_s, pause_s, warmup_s,
                         warmup_w, warmup_lactate, limits)


def bike_lactate_step_for_level(level: str = "recreational", estimated_lt2_w: float | None = None,
                                step_w: float | None = None, **kw: Any) -> Protocol:
    step = step_w or (30.0 if level in ("trained", "elite") else 25.0)
    p = bike_lactate_step(start_power_for(Sport.BIKE, level, estimated_lt2_w, step), step, **kw)
    if level in LEVELS_BIKE:
        p.name = t("assessments.protocol.bike.lactate.name_level", level=t(f"assessments.level.{level}"))
    p.params.update(level=level)
    return p


def bike_ramp(start_w: float = 100.0, step_w: float = 20.0, step_s: float = 60, *, warmup_s: float = 600,
              warmup_w: float | None = None, limits: TreadmillLimits = POWER_LIMITS) -> Protocol:
    """Ramp test on the trainer: a higher watt every minute to exhaustion (max HR, MAP and FTP)."""
    return _ramp(Sport.BIKE, "bike_ramp", start_w, step_w, step_s, warmup_s, warmup_w, limits)


def bike_ftp_20(target_w: float = 200.0, *, warmup_s: float = 900, warmup_w: float | None = None,
                effort_s: float = 1200, limits: TreadmillLimits = POWER_LIMITS) -> Protocol:
    """The 20 minute FTP test: warm-up in ERG, then ``effort_s`` as hard as the rider can hold with
    no power target. ``target_w`` is only a number to aim at on the screen (the stage is ``free``)."""
    if effort_s <= 0:
        raise ValueError(t("assessments.error.step"))
    st: list[Stage] = []
    if warmup_s > 0:
        st.append(Stage(limits.speed(warmup_w if warmup_w else max(_round5(target_w * 0.55), 30.0)), 0.0, warmup_s,
                        t("assessments.stage.warmup"), is_warmup=True))
    st.append(Stage(limits.speed(target_w), 0.0, effort_s, t("assessments.stage.ftp20"), free=True))
    return Protocol(
        name=t("assessments.protocol.bike.ftp20.name"),
        kind=TestKind.FTP.value,
        stages=st,
        lactate_pause_s=0.0,
        description=t("assessments.protocol.bike.ftp20.desc", warmup=int(round(warmup_s / 60)),
                      min=fmt_pct(effort_s / 60), target=fmt_w(st[-1].load)),
        builder="bike_ftp_20",
        params=dict(target_w=target_w, warmup_s=warmup_s, warmup_w=warmup_w, effort_s=effort_s),
        sport=Sport.BIKE.value,
        overflow_speed_kmh=0.0, overflow_incline_pct=0.0,
    )


# ------------------------------------------------------------------------------------- SkiErg
def skierg_lactate_step(start_w: float, step_w: float = 15.0, stages: int = 7, stage_s: float = 240,
                        pause_s: float = 30, *, warmup_s: float = 600, warmup_w: float | None = None,
                        warmup_lactate: bool = True, limits: TreadmillLimits = POWER_LIMITS) -> Protocol:
    """Lactate steps on the SkiErg: 4 min per step and a pause for the sample. The athlete holds the
    watt the screen shows; the stage's load is the watt that was measured."""
    return _lactate_step(Sport.SKIERG, "skierg_lactate_step", start_w, step_w, stages, stage_s, pause_s, warmup_s,
                         warmup_w, warmup_lactate, limits)


def skierg_lactate_step_for_level(level: str = "recreational", estimated_lt2_w: float | None = None,
                                  step_w: float | None = None, **kw: Any) -> Protocol:
    step = step_w or (20.0 if level in ("trained", "elite") else 15.0)
    p = skierg_lactate_step(start_power_for(Sport.SKIERG, level, estimated_lt2_w, step), step, **kw)
    if level in LEVELS_SKIERG:
        p.name = t("assessments.protocol.skierg.lactate.name_level", level=t(f"assessments.level.{level}"))
    p.params.update(level=level)
    return p


def skierg_ramp(start_w: float = 80.0, step_w: float = 15.0, step_s: float = 60, *, warmup_s: float = 600,
                warmup_w: float | None = None, limits: TreadmillLimits = POWER_LIMITS) -> Protocol:
    """Ramp on the SkiErg: a higher target watt every minute to exhaustion (highest heart rate, peak power)."""
    return _ramp(Sport.SKIERG, "skierg_ramp", start_w, step_w, step_s, warmup_s, warmup_w, limits)


# ------------------------------------------------------------------------- filmed technique tests
# The working load of the filmed stage per level, in watt: a steady endurance effort the athlete can
# hold with good form for several minutes (proposals; the coach sets it in the guide).
FIT_W = {"bike": 150.0, "skierg": 120.0}


def _technique(sport: Sport, builder: str, kind: TestKind, work_w: float, film_s: float, warmup_s: float,
               warmup_w: float | None, limits: TreadmillLimits) -> Protocol:
    if film_s < 30 or work_w <= 0:
        raise ValueError(t("assessments.error.film_len"))
    st: list[Stage] = []
    if warmup_s > 0:
        st.append(Stage(limits.speed(warmup_w if warmup_w else max(_round5(work_w * 0.7), 30.0)), 0.0, warmup_s,
                        t("assessments.stage.warmup"), is_warmup=True))
    st.append(Stage(limits.speed(work_w), 0.0, film_s, t("assessments.stage.film")))
    key = f"assessments.protocol.{sport.value}.{kind.value}"
    return Protocol(
        name=t(f"{key}.name"),
        kind=kind.value,
        stages=st,
        lactate_pause_s=0.0,
        description=t(f"{key}.desc", warmup=int(round(warmup_s / 60)), w=fmt_w(st[-1].load), min=fmt_pct(film_s / 60)),
        builder=builder,
        params=dict(work_w=work_w, film_s=film_s, warmup_s=warmup_s, warmup_w=warmup_w),
        sport=sport.value,
        overflow_speed_kmh=0.0, overflow_incline_pct=0.0,
    )


def bike_fit(work_w: float = FIT_W["bike"], film_s: float = 180, *, warmup_s: float = 300,
             warmup_w: float | None = None, limits: TreadmillLimits = POWER_LIMITS) -> Protocol:
    """Bike fit: 5 min warm-up in ERG (not filmed), then ``film_s`` at an even working power, filmed from
    the side (and from the front with a second camera). The position changes with the load, so the
    film is taken at the power the rider trains at, not at an easy spin."""
    return _technique(Sport.BIKE, "bike_fit", TestKind.BIKEFIT, work_w, film_s, warmup_s, warmup_w, limits)


def skierg_technique(work_w: float = FIT_W["skierg"], film_s: float = 120, *, warmup_s: float = 300,
                     warmup_w: float | None = None, limits: TreadmillLimits = POWER_LIMITS) -> Protocol:
    """Double poling technique: warm-up (not filmed), then ``film_s`` at an even target power, filmed
    from the side."""
    return _technique(Sport.SKIERG, "skierg_technique", TestKind.SKI_TECHNIQUE, work_w, film_s, warmup_s, warmup_w,
                      limits)


BUILDERS = {f.__name__: f for f in (bike_lactate_step, bike_ramp, bike_ftp_20, skierg_lactate_step, skierg_ramp,
                                     bike_fit, skierg_technique)}


def default_protocols(sport: Any, limits: TreadmillLimits = POWER_LIMITS) -> list[Protocol]:
    """The protocols shown in the test picker for cycling or SkiErg."""
    sp = sport_of(sport)
    if sp is Sport.BIKE:
        return [
            bike_lactate_step_for_level("recreational", limits=limits),
            bike_lactate_step_for_level("trained", limits=limits),
            bike_lactate_step_for_level("beginner", limits=limits),
            bike_ramp(100.0, 20.0, limits=limits),
            bike_ramp(150.0, 25.0, limits=limits),
            bike_ftp_20(200.0, limits=limits),
            bike_fit(limits=limits),
        ]
    if sp is Sport.SKIERG:
        return [
            skierg_lactate_step_for_level("recreational", limits=limits),
            skierg_lactate_step_for_level("trained", limits=limits),
            skierg_ramp(80.0, 15.0, limits=limits),
            skierg_ramp(120.0, 20.0, limits=limits),
            skierg_technique(limits=limits),
        ]
    return []
