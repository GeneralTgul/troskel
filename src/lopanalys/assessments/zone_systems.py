"""Training zones in several systems, all computed from the same anchors.

"Zone 2" means different things in a 3-zone model, in the Norwegian 5-zone scale and on a Garmin
watch. This module keeps every system as data (``SYSTEMS``) and converts one test result into any of
them, so that the zones on the athlete's watch can match the zones from the test.

Anchors (``Anchors``): heart rate at LT1 and LT2, max HR, resting HR, and the speed at LT1/LT2.
A border of a zone is a ``(kind, factor)`` pair relative to those anchors:

    ("max", 0.60)   60 % of max HR
    ("lt1", 1.0)    heart rate at LT1          ("lt2", 0.85)   85 % of the heart rate at LT2
    ("mid", 0.5)    halfway between LT1 and LT2 (in heart rate)
    ("top", 0.5)    halfway between LT2 and max HR
    None            open-ended (no lower limit)

Each system also carries its nominal borders in % of max HR (``pct_max``) where the system publishes
them. They are the fallback when the test gave no thresholds (a max HR test only): such zones are
marked ``estimated`` and the UI says "uppskattade ur maxpuls".

Systems and sources:
  - ``olympiatoppen``: I1-I5, the Norwegian scale (Olympiatoppen, "Intensitetssoner", 2024 PDF,
    olt-skala.nif.no). Nominal: I1 55-72 % HRmax, I2 72-82 %, I3 82-87 %, I4 87-92 %, I5 92-100 %.
    Lactate: Olympiatoppen's own guide values per zone, checked 2026-11-15 against olt_2024_no.pdf:
    I1 below 1.5 mmol/L, I2 ~1.0-2.0, I3 ~1.5-3.5, none for I4, and lactate does not
    define I5. The values overlap, so they are kept per zone (``ZoneSystem.lactate_ranges``), not as
    shared borders. The PDF has eight zones (I-1 to I-8); I-6 to I-8 are not used. From a lactate
    test the borders are individual (``curve_zones``); its I4/I5 border at ~6 mmol/L is this
    program's own rule, not Olympiatoppen's.
  - ``seiler``: three zones split at LT1 and LT2 (Seiler & Kjerland 2006, Scand J Med Sci Sports
    16:49). Nominal 82 % and 87 % of max HR are typical values, not part of the model.
  - ``swedish``: A1, A2, A3 and A3+, the scale Swedish endurance coaches use. A1 below the aerobic
    threshold, A2 between the thresholds, A3 above the anaerobic threshold, A3+ maximal work.
    Checked 2026-11-14: no Swedish Ski Federation document was found. The nominal
    percentages (A1 60-75, A2 75-85, A3 85-95, A3+ 95-100 % of max HR) match a training booklet by
    Charlotte Kalla (Bonnier 2019), where the top zone is called "An", not "A3+"; other sources use other limits.
    A3+ has no heart-rate definition of its own; here it is the last 5 % up to max HR.
  - ``garmin``: the watch's five zones. With thresholds the borders are our own layout for Garmin's
    "% LTHR"/custom-limits method: zone 1 is the watch's own warm-up zone (50-60 % of max HR), zone 2
    ends at LT1, zone 4 ends at LT2 and the zone 3/4 border lies halfway between them. Without
    thresholds: Garmin's default 50-60-70-80-90-100 % of max HR (Garmin owner's manuals, "Heart
    Rate Zone Calculations"; checked 2026-11-14). Garmin Connect takes zones as bpm, % max HR, % HRR or
    % LTHR (Zones > Based on); the number of borders was not found. Garmin's own default percentages for the % LTHR method are not used.
  - ``polar``: five zones at 50-60-70-80-90-100 % of max HR (Polar's default sport zones). Polar
    defines the zones by max HR only, so they are the same with or without thresholds.
  - ``friel``: seven running zones in % of the lactate threshold heart rate (Joe Friel, "The
    Triathlete's Training Bible"): Z1 < 85 %, Z2 85-89, Z3 90-94, Z4 95-99, Z5a 100-102, Z5b 103-106,
    Z5c > 106 (checked 2026-11-14 against 220 Triathlon). Friel takes LTHR from a 30-minute time trial; we use the heart rate at LT2, which
    is close to it but not the same measurement.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np

from lopanalys.assessments.lactate import LactateResult, pace_str, thresholds_too_close
from lopanalys.core.sport import profile, sport_of
from lopanalys.core.i18n import t

# Olympiatoppen nominal ranges: (code, %HRmax low, high, lactate low, high). The %HRmax columns are shared
# borders from OLT's 2024 scale (olt-skala.nif.no). The lactate columns are Olympiatoppen's own guide
# values per zone (olt_2024_no.pdf, checked 2026-11-15): they overlap (I2 ~1.0-2.0 and I3
# ~1.5-3.5), so they are a range per zone, not borders. None = no value: I1 has no lower value ("under
# 1.5"), OLT gives none for I4, and lactate does not define I5. For display only; they set no border.
OLT_TABLE = [
    ("I1", 55, 72, None, 1.5),
    ("I2", 72, 82, 1.0, 2.0),
    ("I3", 82, 87, 1.5, 3.5),
    ("I4", 87, 92, None, None),
    ("I5", 92, 100, None, None),
]
# Same zones as % of heart-rate reserve (Karvonen), derived from the table for a typical
# HRmax 190 / rest 50 and rounded.
OLT_HRR = [("I1", 45, 64), ("I2", 64, 77), ("I3", 77, 84), ("I4", 84, 90), ("I5", 90, 96)]
SEILER_PCT = [("Z1", 55, 82), ("Z2", 82, 87), ("Z3", 87, 100)]

# Without a measured threshold, a system defined in % of LTHR (Friel) needs an LTHR. The nominal
# anaerobic threshold of the Olympiatoppen table (the I3/I4 border, 87 % of max HR) is used; the
# zones are then marked as estimated.
EST_LTHR_PCT_MAX = 87.0

Ref = tuple[str, float]


@dataclass
class Zone:
    system: str                        # key in SYSTEMS
    code: str                          # "I1".."I5", "Z1".."Z3", "A1".."A3+", "Z5a" ...
    name: str
    purpose: str
    hr_low: int | None = None
    hr_high: int | None = None
    hr_pct_low: float | None = None    # % of max HR
    hr_pct_high: float | None = None
    speed_low_kmh: float | None = None
    speed_high_kmh: float | None = None
    lactate_low: float | None = None
    lactate_high: float | None = None
    basis: str = ""                    # "lactate" | "hr_max" | "karvonen"
    estimated: bool = False            # True: no thresholds were measured, the borders come from max HR
    # Speeds from the heart rate -> speed relation of a test without lactate (hr_zones.zone_speeds):
    speed_basis: str = ""              # "" (from the lactate curve) | "hr"
    speed_low_extrapolated: bool = False    # the border lies outside the measured steps
    speed_high_extrapolated: bool = False
    sport: str = "run"                 # core.sport.Sport value: whose max HR, and the unit of the load

    @property
    def load_low(self) -> float | None:
        """The lower border as a load in the unit of the sport (km/h for running)."""
        return self.speed_low_kmh

    @property
    def load_high(self) -> float | None:
        return self.speed_high_kmh

    @property
    def pace_low(self) -> str:         # slower end
        return pace_str(self.speed_low_kmh, self.sport)

    @property
    def pace_high(self) -> str:
        return pace_str(self.speed_high_kmh, self.sport)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pace_low"], d["pace_high"] = self.pace_low, self.pace_high
        return d


@dataclass(frozen=True)
class ZoneSystem:
    """One zone system as data. ``anchored`` and ``pct_max``/``pct_hrr`` hold n+1 borders for n zones,
    lowest first. ``lactate_ranges`` holds one (low, high) pair per zone, since a system's lactate
    guide values may overlap (Olympiatoppen); None in a pair means no value at that end."""
    key: str
    codes: tuple[str, ...]
    anchored: tuple[Ref | None, ...] | None = None    # borders relative to the anchors; None: % only
    pct_max: tuple[float, ...] | None = None          # nominal borders in % of max HR
    pct_hrr: tuple[float, ...] | None = None          # nominal borders in % of heart-rate reserve
    # nominal lactate per zone (mmol/L), (low, high) with None for no value
    lactate_ranges: tuple[tuple[float | None, float | None], ...] | None = None

    @property
    def name(self) -> str:
        return t(f"assessments.zonesys.{self.key}.name")

    @property
    def note(self) -> str:
        """One sentence on how the system defines its zones."""
        return t(f"assessments.zonesys.{self.key}.note")

    @property
    def n(self) -> int:
        return len(self.codes)


def _borders(rows: list[tuple[Any, ...]], lo: int, hi: int) -> tuple[float, ...]:
    """(code, a, b, ...) rows with shared borders -> the n+1 border values."""
    return tuple([r[lo] for r in rows] + [rows[-1][hi]])


SYSTEMS: dict[str, ZoneSystem] = {s.key: s for s in (
    ZoneSystem("olympiatoppen", tuple(r[0] for r in OLT_TABLE),
               # Anchors only (no lactate curve): I4/I5 halfway between LT2 and max HR. With the
               # curve, curve_zones() puts it where the lactate reaches about 6 mmol/L instead.
               anchored=(("max", 0.55), ("lt1", 1.0), ("mid", 0.5), ("lt2", 1.0), ("top", 0.5), ("max", 1.0)),
               pct_max=_borders(OLT_TABLE, 1, 2), pct_hrr=_borders(OLT_HRR, 1, 2),
               lactate_ranges=tuple((r[3], r[4]) for r in OLT_TABLE)),
    ZoneSystem("seiler", tuple(r[0] for r in SEILER_PCT),
               anchored=(("max", 0.55), ("lt1", 1.0), ("lt2", 1.0), ("max", 1.0)),
               pct_max=_borders(SEILER_PCT, 1, 2)),
    ZoneSystem("swedish", ("A1", "A2", "A3", "A3+"),
               anchored=(("max", 0.55), ("lt1", 1.0), ("lt2", 1.0), ("max", 0.95), ("max", 1.0)),
               pct_max=(60, 75, 85, 95, 100)),
    ZoneSystem("garmin", ("Z1", "Z2", "Z3", "Z4", "Z5"),
               anchored=(("max", 0.50), ("max", 0.60), ("lt1", 1.0), ("mid", 0.5), ("lt2", 1.0), ("max", 1.0)),
               pct_max=(50, 60, 70, 80, 90, 100)),
    ZoneSystem("polar", ("Z1", "Z2", "Z3", "Z4", "Z5"), pct_max=(50, 60, 70, 80, 90, 100)),
    ZoneSystem("friel", ("Z1", "Z2", "Z3", "Z4", "Z5a", "Z5b", "Z5c"),
               anchored=(None, ("lt2", 0.85), ("lt2", 0.90), ("lt2", 0.95), ("lt2", 1.0), ("lt2", 1.03),
                         ("lt2", 1.06), ("max", 1.0))),
)}
DEFAULT_SYSTEM = "garmin"   # as on the watch
# The systems whose borders follow the lactate curve itself when a test result is at hand.
CURVE_SYSTEMS = ("olympiatoppen", "seiler")
# "The same thresholds in a 3-zone system, a 5-zone system and on the watch."
COMPARE_SYSTEMS = ("seiler", "olympiatoppen", "garmin")


def get_system(key: str) -> ZoneSystem:
    try:
        return SYSTEMS[key]
    except KeyError:
        raise ValueError(t("assessments.zones.error.system", system=key)) from None


def _texts(system: str, code: str) -> tuple[str, str]:
    k = f"assessments.zones.{system}.{code}"
    return t(k + ".name"), t(k + ".purpose")


def _pct(hr: float | None, max_hr: int | None) -> float | None:
    return round(100.0 * hr / max_hr, 1) if hr and max_hr else None


def _r(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(float(v), nd)


def _valid_max(max_hr: Any) -> int | None:
    return int(max_hr) if max_hr and 100 <= max_hr <= 240 else None


def _hr_at_speed(result: LactateResult, speed: float, max_hr: int | None) -> float | None:
    pairs = [(p["speed_kmh"], p["hr"]) for p in result.points if p.get("hr")]
    if len(pairs) < 2:
        return None
    xs, hs = map(np.array, zip(*pairs))
    if xs[0] <= speed <= xs[-1]:
        return float(np.interp(speed, xs, hs))
    # Outside the test: linear trend of the last/first two points, capped at max HR.
    if speed > xs[-1]:
        slope = (hs[-1] - hs[-2]) / (xs[-1] - xs[-2]) if xs[-1] > xs[-2] else 0.0
        v = hs[-1] + slope * (speed - xs[-1])
        return float(min(v, max_hr) if max_hr else v)
    slope = (hs[1] - hs[0]) / (xs[1] - xs[0]) if xs[1] > xs[0] else 0.0
    return float(max(hs[0] + slope * (speed - xs[0]), 40.0))


# ---------------------------------------------------------------------- anchors
@dataclass(frozen=True)
class Anchors:
    """What every zone system is computed from. Missing values are None."""
    max_hr: int | None = None
    resting_hr: int | None = None
    lt1_hr: float | None = None
    lt2_hr: float | None = None
    lt1_speed_kmh: float | None = None
    lt2_speed_kmh: float | None = None
    # The lactate test the thresholds came from: lets olympiatoppen/seiler follow the curve.
    source: LactateResult | None = field(default=None, compare=False, repr=False)
    # The sport the heart rates and loads belong to. Zones never move between sports: max HR and
    # threshold HR are lower on the bike than in running, and lower still in double poling.
    sport: str = "run"

    @property
    def has_thresholds(self) -> bool:
        """Both thresholds as heart rates, in the right order."""
        return bool(self.lt1_hr and self.lt2_hr and self.lt1_hr < self.lt2_hr)

    @staticmethod
    def from_lactate(result: LactateResult | None, max_hr: int | None = None,
                     resting_hr: int | None = None, sport: Any = None) -> "Anchors":
        """Anchors from a lactate result. Thresholds the test could not separate are left out, and
        the systems then fall back to max HR (the same rule as zones.from_lactate).

        ``sport``: the sport ``max_hr`` belongs to when there is no lactate result; with a result the
        sport is the test's."""
        max_hr = max_hr or (result.max_hr if result is not None else None)
        sport = result.sport if result is not None else sport_of(sport).value
        base = Anchors(max_hr=max_hr, resting_hr=resting_hr, sport=sport)
        if result is None:
            return base
        lt1, lt2 = result.lt1, result.lt2
        if (lt1 is None or lt2 is None or lt1.speed_kmh >= lt2.speed_kmh
                or thresholds_too_close(lt1.speed_kmh, lt2.speed_kmh)):
            return base
        h1 = lt1.hr or _hr_at_speed(result, lt1.speed_kmh, max_hr)
        h2 = lt2.hr or _hr_at_speed(result, lt2.speed_kmh, max_hr)
        if not h1 or not h2 or h1 >= h2:
            # Speeds but no usable heart rate: only the curve systems can give (speed) zones.
            return replace(base, lt1_speed_kmh=lt1.speed_kmh, lt2_speed_kmh=lt2.speed_kmh, source=result)
        return Anchors(max_hr, resting_hr, float(h1), float(h2), lt1.speed_kmh, lt2.speed_kmh, source=result,
                       sport=sport)


def _resolve(ref: Ref | None, a: Anchors, mx: int | None) -> float | None:
    if ref is None:
        return None
    kind, f = ref
    if kind == "max":
        return f * mx if mx else None
    if kind == "lt1":
        return f * a.lt1_hr if a.lt1_hr else None
    if kind == "lt2":
        return f * a.lt2_hr if a.lt2_hr else None
    if kind == "mid":
        return a.lt1_hr + f * (a.lt2_hr - a.lt1_hr) if a.lt1_hr and a.lt2_hr else None
    if kind == "top":
        return a.lt2_hr + f * (mx - a.lt2_hr) if a.lt2_hr and mx and mx > a.lt2_hr else None
    raise ValueError(kind)


def _speed(ref: Ref | None, a: Anchors) -> float | None:
    """Speed at a border, only where the test measured it: at the thresholds and between them."""
    if ref is None or not a.lt1_speed_kmh or not a.lt2_speed_kmh:
        return None
    kind, f = ref
    if ref == ("lt1", 1.0):
        return a.lt1_speed_kmh
    if ref == ("lt2", 1.0):
        return a.lt2_speed_kmh
    if kind == "mid":
        return a.lt1_speed_kmh + f * (a.lt2_speed_kmh - a.lt1_speed_kmh)
    return None


def _computable(sys: ZoneSystem, a: Anchors) -> bool:
    """True when the anchors hold every threshold the system's borders refer to."""
    kinds = {r[0] for r in sys.anchored or () if r is not None}
    if kinds & {"lt1", "mid"} and not a.has_thresholds:
        return False
    return not (kinds & {"lt2", "top"}) or bool(a.lt2_hr)


def _anchored_zones(sys: ZoneSystem, a: Anchors, mx: int | None, estimated: bool) -> list[Zone]:
    refs = sys.anchored or ()
    vals = [_resolve(r, a, mx) for r in refs]
    if any(v is None for v in vals[1:-1]):
        return []
    speeds = [_speed(r, a) for r in refs]
    # Keep the borders in order from the top down (a measured threshold or max HR wins over a
    # percentage below it), then drop the zones that became empty: with LT2 within 6 % of max HR
    # there is no room for Friel's zone 5c.
    for i in range(len(vals) - 2, -1, -1):
        if vals[i] is not None and vals[i + 1] is not None and vals[i] > vals[i + 1]:
            vals[i], speeds[i] = vals[i + 1], None
    zones = []
    for i, code in enumerate(sys.codes):
        lo, hi = vals[i], vals[i + 1]
        if lo is not None and hi is not None and round(hi) <= round(lo):
            continue
        name, purpose = _texts(sys.key, code)
        zones.append(Zone(sys.key, code, name, purpose,
                          hr_low=round(lo) if lo else None, hr_high=round(hi) if hi else None,
                          hr_pct_low=_pct(lo, mx), hr_pct_high=_pct(hi, mx),
                          speed_low_kmh=_r(speeds[i]), speed_high_kmh=_r(speeds[i + 1]),
                          basis="hr_max" if estimated else "lactate", estimated=estimated, sport=a.sport))
    return zones


def nominal_zones(max_hr: int, resting_hr: int | None = None, system: str = DEFAULT_SYSTEM,
                  sport: Any = None) -> list[Zone]:
    """Zones from the system's nominal % of max HR, or % of heart-rate reserve (Karvonen et al. 1957)
    when the system has such a table and resting HR is given. [] for a system without % of max HR.

    ``sport``: the sport ``max_hr`` was measured in (default running)."""
    sys = get_system(system)
    sport = sport_of(sport).value
    if sys.pct_max is None:
        return []
    karvonen = bool(sys.pct_hrr and resting_hr and 25 <= resting_hr < max_hr - 40)
    zones = []
    for i, code in enumerate(sys.codes):
        name, purpose = _texts(sys.key, code)
        if karvonen:
            hr_lo = resting_hr + sys.pct_hrr[i] / 100 * (max_hr - resting_hr)
            hr_hi = resting_hr + sys.pct_hrr[i + 1] / 100 * (max_hr - resting_hr)
        else:
            hr_lo, hr_hi = sys.pct_max[i] / 100 * max_hr, sys.pct_max[i + 1] / 100 * max_hr
        zones.append(Zone(sys.key, code, name, purpose, hr_low=round(hr_lo), hr_high=round(hr_hi),
                          hr_pct_low=_pct(hr_lo, max_hr), hr_pct_high=_pct(hr_hi, max_hr),
                          lactate_low=sys.lactate_ranges[i][0] if sys.lactate_ranges else None,
                          lactate_high=sys.lactate_ranges[i][1] if sys.lactate_ranges else None,
                          basis="karvonen" if karvonen else "hr_max",
                          # A system that is defined by % of max HR (Polar) is not an estimate.
                          estimated=sys.anchored is not None, sport=sport))
    return zones


def curve_zones(result: LactateResult, max_hr: int | None = None, system: str = DEFAULT_SYSTEM) -> list[Zone]:
    """Individual olympiatoppen/seiler zones that follow the lactate curve: I1/I2 at LT1, I2/I3
    halfway between LT1 and LT2 (in speed), I3/I4 at LT2, I4/I5 where the curve reaches ~6 mmol/L (or
    LT2 + 2 mmol/L if LT2 is high). Heart rate at each border is interpolated from the test; outside
    the test range it is estimated linearly towards max HR. [] when the thresholds cannot be used."""
    max_hr = max_hr or result.max_hr
    lt1, lt2 = result.lt1, result.lt2
    if (lt1 is None or lt2 is None or lt1.speed_kmh >= lt2.speed_kmh
            or thresholds_too_close(lt1.speed_kmh, lt2.speed_kmh)):
        # Thresholds the test could not separate would give empty zones.
        return []

    v1, v2 = lt1.speed_kmh, lt2.speed_kmh
    top_v = max([p["speed_kmh"] for p in result.points] + [v2])
    la = result.lactate_at

    def border(speed: float | None) -> tuple[float | None, float | None, float | None]:
        if speed is None:
            return None, None, None
        hr = _hr_at_speed(result, speed, max_hr)
        return speed, hr, la(speed)

    if system == "seiler":
        # LT2 at the last stage: the top zone has no measured upper speed (not "16,2-16,2 km/h").
        bounds = [(None, v1), (v1, v2), (v2, top_v if top_v > v2 else None)]
        codes = ["Z1", "Z2", "Z3"]
    else:
        target4 = max(6.0, (lt2.lactate_mmol or 4.0) + 2.0)
        v4 = result.speed_at(target4)
        if v4 is None or v4 <= v2:
            v4 = v2 * profile(result.sport).lt2_overshoot   # beyond the test: ~5 % above LT2 speed (per sport)
        v_mid = v1 + 0.5 * (v2 - v1)
        bounds = [(None, v1), (v1, v_mid), (v_mid, v2), (v2, v4), (v4, max(top_v, v4 * 1.04))]
        codes = ["I1", "I2", "I3", "I4", "I5"]

    zones = []
    beyond = False     # True above a border whose heart rate the test cannot tell
    for code, (lo, hi) in zip(codes, bounds):
        name, purpose = _texts(system, code)
        s_lo, hr_lo, la_lo = border(lo)
        s_hi, hr_hi, la_hi = border(hi)
        hr2 = lt2.hr or _hr_at_speed(result, v2, max_hr)
        for at, spd in (("lo", lo), ("hi", hi)):
            # The I4/I5 border often lies past the last stage, where the heart rate is a trend capped
            # at max HR. When the cap leaves no room for I5 ("190-190"), the border is put halfway
            # between LT2 and max HR, as the system does without a curve.
            if system != "seiler" and spd == v4 and max_hr and hr2 and max_hr > hr2:
                h = hr_lo if at == "lo" else hr_hi
                if h is not None and round(h) >= round(max_hr):
                    h = hr2 + 0.5 * (max_hr - hr2)
                    hr_lo, hr_hi = (h, hr_hi) if at == "lo" else (hr_lo, h)
        if lo is None:   # bottom zone: from 55 % of max HR (easy jogging) up to LT1
            hr_lo = 0.55 * max_hr if max_hr else None
            la_lo = None
        if code in ("I5", "Z3"):
            hr_hi = float(max_hr) if max_hr else hr_hi
            la_hi = None
        # The very first border (LT1 itself) uses the threshold values, not the curve.
        if hi == v1:
            hr_hi, la_hi = lt1.hr or hr_hi, lt1.lactate_mmol
        if lo == v1:
            hr_lo, la_lo = lt1.hr or hr_lo, lt1.lactate_mmol
        if hi == v2 and code not in ("I5", "Z3"):
            hr_hi, la_hi = lt2.hr or hr_hi, lt2.lactate_mmol
        if lo == v2:
            hr_lo, la_lo = lt2.hr or hr_lo, lt2.lactate_mmol
        # A zone without room in heart rate ("148-148"): the max HR handed in is no higher than the
        # heart rate the test reached (a submaximal test, its own peak as "max"). The heart rate
        # from there on up is not known, and is left out rather than shown as an empty interval.
        if beyond:
            hr_lo = hr_hi = None
        elif hr_lo and hr_hi and round(hr_hi) <= round(hr_lo):
            hr_hi, beyond = None, True
        zones.append(Zone(system, code, name, purpose,
                          hr_low=round(hr_lo) if hr_lo else None, hr_high=round(hr_hi) if hr_hi else None,
                          hr_pct_low=_pct(hr_lo, max_hr), hr_pct_high=_pct(hr_hi, max_hr),
                          speed_low_kmh=_r(s_lo), speed_high_kmh=_r(s_hi),
                          lactate_low=_r(la_lo), lactate_high=_r(la_hi), basis="lactate", sport=result.sport))
    return zones


# ---------------------------------------------------------------------- conversion
def convert(anchors: Anchors, system: str = DEFAULT_SYSTEM) -> list[Zone]:
    """The zones of one system for these anchors.

    With thresholds: the system's borders at the athlete's own LT1 and LT2. Without them (a max HR
    test only): the system's nominal % of max HR, marked ``estimated``. [] when not even a plausible
    max HR is known. Raises ValueError for an unknown system.
    """
    sys = get_system(system)
    a = anchors
    mx = _valid_max(a.max_hr)
    if a.source is not None and sys.key in CURVE_SYSTEMS:
        zs = curve_zones(a.source, mx, sys.key)
        if zs:
            return zs
    if sys.anchored is not None and _computable(sys, a):
        zs = _anchored_zones(sys, a, mx, estimated=False)
        if zs:
            return zs
    if mx is None:
        return []
    if sys.pct_max is not None:
        return nominal_zones(mx, a.resting_hr, sys.key, a.sport)
    # Defined by % of LTHR only (Friel): estimate the LTHR from max HR.
    est = Anchors(max_hr=mx, resting_hr=a.resting_hr, lt2_hr=EST_LTHR_PCT_MAX / 100 * mx, sport=a.sport)
    return _anchored_zones(sys, est, mx, estimated=True)


def compare(anchors: Anchors, systems: tuple[str, ...] = COMPARE_SYSTEMS) -> dict[str, list[Zone]]:
    """The same anchors in several systems, for the comparison table (systems without zones are left out)."""
    out = {}
    for key in systems:
        zs = convert(anchors, key)
        if zs:
            out[key] = zs
    return out


# ---------------------------------------------------------------------- Garmin
@dataclass
class GarminSetup:
    """Exactly what the athlete types into Garmin Connect (heart-rate zones of the user profile).

    ``method`` "lthr": choose zones based on % LTHR, enter ``lthr`` and set the five zones to the
    limits in ``zones`` (beats/min, with the matching % of LTHR). ``method`` "max": no thresholds
    were measured; enter ``max_hr`` and keep the watch's own % of max HR zones, listed in ``zones``.
    """
    method: str
    max_hr: int | None
    resting_hr: int | None
    lthr: int | None                              # heart rate at LT2
    threshold_speed_kmh: float | None             # speed at LT2
    zones: list[dict[str, Any]]                   # {"zone", "hr_low", "hr_high", "pct_low", "pct_high"}
    estimated: bool = False
    sport: str = "run"                            # the sport the heart rates were measured in

    @property
    def threshold_pace(self) -> str:
        """Lactate threshold pace as 'm:ss' per km ('' when unknown, and for a sport in watt)."""
        return pace_str(self.threshold_speed_kmh, self.sport) if sport_of(self.sport).value == "run" else ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["threshold_pace"] = self.threshold_pace
        return d


def garmin_setup(anchors: Anchors, max_measured: bool = True) -> GarminSetup | None:
    """The values for the athlete's Garmin watch, or None when no zones can be computed.

    ``max_measured`` False: ``anchors.max_hr`` is only the highest heart rate of a test that did not
    go to the limit. It still places zone 1, but it is not handed out as the max HR to type into
    the watch, and zone 5 gets no upper limit.
    """
    if sport_of(anchors.sport).value == "skierg":
        return None          # no watch setting is known for double poling: nothing is handed out
    zs = convert(anchors, "garmin")
    if not zs:
        return None
    estimated = zs[0].estimated
    mx = _valid_max(anchors.max_hr)
    lthr = None if estimated or not anchors.lt2_hr else round(anchors.lt2_hr)
    base = lthr if lthr else mx          # what Garmin shows the percentages of
    rows = [{"zone": int(z.code[1:]), "hr_low": z.hr_low, "hr_high": z.hr_high,
             "pct_low": round(100 * z.hr_low / base) if z.hr_low and base else None,
             "pct_high": round(100 * z.hr_high / base) if z.hr_high and base else None} for z in zs]
    if not max_measured and lthr:
        mx = None
        rows[-1]["hr_high"] = rows[-1]["pct_high"] = None
    return GarminSetup("lthr" if lthr else "max", mx, anchors.resting_hr or None, lthr,
                       anchors.lt2_speed_kmh if lthr else None, rows, estimated, sport_of(anchors.sport).value)


def garmin_card(setup: GarminSetup | dict[str, Any] | None) -> dict[str, Any] | None:
    """The Garmin values as display texts in the current language, the same for the screen and the
    report: {"title", "intro", "fields": [{"label", "value"}], "zones": [{"label", "bpm", "pct"}],
    "why_title", "why", "estimated"}. None without zones."""
    g = setup.to_dict() if isinstance(setup, GarminSetup) else setup
    if not g or not g.get("zones"):
        return None
    lthr = g.get("method") == "lthr"

    def bpm(v: Any, missing: str) -> str:
        return t("assessments.garmin.bpm", v=v) if v else t(missing)

    fields = [{"label": t("assessments.garmin.max_hr"), "value": bpm(g.get("max_hr"), "assessments.garmin.not_measured")},
              {"label": t("assessments.garmin.lthr"), "value": bpm(g.get("lthr"), "assessments.garmin.not_measured")},
              {"label": t("assessments.garmin.resting_hr"),
               "value": bpm(g.get("resting_hr"), "assessments.garmin.rest_missing")}]
    bike = sport_of(g.get("sport")).value == "bike"
    if g.get("threshold_pace") and not bike:
        fields.append({"label": t("assessments.garmin.pace"),
                       "value": t("assessments.garmin.pace_value", v=g["threshold_pace"])})
    if bike and g.get("threshold_speed_kmh"):
        fields.append({"label": t("assessments.garmin.power"),
                       "value": t("assessments.garmin.power_value", v=round(g["threshold_speed_kmh"]))})
    pct_key = "assessments.garmin.pct_lthr" if lthr else "assessments.garmin.pct_max"
    zones = [{"label": t("assessments.garmin.zone", n=z["zone"]),
              "bpm": t("assessments.garmin.zone_bpm", lo=z["hr_low"], hi=z["hr_high"]) if z.get("hr_high")
              else t("assessments.garmin.zone_from", lo=z["hr_low"]),
              "pct": t(pct_key, lo=z["pct_low"], hi=z["pct_high"]) if z.get("pct_low") and z.get("pct_high") else ""}
             for z in g["zones"]]
    return {"title": t("assessments.garmin.title"),
            "intro": t("assessments.garmin.intro_lthr" if lthr else "assessments.garmin.intro_max")
            + (" " + t("assessments.garmin.sport_bike") if bike else ""),
            "fields": fields, "zones": zones, "why_title": t("assessments.garmin.why_title"),
            "why": t("assessments.garmin.why"), "estimated": bool(g.get("estimated"))}


# ---------------------------------------------------------------------- the athlete's watch
# The watch brands the result can say what to type into, and the zone methods each one offers:
# "lthr" % of the lactate threshold heart rate, "hrr" % of the heart-rate reserve (max HR and resting
# HR), "max" % of max HR. Only what is certain is written: Huawei Health's names and menu path
# (Maxpulsprocent, HRR-procent and LTHR-procent under the workout heart rate settings), and Garmin
# Connect's zones by % max HR, % HRR or % LTHR. For the other brands the text speaks of "the heart
# rate zones in the watch's app", and of max HR only.
WATCH_BRANDS: dict[str, tuple[str, ...]] = {
    "garmin": ("lthr", "hrr", "max"),
    "huawei": ("lthr", "hrr", "max"),
    "polar": ("max",),
    "coros": ("max",),
    "suunto": ("max",),
    "apple": ("max",),
}
DEFAULT_WATCH = "garmin"


def watch_brand(brand: str | None) -> str:
    """A known watch brand key; the default (Garmin) for an empty or unknown one."""
    return brand if brand in WATCH_BRANDS else DEFAULT_WATCH


def watch_method(setup: GarminSetup | dict[str, Any] | None, brand: str | None) -> str | None:
    """What the athlete chooses on this watch: "lthr" when a threshold heart rate was measured and
    the watch takes one, else "hrr" when the watch has it and the resting HR is known, else "max";
    None when not even a max HR can be handed out."""
    g = setup.to_dict() if isinstance(setup, GarminSetup) else setup
    if not g:
        return None
    methods = WATCH_BRANDS[watch_brand(brand)]
    if g.get("lthr") and "lthr" in methods:
        return "lthr"
    if not g.get("max_hr"):
        return None
    if g.get("resting_hr") and "hrr" in methods:
        return "hrr"
    return "max"


def _method_name(brand: str, method: str) -> str:
    own = f"assessments.watch.method.{brand}.{method}"
    text = t(own)
    return text if text != own else t(f"assessments.watch.method.{method}")


def watch_card(setup: GarminSetup | dict[str, Any] | None, brand: str | None = None) -> dict[str, Any] | None:
    """What the athlete types into the watch of ``brand``, the same for the screen and the report:
    {"brand", "brand_name", "method", "decision", "where", "enter": [{"label", "value"}], "speed_note",
    "estimated", "details": garmin_card(setup)}. The decision is one sentence ("Välj LTHR-procent och
    skriv in 177."); the zone limits and the why are the details under it. None without zones."""
    g = setup.to_dict() if isinstance(setup, GarminSetup) else setup
    details = garmin_card(g)
    if details is None:
        return None
    b = watch_brand(brand)
    method = watch_method(g, b)
    names = {"brand": b, "brand_name": t(f"assessments.watch.brand.{b}")}
    if method is None:          # a submaximal test without thresholds: nothing certain to type in
        return {**names, "method": None, "decision": t("assessments.watch.decision.none"), "where": "",
                "enter": [], "speed_note": "", "estimated": bool(g.get("estimated")), "details": details}
    bpm = {"lthr": g.get("lthr"), "max": g.get("max_hr"), "rest": g.get("resting_hr")}
    decision = t(f"assessments.watch.decision.{method}", method=_method_name(b, method), **bpm)
    enter = [{"label": t("assessments.garmin.lthr"), "value": t("assessments.garmin.bpm", v=bpm["lthr"])}] \
        if method == "lthr" else []
    if bpm["max"]:
        enter.append({"label": t("assessments.garmin.max_hr"), "value": t("assessments.garmin.bpm", v=bpm["max"])})
    if method == "hrr":
        enter.append({"label": t("assessments.garmin.resting_hr"), "value": t("assessments.garmin.bpm", v=bpm["rest"])})
    where_key = f"assessments.watch.where.{b}"
    where = t(where_key)
    run = sport_of(g.get("sport")).value == "run"
    # The speeds of the zones hold on the treadmill; without a measured threshold they come from max HR.
    speed_note = t("assessments.watch.speeds_from_max" if g.get("estimated") else "assessments.watch.speeds") if run else ""
    return {**names, "method": method, "decision": decision,
            "where": where if where != where_key else t("assessments.watch.where.any"),
            "enter": enter, "speed_note": speed_note, "estimated": bool(g.get("estimated")), "details": details}


def watch_brand_choices() -> list[tuple[str, str]]:
    """(key, name) of the watch brands in display order."""
    return [(k, t(f"assessments.watch.brand.{k}")) for k in WATCH_BRANDS]


__all__ = ["Anchors", "COMPARE_SYSTEMS", "DEFAULT_SYSTEM", "DEFAULT_WATCH", "GarminSetup", "SYSTEMS", "WATCH_BRANDS",
           "Zone", "ZoneSystem", "compare", "convert", "curve_zones", "garmin_card", "garmin_setup", "get_system",
           "nominal_zones", "watch_brand", "watch_brand_choices", "watch_card", "watch_method"]
