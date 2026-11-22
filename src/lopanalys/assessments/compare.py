"""Change since the previous test: threshold shifts and how the lactate curve has moved.

A curve that moves to the right (higher speed at the same lactate) means better endurance.
Fixed lactate levels 2, 3 and 4 mmol/L are compared where both tests reached them; fixed speeds
are compared where both tests covered them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from lopanalys.assessments.lactate import LactateResult, Threshold
from lopanalys.core.i18n import localize_number, t
from lopanalys.core.sport import Sport, profile

LEVELS_MMOL = (2.0, 3.0, 4.0)
RIGHT_SHIFT_KMH = 0.2     # smaller mean shifts are within the method's noise (running; per sport in the profile)


@dataclass
class ThresholdChange:
    which: str                       # "lt1" | "lt2"
    method: str
    speed_before: float
    speed_after: float
    speed_delta: float
    hr_before: float | None
    hr_after: float | None
    hr_delta: float | None
    lactate_before: float
    lactate_after: float
    pace_delta_s: float              # seconds per km, negative = faster


@dataclass
class LactateComparison:
    lt1: ThresholdChange | None
    lt2: ThresholdChange | None
    speed_at_level: list[dict[str, Any]] = field(default_factory=list)    # {"mmol", "before", "after", "delta"}
    lactate_at_speed: list[dict[str, Any]] = field(default_factory=list)  # {"speed_kmh", "before", "after", "delta"}
    curve_shift_kmh: float | None = None
    shift: str = "unknown"           # "right" (better) | "left" (worse) | "none" | "unknown"
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_result(r: LactateResult | dict[str, Any]) -> LactateResult:
    return r if isinstance(r, LactateResult) else LactateResult.from_dict(r)


def _change(which: str, a: Threshold | None, b: Threshold | None) -> ThresholdChange | None:
    if a is None or b is None:
        return None
    hd = round(b.hr - a.hr, 1) if a.hr is not None and b.hr is not None else None
    return ThresholdChange(which, b.method, a.speed_kmh, b.speed_kmh, round(b.speed_kmh - a.speed_kmh, 2),
                           a.hr, b.hr, hd, a.lactate_mmol, b.lactate_mmol,
                           _pace_delta(a, b))


def _pace_delta(a: Threshold, b: Threshold) -> float:
    """The change in pace in seconds (per km, or per 500 m on a SkiErg); 0 for a sport without pace."""
    p = profile(b.sport)
    pa, pb = p.pace_s(a.speed_kmh), p.pace_s(b.speed_kmh)
    return round(pb - pa, 1) if pa is not None and pb is not None else 0.0


def compare_lactate(current: LactateResult | dict[str, Any], previous: LactateResult | dict[str, Any]) -> LactateComparison:
    """Compare a lactate test with an earlier one. Thresholds use the current test's methods on both."""
    cur, prev = _as_result(current), _as_result(previous)
    prof = profile(cur.sport)
    run = prof.sport is Sport.RUN
    noise = RIGHT_SHIFT_KMH if run else prof.curve_shift
    def same(r: LactateResult, m: str | None) -> Threshold | None:
        return r.thresholds.get(m) if m else None
    c = LactateComparison(_change("lt1", same(prev, cur.lt1_method) or prev.lt1, cur.lt1),
                          _change("lt2", same(prev, cur.lt2_method) or prev.lt2, cur.lt2))
    deltas = []
    for mm in LEVELS_MMOL:
        a, b = prev.speed_at(mm), cur.speed_at(mm)
        if a is not None and b is not None:
            d = round(b - a, 2)
            deltas.append(d)
            c.speed_at_level.append({"mmol": mm, "before": round(a, 2), "after": round(b, 2), "delta": d})
    if cur.points and prev.points:
        lo = max(cur.points[0]["speed_kmh"], prev.points[0]["speed_kmh"])
        hi = min(cur.points[-1]["speed_kmh"], prev.points[-1]["speed_kmh"])
        grid = 1.0 if run else 25.0          # every km/h, or every 25 W
        v = float(round(lo / grid) * grid)
        while v <= hi + 1e-9:
            if v >= lo - 1e-9:
                a, b = prev.lactate_at(v), cur.lactate_at(v)
                if a is not None and b is not None:
                    c.lactate_at_speed.append({"speed_kmh": v, "before": round(a, 2), "after": round(b, 2),
                                               "delta": round(b - a, 2)})
            v += grid
    if deltas:
        c.curve_shift_kmh = round(sum(deltas) / len(deltas), 2)
        c.shift = ("right" if c.curve_shift_kmh >= noise else
                   "left" if c.curve_shift_kmh <= -noise else "none")
    parts = []
    for ch in (c.lt1, c.lt2):
        if ch and run:
            parts.append(t(f"assessments.compare.{ch.which}", delta=_signed(ch.speed_delta),
                           pace=_signed(ch.pace_delta_s, 0)))
        elif ch:
            key = f"assessments.compare.{ch.which}_pace" if prof.pace_unit else f"assessments.compare.{ch.which}_load"
            parts.append(t(key, delta=_signed(ch.speed_delta, 0), unit=prof.load_unit,
                           pace=_signed(ch.pace_delta_s, 0), pace_unit=prof.pace_unit or ""))
    if run or c.shift in ("none", "unknown"):
        parts.append(t(f"assessments.compare.shift.{c.shift}", kmh=_signed(c.curve_shift_kmh or 0.0)))
    else:
        parts.append(t(f"assessments.compare.shift.{c.shift}_load", delta=_signed(c.curve_shift_kmh or 0.0, 0),
                       unit=prof.load_unit))
    c.summary = " ".join(parts)
    return c


def compare_max_hr(current: dict[str, Any] | Any, previous: dict[str, Any] | Any) -> dict[str, Any]:
    """Peak and recovery HR change between two max HR tests (MaxHrResult or dict)."""
    g = (lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None))
    out: dict[str, Any] = {}
    for k in ("peak_hr", "recovery_hr_60", "recovery_drop_60"):
        a, b = g(previous, k), g(current, k)
        out[k] = {"before": a, "after": b, "delta": (b - a) if a is not None and b is not None else None}
    return out


def _signed(v: float, nd: int = 1) -> str:
    s = f"{v:+.{nd}f}" if nd else f"{round(v):+d}"
    return localize_number(s).replace("-", "−")
