"""Number and time formatting for the UI (Swedish style: decimal comma, m:ss)."""
from __future__ import annotations

import math
from typing import Any

from lopanalys.core.sport import profile


def num(v: float | None, decimals: int = 1) -> str:
    """Decimal comma, '–' for missing values."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return f"{v:.{decimals}f}".replace(".", ",") if decimals else f"{v:.0f}"


def pace(speed_kmh: float | None, sport: Any = None) -> str:
    """Pace for a load in the sport's own pace: min/km for running (12.0 -> '5:00'), /500 m for
    SkiErg. '–:–' without a load or for a sport without a pace."""
    if not speed_kmh or speed_kmh <= 0.5:
        return "–:–"
    return profile(sport).pace_str(speed_kmh) or "–:–"


def load(v: float | None, sport: Any = None, unit: bool = True) -> str:
    """A load with the sport's decimals and unit: '12,0 km/h' for running, '200 W' for cycling."""
    p = profile(sport)
    text = num(v, p.load_decimals)
    return f"{text} {p.load_unit}" if unit and text != "–" else text


def load_unit(sport: Any = None) -> str:
    """The unit of the load in a sport ('km/h', 'W')."""
    return profile(sport).load_unit


def clock(seconds: float | None) -> str:
    """m:ss (or h:mm:ss) for durations."""
    if seconds is None:
        return "–:–"
    s = max(0, int(math.ceil(seconds - 1e-6)))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
