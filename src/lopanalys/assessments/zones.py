"""Training zones from a lactate test, or from heart rate as a fallback.

The zone systems themselves (their borders as data, and the conversion between them) live in
``zone_systems``; this module is the entry point for the default use: the Norwegian I-scale
(``olympiatoppen``) or Seiler's three zones (``seiler``) for one test.

From a lactate test the zones are individual (``zone_systems.curve_zones``). From heart rate only:
% of max HR, or Karvonen (% of heart-rate reserve) when resting HR is known (Karvonen et al. 1957).
"""
from __future__ import annotations

from lopanalys.assessments.lactate import LactateResult
from lopanalys.assessments.zone_systems import (OLT_HRR, OLT_TABLE, SEILER_PCT, Zone, curve_zones,
                                                nominal_zones)
from lopanalys.core.i18n import t

__all__ = ["OLT_HRR", "OLT_TABLE", "SEILER_PCT", "Zone", "from_heart_rate", "from_lactate"]


def from_lactate(result: LactateResult, max_hr: int | None = None, resting_hr: int | None = None,
                 system: str = "olympiatoppen") -> list[Zone]:
    """Individual zones from LT1 and LT2. Falls back to from_heart_rate() when a threshold is missing
    or the test could not separate the two (and returns [] if neither thresholds nor max HR are known)."""
    max_hr = max_hr or result.max_hr
    zones = curve_zones(result, max_hr, system)
    if zones:
        return zones
    return from_heart_rate(max_hr, resting_hr, system, result.sport) if max_hr else []


def from_heart_rate(max_hr: int | None, resting_hr: int | None = None, system: str = "olympiatoppen",
                    sport: str | None = None) -> list[Zone]:
    """Zones from max HR (% of HRmax), or Karvonen (% of heart-rate reserve) when resting HR is given.

    ``sport``: the sport the max HR was measured in (default running); the zones are that sport's."""
    if not max_hr or max_hr < 100 or max_hr > 240:
        raise ValueError(t("assessments.zones.error.max_hr"))
    return nominal_zones(max_hr, resting_hr, system, sport)
