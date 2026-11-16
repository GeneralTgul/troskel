"""Ready-made treadmill test protocols, built from a few parameters.

Every builder returns an :class:`~lopanalys.assessments.protocol.Protocol` with Swedish stage labels
and a description the coach can read before the test. Speeds and inclines are clamped to the
treadmill's range (:class:`TreadmillLimits`); the runner clamps again against the real device.

Protocol choices follow common exercise-physiology lab practice:
  - Lactate step test: 3-5 min stages (4 min is the usual compromise for a lactate steady state
    per stage), +0.5-1.0 km/h per stage, 1 % incline to mimic outdoor air resistance
    (Jones & Doust 1996), 6-9 stages, 20-60 s pause for a capillary sample or sampling on the move.
    See Jamnick et al. 2020 (Sports Med 50:1729) on how protocol design changes thresholds.
  - Max HR ramp: thorough warm-up, then a short ramp (incline +1 %/min at constant speed, or
    +0.5 km/h per 30 s) to volitional exhaustion in about 8-12 min, optional final sprint.
  - Combined: lactate steps, then straight into the ramp (one visit gives LT1, LT2 and max HR).
  - Heart rate and speed zones (no lactate): 4-5 steps of 3 min (4 min can be chosen) without
    pauses, the last one a little below the estimated threshold so that the heart rate settles in every step, then
    straight into a speed ramp (+0.5 km/h per minute) to exhaustion. Constant 1 % incline.
  - Gait check: 2-3 speeds x 120 s, recorded on video: the stride settles after the speed change,
    and there are more steps to analyse.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lopanalys.assessments.protocol import Protocol, Stage
from lopanalys.core.i18n import t
from lopanalys.core.models import TestKind
from lopanalys.core.sport import Sport, profile, sport_of


@dataclass(frozen=True)
class TreadmillLimits:
    """Speed/incline range of a treadmill. Defaults fit most gym and lab treadmills."""
    min_speed_kmh: float = 1.0
    max_speed_kmh: float = 20.0
    max_incline_pct: float = 15.0
    min_incline_pct: float = 0.0

    @staticmethod
    def of(treadmill: Any) -> "TreadmillLimits":
        """Limits of a connected devices.base.Treadmill (falls back to defaults per attribute)."""
        d = TreadmillLimits()
        return TreadmillLimits(
            min_speed_kmh=float(getattr(treadmill, "min_speed_kmh", d.min_speed_kmh) or d.min_speed_kmh),
            max_speed_kmh=float(getattr(treadmill, "max_speed_kmh", d.max_speed_kmh) or d.max_speed_kmh),
            max_incline_pct=float(getattr(treadmill, "max_incline_pct", d.max_incline_pct) or d.max_incline_pct),
            min_incline_pct=float(getattr(treadmill, "min_incline_pct", d.min_incline_pct) or 0.0),
        )

    def speed(self, v: float) -> float:
        return round(min(max(v, self.min_speed_kmh), self.max_speed_kmh), 2)

    def incline(self, v: float) -> float:
        return round(min(max(v, self.min_incline_pct), self.max_incline_pct), 2)


DEFAULT_LIMITS = TreadmillLimits()

# Estimated LT2 (anaerobic threshold) speed per level, km/h at 1 % incline. Used to pick the
# start speed so that about 4 stages fall below LT2 and 2-3 above it.
LEVELS: dict[str, float] = {
    "beginner": 10.0,      # motionär som springer lite
    "recreational": 12.5,  # tränar regelbundet, mil på ~50 min
    "trained": 15.0,       # tävlar, mil på ~40 min
    "elite": 18.0,         # mil under ~33 min
}


def _round_half(v: float) -> float:
    return round(v * 2) / 2


def fmt_kmh(v: float) -> str:
    """Swedish decimal comma, one decimal: 12.0 -> "12,0"."""
    return f"{v:.1f}".replace(".", ",")


def fmt_pct(v: float) -> str:
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def start_speed_for(level: str | None = None, estimated_lt2_kmh: float | None = None,
                    step_kmh: float = 1.0, stages_below: int = 4) -> float:
    """Start speed so that `stages_below` stages fall below the estimated threshold."""
    lt2 = estimated_lt2_kmh if estimated_lt2_kmh else LEVELS.get(level or "recreational", 12.5)
    return max(6.0, _round_half(lt2 - stages_below * step_kmh))


def lactate_step(start_kmh: float, step_kmh: float = 1.0, stages: int = 7, stage_s: float = 240,
                 incline_pct: float = 1.0, pause_s: float = 30, *, warmup_s: float = 600,
                 warmup_kmh: float | None = None, warmup_lactate: bool = True,
                 limits: TreadmillLimits = DEFAULT_LIMITS) -> Protocol:
    """Incremental lactate step test.

    Warm-up (default 10 min, 2 km/h below the first stage, with a baseline sample), then
    `stages` stages of `stage_s` seconds, +`step_kmh` per stage, a blood sample after each.
    `pause_s` = 0 means the sample is taken on the move (the belt keeps running).
    """
    if stages < 1:
        raise ValueError(t("assessments.error.stages"))
    if step_kmh <= 0 or stage_s <= 0:
        raise ValueError(t("assessments.error.step"))
    inc = limits.incline(incline_pct)
    st: list[Stage] = []
    if warmup_s > 0:
        wk = limits.speed(warmup_kmh if warmup_kmh else max(start_kmh - 2.0, 5.0))
        st.append(Stage(wk, inc, warmup_s, t("assessments.stage.warmup"),
                        sample_lactate=warmup_lactate, is_warmup=True))
    for i in range(stages):
        st.append(Stage(limits.speed(start_kmh + i * step_kmh), inc, stage_s,
                        t("assessments.stage.n", n=i + 1), sample_lactate=True))
    sampling = (t("assessments.desc.pause", s=int(pause_s)) if pause_s > 0
                else t("assessments.desc.on_the_move"))
    return Protocol(
        name=t("assessments.protocol.lactate.name"),
        kind=TestKind.LACTATE_STEP.value,
        stages=st,
        lactate_pause_s=float(max(pause_s, 0)),
        description=t("assessments.protocol.lactate.desc", start=fmt_kmh(st[1 if warmup_s > 0 else 0].speed_kmh),
                      step=fmt_kmh(step_kmh), min=fmt_pct(stage_s / 60), stages=stages,
                      incline=fmt_pct(inc), sampling=sampling) + " " + t("assessments.protocol.lactate.end"),
        builder="lactate_step",
        params=dict(start_kmh=start_kmh, step_kmh=step_kmh, stages=stages, stage_s=stage_s,
                    incline_pct=incline_pct, pause_s=pause_s, warmup_s=warmup_s,
                    warmup_kmh=warmup_kmh, warmup_lactate=warmup_lactate),
    )


def lactate_step_for_level(level: str = "recreational", estimated_lt2_kmh: float | None = None,
                           step_kmh: float = 1.0, **kw: Any) -> Protocol:
    """Lactate step test with the start speed chosen from level or an estimated threshold speed."""
    start = start_speed_for(level, estimated_lt2_kmh, step_kmh)
    p = lactate_step(start, step_kmh, **kw)
    if not estimated_lt2_kmh and level in LEVELS:
        p.name = t("assessments.protocol.lactate.name_level", level=t(f"assessments.level.{level}"))
    p.params.update(level=level, estimated_lt2_kmh=estimated_lt2_kmh)
    return p


def max_hr_ramp(speed_kmh: float = 12.0, *, mode: str = "incline", start_incline_pct: float = 1.0,
                incline_step_pct: float = 1.0, step_s: float = 60, speed_step_kmh: float = 0.5,
                warmup_s: float = 600, warmup_kmh: float | None = None, final_sprint_s: float = 0,
                final_sprint_kmh: float = 2.0, final_sprint_auto: bool = False,
                limits: TreadmillLimits = DEFAULT_LIMITS) -> Protocol:
    """Ramp to exhaustion for maximal heart rate.

    mode "incline": constant `speed_kmh`, incline +`incline_step_pct` every `step_s` (default 60 s).
    mode "speed": constant incline, speed +`speed_step_kmh` every `step_s` (use step_s=30).
    Open-ended: continues until the coach presses "Utmattad" (or the safety cap).
    `final_sprint_s` > 0 lets the coach start a final sprint (+`final_sprint_kmh`); with
    `final_sprint_auto` the runner starts it itself (see runner.SPRINT_AFTER_S).
    At the treadmill's highest incline the speed rises instead, and the other way round (ramp.py).
    """
    if mode not in ("incline", "speed"):
        raise ValueError(t("assessments.error.mode"))
    v = limits.speed(speed_kmh)
    inc = limits.incline(start_incline_pct)
    st: list[Stage] = []
    if warmup_s > 0:
        wk = limits.speed(warmup_kmh if warmup_kmh else max(v - 2.0, 6.0))
        # the warm-up and step 1 have the same incline, so step N of an incline ramp has N % at 1 %/step
        st.append(Stage(wk, inc if mode == "incline" else limits.incline(1.0), warmup_s,
                        t("assessments.stage.warmup"), is_warmup=True))
    st.append(Stage(v, inc, step_s, t("assessments.stage.ramp", n=1)))
    if mode == "incline":
        d_speed, d_inc = 0.0, incline_step_pct
        how = t("assessments.desc.ramp_incline", speed=fmt_kmh(v), start=fmt_pct(inc),
                step=fmt_pct(incline_step_pct), s=int(step_s))
    else:
        d_speed, d_inc = speed_step_kmh, 0.0
        how = t("assessments.desc.ramp_speed", step=fmt_kmh(speed_step_kmh), s=int(step_s), incline=fmt_pct(inc))
    sprint = (t("assessments.desc.sprint_auto" if final_sprint_auto else "assessments.desc.sprint",
                s=int(final_sprint_s), kmh=fmt_kmh(final_sprint_kmh)) if final_sprint_s > 0 else "")
    sprint = (_overflow_text(mode) + " " + sprint).strip()
    return Protocol(
        name=t("assessments.protocol.maxhr.name_" + mode),
        kind=TestKind.MAX_HR.value,
        stages=st,
        lactate_pause_s=0.0,
        open_ended=True,
        increment_speed_kmh=d_speed,
        increment_incline_pct=d_inc,
        final_sprint_s=float(final_sprint_s),
        final_sprint_kmh=float(final_sprint_kmh) if final_sprint_s > 0 else 0.0,
        final_sprint_auto=bool(final_sprint_auto and final_sprint_s > 0),
        description=t("assessments.protocol.maxhr.desc", warmup=int(round(warmup_s / 60)), how=how, sprint=sprint),
        builder="max_hr_ramp",
        params=dict(speed_kmh=speed_kmh, mode=mode, start_incline_pct=start_incline_pct,
                    incline_step_pct=incline_step_pct, step_s=step_s, speed_step_kmh=speed_step_kmh,
                    warmup_s=warmup_s, warmup_kmh=warmup_kmh, final_sprint_s=final_sprint_s,
                    final_sprint_kmh=final_sprint_kmh, final_sprint_auto=final_sprint_auto),
    )


def lactate_to_max(start_kmh: float, step_kmh: float = 1.0, stages: int = 6, stage_s: float = 240,
                   incline_pct: float = 1.0, pause_s: float = 30, *, ramp_step_s: float = 60,
                   ramp_incline_step_pct: float = 1.0, warmup_s: float = 600,
                   limits: TreadmillLimits = DEFAULT_LIMITS) -> Protocol:
    """Combined test: lactate steps, then (after the last sample) an incline ramp to exhaustion.

    The ramp keeps the speed of the last lactate stage; its step 1 has the lactate stages' incline
    (step N has N % at 1 % per step) and every `ramp_step_s` adds `ramp_incline_step_pct`.
    One visit gives LT1, LT2, zones and max HR.
    """
    base = lactate_step(start_kmh, step_kmh, stages, stage_s, incline_pct, pause_s,
                        warmup_s=warmup_s, limits=limits)
    last = base.stages[-1]
    base.stages.append(Stage(last.speed_kmh, last.incline_pct, ramp_step_s, t("assessments.stage.ramp", n=1)))
    base.name = t("assessments.protocol.combined.name")
    base.kind = TestKind.LACTATE_STEP.value
    base.open_ended = True
    base.increment_speed_kmh = 0.0
    base.increment_incline_pct = ramp_incline_step_pct
    base.description = base.description.removesuffix(" " + t("assessments.protocol.lactate.end")) + " " + t(
        "assessments.protocol.combined.desc", speed=fmt_kmh(last.speed_kmh), start=fmt_pct(last.incline_pct),
        step=fmt_pct(ramp_incline_step_pct), s=int(ramp_step_s)) + " " + _overflow_text("incline")
    base.builder = "lactate_to_max"
    base.params = dict(start_kmh=start_kmh, step_kmh=step_kmh, stages=stages, stage_s=stage_s,
                       incline_pct=incline_pct, pause_s=pause_s, ramp_step_s=ramp_step_s,
                       ramp_incline_step_pct=ramp_incline_step_pct, warmup_s=warmup_s)
    return base


def _overflow_text(mode: str) -> str:
    """What happens at the treadmill's limit (Protocol.overflow_*, ramp.next_step)."""
    if mode == "incline":
        return t("assessments.desc.overflow_speed", step=fmt_kmh(Protocol.overflow_speed_kmh))
    return t("assessments.desc.overflow_incline", step=fmt_pct(Protocol.overflow_incline_pct))


# The zones test's steps: 3 min is enough for the heart rate to settle below the threshold (the steady
# value is the mean of the last minute, hr_zones.STEADY_WINDOW_S) and keeps the test short; 4 min can be
# chosen. Changed from 4 min and a 10 min warm-up after a test on the treadmill (2026-11-15).
HR_ZONES_STAGE_S = 180.0
HR_ZONES_WARMUP_S = 360.0


def hr_zones_start_for(level: str | None = None, estimated_lt2_kmh: float | None = None,
                        step_kmh: float = 1.0, stages: int = 5) -> float:
    """Start speed so that the last steady step lands half a km/h below the estimated threshold:
    above it the heart rate does not settle in a step."""
    lt2 = estimated_lt2_kmh if estimated_lt2_kmh else LEVELS.get(level or "recreational", 12.5)
    return max(6.0, _round_half(lt2 - 0.5 - (stages - 1) * step_kmh))


def hr_zones(start_kmh: float, step_kmh: float = 1.0, stages: int = 5, stage_s: float = HR_ZONES_STAGE_S,
             incline_pct: float = 1.0, *, ramp_step_s: float = 60, ramp_speed_step_kmh: float = 0.5,
             warmup_s: float = HR_ZONES_WARMUP_S, warmup_kmh: float | None = None,
             final_sprint_s: float = 30, final_sprint_kmh: float = 2.0,
             limits: TreadmillLimits = DEFAULT_LIMITS) -> Protocol:
    """Heart rate and speed zones without lactate: steady steps, then a speed ramp to exhaustion.

    Warm-up, `stages` steps of `stage_s` seconds (+`step_kmh` each) with no pause and no blood
    sample, then at once the ramp: +`ramp_speed_step_kmh` every `ramp_step_s` from the last step's
    speed until the athlete stops. The incline stays at `incline_pct` all the way, so one relation
    between heart rate and speed holds for the whole test. Runs without anyone at the computer.
    `final_sprint_s` > 0: the coach can start a final sprint in the ramp (*Slutspurt*, +`final_sprint_kmh`;
    at the belt's highest speed the incline rises instead, see runner.SPRINT_INCLINE_PCT).
    """
    if ramp_step_s <= 0 or ramp_speed_step_kmh <= 0:
        raise ValueError(t("assessments.error.step"))
    base = lactate_step(start_kmh, step_kmh, stages, stage_s, incline_pct, 0, warmup_s=warmup_s,
                        warmup_kmh=warmup_kmh, warmup_lactate=False, limits=limits)
    for s in base.stages:
        s.sample_lactate = False
    last = base.stages[-1]
    base.stages.append(Stage(limits.speed(last.speed_kmh + ramp_speed_step_kmh), last.incline_pct,
                             ramp_step_s, t("assessments.stage.ramp", n=1)))
    first = base.stages[1 if warmup_s > 0 else 0]
    base.name = t("assessments.protocol.hrzones.name")
    base.kind = TestKind.HR_ZONES.value
    base.lactate_pause_s = 0.0
    base.open_ended = True
    base.increment_speed_kmh = ramp_speed_step_kmh
    base.increment_incline_pct = 0.0
    base.description = t("assessments.protocol.hrzones.desc", warmup=int(round(warmup_s / 60)), stages=stages,
                         min=fmt_pct(stage_s / 60), start=fmt_kmh(first.speed_kmh), step=fmt_kmh(step_kmh),
                         incline=fmt_pct(last.incline_pct), ramp=fmt_kmh(ramp_speed_step_kmh), s=int(ramp_step_s))
    if final_sprint_s > 0:
        base.final_sprint_s, base.final_sprint_kmh = float(final_sprint_s), float(final_sprint_kmh)
        base.description += " " + t("assessments.desc.sprint", s=int(final_sprint_s), kmh=fmt_kmh(final_sprint_kmh))
    base.builder = "hr_zones"
    base.params = dict(start_kmh=start_kmh, step_kmh=step_kmh, stages=stages, stage_s=stage_s,
                       incline_pct=incline_pct, ramp_step_s=ramp_step_s,
                       ramp_speed_step_kmh=ramp_speed_step_kmh, warmup_s=warmup_s, warmup_kmh=warmup_kmh,
                       final_sprint_s=final_sprint_s, final_sprint_kmh=final_sprint_kmh)
    return base


def hr_zones_for_level(level: str = "recreational", estimated_lt2_kmh: float | None = None,
                       step_kmh: float = 1.0, stages: int = 5, **kw: Any) -> Protocol:
    """The zones test with the start speed chosen from level, or from a threshold speed estimated
    in an earlier test (then named after that)."""
    p = hr_zones(hr_zones_start_for(level, estimated_lt2_kmh, step_kmh, stages), step_kmh, stages, **kw)
    if estimated_lt2_kmh:
        p.name = t("assessments.protocol.hrzones.name_previous")
    elif level in LEVELS:
        p.name = t("assessments.protocol.hrzones.name_level", level=t(f"assessments.level.{level}"))
    p.params.update(level=level, estimated_lt2_kmh=estimated_lt2_kmh)
    return p


def gait_check(speeds_kmh: list[float], stage_s: float = 120, *, incline_pct: float = 1.0,
               warmup_s: float = 0, limits: TreadmillLimits = DEFAULT_LIMITS) -> Protocol:
    """Running-technique check: each speed for `stage_s` seconds with video and gait metrics."""
    if not speeds_kmh:
        raise ValueError(t("assessments.error.speeds"))
    inc = limits.incline(incline_pct)
    st: list[Stage] = []
    if warmup_s > 0:
        st.append(Stage(limits.speed(min(speeds_kmh)), inc, warmup_s, t("assessments.stage.warmup"), is_warmup=True))
    for i, v in enumerate(speeds_kmh):
        # pace first, km/h after: the runner reads the pace
        label = t("assessments.stage.speed", n=i + 1, pace=profile(Sport.RUN).pace_str(limits.speed(v)),
                  kmh=fmt_kmh(limits.speed(v)))
        st.append(Stage(limits.speed(v), inc, stage_s, label,
                        record_gait=True))
    return Protocol(
        name=t("assessments.protocol.gait.name"),
        kind=TestKind.GAIT.value,
        stages=st,
        lactate_pause_s=0.0,
        description=t("assessments.protocol.gait.desc", speeds=", ".join(fmt_kmh(s.speed_kmh) for s in st if not s.is_warmup),
                      s=int(stage_s), incline=fmt_pct(inc)),
        builder="gait_check",
        params=dict(speeds_kmh=list(speeds_kmh), stage_s=stage_s, incline_pct=incline_pct, warmup_s=warmup_s),
    )


def clamp_protocol(p: Protocol, limits: TreadmillLimits) -> Protocol:
    """Copy of `p` with every stage clamped to the treadmill's range."""
    q = Protocol.from_dict(p.to_dict())
    for s in q.stages:
        s.speed_kmh = limits.speed(s.speed_kmh)
        s.incline_pct = limits.incline(s.incline_pct)
    return q


def default_protocols(limits: TreadmillLimits = DEFAULT_LIMITS, sport: Any = None) -> list[Protocol]:
    """The protocols shown in the test picker for a sport, in a sensible order.

    Everything in this module is running on a treadmill (km/h and % incline). Cycling and SkiErg
    have their protocols in ``library_power`` (watt)."""
    if sport_of(sport) is not Sport.RUN:
        from lopanalys.assessments import library_power
        return library_power.default_protocols(sport)
    return [
        lactate_step_for_level("recreational", limits=limits),
        lactate_step_for_level("trained", limits=limits),
        lactate_to_max(start_speed_for("recreational"), limits=limits),
        max_hr_ramp(12.0, limits=limits),
        # the final sprint is on by default for the speed ramp only, and starts by itself
        max_hr_ramp(10.0, mode="speed", step_s=30, start_incline_pct=1.0, final_sprint_s=30,
                    final_sprint_auto=True, limits=limits),
        hr_zones_for_level("recreational", limits=limits),
        hr_zones_for_level("beginner", limits=limits),
        hr_zones_for_level("trained", limits=limits),
        hr_zones_for_level("elite", limits=limits),
        gait_check([10.0, 12.0, 14.0], limits=limits),
    ]
