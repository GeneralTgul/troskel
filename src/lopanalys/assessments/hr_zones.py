"""Heart rate and speed zones without a lactate meter ("Puls- och fartzoner").

The test is 4-5 steady steps of 4 minutes straight into a speed ramp to exhaustion (library.hr_zones).
A max test with one-minute stages cannot give a speed for a heart rate: the heart rate lags the speed
by 2-3 minutes. So the two parts answer different questions:

  - the steps give the relation heart rate -> speed. The stable heart rate of a step is the mean of
    its last ``STEADY_WINDOW_S`` seconds, and the relation is the weighted straight line
    HR = intercept + slope * speed through the steps (heart rate rises close to linearly with speed
    below the anaerobic threshold; with 4-5 points nothing more than a line can be supported),
  - the ramp gives the max heart rate, by the rules of the max HR test (max_hr.analyze).

A step whose heart rate still rises more than ``UNSTABLE_BPM`` during its last minute has not
settled: it gets a warning and a lower weight in the fit. Steps shorter than ``MIN_STEP_S`` are left
out. The quality of the relation is R2 and the residual spread in bpm.

The zones are any zone system's borders in % of max HR (Garmin's five by default), each border turned
into a speed by the line. Outside the measured steps the line is followed for at most
``EXTRAPOLATE_KMH`` (and never past the fastest speed run in the ramp) and the speed is marked as
extrapolated; further out no speed is given.
Everything from this module is an estimate: speed from heart rate, not from lactate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from lopanalys.assessments.max_hr import HR_MAX, HR_MIN, clean_hr
from lopanalys.assessments.zone_systems import EST_LTHR_PCT_MAX, Zone
from lopanalys.core.i18n import t

STEADY_WINDOW_S = 60.0     # the stable heart rate of a step: the mean of its last minute
MIN_STEP_S = 180.0         # heart rate needs 2-3 min to settle: shorter steps are left out
UNSTABLE_BPM = 3.0         # rising more than this over the last minute: not settled
MIN_WEIGHT = 0.25          # the lowest weight of an unsettled step in the fit
MIN_POINTS = 3             # steps needed for a relation
EXTRAPOLATE_KMH = 2.0      # how far outside the measured steps a speed is still given (marked)
SPEED_TOL_KMH = 0.3        # a sample belongs to a step when the belt is this close to its speed
MIN_HR_SPAN = 12.0         # bpm between the lowest and highest step for a usable line
GOOD_R2, FAIR_R2 = 0.98, 0.95
GOOD_SEE_BPM, FAIR_SEE_BPM = 2.5, 4.0
RAMP_MIN_S = 60.0          # the ramp must have run this long for the peak to count as a max attempt


@dataclass
class StepPoint:
    """One steady step: its stable heart rate and whether it had settled."""
    index: int
    speed_kmh: float
    incline_pct: float
    duration_s: float
    hr: float | None = None          # mean of the last STEADY_WINDOW_S seconds
    rise_bpm: float | None = None    # rise over the last minute (bpm per minute), None: unknown
    stable: bool | None = None
    weight: float = 0.0              # weight in the fit, 0: left out
    note: str = ""                   # "" | "unstable" | "short" | "no_hr"


@dataclass
class HrSpeedResult:
    """The relation heart rate -> speed from the steady steps."""
    points: list[StepPoint] = field(default_factory=list)
    slope: float | None = None        # bpm per km/h
    intercept: float | None = None    # bpm at 0 km/h (a fit parameter, not a resting heart rate)
    r2: float | None = None
    see_bpm: float | None = None      # residual spread of the steps around the line
    quality: str | None = None        # "good" | "fair" | "poor"
    speed_min: float | None = None    # the measured range: slowest and fastest step in the fit
    speed_max: float | None = None
    incline_pct: float | None = None  # the incline the relation holds for
    extrapolate_kmh: float = EXTRAPOLATE_KMH
    ramp_s: float = 0.0               # how long the ramp to max ran
    ramp_top_kmh: float | None = None  # the fastest ramp stage that was run
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.slope is not None and self.intercept is not None and self.slope > 0

    @property
    def ramp_reached(self) -> bool:
        """True when the ramp ran long enough for the test's peak to be a max attempt."""
        return self.ramp_s >= RAMP_MIN_S

    @property
    def used(self) -> list[StepPoint]:
        return [p for p in self.points if p.weight > 0 and p.hr is not None]

    def hr_at(self, speed_kmh: float) -> float | None:
        return self.intercept + self.slope * speed_kmh if self.ok else None

    def speed_at(self, hr: float | None) -> tuple[float | None, bool]:
        """(speed, extrapolated) for a heart rate. (None, False) outside what the test can say:
        more than ``extrapolate_kmh`` below the slowest or above the fastest step."""
        if not self.ok or hr is None or self.speed_min is None or self.speed_max is None:
            return None, False
        v = (float(hr) - self.intercept) / self.slope
        if self.speed_min - 1e-6 <= v <= self.speed_max + 1e-6:
            return round(v, 1), False
        if self.speed_min - self.extrapolate_kmh <= v <= self._top():
            return round(v, 1), True
        return None, False

    def _top(self) -> float:
        """The fastest speed the line is followed to: never past what was run in the ramp."""
        top = self.speed_max + self.extrapolate_kmh
        if self.ramp_top_kmh is not None:
            top = min(top, max(self.ramp_top_kmh, self.speed_max))
        return top

    def line(self) -> list[tuple[float, float, bool]]:
        """The line as (speed, hr, extrapolated) at the ends of the extrapolated and measured ranges."""
        if not self.ok or self.speed_min is None or self.speed_max is None:
            return []
        xs = [(max(0.5, self.speed_min - self.extrapolate_kmh), True), (self.speed_min, False),
              (self.speed_max, False), (self._top(), True)]
        return [(round(x, 2), round(self.intercept + self.slope * x, 1), e) for x, e in xs]

    def est_threshold_kmh(self, max_hr: float | None) -> float | None:
        """The speed at the nominal anaerobic threshold (87 % of max HR), for the next test's start
        speed. Never shown as a threshold: no lactate was measured."""
        if not max_hr:
            return None
        return self.speed_at(EST_LTHR_PCT_MAX / 100 * max_hr)[0]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok"], d["ramp_reached"] = self.ok, self.ramp_reached
        d["line"] = [list(x) for x in self.line()]
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "HrSpeedResult":
        known = set(HrSpeedResult.__dataclass_fields__)
        kw = {k: v for k, v in d.items() if k in known}
        pk = set(StepPoint.__dataclass_fields__)
        kw["points"] = [StepPoint(**{k: v for k, v in p.items() if k in pk}) for p in d.get("points") or []]
        return HrSpeedResult(**kw)


def _get(o: Any, k: str) -> Any:
    return o.get(k) if isinstance(o, Mapping) else getattr(o, k, None)


def step_indexes(protocol: Mapping[str, Any] | Any | None) -> set[int] | None:
    """The protocol stage indexes that are steady steps: every stage but the warm-up and, in a test
    that ends in an open ramp, the ramp's first stage (the last protocol stage). None: unknown."""
    stages = _get(protocol, "stages") if protocol is not None else None
    if not stages:
        return None
    last = len(stages) - 1 if _get(protocol, "open_ended") else len(stages)
    return {i for i, s in enumerate(stages) if i < last and not _get(s, "is_warmup")}


def _windows_backwards(recs: Sequence[Any], end_t_s: float) -> list[tuple[float, float]]:
    """(start, end) of each record counted back from the end of the last one. Exact when the test
    ran without pauses, which is how this protocol runs."""
    out: list[tuple[float, float]] = []
    t_end = float(end_t_s)
    for r in reversed(recs):
        d = float(_get(r, "duration_s") or 0.0)
        out.append((t_end - d, t_end))
        t_end -= d
    out.reverse()
    return out


def _window_by_speed(ts: np.ndarray, sp: np.ndarray, speed: float, after: float) -> tuple[float, float] | None:
    """The longest unbroken run of samples at ``speed`` after time ``after``."""
    ok = (np.abs(sp - speed) <= SPEED_TOL_KMH) & (ts >= after)
    best: tuple[float, float] | None = None
    start = None
    for i, flag in enumerate(ok):
        if flag and start is None:
            start = i
        if start is not None and (not flag or i == len(ok) - 1):
            end = i if flag else i - 1
            if best is None or ts[end] - ts[start] > best[1] - best[0]:
                best = (float(ts[start]), float(ts[end]))
            start = None
    return best


def _stage_windows(recs: Sequence[Any], samples: Sequence[Any], end_t_s: float | None) -> list[tuple[float, float] | None]:
    """When each record was run, in seconds from the start of the test.

    Counted back from the exhaustion press when the belt speed in the samples agrees with that; else
    (no press, or the test was paused) found in the samples by the belt speed. None: not found."""
    pts = [(float(_get(s, "t_s")), _get(s, "speed_kmh")) for s in samples]
    pts = [(a, float(b)) for a, b in pts if b is not None]
    ts = np.array([p[0] for p in pts])
    sp = np.array([p[1] for p in pts])
    if end_t_s is not None and recs:
        wins = _windows_backwards(recs, end_t_s)
        if ts.size == 0:
            return list(wins)
        agree = True
        for r, (a, b) in zip(recs, wins):
            if b - a < 30:
                continue
            inside = sp[(ts >= a + 15) & (ts <= b - 1)]     # the belt needs some seconds to change speed
            if inside.size and abs(float(np.median(inside)) - float(_get(r, "speed_kmh"))) > SPEED_TOL_KMH:
                agree = False
                break
        if agree:
            return list(wins)
    if ts.size == 0:
        return [None] * len(recs)
    out: list[tuple[float, float] | None] = []
    after = 0.0
    for r in recs:
        w = _window_by_speed(ts, sp, float(_get(r, "speed_kmh")), after)
        out.append(w)
        if w is not None:
            after = w[1]
    return out


def _steady(grid: np.ndarray, hr: np.ndarray, start: float, end: float) -> tuple[float | None, float | None]:
    """(mean heart rate, rise in bpm per minute) over the last STEADY_WINDOW_S seconds of a window."""
    lo = max(start, end - STEADY_WINDOW_S)
    m = (grid > lo) & (grid <= end) & ~np.isnan(hr)
    if m.sum() < 0.5 * min(STEADY_WINDOW_S, max(end - lo, 1.0)):
        return None, None
    x, y = grid[m], hr[m]
    rise = None
    if x.size >= 20 and np.ptp(x) >= 30:
        rise = float(np.polyfit(x, y, 1)[0] * 60.0)
    return float(y.mean()), rise


def analyze(records: Iterable[Any], samples: Iterable[Any] = (), *, end_t_s: float | None = None,
            steps: set[int] | None = None) -> HrSpeedResult:
    """The relation heart rate -> speed of one test.

    records: the work stages in order (StepRecords or dicts, warm-up left out), the steady steps
    first and then the ramp. samples: the 1 Hz series (t_s, hr, speed_kmh). end_t_s: the exhaustion
    press (the end of the last record). steps: the record indexes that are steady steps
    (``step_indexes(protocol)``); None: every record of at least MIN_STEP_S.
    """
    recs = [r for r in records if _get(r, "duration_s")]
    smp = list(samples)
    res = HrSpeedResult()

    def warn(code: str, **kw: Any) -> None:
        res.warnings.append({"code": code, "text": t(f"assessments.hrzones.warn.{code}", **kw)})

    def is_step(r: Any) -> bool:
        if steps is not None:
            return _get(r, "index") in steps
        return float(_get(r, "duration_s")) >= MIN_STEP_S

    grid, hr, _ = clean_hr(smp)
    wins = _stage_windows(recs, smp, end_t_s)
    ramp = [r for r in recs if not is_step(r)]
    res.ramp_s = round(sum(float(_get(r, "duration_s")) for r in ramp), 1)
    res.ramp_top_kmh = max((float(_get(r, "speed_kmh")) for r in ramp), default=None)

    for r, w in zip(recs, wins):
        if not is_step(r):
            continue
        p = StepPoint(int(_get(r, "index")), float(_get(r, "speed_kmh")), float(_get(r, "incline_pct") or 0.0),
                      float(_get(r, "duration_s")))
        if w is not None and grid.size:
            p.hr, p.rise_bpm = _steady(grid, hr, *w)
        if p.hr is None:                       # no series: the runner's own end-of-stage heart rate
            h = _get(r, "hr_end")
            p.hr = float(h) if h and HR_MIN <= h <= HR_MAX else None
        if p.hr is None:
            p.note = "no_hr"
        elif p.duration_s < MIN_STEP_S:
            p.note = "short"
        else:
            p.stable = None if p.rise_bpm is None else p.rise_bpm <= UNSTABLE_BPM
            if p.stable is False:
                p.note = "unstable"
                p.weight = round(max(MIN_WEIGHT, UNSTABLE_BPM / p.rise_bpm), 2)
            else:
                p.weight = 1.0
        if p.hr is not None:
            p.hr = round(p.hr, 1)
        if p.rise_bpm is not None:
            p.rise_bpm = round(p.rise_bpm, 1) + 0.0     # + 0.0: never "-0.0"
        res.points.append(p)

    unstable = [p for p in res.points if p.note == "unstable"]
    if unstable:
        warn("unstable", steps=", ".join(_kmh(p.speed_kmh) for p in unstable), bpm=int(UNSTABLE_BPM))
    short = [p for p in res.points if p.note == "short"]
    if short:
        warn("short", steps=", ".join(_kmh(p.speed_kmh) for p in short), min=int(MIN_STEP_S // 60))
    if not res.ramp_reached:
        warn("no_ramp")

    used = res.used
    if len(used) < MIN_POINTS:
        warn("too_few", n=len(used), need=MIN_POINTS)
        return res
    x = np.array([p.speed_kmh for p in used])
    y = np.array([p.hr for p in used])
    w = np.array([p.weight for p in used])
    if np.ptp(x) < 1e-6 or np.ptp(y) < MIN_HR_SPAN:
        warn("narrow", bpm=int(MIN_HR_SPAN))
        return res
    # numpy weights multiply the residuals, so the square root gives weights on the squared errors
    slope, intercept = np.polyfit(x, y, 1, w=np.sqrt(w))
    if slope <= 0:
        warn("not_rising")
        return res
    fit = intercept + slope * x
    ss_res = float(np.sum(w * (y - fit) ** 2))
    ss_tot = float(np.sum(w * (y - np.average(y, weights=w)) ** 2))
    res.slope, res.intercept = round(float(slope), 3), round(float(intercept), 2)
    res.r2 = round(1 - ss_res / ss_tot, 4) if ss_tot > 0 else None
    res.see_bpm = round(float(np.sqrt(ss_res / max(float(w.sum()) - 2.0, 1.0))), 1)
    res.speed_min, res.speed_max = float(x.min()), float(x.max())
    incs = sorted({p.incline_pct for p in used})
    res.incline_pct = incs[0] if len(incs) == 1 else None
    r2 = res.r2 or 0.0
    stable_n = sum(1 for p in used if p.weight >= 1.0)
    if r2 >= GOOD_R2 and res.see_bpm <= GOOD_SEE_BPM and stable_n >= 4:
        res.quality = "good"
    elif r2 >= FAIR_R2 and res.see_bpm <= FAIR_SEE_BPM and stable_n >= MIN_POINTS:
        res.quality = "fair"
    else:
        res.quality = "poor"
        warn("poor_fit")
    return res


def _kmh(v: float) -> str:
    from lopanalys.assessments.library import fmt_kmh   # the library imports nothing from here
    return fmt_kmh(v)


def zone_speeds(zones: Sequence[Zone], relation: HrSpeedResult | None) -> list[Zone]:
    """The same zones with a speed at every heart-rate border the relation covers.

    The zones keep their heart rates (and ``estimated``: they still come from % of max HR). A border
    outside the measured steps is marked extrapolated; further out than that it stays open."""
    if relation is None or not relation.ok:
        return list(zones)
    out = []
    for z in zones:
        lo, lo_x = relation.speed_at(z.hr_low)
        hi, hi_x = relation.speed_at(z.hr_high)
        if lo is None and hi is None:
            out.append(z)
            continue
        out.append(replace(z, speed_low_kmh=lo, speed_high_kmh=hi, speed_low_extrapolated=lo_x,
                           speed_high_extrapolated=hi_x, speed_basis="hr"))
    return out


def quality_text(result: HrSpeedResult | Mapping[str, Any] | None) -> str:
    """One line on how well the steps follow the line, in the current language."""
    r = result.to_dict() if isinstance(result, HrSpeedResult) else result
    if not r or not r.get("ok") or not r.get("quality"):
        return ""
    n = sum(1 for p in r.get("points") or [] if p.get("weight", 0) > 0)
    return t(f"assessments.hrzones.quality.{r['quality']}", n=n, r2=_dec(r.get("r2"), 2), see=_dec(r.get("see_bpm"), 1))


def _dec(v: Any, nd: int) -> str:
    from lopanalys.core.i18n import language
    if v is None:
        return "–"
    s = f"{float(v):.{nd}f}"
    return s.replace(".", ",") if language() == "sv" else s


__all__ = ["EXTRAPOLATE_KMH", "HrSpeedResult", "MIN_STEP_S", "STEADY_WINDOW_S", "StepPoint", "UNSTABLE_BPM",
           "analyze", "quality_text", "step_indexes", "zone_speeds"]
