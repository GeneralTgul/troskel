"""Sport as a concept: running, cycling and SkiErg, and what each one means for a test.

A test (Session), a protocol, the thresholds and the zones carry a ``Sport`` value. The
``SportProfile`` says what the sport's *load* is (the quantity the protocol steps up and the lactate
curve is drawn against), its unit, how a pace is derived from it, and which device modes set it.

All three sports can be chosen (``selectable``). Cycling runs on a trainer in ERG mode (the
program sets the watt), SkiErg on a Concept2 PM5 that is read and never set (the screen shows the
target). No Qt imports, no i18n: the texts live in the UI and the report.

Naming: fields that hold a load are still called ``speed_kmh`` in most of the code (running was
first). New code reads ``.load`` (StepRecord, TimeSample, Stage, Threshold, Zone) and takes the unit
from the profile of the test's sport.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class Sport(str, Enum):
    RUN = "run"
    BIKE = "bike"
    SKIERG = "skierg"


DEFAULT_SPORT = Sport.RUN   # every test stored before sport existed is a running test


def _run_pace_s(speed_kmh: float) -> float:
    """Seconds per kilometre at a speed in km/h."""
    return 3600.0 / speed_kmh


def _skierg_pace_s(power_w: float) -> float:
    """Seconds per 500 m at a power in watt (Concept2: W = 2.80 / (s/m)^3)."""
    return 500.0 * (2.80 / power_w) ** (1.0 / 3.0)


@dataclass(frozen=True)
class SportProfile:
    sport: Sport
    load_quantity: str                # "speed" | "power"
    load_unit: str                    # "km/h" | "W"
    load_decimals: int                # decimals when the load is shown
    has_incline: bool                 # the ergometer has an incline that belongs to the load
    pace_unit: str | None             # "min/km" | "/500 m"; None: the sport has no pace
    pace_seconds: Callable[[float], float] | None   # load -> seconds per pace distance
    ergometer: str                    # "treadmill" | "bike" | "skierg"
    load_sources: tuple[str, ...]     # device modes that set or report the load, the default first
    controllable: bool                # the program can set the load on the ergometer
    cycle: str                        # the movement cycle: "step" | "pedal_rev" | "stroke"
    cadence_unit: str                 # "spm" (steps/min) | "rpm" | "strokes/min"
    selectable: bool                  # can be chosen in the UI
    # What the test runner and the analysis need to know about the load's scale:
    cooldown_load: float = 0.0        # the easy load after exhaustion (walking speed, easy spinning; 0: none set)
    moving_load: float = 0.0          # at or above this the athlete is working ...
    still_load: float = 0.0           # ... and at or below this the athlete has stopped
    stop_s: float = 2.0               # stopped for this long in a stage = the athlete ended the test
    same_load: float = 0.0            # two loads closer than this share of the load are one stage (0: exact)
    lt2_overshoot: float = 1.05       # the border of the top zone when the test ended below it: LT2 times this
    curve_shift: float = 0.2          # a mean shift of the lactate curve smaller than this is noise (load units)

    def pace_s(self, load: float | None) -> float | None:
        """Seconds per pace distance at a load; None without a pace or a positive load."""
        if self.pace_seconds is None or not load or load <= 0:
            return None
        return self.pace_seconds(float(load))

    def pace_str(self, load: float | None) -> str:
        """Pace as 'm:ss' ('' when the sport has no pace or the load is missing)."""
        sec = self.pace_s(load)
        if sec is None:
            return ""
        sec = round(sec)
        return f"{sec // 60}:{sec % 60:02d}"

    def load_str(self, load: float | None) -> str:
        """The load with the sport's decimals and a decimal point, without unit ('' if missing)."""
        return "" if load is None else f"{load:.{self.load_decimals}f}"


PROFILES: dict[Sport, SportProfile] = {p.sport: p for p in (
    SportProfile(Sport.RUN, "speed", "km/h", 1, True, "min/km", _run_pace_s, "treadmill",
                 ("ftms", "qz", "manual", "sim"), True, "step", "spm", True,
                 cooldown_load=5.0, moving_load=2.0, still_load=0.5, stop_s=2.0),
    # Trainer in ERG mode (FTMS Indoor Bike); no pace, the load is the power itself. The top zone
    # starts at 106 % of the threshold power in Coggan's zones (TrainerRoad), used when the test
    # ended below it.
    SportProfile(Sport.BIKE, "power", "W", 0, False, None, None, "bike",
                 ("ftms", "manual", "sim"), True, "pedal_rev", "rpm", True,
                 cooldown_load=80.0, moving_load=30.0, still_load=5.0, stop_s=5.0, same_load=0.02,
                 lt2_overshoot=1.06, curve_shift=4.0),
    # Concept2 PM5 is read, never set: the screen shows the target and the athlete holds it. Watt
    # goes as the speed cubed on a SkiErg, so 10 % more watt is about 3 % more speed: the overshoot
    # is the program's own choice, not from a source.
    SportProfile(Sport.SKIERG, "power", "W", 0, False, "/500 m", _skierg_pace_s, "skierg",
                 ("pm5", "manual", "sim"), False, "stroke", "strokes/min", True,
                 cooldown_load=0.0, moving_load=20.0, still_load=5.0, stop_s=8.0, same_load=0.02,
                 lt2_overshoot=1.10, curve_shift=3.0),
)}


def sport_of(value: Any) -> Sport:
    """A Sport from a stored value. Missing (None, '') means running: tests from before sport existed."""
    if isinstance(value, Sport):
        return value
    if value is None or value == "":
        return DEFAULT_SPORT
    return Sport(getattr(value, "value", value))


def profile(sport: Any = None) -> SportProfile:
    """The profile of a sport (or of a stored sport value; missing means running)."""
    return PROFILES[sport_of(sport)]


def selectable_sports() -> list[Sport]:
    """The sports the UI offers, in menu order."""
    return [s for s in Sport if PROFILES[s].selectable]


__all__ = ["DEFAULT_SPORT", "PROFILES", "Sport", "SportProfile", "profile", "selectable_sports", "sport_of"]
