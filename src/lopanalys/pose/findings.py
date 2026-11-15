"""Turn a ``GaitSummary`` into coach-facing observations (``Finding``) with a cue and a drill.

These are observations from 2D video, not diagnoses. Every limit below is a reference value for
recreational runners, chosen from the sources in the comments and rounded; individual runners can
be outside them without any problem. The report says so.

Sources (cited in the rule comments):
  [Souza16]   Souza RB. An evidence-based videotaped running biomechanics analysis.
              Phys Med Rehabil Clin N Am 2016;27:217-236.
  [Heid11]    Heiderscheit BC et al. Effects of step rate manipulation on joint mechanics during
              running. Med Sci Sports Exerc 2011;43:296-302.
  [Napier18]  Napier C et al. Kinematic correlates of kinetic outcomes associated with running-related
              injury. J Appl Biomech 2019;35:123-130 (shank angle and overstride at contact).
  [Bramah18]  Bramah C et al. Is there a pathological gait associated with common soft tissue running
              injuries? Am J Sports Med 2018;46:3023-3031 (contralateral pelvic drop: injured
              ~6.4 deg vs uninjured ~3.7 deg).
  [Altman12]  Altman AR, Davis IS. A kinematic method for footstrike pattern detection in barefoot
              and shod runners. Gait Posture 2012;35:298-300.
  [Hamill17]  Hamill J, Gruber AH. Is changing footstrike pattern beneficial to runners?
              J Sport Health Sci 2017;6:146-153.
  [Teng14]    Teng HL, Powers CM. Sagittal plane trunk posture influences patellofemoral joint
              stress during running. J Orthop Sports Phys Ther 2014;44:785-792.
  [Meardon14] Meardon SA, Derrick TR. Effect of step width manipulation on tibial stress during
              running. J Biomech 2014;47:2738-2744.
  [Novac98]   Novacheck TF. The biomechanics of running. Gait Posture 1998;7:77-95.
  [Folland17] Folland JP et al. Running technique is an important component of running economy and
              performance. Med Sci Sports Exerc 2017;49:1412-1423.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Callable

from lopanalys.core.i18n import language, t
from lopanalys.pose.series import nanmedian
from lopanalys.pose.types import Finding, GaitSummary, Severity, Side, StepMetrics, View

_ORDER = {Severity.FIX: 0, Severity.WATCH: 1, Severity.OK: 2}


def num(v: float | None, decimals: int = 0) -> str:
    """A number formatted for the current language (Swedish decimal comma)."""
    if v is None:
        return "–"
    s = f"{v:.{decimals}f}"
    if s in ("-0", "-0.0"):
        s = s[1:]
    return s.replace(".", ",").replace("-", "−") if language() == "sv" else s


def _side_word(side: Side | str | None) -> str:
    if side is None:
        return ""
    return t(f"pose.side.{Side(side).value}")


def _worse(a: Severity, b: Severity) -> Severity:
    return a if _ORDER[a] <= _ORDER[b] else b


# --- reference limits -----------------------------------------------------------------------------

def cadence_limits(speed_kmh: float | None) -> tuple[float, float]:
    """(watch below, fix below) in steps/min. Cadence rises with speed, about 2-3 spm per km/h in
    recreational runners; at easy speeds 155-165 is common. A 5-10 % increase reduces hip and knee
    loading [Heid11]. Without speed a fixed, conservative limit is used."""
    if speed_kmh is None:
        watch = 155.0
    else:
        watch = min(max(155.0 + 2.5 * (speed_kmh - 10.0), 150.0), 175.0)
    return watch, watch - 10.0


def overstride_limits(height_cm: float | None, speed_kmh: float | None) -> tuple[float, float] | None:
    """(watch above, fix above) heel-ahead-of-hip distance in cm, scaled to leg length
    (0.491 x height). Overstride and shank angle at contact go with higher braking and loading
    rates [Napier18]; the cut-offs are practical rules of thumb, not validated thresholds."""
    if not height_cm:
        return None
    leg = 0.491 * height_cm
    extra = max(0.0, (speed_kmh or 12.0) - 12.0) * 0.01
    return leg * (0.30 + extra), leg * (0.38 + extra)


SHIN_WATCH, SHIN_FIX = 8.0, 13.0          # deg; tibia should be near vertical at contact [Souza16]
KNEE_IC_MIN = 10.0                        # deg; a slightly flexed knee at contact [Souza16]
KNEE_MID_LO, KNEE_MID_HI = 30.0, 55.0     # deg; peak stance flexion ~40-45 deg typical [Novac98]
TRUNK_LO, TRUNK_HI = 2.0, 15.0            # deg forward; some forward lean unloads the knee [Teng14]
# Arms from the side: elbow angle (180 = straight). Rules of thumb from coaching, not research:
# Checked 2026-11-14: no evidence-based range exists. Coaching sources say 70-110 deg or
# about 90 deg; studies (Kram/Arellano, Yegian & Lieberman 2019, Shoulder Elbow 2024 with a brace at
# 45-90 deg) found little or no effect of elbow angle on running economy at moderate speed. Nothing
# was found for the arm-swing amplitude either. The range is a coaching rule of thumb, not research.
ELBOW_LO, ELBOW_HI = 60.0, 120.0
HIP_EXT_MIN = 5.0                         # deg thigh behind vertical at toe-off [Souza16]
VOSC_WATCH = 10.0                         # cm; less bounce goes with better economy [Folland17]
DROP_WATCH, DROP_FIX = 5.0, 7.0           # deg [Bramah18]
VALGUS_WATCH, VALGUS_FIX = 10.0, 15.0     # deg FPPA in stance; rule of thumb from [Souza16]
WIDTH_WATCH, WIDTH_FIX = 2.0, -2.0        # cm; narrow / crossover steps load the tibia [Meardon14]
ARM_CROSS_WATCH = 2.0                     # cm past the midline [Souza16]
WHIP_WATCH = 10.0                         # deg
ASYM = {  # metric -> (watch %, fix %); some asymmetry is normal, so limits are generous
    "ground_contact_ms": (6.0, 10.0),
    "knee_flex_midstance_deg": (15.0, 25.0),
    "overstride_cm": (25.0, 40.0),
    "hip_extension_toe_off_deg": (35.0, 60.0),
}


# --- reference ranges: the single source for findings, the report table and the live tiles ---------

@dataclass(frozen=True)
class MetricRange:
    """The ok / watch / fix ranges of one ``GaitSummary`` metric, as used for one analysis.

    ``ok_lo``..``ok_hi`` is the green range (None = open-ended). Outside it a value is *watch*
    until it passes ``fix_lo`` / ``fix_hi`` (None = that side never becomes *fix*). The bounds
    themselves belong to the better level. ``value`` and ``severity`` are filled in for an analysis
    (``analysis_ranges``). Values are judged rounded to ``decimals``, as they are printed. ``value``
    is the value that was judged (for pelvic drop and knee valgus the worse
    side) and its level.
    """
    metric: str
    ok_lo: float | None = None
    ok_hi: float | None = None
    fix_lo: float | None = None
    fix_hi: float | None = None
    unit: str = ""                 # localised display unit: "°", "cm", "steg/min"
    decimals: int = 0
    text: str = ""                 # localised ok range with unit, e.g. "minst 160 steg/min"
    codes: tuple[str, ...] = ()    # Finding.code values that are judged with this range
    value: float | None = None
    severity: str | None = None    # "ok" | "watch" | "fix"

    def classify(self, value: float | None) -> Severity | None:
        """The level of ``value`` (None when there is no value)."""
        if value is None or value != value:
            return None
        value = round(value, self.decimals)   # judge what is printed: "160, at least 160" is ok
        if (self.ok_lo is None or value >= self.ok_lo) and (self.ok_hi is None or value <= self.ok_hi):
            return Severity.OK
        if (self.fix_lo is not None and value < self.fix_lo) or (self.fix_hi is not None and value > self.fix_hi):
            return Severity.FIX
        return Severity.WATCH

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["codes"] = list(self.codes)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MetricRange":
        names = set(cls.__dataclass_fields__)
        kw = {k: v for k, v in d.items() if k in names}
        kw["codes"] = tuple(kw.get("codes") or ())
        return cls(**kw)


def _range_text(lo: float | None, hi: float | None, unit: str, decimals: int = 0) -> str:
    u = unit if unit in ("°", "") else " " + unit
    if lo is not None and hi is not None:
        return t("pose.range.between", lo=num(lo, decimals), hi=num(hi, decimals), unit=u)
    if lo is not None:
        return t("pose.range.min", v=num(lo, decimals), unit=u)
    if hi is not None:
        return t("pose.range.max", v=num(hi, decimals), unit=u)
    return ""


def _mr(metric: str, unit: str, codes: tuple[str, ...], ok_lo: float | None = None, ok_hi: float | None = None,
        fix_lo: float | None = None, fix_hi: float | None = None, decimals: int = 0) -> MetricRange:
    return MetricRange(metric, ok_lo, ok_hi, fix_lo, fix_hi, unit, decimals,
                       _range_text(ok_lo, ok_hi, unit, 0), codes)


def reference_ranges(view: View | str | None, height_cm: float | None = None,
                     speed_kmh: float | None = None) -> dict[str, MetricRange]:
    """metric -> ``MetricRange`` for a camera view, the athlete's height and the treadmill speed.

    These are exactly the limits ``evaluate`` judges with. ``view`` None gives every metric (for
    stored analyses that do not say which view they are). Metrics without a reference value
    (ground contact time, flight time) are not in the result.
    """
    spm = t("pose.unit.spm")
    c_watch, c_fix = cadence_limits(speed_kmh)
    out = [_mr("cadence_spm", spm, ("cadence",), ok_lo=c_watch, fix_lo=c_fix)]
    v = None if view is None else View(view)
    if v is None or v in (View.SIDE_LEFT, View.SIDE_RIGHT):
        lims = overstride_limits(height_cm, speed_kmh)
        if lims:
            out.append(_mr("overstride_cm", "cm", ("overstride",), ok_hi=lims[0], fix_hi=lims[1]))
        out += [
            _mr("shin_angle_deg", "°", ("overstride",), ok_hi=SHIN_WATCH, fix_hi=SHIN_FIX),
            _mr("knee_flex_contact_deg", "°", ("knee_stiff_contact",), ok_lo=KNEE_IC_MIN),
            _mr("knee_flex_midstance_deg", "°", ("knee_flex_low", "knee_flex_high"),
                ok_lo=KNEE_MID_LO, ok_hi=KNEE_MID_HI),
            _mr("trunk_lean_deg", "°", ("trunk_upright", "trunk_forward", "trunk"), ok_lo=TRUNK_LO, ok_hi=TRUNK_HI),
            _mr("hip_extension_toe_off_deg", "°", ("hip_ext_low",), ok_lo=HIP_EXT_MIN),
            _mr("vertical_oscillation_cm", "cm", ("vertical_osc",), ok_hi=VOSC_WATCH, decimals=1),
            _mr("elbow_angle_deg", "°", ("elbow",), ok_lo=ELBOW_LO, ok_hi=ELBOW_HI),
        ]
    if v is None or v in (View.REAR, View.FRONT):
        out += [
            _mr("pelvic_drop_deg", "°", ("pelvic_drop",), ok_hi=DROP_WATCH, fix_hi=DROP_FIX, decimals=1),
            _mr("knee_valgus_deg", "°", ("knee_valgus",), ok_hi=VALGUS_WATCH, fix_hi=VALGUS_FIX),
            _mr("step_width_cm", "cm", ("step_width",), ok_lo=WIDTH_WATCH, fix_lo=WIDTH_FIX),
            _mr("arm_cross_cm", "cm", ("arm_cross",), ok_hi=ARM_CROSS_WATCH),
            _mr("heel_whip_deg", "°", ("heel_whip",), ok_hi=WHIP_WATCH),   # judged on the absolute value
        ]
    return {r.metric: r for r in out}


def live_targets(speed_kmh: float | None = None, height_cm: float | None = None,
                 view: View | str | None = None) -> dict[str, MetricRange]:
    """Targets for the live tiles: metric -> ``MetricRange``.

    ``.text`` is the ok range to print on the tile ("minst 160 steg/min"), ``.classify(value)``
    gives ``Severity`` (or None without a value). Same limits as the findings and the report; call
    again when the treadmill speed changes, because cadence and overstride follow the speed.
    """
    return reference_ranges(view, height_cm, speed_kmh)


def _judged(summary: GaitSummary, steps: list[StepMetrics], metric: str) -> float | None:
    """The value a metric is judged on: the summary median, or the worse side where sides differ."""
    v = getattr(summary, metric, None)
    if v is None:
        return None
    if metric in ("pelvic_drop_deg", "knee_valgus_deg"):
        _, sv = _worst_side(steps, metric)
        return max(v, sv if sv is not None else v)
    if metric == "heel_whip_deg":
        return abs(v)
    return v


def analysis_ranges(summary: GaitSummary | None, steps: list[StepMetrics], view: View | str,
                    height_cm: float | None = None, speed_kmh: float | None = None) -> dict[str, MetricRange]:
    """``reference_ranges`` for an analysis, with the judged ``value`` and its ``severity`` filled in.

    The report table draws these, so its colours always agree with the findings.
    """
    if summary is None:
        return {}
    sp = speed_kmh if speed_kmh is not None else summary.speed_kmh
    out: dict[str, MetricRange] = {}
    for key, r in reference_ranges(view, height_cm, sp).items():
        v = _judged(summary, steps, key)
        if v is None:
            continue
        sev = r.classify(v)
        out[key] = replace(r, value=round(float(v), 2), severity=sev.value if sev else None)
    return out


# --- helpers to pick the step that shows a finding best ---------------------------------------------

def _rep_step(steps: list[StepMetrics], key: str, target: float | None,
              prefer: Side | None = None, side: Side | None = None) -> StepMetrics | None:
    """The step whose value is closest to ``target`` (the summary value), optionally one side only."""
    cand = [s for s in steps if getattr(s, key) is not None and (side is None or s.side == side)]
    if prefer is not None and any(s.side == prefer for s in cand):
        cand = [s for s in cand if s.side == prefer]
    if not cand:
        return None
    if target is None:
        return cand[len(cand) // 2]
    return min(cand, key=lambda s: abs(getattr(s, key) - target))


def _side_median(steps: list[StepMetrics], key: str, side: Side) -> float | None:
    return nanmedian(getattr(s, key) for s in steps if s.side == side)


def _worst_side(steps: list[StepMetrics], key: str, sign: float = 1.0) -> tuple[Side | None, float | None]:
    vals = {sd: _side_median(steps, key, sd) for sd in (Side.LEFT, Side.RIGHT)}
    vals = {k: v for k, v in vals.items() if v is not None}
    if not vals:
        return None, None
    sd = max(vals, key=lambda k: sign * vals[k])
    return sd, vals[sd]


@dataclass
class _Ctx:
    summary: GaitSummary
    steps: list[StepMetrics]
    view: View
    height_cm: float | None
    speed_kmh: float | None
    near: Side | None
    ranges: dict[str, MetricRange]

    def sev(self, metric: str, value: float | None) -> Severity:
        r = self.ranges.get(metric)
        return (r.classify(value) if r else None) or Severity.OK


def _make(code: str, sev: Severity, amount: float | None, unit: str, reference: str,
          step: StepMetrics | None, frame: str, highlight: list[str], side: Side | None = None,
          variant: str | None = None, **fmt: str) -> Finding:
    base = f"pose.finding.{code}"
    key = variant or ("ok" if sev == Severity.OK else "problem")
    frame_index = None
    if step is not None:
        frame_index = {"contact": step.contact_frame, "midstance": step.midstance_frame,
                       "toe_off": step.toe_off_frame}.get(frame) or step.contact_frame
    sw = _side_word(side)
    return Finding(
        code=code, severity=sev, title=t(f"{base}.title"),
        explanation=t(f"{base}.{key}", side=sw, **fmt),
        cue="" if sev == Severity.OK else t(f"{base}.cue", **fmt),
        drill="" if sev == Severity.OK else t(f"{base}.drill", **fmt),
        value=None if amount is None else round(float(amount), 1), unit=unit, reference=reference,
        frame_index=frame_index, highlight=highlight, side=None if side is None else side.value,
    )


def _lm(side: Side | None, *parts: str) -> list[str]:
    pre = "r_" if side == Side.RIGHT else "l_"
    return [pre + p for p in parts]


# --- the rules ------------------------------------------------------------------------------------

def _cadence(c: _Ctx) -> list[Finding]:
    v = c.summary.cadence_spm
    if v is None:
        return []
    watch, fix = cadence_limits(c.speed_kmh)
    sev = c.sev("cadence_spm", v)
    sp = c.speed_kmh
    ref = (t("pose.ref.cadence_speed", lim=num(watch), speed=num(sp, 0 if sp is not None and float(sp).is_integer() else 1)) if sp
           else t("pose.ref.cadence", lim=num(watch)))
    target = max(watch, v * 1.05)
    step = _rep_step(c.steps, "overstride_cm", c.summary.overstride_cm, prefer=c.near) or (c.steps[0] if c.steps else None)
    return [_make("cadence", sev, v, t("pose.unit.spm"), ref, step, "contact", [],
                  value=num(v), target=num(target))]


def _overstride(c: _Ctx) -> list[Finding]:
    s = c.summary
    if s.shin_angle_deg is None and s.overstride_cm is None:
        return []
    lims = overstride_limits(c.height_cm, c.speed_kmh)
    sev = _worse(c.sev("shin_angle_deg", s.shin_angle_deg), c.sev("overstride_cm", s.overstride_cm))
    side, _ = _worst_side(c.steps, "shin_angle_deg")
    side = c.near or side
    step = _rep_step(c.steps, "shin_angle_deg", s.shin_angle_deg, prefer=side)
    if s.overstride_cm is not None and lims:
        ref = t("pose.ref.overstride", lim=num(lims[0]), shin=num(SHIN_WATCH))
        variant = None if sev == Severity.OK else "problem"
        return [_make("overstride", sev, s.overstride_cm, "cm", ref, step, "contact",
                      _lm(side, "heel", "ankle", "knee"), side=None, variant=variant,
                      value=num(s.overstride_cm), shin=num(s.shin_angle_deg))]
    ref = t("pose.ref.shin", lim=num(SHIN_WATCH))
    return [_make("overstride", sev, s.shin_angle_deg, "°", ref, step, "contact",
                  _lm(side, "heel", "ankle", "knee"),
                  variant="ok_shin" if sev == Severity.OK else "problem_shin",
                  shin=num(s.shin_angle_deg), value=num(s.shin_angle_deg))]


def _knee(c: _Ctx) -> list[Finding]:
    out = []
    s = c.summary
    if c.sev("knee_flex_contact_deg", s.knee_flex_contact_deg) != Severity.OK:
        step = _rep_step(c.steps, "knee_flex_contact_deg", s.knee_flex_contact_deg, prefer=c.near)
        out.append(_make("knee_stiff_contact", Severity.WATCH, s.knee_flex_contact_deg, "°",
                         t("pose.ref.min_deg", lim=num(KNEE_IC_MIN)), step, "contact",
                         _lm(c.near, "knee"), value=num(s.knee_flex_contact_deg)))
    v = s.knee_flex_midstance_deg
    if v is not None:
        ref = t("pose.ref.range_deg", lo=num(KNEE_MID_LO), hi=num(KNEE_MID_HI))
        step = _rep_step(c.steps, "knee_flex_midstance_deg", v, prefer=c.near)
        off = c.sev("knee_flex_midstance_deg", v) != Severity.OK
        if off and v < KNEE_MID_LO:
            out.append(_make("knee_flex_low", Severity.WATCH, v, "°", ref, step, "midstance",
                             _lm(c.near, "knee"), value=num(v)))
        elif off:
            out.append(_make("knee_flex_high", Severity.WATCH, v, "°", ref, step, "midstance",
                             _lm(c.near, "knee"), value=num(v)))
    return out


def _trunk(c: _Ctx) -> list[Finding]:
    v = c.summary.trunk_lean_deg
    if v is None:
        return []
    ref = t("pose.ref.range_deg", lo=num(TRUNK_LO), hi=num(TRUNK_HI))
    step = _rep_step(c.steps, "trunk_lean_deg", v, prefer=c.near)
    hl = _lm(c.near, "shoulder", "hip")
    off = c.sev("trunk_lean_deg", v) != Severity.OK
    if off and v < TRUNK_LO:
        return [_make("trunk_upright", Severity.WATCH, v, "°", ref, step, "midstance", hl, value=num(v))]
    if off:
        return [_make("trunk_forward", Severity.WATCH, v, "°", ref, step, "midstance", hl, value=num(v))]
    return [_make("trunk", Severity.OK, v, "°", ref, step, "midstance", hl, value=num(v))]


def _hip_ext(c: _Ctx) -> list[Finding]:
    v = c.summary.hip_extension_toe_off_deg
    if c.sev("hip_extension_toe_off_deg", v) == Severity.OK:
        return []
    step = _rep_step(c.steps, "hip_extension_toe_off_deg", v, prefer=c.near)
    return [_make("hip_ext_low", Severity.WATCH, v, "°", t("pose.ref.min_deg", lim=num(HIP_EXT_MIN)),
                  step, "toe_off", _lm(c.near, "hip", "knee"), variant="problem" if v >= 0 else "problem_neg",
                  value=num(v))]


def _elbow(c: _Ctx) -> list[Finding]:
    """Arms from the side: the elbow angle, with the swing in the text."""
    v = c.summary.elbow_angle_deg
    if v is None or c.sev("elbow_angle_deg", v) == Severity.OK:
        return []
    step = c.steps[len(c.steps) // 2] if c.steps else None
    swing = c.summary.arm_swing_deg
    straight = v > ELBOW_HI
    return [_make("elbow", Severity.WATCH, v, "°",
                  t("pose.ref.range_deg", lo=num(ELBOW_LO), hi=num(ELBOW_HI)), step, "midstance",
                  _lm(c.near, "shoulder", "elbow", "wrist"),
                  variant="problem_straight" if straight else "problem_tight", value=num(v),
                  swing=num(swing) if swing is not None else "–")]


def _vosc(c: _Ctx) -> list[Finding]:
    v = c.summary.vertical_oscillation_cm
    if v is None:
        return []
    sev = c.sev("vertical_oscillation_cm", v)
    step = _rep_step(c.steps, "vertical_oscillation_cm", v, prefer=c.near)
    return [_make("vertical_osc", sev, v, "cm", t("pose.ref.max_cm", lim=num(VOSC_WATCH)), step,
                  "midstance", ["l_hip", "r_hip"], value=num(v, 1))]


def _strike(c: _Ctx) -> list[Finding]:
    fs = c.summary.foot_strike
    if fs is None:
        return []
    step = _rep_step(c.steps, "foot_angle_deg", c.summary.foot_angle_deg, prefer=c.near)
    return [_make("foot_strike", Severity.OK, c.summary.foot_angle_deg, "°",
                  "", step, "contact", _lm(c.near, "heel", "foot"),
                  variant=fs, value=num(c.summary.foot_angle_deg))]


def _asym(c: _Ctx) -> list[Finding]:
    best: tuple[Severity, str, float] | None = None
    for key, (w, f) in ASYM.items():
        v = c.summary.asymmetry.get(key)
        if v is None:
            continue
        sev = Severity.FIX if abs(v) >= f else Severity.WATCH if abs(v) >= w else None
        if sev is None:
            continue
        if best is None or _ORDER[sev] < _ORDER[best[0]] or (sev == best[0] and abs(v) > abs(best[2])):
            best = (sev, key, v)
    if best is None:
        return []
    sev, key, v = best
    lv = _side_median(c.steps, key, Side.LEFT)
    rv = _side_median(c.steps, key, Side.RIGHT)
    unit = t(f"pose.metric_unit.{key}")
    d = 0 if key == "ground_contact_ms" else 1 if key.endswith("_cm") else 0
    w, f = ASYM[key]
    step = _rep_step(c.steps, key, lv if v > 0 else rv, side=Side.LEFT if v > 0 else Side.RIGHT)
    return [_make("asymmetry", sev, abs(v), "%", t("pose.ref.max_pct", lim=num(w)), step, "contact", [],
                  metric=t(f"pose.metric.{key}"), value=num(abs(v)), left=f"{num(lv, d)} {unit}",
                  right=f"{num(rv, d)} {unit}")]


def _pelvic_drop(c: _Ctx) -> list[Finding]:
    v = c.summary.pelvic_drop_deg
    if v is None:
        return []
    side, sv = _worst_side(c.steps, "pelvic_drop_deg")
    worst = max(v, sv or v)
    sev = c.sev("pelvic_drop_deg", worst)
    step = _rep_step(c.steps, "pelvic_drop_deg", sv, side=side)
    return [_make("pelvic_drop", sev, worst, "°", t("pose.ref.max_deg", lim=num(DROP_WATCH)), step,
                  "midstance", ["l_hip", "r_hip"], side=side, value=num(worst, 1))]


def _valgus(c: _Ctx) -> list[Finding]:
    v = c.summary.knee_valgus_deg
    if v is None:
        return []
    side, sv = _worst_side(c.steps, "knee_valgus_deg")
    worst = max(v, sv or v)
    sev = c.sev("knee_valgus_deg", worst)
    if sev == Severity.OK:
        return []
    step = _rep_step(c.steps, "knee_valgus_deg", sv, side=side)
    return [_make("knee_valgus", sev, worst, "°", t("pose.ref.max_deg", lim=num(VALGUS_WATCH)), step,
                  "midstance", _lm(side, "knee"), side=side, value=num(worst))]


def _width(c: _Ctx) -> list[Finding]:
    v = c.summary.step_width_cm
    if v is None:
        return []
    sev = c.sev("step_width_cm", v)
    if sev == Severity.OK:
        return []
    step = _rep_step(c.steps, "step_width_cm", v)
    if v < 0:
        return [_make("step_width", sev, v, "cm", t("pose.ref.min_cm", lim=num(WIDTH_WATCH)), step,
                      "midstance", ["l_ankle", "r_ankle"], variant="problem_cross", value=num(-v / 2))]
    return [_make("step_width", sev, v, "cm", t("pose.ref.min_cm", lim=num(WIDTH_WATCH)), step,
                  "midstance", ["l_ankle", "r_ankle"], value=num(v))]


def _arms(c: _Ctx) -> list[Finding]:
    v = c.summary.arm_cross_cm
    if c.sev("arm_cross_cm", v) == Severity.OK:
        return []
    side, sv = _worst_side(c.steps, "arm_cross_cm")
    step = _rep_step(c.steps, "arm_cross_cm", sv, side=side)
    arm = None if side is None else (Side.RIGHT if side == Side.LEFT else Side.LEFT)
    return [_make("arm_cross", Severity.WATCH, v, "cm", t("pose.ref.max_cm", lim=num(ARM_CROSS_WATCH)),
                  step, "midstance", _lm(arm, "wrist"), side=arm, value=num(v),
                  side_cap=t(f"pose.side_title.{arm.value}") if arm else "")]


def _whip(c: _Ctx) -> list[Finding]:
    v = c.summary.heel_whip_deg
    if v is None or c.sev("heel_whip_deg", abs(v)) == Severity.OK:
        return []
    side, sv = _worst_side(c.steps, "heel_whip_deg", sign=1.0 if v > 0 else -1.0)
    step = _rep_step(c.steps, "heel_whip_deg", sv, side=side)
    return [_make("heel_whip", Severity.WATCH, abs(v), "°", t("pose.ref.max_deg", lim=num(WHIP_WATCH)),
                  step, "toe_off", _lm(side, "heel"), side=side, value=num(abs(v)),
                  direction=t("pose.dir.in" if v > 0 else "pose.dir.out"))]


_SAGITTAL: list[Callable[[_Ctx], list[Finding]]] = [_cadence, _overstride, _knee, _trunk, _hip_ext, _vosc,
                                                    _elbow, _strike, _asym]
_FRONTAL: list[Callable[[_Ctx], list[Finding]]] = [_cadence, _pelvic_drop, _valgus, _width, _arms, _whip,
                                                   _asym]


def evaluate(summary: GaitSummary | None, steps: list[StepMetrics], view: View | str,
             height_cm: float | None = None, speed_kmh: float | None = None) -> list[Finding]:
    """All findings for a window, most important first (fix, watch, then what looks good)."""
    if summary is None or summary.steps < 4:
        return []
    v = View(view)
    near = Side.LEFT if v == View.SIDE_LEFT else Side.RIGHT if v == View.SIDE_RIGHT else None
    sp = speed_kmh if speed_kmh is not None else summary.speed_kmh
    ctx = _Ctx(summary, steps, v, height_cm, sp, near, reference_ranges(v, height_cm, sp))
    rules = _SAGITTAL if v in (View.SIDE_LEFT, View.SIDE_RIGHT) else _FRONTAL
    out: list[Finding] = []
    for rule in rules:
        out += rule(ctx)
    return sorted(out, key=lambda f: _ORDER[f.severity])
