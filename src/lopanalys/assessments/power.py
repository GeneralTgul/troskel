"""What a cycling test in watt gives besides thresholds: FTP, the best one-minute power and W/kg.

FTP (functional threshold power) is estimated in two ways, each named in the result:
  - ``ftp20``: 95 % of the best 20 minute mean power (the 20 minute test; Allen & Coggan).
  - ``ramp``: 75 % of the best one-minute power of a ramp test (MAP, maximal aerobic power).
Both carry an individual error of some 3-5 %, and FTP is not the same number as LT2 from a lactate
test: studies place the maximal lactate steady state at about 88-93 % of the 20 minute power
(docs/sporter/tester-och-zoner.md). The program therefore shows them side by side, never mixed.

The best mean power is searched in the 1 Hz samples of the whole test (``TimeSample.load`` in
watt), so the analysis does not depend on the stage clock: a test that was stopped early simply
has no 20 minutes of work.

Coggan's seven power zones are given in per cent of FTP: <55, 55-75, 76-90, 91-105, 106-120,
121-150, >150 (the borders differ a little between sources; these are TrainerRoad's).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import numpy as np

from lopanalys.core.i18n import t

FTP_FROM_20MIN = 0.95
FTP_FROM_MAP = 0.75
FTP_WINDOW_S = 1200
MAP_WINDOW_S = 60
UNEVEN_DROP = 0.08           # the second half more than 8 % below the first: the effort was not even
COGGAN = [("z1", 0.0, 0.55), ("z2", 0.55, 0.75), ("z3", 0.75, 0.90), ("z4", 0.90, 1.05), ("z5", 1.05, 1.20),
          ("z6", 1.20, 1.50), ("z7", 1.50, None)]


@dataclass
class PowerZone:
    code: str
    name: str
    low_w: int | None
    high_w: int | None        # None: no upper border


@dataclass
class PowerResult:
    """FTP and the powers it was computed from. Values that could not be computed are None."""
    method: str = ""                      # "ftp20" | "ramp" | "" (no FTP)
    ftp_w: float | None = None
    ftp_w_per_kg: float | None = None
    mean_20min_w: float | None = None     # best 20 minute mean power
    map_w: float | None = None            # best one-minute mean power
    map_w_per_kg: float | None = None
    peak_w: float | None = None           # highest 5 s mean
    hr_mean_20min: int | None = None      # mean heart rate of the best 20 minutes
    cadence_mean: float | None = None
    work_s: float = 0.0                   # seconds with power in the test
    weight_kg: float | None = None
    zones: list[PowerZone] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def method_text(self) -> str:
        return t(f"assessments.power.method.{self.method}") if self.method else ""

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self), method_text=self.method_text)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PowerResult":
        d = {k: v for k, v in d.items() if k in PowerResult.__dataclass_fields__}
        d["zones"] = [PowerZone(**z) for z in d.get("zones") or []]
        return PowerResult(**d)


def _get(o: Any, k: str) -> Any:
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def _series(samples: Iterable[Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Power, heart rate and cadence on a 1 s grid from the first sample (gaps are 0 W / NaN)."""
    rows = [(float(_get(s, "t_s")), _get(s, "speed_kmh"), _get(s, "hr"), _get(s, "cadence_spm")) for s in samples
            if _get(s, "t_s") is not None]
    if not rows:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    t0 = int(min(r[0] for r in rows))
    n = int(max(r[0] for r in rows)) - t0 + 1
    power, hr, cad = np.zeros(n), np.full(n, np.nan), np.full(n, np.nan)
    for ts, p, h, c in rows:
        i = int(ts) - t0
        power[i] = max(float(p), 0.0) if p is not None else 0.0
        if h:
            hr[i] = float(h)
        if c:
            cad[i] = float(c)
    return power, hr, cad


def best_mean(power: np.ndarray, window_s: int) -> tuple[float | None, int]:
    """The best mean power over ``window_s`` consecutive seconds and where it starts (None when the
    series is shorter than the window)."""
    n = int(window_s)
    if power.size < n or n <= 0:
        return None, 0
    csum = np.concatenate(([0.0], np.cumsum(power)))
    means = (csum[n:] - csum[:-n]) / n
    i = int(np.argmax(means))
    return float(means[i]), i


def coggan_zones(ftp_w: float | None) -> list[PowerZone]:
    """Coggan's seven power zones for an FTP ([] without one)."""
    if not ftp_w or ftp_w <= 0:
        return []
    return [PowerZone(code, t(f"assessments.power.zone.{code}"), round(ftp_w * lo) if lo else None,
                      round(ftp_w * hi) if hi else None) for code, lo, hi in COGGAN]


def analyze(samples: Iterable[Any], kind: str, weight_kg: float | None = None, measured: bool = True) -> PowerResult:
    """FTP and the best powers of a test in watt.

    kind: ``"ftp"`` (the 20 minute test: FTP = 95 % of the best 20 minutes) or ``"max_hr"`` (a ramp:
    FTP = 75 % of the best minute). Any other kind only gets the best powers. ``measured`` False:
    the powers in the samples are targets (manual mode), so nothing is computed from them.
    """
    res = PowerResult(weight_kg=weight_kg if weight_kg and weight_kg > 0 else None)
    power, hr, cad = _series(samples)
    if not measured or power.size == 0 or float(power.max(initial=0.0)) <= 0:
        res.warnings.append({"code": "no_power", "text": t("assessments.power.warn.no_power")})
        return res
    res.work_s = float(np.count_nonzero(power > 0))
    work_cad = cad[(power > 0) & ~np.isnan(cad)]
    res.cadence_mean = round(float(work_cad.mean()), 1) if work_cad.size else None
    peak, _ = best_mean(power, 5)
    res.peak_w = round(peak) if peak else None
    m1, _ = best_mean(power, MAP_WINDOW_S)
    res.map_w = round(m1) if m1 else None
    m20, i20 = best_mean(power, FTP_WINDOW_S)
    if m20:
        res.mean_20min_w = round(m20)
        h = hr[i20:i20 + FTP_WINDOW_S]
        res.hr_mean_20min = int(round(float(np.nanmean(h)))) if np.count_nonzero(~np.isnan(h)) > 60 else None
    if kind == "ftp":
        if m20:
            res.method, res.ftp_w = "ftp20", round(res.mean_20min_w * FTP_FROM_20MIN)
            first = float(power[i20:i20 + FTP_WINDOW_S // 2].mean())
            second = float(power[i20 + FTP_WINDOW_S // 2:i20 + FTP_WINDOW_S].mean())
            if first > 0 and (first - second) / first > UNEVEN_DROP:
                res.warnings.append({"code": "uneven", "text": t("assessments.power.warn.uneven",
                                                                 pct=round(100 * (first - second) / first))})
        else:
            res.warnings.append({"code": "short", "text": t("assessments.power.warn.short",
                                                            min=int(res.work_s // 60))})
    elif kind == "max_hr" and m1:
        res.method, res.ftp_w = "ramp", round(res.map_w * FTP_FROM_MAP)
    if res.weight_kg:
        res.ftp_w_per_kg = round(res.ftp_w / res.weight_kg, 2) if res.ftp_w else None
        res.map_w_per_kg = round(res.map_w / res.weight_kg, 2) if res.map_w else None
    res.zones = coggan_zones(res.ftp_w)
    return res


def w_per_kg(watt: float | None, weight_kg: float | None) -> float | None:
    """Watt per kilogram with two decimals (None without both)."""
    if not watt or not weight_kg or weight_kg <= 0:
        return None
    return round(float(watt) / float(weight_kg), 2)


__all__ = ["PowerResult", "PowerZone", "analyze", "best_mean", "coggan_zones", "w_per_kg", "FTP_FROM_20MIN",
           "FTP_FROM_MAP"]
