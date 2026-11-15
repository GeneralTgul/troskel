"""Max heart rate from a ramp test.

Peak HR = highest 5-s rolling mean of the cleaned 1 Hz signal (single-beat spikes from a dry strap
or a cable are removed first: values outside 30-240 bpm, and points more than 12 bpm from the
rolling median of their neighbours; gaps of up to 5 s are interpolated, longer gaps are left out).

"Is this a true max?" criteria (any two met -> likely; Howley et al. 1995, Med Sci Sports Exerc
27:1292; Midgley et al. 2007, Sports Med 37:1019):
  - plateau: HR rose at most 3 bpm over the last 60 s of work despite rising workload,
  - age-predicted: peak within 10 bpm of 208 - 0.7 x age (Tanaka et al. 2001, JACC 37:153),
  - RPE >= 19 on the Borg 6-20 scale at the end,
  - blood lactate >= 8 mmol/L after the test.
Recovery: HR 60 s after exhaustion and the drop from peak (a drop of < 12 bpm is a known risk
marker in clinical tests; here it is reported, not judged).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import numpy as np

from lopanalys.core.i18n import t
from lopanalys.core.sport import DEFAULT_SPORT, sport_of

HR_MIN, HR_MAX = 30, 240
SPIKE_BPM = 12.0
MAX_GAP_S = 5
WINDOW_S = 5
PLATEAU_BPM = 3.0
PLATEAU_WINDOW_S = 60


@dataclass
class Criterion:
    code: str           # "plateau" | "age" | "rpe" | "lactate"
    met: bool | None    # None: not measurable (no age, no RPE ...)
    value: float | None
    text: str


@dataclass
class MaxHrResult:
    peak_hr: int | None
    peak_t_s: float | None
    plateau: bool | None
    plateau_rise_bpm: float | None
    stage_hr: list[dict[str, Any]] = field(default_factory=list)   # {"index", "speed_kmh", "incline_pct", "hr"}
    end_t_s: float | None = None
    recovery_hr_60: int | None = None
    recovery_drop_60: int | None = None
    criteria: list[Criterion] = field(default_factory=list)
    likely_max: bool = False
    artefacts_removed: int = 0
    warnings: list[dict[str, str]] = field(default_factory=list)
    # The highest single reading of the cleaned 1 Hz signal (spikes from a dry strap removed). The
    # max HR is the 5-s mean (``peak_hr``); this one is shown beside it, it is not used.
    peak_single_hr: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MaxHrResult":
        d = dict(d)
        d["criteria"] = [Criterion(**c) for c in d.get("criteria", [])]
        return MaxHrResult(**{k: v for k, v in d.items() if k in MaxHrResult.__dataclass_fields__})


def _get(o: Any, k: str) -> Any:
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def clean_hr(samples: Iterable[Any]) -> tuple[np.ndarray, np.ndarray, int]:
    """1 Hz grid (t, hr) with artefacts removed and short gaps interpolated (NaN = no data)."""
    raw = [(float(_get(s, "t_s")), _get(s, "hr")) for s in samples]
    pts = [(tt, float(h)) for tt, h in raw if h is not None and HR_MIN <= h <= HR_MAX]
    removed = sum(1 for _, h in raw if h is not None and h != 0 and not (HR_MIN <= h <= HR_MAX))
    if not pts:
        return np.array([]), np.array([]), removed
    pts.sort()
    ts = np.array([p[0] for p in pts])
    hs = np.array([p[1] for p in pts])
    keep = np.ones(len(hs), bool)
    for i in range(len(hs)):
        lo, hi = max(0, i - 3), min(len(hs), i + 4)
        neigh = np.concatenate([hs[lo:i], hs[i + 1:hi]])
        if neigh.size >= 2 and abs(hs[i] - np.median(neigh)) > SPIKE_BPM:
            keep[i] = False
    removed += int((~keep).sum())
    ts, hs = ts[keep], hs[keep]
    grid = np.arange(math.floor(ts[0]), math.floor(ts[-1]) + 1, 1.0)
    out = np.full(grid.shape, np.nan)
    idx = np.searchsorted(ts, grid)
    for gi, g in enumerate(grid):
        j = idx[gi]
        if j < len(ts) and abs(ts[j] - g) < 0.51:
            out[gi] = hs[j]
        elif j > 0 and abs(ts[j - 1] - g) < 0.51:
            out[gi] = hs[j - 1]
        elif 0 < j < len(ts) and ts[j] - ts[j - 1] <= MAX_GAP_S + 1:
            out[gi] = hs[j - 1] + (hs[j] - hs[j - 1]) * (g - ts[j - 1]) / (ts[j] - ts[j - 1])
    return grid, out, removed


def rolling_mean(hr: np.ndarray, window: int = WINDOW_S) -> np.ndarray:
    """Mean over `window` consecutive seconds, NaN if any second is missing (aligned to the window end)."""
    out = np.full(hr.shape, np.nan)
    for i in range(window - 1, len(hr)):
        w = hr[i - window + 1:i + 1]
        if not np.isnan(w).any():
            out[i] = w.mean()
    return out


def _at(grid: np.ndarray, sm: np.ndarray, t_s: float) -> float | None:
    if grid.size == 0:
        return None
    i = int(round(t_s - grid[0]))
    if 0 <= i < len(sm):
        for j in (i, i + 1, i - 1, i + 2, i - 2):
            if 0 <= j < len(sm) and not np.isnan(sm[j]):
                return float(sm[j])
    return None


def analyze(samples: Iterable[Any], *, end_t_s: float | None = None, records: Iterable[Any] | None = None,
            age: float | None = None, final_rpe: int | None = None,
            peak_lactate: float | None = None, sport: Any = None) -> MaxHrResult:
    """Analyze a ramp to exhaustion.

    samples: 1 Hz TimeSamples (t_s, hr). end_t_s: time of the exhaustion press (runner.exhaustion_t_s);
    without it the peak time is used for recovery HR. records: StepRecords for HR per stage (and RPE).
    age, final_rpe, peak_lactate: for the true-max criteria. sport: the age criterion is running's
    (the peak heart rate is lower on a bike and lower still in double poling, by an amount that
    differs between people), so it is not judged in the other sports.
    """
    samples = list(samples)
    recs = list(records or [])
    grid, hr, removed = clean_hr(samples)
    res = MaxHrResult(None, None, None, None, artefacts_removed=removed)
    if removed > max(5, 0.05 * max(len(samples), 1)):
        res.warnings.append({"code": "artefacts", "text": t("assessments.maxhr.warn.artefacts", n=removed)})
    res.stage_hr = [{"index": _get(r, "index"), "speed_kmh": _get(r, "speed_kmh"),
                     "incline_pct": _get(r, "incline_pct"), "hr": _get(r, "hr_end")} for r in recs]
    if grid.size == 0:
        res.warnings.append({"code": "no_hr", "text": t("assessments.maxhr.warn.no_hr")})
        return res
    if np.isnan(hr).mean() > 0.2:
        res.warnings.append({"code": "gaps", "text": t("assessments.maxhr.warn.gaps")})
    sm = rolling_mean(hr)
    # Only look up to the exhaustion press (+5 s for HR lag), never in recovery.
    limit = np.ones(sm.shape, bool) if end_t_s is None else grid <= end_t_s + 5
    cand = np.where(limit & ~np.isnan(sm), sm, -1)
    if cand.max() < 0:
        res.warnings.append({"code": "no_hr", "text": t("assessments.maxhr.warn.no_hr")})
        return res
    ip = int(np.argmax(cand))
    peak = float(sm[ip])
    res.peak_hr = int(round(peak))
    single = np.where(limit & ~np.isnan(hr), hr, -1)
    res.peak_single_hr = int(round(float(single.max()))) if single.max() > 0 else None
    res.peak_t_s = float(grid[ip] - (WINDOW_S - 1) / 2)
    res.end_t_s = end_t_s

    # Plateau: rise over the last minute of work (up to the exhaustion press, else the peak).
    t_end = min(end_t_s, float(grid[-1])) if end_t_s is not None else float(grid[ip])
    last = sm[(grid >= t_end - 10) & (grid <= t_end) & ~np.isnan(sm)]
    before = _at(grid, sm, t_end - PLATEAU_WINDOW_S)
    if before is not None and last.size:
        res.plateau_rise_bpm = round(float(last.max()) - before, 1)
        res.plateau = res.plateau_rise_bpm <= PLATEAU_BPM
    else:
        res.warnings.append({"code": "short", "text": t("assessments.maxhr.warn.short")})

    end = end_t_s if end_t_s is not None else float(grid[ip])
    h60 = _at(grid, sm, end + 60)
    if h60 is not None:
        res.recovery_hr_60 = int(round(h60))
        res.recovery_drop_60 = int(round(peak - h60))

    if final_rpe is None:
        rpes = [_get(r, "rpe") for r in recs if _get(r, "rpe")]
        final_rpe = rpes[-1] if rpes else None
    if peak_lactate is None:
        las = [_get(r, "lactate_mmol") for r in recs if _get(r, "lactate_mmol") is not None]
        peak_lactate = max(las) if las else None

    crit = [Criterion("plateau", res.plateau, res.plateau_rise_bpm,
                      t("assessments.maxhr.crit.plateau", bpm=int(PLATEAU_BPM)))]
    if sport_of(sport) is not DEFAULT_SPORT:
        crit.append(Criterion("age", None, None, t("assessments.maxhr.crit.age_other_sport")))
    elif age:
        pred = 208 - 0.7 * float(age)
        crit.append(Criterion("age", peak >= pred - 10, round(pred), t("assessments.maxhr.crit.age", pred=round(pred))))
    else:
        crit.append(Criterion("age", None, None, t("assessments.maxhr.crit.age_missing")))
    crit.append(Criterion("rpe", (final_rpe >= 19) if final_rpe else None, final_rpe, t("assessments.maxhr.crit.rpe")))
    crit.append(Criterion("lactate", (peak_lactate >= 8.0) if peak_lactate is not None else None, peak_lactate,
                          t("assessments.maxhr.crit.lactate")))
    res.criteria = crit
    res.likely_max = sum(1 for c in crit if c.met) >= 2
    return res


# ---------------------------------------------------------------------------- gait in a max HR test
GAIT_HR_FRACTION = 0.90   # film stages run below 90 % of the test's peak heart rate ...
GAIT_TAIL_S = 120.0       # ... and never the last two minutes before the end, where technique breaks down
GAIT_FALLBACK_DROP = 3    # without heart rate: all ramp stages but the last three


def gait_window(records: Iterable[Any], samples: Iterable[Any] = (), end_t_s: float | None = None,
                fraction: float = GAIT_HR_FRACTION, tail_s: float = GAIT_TAIL_S) -> dict[str, Any]:
    """Which stages of a max HR ramp to analyse for running technique (protocol "maxpuls med löpteknik").

    The technique at the very end of a ramp to exhaustion says little about how someone runs, so
    only stages below ``fraction`` of the peak heart rate count, and none that ended in the last
    ``tail_s`` seconds. Without heart rate: every ramp stage but the last ``GAIT_FALLBACK_DROP``.

    ``records``: the ramp stages (warm-up left out), in order, with ``duration_s`` and ``hr_end``.
    ``end_t_s``: the time of the end of the last record (the exhaustion press), else the last sample.
    Returns {"stages": [index, ...], "cutoff_s": seconds from the start of the test (the end of the
    last chosen stage; the end of the warm-up when no stage qualifies) or None (no limit),
    "basis": "hr" | "count", "peak_hr": int | None}.
    """
    recs = [r for r in records if _get(r, "duration_s")]
    smp = list(samples)
    if end_t_s is None:
        end_t_s = float(_get(smp[-1], "t_s")) if smp else sum(float(_get(r, "duration_s")) for r in recs)
    ends: list[float] = []
    t_end = float(end_t_s)
    for r in reversed(recs):                       # the last record ends at end_t_s
        ends.append(t_end)
        t_end -= float(_get(r, "duration_s"))
    ends.reverse()
    hrs = [_get(r, "hr_end") for r in recs]
    peak_s = [int(_get(s, "hr")) for s in smp if _get(s, "hr") and HR_MIN <= _get(s, "hr") <= HR_MAX]
    peak = max([h for h in hrs if h] + peak_s, default=0) or None
    latest = float(end_t_s) - tail_s
    if peak and all(hrs):
        chosen = [i for i, (r, e) in enumerate(zip(recs, ends)) if hrs[i] < fraction * peak and e <= latest + 1e-6]
        basis = "hr"
    else:
        chosen = [i for i, e in enumerate(ends[:max(0, len(recs) - GAIT_FALLBACK_DROP)]) if e <= latest + 1e-6]
        basis = "count"
    # only the stages before the first one left out: technique is judged before the break-down
    run = []
    for k, i in enumerate(chosen):
        if i != k:
            break
        run.append(i)
    if run:
        cutoff: float | None = ends[run[-1]]
    else:   # not even the first ramp stage: the warm-up only (if there was one)
        cutoff = ends[0] - float(_get(recs[0], "duration_s")) if recs else None
        cutoff = cutoff if cutoff and cutoff > 30 else None
    return {"stages": [int(_get(recs[i], "index")) for i in run],
            "cutoff_s": round(cutoff, 1) if cutoff else None, "basis": basis, "peak_hr": peak}


# ---------------------------------------------------------------------------- what the zones are built on
def zone_basis(used_max: float | None, saved_max: float | None, saved_source: str = "", saved_date: Any = None,
               test_peak: float | None = None, likely_max: bool | None = None,
               test_date: Any = None) -> str:
    """One sentence on which max HR the heart rate zones use and where it comes from.

    A test whose peak is below the saved max HR says "Maxpuls <peak>" while zone 5 ends at the saved value,
    since the zones use the saved max HR. That is right (the peak was not a max), but it must be said.
    ``used_max``: the max HR the zones were computed with. ``saved_*``: the athlete's max_hr, its source
    ("entered" | "test" | "") and date. ``test_peak``/``likely_max``: this test's peak and verdict.
    """
    if not used_max:
        return ""
    used = int(round(float(used_max)))
    d = str(saved_date or "")[:10]
    td = str(test_date or "")[:10]
    from_this_test = test_peak is not None and int(round(float(test_peak))) == used and (
        not saved_max or (saved_source == "test" and (not d or d == td)) or int(round(float(saved_max))) != used)
    if from_this_test:
        return t("assessments.maxhr.basis.this_test", bpm=used)
    if not saved_max and test_peak is None:
        # nothing saved and the test did not go to the limit: its highest heart rate is not a max HR
        return t("assessments.maxhr.basis.test_top", bpm=used)
    if saved_source == "entered" and d:
        origin = t("assessments.maxhr.basis.entered", date=d)
    elif saved_source == "test" and d:
        origin = t("assessments.maxhr.basis.test", date=d)
    else:
        origin = t("assessments.maxhr.basis.saved")
    text = t("assessments.maxhr.basis.saved_max", bpm=used, origin=origin)
    if test_peak is not None and int(round(float(test_peak))) < used:
        key = "assessments.maxhr.basis.peak_not_max" if likely_max is not True else "assessments.maxhr.basis.peak_lower"
        text += " " + t(key, peak=int(round(float(test_peak))))
    return text
