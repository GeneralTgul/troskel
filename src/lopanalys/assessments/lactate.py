"""Lactate thresholds from a step test, with several methods so the coach can choose.

Input: StepRecords with ``lactate_mmol`` (warm-up excluded). The work rate axis is the load of the
test's sport: speed (km/h) for running, power (watt) for cycling and SkiErg. The result and its
thresholds carry the sport; the fields are still named ``speed_kmh`` and are read as ``load`` in new
code. Every method works on (load, lactate) pairs, so watt on the x-axis changes nothing in the
mathematics; what follows the sport is the scale: the exponential fit works on a normalised axis,
the tolerances are shares of the load, and stages whose measured loads are almost the same are one
point (on a SkiErg the athlete holds the watt, the program does not set it). SkiErg thresholds are
always computed on watt, never on pace: watt goes as the speed cubed, and Dmax and log-log would
land elsewhere on a pace axis.

Curve model (``model``):
  - ``exp``: La = a + b*exp(c*v). Monotone, 3 parameters, robust with few or noisy points;
    the default (Newell et al. 2007, J Sports Sci 25:1403, fit exponential and polynomial curves).
  - ``poly3``: 3rd-order polynomial, the classic Dmax fit (Cheng et al. 1992, Int J Sports Med 13:518).
    With 4 points it just interpolates, so ``auto`` only prefers it with >= 6 points and a clearly
    better AICc.

LT1 methods (first/aerobic threshold):
  - ``bsln_0_5`` / ``bsln_1_0``: speed where the curve reaches baseline + 0.5 / + 1.0 mmol/L
    (Jamnick et al. 2020, Sports Med 50:1729; Faude et al. 2009, Sports Med 39:469). Baseline =
    lowest measured value (a falling start, the "lactate dip", is ignored).
  - ``log_log``: breakpoint of two straight lines in log(La) vs log(v) (Beaver et al. 1985,
    J Appl Physiol 59:1936).
  - ``first_rise``: last stage before the first sustained rise of >= 0.4 mmol/L (a stage value,
    not interpolated; Faude et al. 2009 "first rise above baseline").
  - ``fixed_2_0``: 2.0 mmol/L (Kindermann et al. 1979, the "aerobic threshold").
LT2 methods (second/anaerobic threshold):
  - ``mod_dmax``: Bishop et al. 1998 (Med Sci Sports Exerc 30:1270): max distance from the curve
    to the line from the point before the first rise of >= 0.4 mmol/L to the last point.
  - ``dmax``: Cheng et al. 1992: same, line from the first to the last point of the curve.
  - ``obla_4``: 4.0 mmol/L (Sjödin & Jacobs 1981; Heck et al. 1985).
  - ``bsln_1_5``: baseline + 1.5 mmol/L (Jamnick et al. 2020).
Defaults: LT1 = bsln_1_0 (then log_log), LT2 = mod_dmax (then dmax, obla_4). Jamnick et al. 2018
(PLoS One 13:e0199794) found mod-Dmax among the methods closest to maximal lactate steady state.
"""
from __future__ import annotations

import math
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from lopanalys.core.i18n import localize_number, t
from lopanalys.core.sport import profile, sport_of

LT1_METHODS = ["bsln_1_0", "log_log", "bsln_0_5", "first_rise", "fixed_2_0"]
LT2_METHODS = ["mod_dmax", "dmax", "obla_4", "bsln_1_5"]
RISE_MMOL = 0.4          # Bishop 1998: first increase of >= 0.4 mmol/L
GRID = 400
CURVE_POINTS = 60        # stored curve for charts


def pace_str(speed_kmh: float | None, sport: Any = None) -> str:
    """Pace as 'm:ss' for a load: min/km for running, /500 m for SkiErg ('' if no load or no pace)."""
    return profile(sport).pace_str(speed_kmh)


@dataclass
class Threshold:
    method: str
    lactate_mmol: float
    speed_kmh: float
    hr: float | None = None
    hr_pct_max: float | None = None
    incline_pct: float | None = None
    note: str = ""
    sport: str = "run"                 # core.sport.Sport value: the unit of the load and the pace

    @property
    def name(self) -> str:
        return t(f"assessments.lactate.method.{self.method}")

    @property
    def load(self) -> float:
        """The threshold's load in the unit of the sport (km/h for running)."""
        return self.speed_kmh

    @property
    def pace_min_per_km(self) -> float:
        return 60.0 / self.speed_kmh if self.speed_kmh > 0 else float("nan")

    @property
    def pace(self) -> str:
        return pace_str(self.speed_kmh, self.sport)

    def to_dict(self) -> dict[str, Any]:
        return {"method": self.method, "name": self.name, "lactate_mmol": self.lactate_mmol,
                "speed_kmh": self.speed_kmh, "pace_min_per_km": round(self.pace_min_per_km, 3),
                "pace": self.pace, "hr": self.hr, "hr_pct_max": self.hr_pct_max,
                "incline_pct": self.incline_pct, "note": self.note, "sport": self.sport}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Threshold":
        return Threshold(d["method"], d["lactate_mmol"], d["speed_kmh"], d.get("hr"), d.get("hr_pct_max"),
                         d.get("incline_pct"), d.get("note", ""), sport_of(d.get("sport")).value)


@dataclass
class LactateWarning:
    code: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "text": self.text}


@dataclass
class LactateResult:
    points: list[dict[str, Any]]            # index, speed_kmh, incline_pct, lactate_mmol, hr
    baseline_mmol: float | None
    peak_mmol: float | None
    model: str                              # "exp" | "poly3" | "none"
    fit: dict[str, Any]                     # model parameters (see _Model)
    r2: float | None
    curve: list[list[float]]                # [[speed, lactate], ...] for charts
    thresholds: dict[str, Threshold]
    lt1_method: str | None
    lt2_method: str | None
    max_hr: int | None = None
    warnings: list[LactateWarning] = field(default_factory=list)
    sport: str = "run"                      # core.sport.Sport value; a stored result without it is running

    @property
    def lt1(self) -> Threshold | None:
        return self.thresholds.get(self.lt1_method) if self.lt1_method else None

    @property
    def lt2(self) -> Threshold | None:
        return self.thresholds.get(self.lt2_method) if self.lt2_method else None

    @property
    def ok(self) -> bool:
        return self.lt1 is not None and self.lt2 is not None

    def lactate_at(self, speed_kmh: float) -> float | None:
        """Fitted lactate at a speed (None outside the tested range or without a fit)."""
        m = _Model.from_dict(self.fit) if self.fit else None
        if m is None or not self.points:
            return None
        lo, hi = self.points[0]["speed_kmh"], self.points[-1]["speed_kmh"]
        if not (lo - 1e-9 <= speed_kmh <= hi + 1e-9):
            return None
        return float(m(np.array([speed_kmh]))[0])

    def speed_at(self, lactate_mmol: float) -> float | None:
        """Speed where the fitted curve first reaches a lactate level after its lowest point."""
        m = _Model.from_dict(self.fit) if self.fit else None
        if m is None or len(self.points) < 2:
            return None
        xs = np.array([p["speed_kmh"] for p in self.points])
        ys = np.array([p["lactate_mmol"] for p in self.points])
        return _crossing(m, xs, ys, lactate_mmol)

    def select(self, lt1_method: str | None = None, lt2_method: str | None = None) -> "LactateResult":
        """Same result with the coach's choice of methods (unknown/missing methods raise ValueError)."""
        for m in (lt1_method, lt2_method):
            if m is not None and m not in self.thresholds:
                raise ValueError(t("assessments.lactate.error.method", method=m))
        r = LactateResult(**{k: getattr(self, k) for k in self.__dataclass_fields__})
        r.lt1_method = lt1_method or self.lt1_method
        r.lt2_method = lt2_method or self.lt2_method
        return r

    def to_dict(self) -> dict[str, Any]:
        return {
            "points": self.points, "baseline_mmol": self.baseline_mmol, "peak_mmol": self.peak_mmol,
            "model": self.model, "fit": self.fit, "r2": self.r2, "curve": self.curve,
            "thresholds": {k: v.to_dict() for k, v in self.thresholds.items()},
            "lt1_method": self.lt1_method, "lt2_method": self.lt2_method,
            "lt1": self.lt1.to_dict() if self.lt1 else None, "lt2": self.lt2.to_dict() if self.lt2 else None,
            "max_hr": self.max_hr, "warnings": [w.to_dict() for w in self.warnings], "sport": self.sport,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "LactateResult":
        return LactateResult(
            points=d.get("points", []), baseline_mmol=d.get("baseline_mmol"), peak_mmol=d.get("peak_mmol"),
            model=d.get("model", "none"), fit=d.get("fit", {}), r2=d.get("r2"), curve=d.get("curve", []),
            thresholds={k: Threshold.from_dict(v) for k, v in d.get("thresholds", {}).items()},
            lt1_method=d.get("lt1_method"), lt2_method=d.get("lt2_method"), max_hr=d.get("max_hr"),
            warnings=[LactateWarning(w["code"], w.get("text", "")) for w in d.get("warnings", [])],
            sport=sport_of(d.get("sport")).value,
        )


# ---------------------------------------------------------------------- curve models
class _Model:
    """``sx`` scales the x-axis: the model is evaluated at (x - x0) / sx. 1 for running (km/h); for
    watt it is chosen so that the test spans about as many units as a running test does, which
    keeps the fit's start values and bounds valid."""

    def __init__(self, kind: str, params: list[float], x0: float = 0.0, sx: float = 1.0) -> None:
        self.kind, self.params, self.x0 = kind, [float(p) for p in params], float(x0)
        self.sx = float(sx) if sx else 1.0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        x = (np.asarray(x, dtype=float) - self.x0) / self.sx
        if self.kind == "exp":
            a, b, c = self.params
            return a + b * np.exp(np.clip(c * x, -50, 50))
        return np.polyval(self.params, x)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.kind, "params": self.params, "x0": self.x0}
        if self.sx != 1.0:
            d["sx"] = self.sx
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "_Model | None":
        if not d or d.get("type") not in ("exp", "poly3"):
            return None
        return _Model(d["type"], d["params"], d.get("x0", 0.0), d.get("sx", 1.0))


FIT_SPAN = 6.0   # a running test spans about 6 km/h; a test in watt is fitted on an axis of that width


def _x_scale(x: np.ndarray, sport: Any) -> float:
    """The x-axis unit of the fit: 1 for speed in km/h, span / FIT_SPAN for power in watt."""
    if profile(sport).load_quantity != "power":
        return 1.0
    span = float(x[-1] - x[0])
    return span / FIT_SPAN if span > 0 else 1.0


def _fit_exp(x: np.ndarray, y: np.ndarray, sx: float = 1.0) -> _Model | None:
    from scipy.optimize import curve_fit

    x0 = float(x[0])
    xs = (x - x0) / sx

    def f(v: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
        return a + b * np.exp(np.clip(c * v, -50, 50))

    ymin = float(np.min(y))
    best: tuple[float, list[float]] | None = None
    for a0 in (ymin * 0.8, ymin - 0.3, 0.0):
        z = y - a0
        if np.any(z <= 0):
            z = np.clip(z, 0.05, None)
        try:
            c0, lnb = np.polyfit(xs, np.log(z), 1)
        except Exception:
            continue
        p0 = [a0, max(math.exp(lnb), 1e-3), float(np.clip(c0, 0.01, 3.0))]
        try:
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                p, _ = curve_fit(f, xs, y, p0=p0, bounds=([-5.0, 1e-6, 1e-4], [ymin + 0.5, 50.0, 5.0]), maxfev=20000)
        except Exception:
            continue
        rss = float(np.sum((f(xs, *p) - y) ** 2))
        if best is None or rss < best[0]:
            best = (rss, list(p))
    return _Model("exp", best[1], x0, sx) if best else None


def _fit_poly(x: np.ndarray, y: np.ndarray, sx: float = 1.0) -> _Model | None:
    deg = min(3, len(x) - 1)
    if deg < 2:
        return None
    x0 = float(x[0])
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        c = np.polyfit((x - x0) / sx, y, deg)
    return _Model("poly3", list(c), x0, sx)


def _r2(m: _Model, x: np.ndarray, y: np.ndarray) -> float:
    ss_res = float(np.sum((m(x) - y) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def _aicc(m: _Model, x: np.ndarray, y: np.ndarray, k: int) -> float:
    n = len(x)
    rss = max(float(np.sum((m(x) - y) ** 2)), 1e-12)
    if n - k - 1 <= 0:
        return math.inf
    return n * math.log(rss / n) + 2 * k + 2 * k * (k + 1) / (n - k - 1)


def _crossing(m: _Model, xs: np.ndarray, ys: np.ndarray, level: float) -> float | None:
    """First speed >= the lowest measured point where the curve reaches `level` (linear interp on grid)."""
    start = float(xs[int(np.argmin(ys))])
    g = np.linspace(start, float(xs[-1]), GRID)
    v = m(g)
    if v[0] >= level:
        return None if float(ys.min()) >= level else float(g[0])
    idx = np.nonzero(v >= level)[0]
    if idx.size == 0:
        return None
    i = int(idx[0])
    x1, x2, y1, y2 = g[i - 1], g[i], v[i - 1], v[i]
    return float(x1 + (level - y1) * (x2 - x1) / (y2 - y1)) if y2 != y1 else float(x2)


def _dmax(m: _Model, xa: float, ya: float, xb: float, yb: float) -> float | None:
    """Speed of max distance below the line (xa,ya)-(xb,yb). Perpendicular distance to a fixed line
    is proportional to the vertical distance, so the result does not depend on axis units."""
    if xb - xa <= 1e-6:
        return None
    g = np.linspace(xa, xb, GRID)
    line = ya + (yb - ya) * (g - xa) / (xb - xa)
    d = line - m(g)
    i = int(np.argmax(d))
    if d[i] <= 1e-6:
        return None
    return float(g[i])


def _rise_index(ys: np.ndarray) -> int | None:
    """Index of the point preceding the first sustained increase of >= RISE_MMOL (after the minimum)."""
    imin = int(np.argmin(ys))
    for i in range(imin, len(ys) - 1):
        if ys[i + 1] - ys[i] >= RISE_MMOL - 1e-6:
            # Sustained: the following point does not fall back (a lone spike is a bad sample).
            if i + 2 >= len(ys) or ys[i + 2] >= ys[i + 1] - 0.2 - 1e-9:
                return i
    return None


def _log_log(xs: np.ndarray, ys: np.ndarray) -> float | None:
    """Beaver 1985: two-segment regression in log-log space, breakpoint = intersection."""
    if len(xs) < 4 or np.any(ys <= 0) or np.any(xs <= 0):
        return None
    lx, ly = np.log(xs), np.log(ys)
    best: tuple[float, float] | None = None
    for k in range(2, len(xs) - 1):
        m1, b1 = np.polyfit(lx[:k], ly[:k], 1)
        m2, b2 = np.polyfit(lx[k:], ly[k:], 1)
        if m2 <= m1 + 1e-9:
            continue
        rss = float(np.sum((np.polyval([m1, b1], lx[:k]) - ly[:k]) ** 2) +
                    np.sum((np.polyval([m2, b2], lx[k:]) - ly[k:]) ** 2))
        xi = (b1 - b2) / (m2 - m1)
        xi = float(np.clip(xi, lx[k - 1] - 0.5 * (lx[k] - lx[k - 1]), lx[k] + 0.5 * (lx[k] - lx[k - 1])))
        xi = float(np.clip(xi, lx[0], lx[-1]))
        if best is None or rss < best[0]:
            best = (rss, xi)
    return float(math.exp(best[1])) if best else None


# ---------------------------------------------------------------------- analysis
def _points(records: Iterable[Any], same_load: float = 0.0) -> list[dict[str, Any]]:
    """The (load, lactate) points, sorted by load. ``same_load``: two stages whose loads differ by
    less than this share are one point (measured watt: the athlete held nearly the same twice)."""
    pts = []
    for r in records:
        get = (lambda k, r=r: r.get(k)) if isinstance(r, dict) else (lambda k, r=r: getattr(r, k, None))
        la = get("lactate_mmol")
        sp = get("speed_kmh")
        if la is None or sp is None or la < 0 or sp <= 0:
            continue
        pts.append({"index": get("index"), "speed_kmh": float(sp), "incline_pct": get("incline_pct"),
                    "lactate_mmol": float(la), "hr": get("hr_end")})
    pts.sort(key=lambda p: (p["speed_kmh"], p["index"] if p["index"] is not None else 0))
    # Same speed twice (e.g. a repeated stage): keep the later sample.
    out: list[dict[str, Any]] = []
    for p in pts:
        if out and abs(out[-1]["speed_kmh"] - p["speed_kmh"]) < max(1e-6, same_load * p["speed_kmh"]):
            # the later sample counts (pts is sorted by load, so compare the stage indexes)
            if (p["index"] if p["index"] is not None else 0) >= (out[-1]["index"] if out[-1]["index"] is not None else 0):
                out[-1] = p
        else:
            out.append(p)
    return out


def _hr_at(points: Sequence[dict[str, Any]], speed: float, near: float = 0.3) -> float | None:
    """Heart rate at a load, interpolated between the stages. ``near``: how far from a lone point
    its heart rate still counts (0.3 km/h, or a share of the load for watt)."""
    pairs = [(p["speed_kmh"], p["hr"]) for p in points if p.get("hr")]
    if len(pairs) < 2:
        return float(pairs[0][1]) if len(pairs) == 1 and abs(pairs[0][0] - speed) < near else None
    xs, hs = zip(*pairs)
    if speed < xs[0] - 1e-9 or speed > xs[-1] + 1e-9:
        return None
    return round(float(np.interp(speed, xs, hs)), 1)


# LT1 normally lies some 10-20 % below LT2 in speed. When the two land within 5 % of each other the
# test did not separate them (too few stages, or it ended too early), and zones between them would be
# empty intervals such as "150-150 beats". Then no lactate zones are given (zones.from_lactate).
MIN_THRESHOLD_GAP = 0.05


def thresholds_too_close(lt1_speed: float, lt2_speed: float) -> bool:
    """True when LT1 is within 5 % of LT2 in speed: the test could not tell them apart."""
    return lt1_speed > lt2_speed * (1.0 - MIN_THRESHOLD_GAP)


def analyze(records: Iterable[Any], max_hr: int | None = None, *, model: str = "auto",
            lt1_method: str | None = None, lt2_method: str | None = None, sport: Any = None) -> LactateResult:
    """Analyze a lactate step test.

    sport: the test's sport (default running). Running is analysed exactly as before sport existed.
    For watt (cycling, SkiErg) the fit runs on a normalised axis, the tolerances are shares of the
    load, stages with nearly the same measured load are one point, and the incline is not looked at.

    records: StepRecords (or dicts) with speed_kmh, lactate_mmol and hr_end; warm-up excluded.
    max_hr: for % of max HR at the thresholds. model: "auto" | "exp" | "poly3".
    lt1_method / lt2_method: force a method (default: the recommended one that could be computed).
    Never raises on bad data: problems are returned as ``warnings``.
    """
    prof = profile(sport)
    power = prof.load_quantity == "power"
    all_pts = _points(records)
    pts = _points(records, prof.same_load) if prof.same_load else all_pts
    sport = sport_of(sport).value
    warns: list[LactateWarning] = []

    def warn(code: str, **kw: Any) -> None:
        if all(w.code != code for w in warns):
            warns.append(LactateWarning(code, t(f"assessments.lactate.warn.{code}", **kw)))

    n = len(pts)
    empty = LactateResult(pts, None, None, "none", {}, None, [], {}, None, None, max_hr, warns, sport)
    if n < 3:
        warn("too_few", n=n)
        if pts:
            empty.baseline_mmol = min(p["lactate_mmol"] for p in pts)
            empty.peak_mmol = max(p["lactate_mmol"] for p in pts)
        return empty
    if n < 5:
        warn("few_points", n=n)
    if len(all_pts) > n:
        warn("same_load", n=len(all_pts) - n)
    xs = np.array([p["speed_kmh"] for p in pts])
    ys = np.array([p["lactate_mmol"] for p in pts])
    sx = _x_scale(xs, sport)
    inclines = {round(float(p["incline_pct"] or 0), 1) for p in pts}
    if len(inclines) > 1 and prof.has_incline:
        warn("incline_varies")
    if any(p.get("hr") is None for p in pts):
        warn("missing_hr")

    baseline = float(ys.min())
    peak = float(ys.max())
    imin = int(np.argmin(ys))
    if np.any(np.diff(ys[imin:]) < -0.5):
        warn("noisy")
    if peak - baseline < 1.0:
        warn("no_rise")

    m_exp = _fit_exp(xs, ys, sx)
    m_poly = _fit_poly(xs, ys, sx) if n >= 4 else None
    chosen: _Model | None
    if model == "exp":
        chosen = m_exp or m_poly
    elif model == "poly3":
        chosen = m_poly or m_exp
    else:
        chosen = m_exp
        if m_poly is not None and n >= 6 and (m_exp is None or _aicc(m_poly, xs, ys, 4) < _aicc(m_exp, xs, ys, 3) - 2):
            chosen = m_poly
        chosen = chosen or m_poly
    if chosen is None:
        warn("fit_failed")
        empty.baseline_mmol, empty.peak_mmol = baseline, peak
        return empty
    r2 = _r2(chosen, xs, ys)
    if r2 < 0.9:
        warn("poor_fit", r2=localize_number(f"{r2:.2f}"))
    if chosen.kind == "poly3" and n < 5:
        warn("poly_interpolates")

    def mk(method: str, speed: float | None, lactate: float | None = None, note: str = "") -> None:
        if speed is None or not math.isfinite(speed):
            return
        la = float(chosen(np.array([speed]))[0]) if lactate is None else lactate
        hr = _hr_at(pts, speed, 0.03 * speed if power else 0.3)
        inc = float(np.interp(speed, xs, [p["incline_pct"] or 0.0 for p in pts]))
        th[method] = Threshold(method, round(la, 2), round(speed, 1 if power else 2), hr,
                               round(100.0 * hr / max_hr, 1) if hr and max_hr else None, round(inc, 1), note,
                               sport)

    th: dict[str, Threshold] = {}
    # LT1
    mk("bsln_0_5", _crossing(chosen, xs, ys, baseline + 0.5))
    mk("bsln_1_0", _crossing(chosen, xs, ys, baseline + 1.0))
    mk("fixed_2_0", _crossing(chosen, xs, ys, 2.0) if baseline < 2.0 else None)
    ll = _log_log(xs, ys)
    if ll is not None:
        mk("log_log", ll)
    ri = _rise_index(ys)
    if ri is not None:
        mk("first_rise", float(xs[ri]), float(ys[ri]))
    # LT2
    mk("obla_4", _crossing(chosen, xs, ys, 4.0) if baseline < 4.0 else None)
    mk("bsln_1_5", _crossing(chosen, xs, ys, baseline + 1.5))
    if n >= 4:
        x0, x1 = float(xs[0]), float(xs[-1])
        d = _dmax(chosen, x0, float(chosen(np.array([x0]))[0]), x1, float(chosen(np.array([x1]))[0]))
        mk("dmax", d)
        if ri is not None and ri < n - 2:
            md = _dmax(chosen, float(xs[ri]), float(ys[ri]), x1, float(ys[-1]))
            mk("mod_dmax", md)
        for k in ("dmax", "mod_dmax"):
            if k in th and (th[k].speed_kmh >= x1 - 0.02 * (x1 - x0)):
                warn("edge", method=t(f"assessments.lactate.method.{k}"))
    if "obla_4" not in th and peak < 4.0:
        warn("obla_not_reached")

    # Recommended / chosen methods
    def pick(order: list[str], forced: str | None) -> str | None:
        if forced:
            if forced in th:
                return forced
            warn("method_missing", method=t(f"assessments.lactate.method.{forced}"))
        return next((m for m in order if m in th), None)

    lt2_m = pick(LT2_METHODS, lt2_method)
    lt1_order = list(LT1_METHODS)
    if lt2_m and not lt1_method:
        # LT1 must lie below LT2: skip methods that land at or above it.
        gap = 0.005 * th[lt2_m].speed_kmh if power else 0.05      # a share of the load for watt
        lt1_order = [m for m in LT1_METHODS if m in th and th[m].speed_kmh < th[lt2_m].speed_kmh - gap] or LT1_METHODS
    lt1_m = pick(lt1_order, lt1_method)
    if lt1_m and lt2_m and th[lt1_m].speed_kmh >= th[lt2_m].speed_kmh:
        warn("order")
    elif lt1_m and lt2_m and thresholds_too_close(th[lt1_m].speed_kmh, th[lt2_m].speed_kmh):
        warn("close")
    if lt1_m is None:
        warn("no_lt1")
    if lt2_m is None:
        warn("no_lt2")

    g = np.linspace(float(xs[0]), float(xs[-1]), CURVE_POINTS)
    curve = [[round(float(a), 3), round(float(b), 3)] for a, b in zip(g, chosen(g))]
    return LactateResult(pts, round(baseline, 2), round(peak, 2), chosen.kind, chosen.to_dict(), round(r2, 4),
                         curve, th, lt1_m, lt2_m, max_hr, warns, sport)
