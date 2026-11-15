"""Estimated VO2max from the tests the program already runs. Never a measurement.

VO2max can only be measured with a gas analyser. What a treadmill, a heart-rate strap and a clock
can give is an estimate with a method and an interval, and that is what this module returns: a
``VO2maxEstimate`` is never a lone number.

Methods, in the fixed order of preference (``RANK``):
  1. ``acsm_final_stage``: the ACSM running equation for the last completed stage of a test to
     exhaustion: VO2 = 3.5 + 0.2 * v + 0.9 * v * grade, v in m/min, grade as a fraction
     (ACSM's Guidelines for Exercise Testing and Prescription, metabolic equations). The equation
     describes steady-state running, so a short last stage overestimates.
  2. ``leger_mercier``: VO2 = 2.209 + 3.163 * speed (km/h) for level running (pooled regression, 10 studies,
     130 subjects) (Léger & Mercier 1984, Sports Med
     1:270), for the same last stage when the treadmill was level. Checked 2026-11-14: the summaries of the paper give a
     pooled treadmill regression 2.209 + 3.163 * speed (10 studies, 130 subjects), about 4-6 % below
     3.5 * speed at 12-18 km/h. This method and ACSM's lie on nearly the same line, so they are not
     two independent confirmations.
  3. ``hr_extrapolation``: the straight line heart rate -> VO2 (ACSM equation per stage) through the
     steady stages of a step test, extrapolated to max HR (the principle of the ACSM/YMCA submaximal
     tests). Needs a known or measured max HR and at least 3 stages.
  4. ``uth_sorensen``: 15.3 * HRmax / HRrest (Uth, Sørensen, Overgaard & Pedersen 2004, Eur J Appl
     Physiol 91:111). The coarsest fallback: no speed is used at all.

Intervals (``UNCERTAINTY_PCT``) are rounded working values, not statistics of this program:
  - Heart-rate based estimates: published standard errors are typically above 10-15 % of the value
    (ACSM submaximal protocols: SEE around 4-5 ml/kg/min, 15-16 % in one cycle study). -> 15 %.
  - Uth-Sørensen: the original study reports about 5-8 % in trained men; the factor 15.3 came from
    10 men, and a casually measured resting HR adds more. -> 15 %.
  - ACSM equation / Léger-Mercier at the final stage: no standard error was found in our search
    (2026-11-14). Running economy differs by roughly +/-10 % between runners, which the equations
    cannot see. -> 10 %, and 15 % when the last stage was shorter than 2 min (not steady state).
  Checked 2026-11-14, second-hand through search summaries: no published SEE exists for
  the ACSM equation itself, and independent studies find it overestimates (+9.8 ml/kg/min at a max
  test in 99 active adults; +14.6 % in athletes). Uth-Sorensen: SEE 4.7 ml/kg/min (7.8 %) with an
  age-predicted max HR; the 15.3 (SD 0.7) came from 10 well-trained men; later validation: fine in
  trained men, about +2 ml/kg/min in untrained. The percentages above remain working values, NOT
  VERIFIED as statistics.

A real incline ramp (2026-11-14) gave an estimate far above the athlete's watch value.
The principle: rather no value than a half-good one. So (``estimate``):
  - a test to exhaustion that did not meet the max criteria (max_hr.py) gives no value at all,
  - the final-stage equations (ACSM, Léger-Mercier) are not used when the last stage was steeper
    than ``MAX_FINAL_INCLINE_PCT`` or shorter than ``SHORT_STAGE_S`` (not steady state; ACSM
    overestimates at a steep incline, and a source check found 10-15 % overestimation already in ordinary cases),
  - an estimate more than ``PLAUSIBLE_VS_UTH_PCT`` from Uth-Sørensen (when resting HR is known) is dropped.
What was left out and why is kept in ``VO2maxResult.reasons``; the card then says "Kan inte uppskattas
ur det här testet" with the reasons.

No classification against age norms: the program has no vetted tables for that.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from lopanalys.core.i18n import language, t

RANK = ("acsm_final_stage", "leger_mercier", "hr_extrapolation", "uth_sorensen")
UNCERTAINTY_PCT = {"acsm_final_stage": 15.0, "leger_mercier": 10.0, "hr_extrapolation": 15.0, "uth_sorensen": 15.0}
SHORT_STAGE_S = 120.0            # a final stage shorter than this is not steady state: not used
MAX_FINAL_INCLINE_PCT = 5.0      # a steeper final stage is not used (ACSM overestimates there)
PLAUSIBLE_VS_UTH_PCT = 15.0      # an estimate further than this from Uth-Sørensen is not shown
WATCH_DIFF_PCT = 10.0            # the report warns when the watch's VO2max differs more than this
REASONS = ("not_max", "steep", "short", "implausible")
# ACSM: the running equation holds above 134 m/min, and from 80 m/min (4.8 km/h) when truly jogging.
MIN_RUN_KMH = 4.8
# 1 % treadmill incline is the accepted equivalent of level outdoor running (Jones & Doust 1996,
# J Sports Sci 14:321), so Léger-Mercier ("level running") is applied up to 1 %. Checked 2026-11-14: 9 trained
# men; 1 % matched road cost up to about 15 km/h, between 1 and 2 % at 18 km/h.
LEVEL_MAX_INCLINE_PCT = 1.0
MIN_STAGES = 3
STEADY_STAGE_S = 180.0           # heart rate needs about 3 min to settle on a stage (ACSM)
COMPLETED_FRACTION = 0.95        # a stage counts as completed at 95 % of its planned length
PLAUSIBLE = (15.0, 95.0)         # ml/kg/min; outside this the inputs are wrong, not the athlete


@dataclass
class VO2maxEstimate:
    method: str                  # one of RANK
    value: float                 # ml/kg/min
    low: float
    high: float
    uncertainty_pct: float
    inputs: dict[str, Any] = field(default_factory=dict)   # what the estimate was computed from

    @property
    def name(self) -> str:
        return t(f"assessments.vo2max.method.{self.method}")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "name": self.name}


@dataclass
class VO2maxResult:
    """Every method that could be computed, best first, and why others were left out (REASONS).
    ``estimates`` may be empty: then nothing is shown but the reasons."""
    estimates: list[VO2maxEstimate]
    reasons: list[str] = field(default_factory=list)

    @property
    def best(self) -> VO2maxEstimate | None:
        return self.estimates[0] if self.estimates else None

    def to_dict(self) -> dict[str, Any]:
        b = self.best
        return {"best": b.to_dict() if b else None, "estimates": [e.to_dict() for e in self.estimates],
                "reasons": list(self.reasons)}


def _get(o: Any, k: str) -> Any:
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _make(method: str, value: float, inputs: dict[str, Any], pct: float | None = None) -> VO2maxEstimate | None:
    if not np.isfinite(value) or not PLAUSIBLE[0] <= value <= PLAUSIBLE[1]:
        return None
    pct = UNCERTAINTY_PCT[method] if pct is None else pct
    return VO2maxEstimate(method, round(value, 1), round(value * (1 - pct / 100), 1),
                          round(value * (1 + pct / 100), 1), pct, inputs)


def acsm_running_vo2(speed_kmh: float, incline_pct: float = 0.0) -> float:
    """Oxygen cost of steady running (ml/kg/min): 3.5 + 0.2 * v + 0.9 * v * grade, v in m/min."""
    v = speed_kmh * 1000.0 / 60.0
    return 3.5 + 0.2 * v + 0.9 * v * incline_pct / 100.0


def last_completed_stage(records: Iterable[Any], stage_durations: Sequence[float] | None = None) -> Any | None:
    """The last stage that was run to its end.

    ``stage_durations``: planned seconds per protocol stage, indexed like ``StepRecord.index``; an
    open-ended ramp repeats its last stage, so higher indexes use the last value. Without it every
    record counts as completed.
    """
    done = None
    for r in records:
        dur, idx = _num(_get(r, "duration_s")), _get(r, "index")
        if stage_durations and dur is not None and isinstance(idx, int) and idx >= 0:
            planned = stage_durations[min(idx, len(stage_durations) - 1)]
            if planned and dur < COMPLETED_FRACTION * planned:
                continue
        done = r
    return done


def final_stage_refusal(incline_pct: float | None, duration_s: float | None) -> str:
    """Why the final-stage equations cannot be used for this stage ("steep", "short") or ""."""
    inc, dur = _num(incline_pct) or 0.0, _num(duration_s)
    if inc > MAX_FINAL_INCLINE_PCT + 1e-9:
        return "steep"
    if dur is not None and dur < SHORT_STAGE_S:
        return "short"
    return ""


def from_final_stage(speed_kmh: float | None, incline_pct: float | None = 0.0,
                     duration_s: float | None = None) -> list[VO2maxEstimate]:
    """ACSM (and Léger-Mercier on a level treadmill) for the last completed stage of a test to
    exhaustion. [] when the speed is missing or below running speed, or when the stage is steeper
    than MAX_FINAL_INCLINE_PCT or shorter than SHORT_STAGE_S (final_stage_refusal)."""
    v, inc = _num(speed_kmh), _num(incline_pct) or 0.0
    if v is None or v < MIN_RUN_KMH or inc < 0 or final_stage_refusal(inc, duration_s):
        return []
    dur = _num(duration_s)
    inputs = {"speed_kmh": round(v, 2), "incline_pct": round(inc, 2), "stage_s": dur}
    out = [_make("acsm_final_stage", acsm_running_vo2(v, inc), inputs)]
    if inc <= LEVEL_MAX_INCLINE_PCT:
        out.append(_make("leger_mercier", 2.209 + 3.163 * v, inputs))
    return [e for e in out if e is not None]


def from_stages(records: Iterable[Any], max_hr: float | None) -> VO2maxEstimate | None:
    """Heart rate -> VO2 through the steady stages of a step test, extrapolated to max HR.

    None with fewer than 3 usable stages (speed, heart rate, at least 3 min), without max HR, when
    max HR is below the highest stage heart rate, or when heart rate does not rise with the work.
    """
    mx = _num(max_hr)
    pts = []
    for r in records:
        v, hr, dur = _num(_get(r, "speed_kmh")), _num(_get(r, "hr_end")), _num(_get(r, "duration_s"))
        if v is None or hr is None or v < MIN_RUN_KMH or hr <= 0 or (dur is not None and dur < STEADY_STAGE_S):
            continue
        pts.append((hr, acsm_running_vo2(v, _num(_get(r, "incline_pct")) or 0.0)))
    if mx is None or len(pts) < MIN_STAGES:
        return None
    hrs, vo2 = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    if np.ptp(hrs) < 10 or mx < hrs.max():
        return None                      # no heart-rate range to fit, or a max HR the test itself exceeded
    slope, intercept = np.polyfit(hrs, vo2, 1)
    if slope <= 0:
        return None
    return _make("hr_extrapolation", float(intercept + slope * mx),
                 {"stages": len(pts), "max_hr": round(mx), "hr_low": round(float(hrs.min())),
                  "hr_high": round(float(hrs.max()))})


def from_heart_rates(max_hr: float | None, resting_hr: float | None) -> VO2maxEstimate | None:
    """Uth-Sørensen: 15.3 * HRmax / HRrest. None without both, or with implausible values."""
    mx, rest = _num(max_hr), _num(resting_hr)
    if mx is None or rest is None or not 100 <= mx <= 240 or not 25 <= rest <= 110 or rest >= mx:
        return None
    return _make("uth_sorensen", 15.3 * mx / rest, {"max_hr": round(mx), "resting_hr": round(rest)})


def estimate(records: Iterable[Any] | None = None, max_hr: float | None = None, resting_hr: float | None = None,
             *, to_exhaustion: bool = False, stage_durations: Sequence[float] | None = None,
             likely_max: bool | None = None) -> VO2maxResult | None:
    """Every estimate the data allows, best first; None when nothing can be estimated and nothing
    was refused. A result with no estimates carries the reasons (REASONS).

    records: the work stages (StepRecords or dicts, warm-up excluded). max_hr: known or measured.
    to_exhaustion: the test ended at the athlete's limit; only then does the last stage say
    anything about the maximum. stage_durations: see last_completed_stage().
    likely_max: max_hr.MaxHrResult.likely_max of a test to exhaustion; False gives no value at all.
    """
    recs = list(records or [])
    if to_exhaustion and likely_max is False:
        return VO2maxResult([], ["not_max"])
    found: list[VO2maxEstimate] = []
    reasons: list[str] = []
    if to_exhaustion:
        last = last_completed_stage(recs, stage_durations)
        if last is None and recs:
            last = recs[-1]                       # only a stage cut short: say why it is not used
        if last is not None:
            why = final_stage_refusal(_get(last, "incline_pct"), _get(last, "duration_s"))
            if why:
                reasons.append(why)
            found += from_final_stage(_get(last, "speed_kmh"), _get(last, "incline_pct"), _get(last, "duration_s"))
    for e in (from_stages(recs, max_hr), from_heart_rates(max_hr, resting_hr)):
        if e is not None:
            found.append(e)
    uth = next((e for e in found if e.method == "uth_sorensen"), None)
    if uth is not None:
        ok = [e for e in found if abs(e.value - uth.value) <= PLAUSIBLE_VS_UTH_PCT / 100 * uth.value + 1e-9]
        if len(ok) < len(found):
            reasons.append("implausible")
        found = ok
    found.sort(key=lambda e: RANK.index(e.method))
    if not found and not reasons:
        return None
    return VO2maxResult(found, reasons)


def _fmt(v: float) -> str:
    s = f"{v:.1f}"
    return s.replace(".", ",") if language() == "sv" else s


def watch_line(watch_vo2max: float | None, watch_date: Any = None, estimate: float | None = None) -> dict[str, Any] | None:
    """The athlete's VO2max from the watch, next to the test's estimate: {"text", "warn"} or None.
    ``warn`` is set when the two differ more than WATCH_DIFF_PCT."""
    w = _num(watch_vo2max)
    if w is None or w <= 0:
        return None
    d = str(watch_date or "")[:10]
    text = t("assessments.vo2max.watch_dated" if d else "assessments.vo2max.watch", value=_fmt(w), date=d)
    warn = ""
    if estimate is not None and abs(estimate - w) > WATCH_DIFF_PCT / 100 * w:
        warn = t("assessments.vo2max.watch_differs", pct=f"{abs(estimate - w) / w * 100:.0f}", limit=f"{WATCH_DIFF_PCT:.0f}")
    return {"text": text, "warn": warn}


def card(result: VO2maxResult | dict[str, Any] | None, watch_vo2max: float | None = None,
         watch_date: Any = None) -> dict[str, Any] | None:
    """The estimate as display texts in the current language, the same for the screen and the report:
    {"title", "value", "unit", "range", "method", "disclaimer", "others": [{"method", "value", "range"}],
    "none": False, "watch": watch_line() or None}. The title always says "estimated" and the disclaimer
    always follows the number. A result without an estimate but with reasons gives
    {"none": True, "title", "text", "reasons": [text, ...], "watch"}."""
    r = result.to_dict() if isinstance(result, VO2maxResult) else result
    if r and not r.get("best") and r.get("reasons"):
        return {"none": True, "title": t("assessments.vo2max.title"), "text": t("assessments.vo2max.none"),
                "reasons": [t(f"assessments.vo2max.reason.{x}", deg=_fmt(MAX_FINAL_INCLINE_PCT).rstrip("0").rstrip(",."),
                              s=int(SHORT_STAGE_S), pct=f"{PLAUSIBLE_VS_UTH_PCT:.0f}") for x in r["reasons"]],
                "watch": watch_line(watch_vo2max, watch_date)}
    if not r or not r.get("best"):
        return None

    def rng(e: dict[str, Any]) -> str:
        return t("assessments.vo2max.range", low=_fmt(e["low"]), high=_fmt(e["high"]), pct=f"{e['uncertainty_pct']:.0f}")

    def name(e: dict[str, Any]) -> str:
        return t(f"assessments.vo2max.method.{e['method']}")

    best = r["best"]
    return {"title": t("assessments.vo2max.title"), "value": _fmt(best["value"]), "unit": t("assessments.vo2max.unit"),
            "range": rng(best), "method": name(best), "disclaimer": t("assessments.vo2max.disclaimer"),
            "others": [{"method": name(e), "value": _fmt(e["value"]), "range": rng(e)}
                       for e in r.get("estimates", []) if e["method"] != best["method"]],
            "none": False, "watch": watch_line(watch_vo2max, watch_date, float(best["value"]))}


__all__ = ["RANK", "UNCERTAINTY_PCT", "VO2maxEstimate", "VO2maxResult", "acsm_running_vo2", "card", "estimate",
           "final_stage_refusal", "from_final_stage", "from_heart_rates", "from_stages", "last_completed_stage",
           "watch_line"]
