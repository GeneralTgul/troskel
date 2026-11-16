"""The swimming test (#666, step 3): critical swim speed (CSS) from a 400 m and a 200 m time trial, without equipment.

A manual test: the athlete swims 400 m and then, after a full rest, 200 m, both as fast as an even pace allows, and
the coach writes in the two times. CSS is the speed between them, a measure of the threshold in the water:

    CSS (m/s) = (400 - 200) / (T400 - T200)        pace per 100 m = (T400 - T200) / 2 seconds

The training plan gives swimming its pace per 100 m from it (plan.resolve.swim_targets). Without the test the
plan's swimming is on effort (RPE) only.

The test is stored as a manual test: ``to_result`` is what goes in ``session.result`` of a session without
measurements (its steps are empty), and ``from_result`` reads it back. ``capacity`` is the plan's ``Capacity``.

No Qt. The texts of the errors are i18n keys (assessments.css.error.*).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

KIND = "swim_css"
# A 200 m between 1:40 and 10:00 and a 400 m between 3:20 and 20:00 (seconds): outside it the time is a typing error.
T200_RANGE = (100.0, 600.0)
T400_RANGE = (200.0, 1200.0)


@dataclass(frozen=True)
class CssTest:
    t400_s: float                    # the 400 m time trial, seconds
    t200_s: float                    # the 200 m time trial, seconds
    tested: date | None = None
    pool_m: int = 25                 # the pool's length, for the record (25 or 50 m)

    @property
    def css_s100(self) -> float:
        """The CSS pace in seconds per 100 m, to a tenth of a second."""
        return round((self.t400_s - self.t200_s) / 2, 1)

    @property
    def css_ms(self) -> float:
        """The critical swim speed in metres per second."""
        return 200.0 / (self.t400_s - self.t200_s)


def check(t400_s: float | None, t200_s: float | None) -> str | None:
    """The error key when the two times cannot give a CSS, else None.

    The 400 m is always swum slower per 100 m than the 200 m, so it takes more than twice as long; a 400 m in less
    is a mistake (the times swapped, or a wrong digit)."""
    if t400_s is None or t200_s is None:
        return "assessments.css.error.missing"
    if not T200_RANGE[0] <= t200_s <= T200_RANGE[1] or not T400_RANGE[0] <= t400_s <= T400_RANGE[1]:
        return "assessments.css.error.range"
    if t400_s <= 2 * t200_s:
        return "assessments.css.error.order"
    return None


def make(t400_s: float, t200_s: float, tested: date | None = None, pool_m: int = 25) -> CssTest:
    """The test from the two times; raises ValueError with the error key (``check``)."""
    err = check(t400_s, t200_s)
    if err:
        raise ValueError(err)
    return CssTest(float(t400_s), float(t200_s), tested, pool_m)


def parse_time(text: str) -> float | None:
    """A time as the coach writes it: '6:40', '6:40.5' (or '6:40,5') or seconds ('400'). None when it is no time."""
    s = (text or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        if ":" in s:
            m, rest = s.split(":", 1)
            sec = float(rest)
            if not 0 <= sec < 60:
                return None
            return int(m) * 60 + sec
        return float(s)
    except ValueError:
        return None


def pace_str(seconds: float | None) -> str:
    """Seconds per 100 m as 'm:ss'."""
    if not seconds or seconds <= 0:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def to_result(test: CssTest) -> dict[str, Any]:
    """The test as the ``result`` of a manual session."""
    return {"kind": KIND, "manual": True, "t400_s": test.t400_s, "t200_s": test.t200_s, "pool_m": test.pool_m,
            "css_s100": test.css_s100}


def from_result(result: dict[str, Any] | None, tested: date | None = None) -> CssTest | None:
    """The test of a manual session's ``result``, or None when the result is not a CSS test or not valid."""
    if not result or result.get("kind") != KIND:
        return None
    t400, t200 = result.get("t400_s"), result.get("t200_s")
    if check(t400, t200):
        return None
    return CssTest(float(t400), float(t200), tested, int(result.get("pool_m", 25)))


def capacity(test: CssTest, source: str = "CSS-test") -> Any:
    """The plan's ``Capacity`` for swimming: no heart rates, the CSS pace per 100 m."""
    from lopanalys.plan.models import SWIM, Capacity     # the plan imports assessments: import here, not on top
    return Capacity(SWIM, None, test.tested, source, css_s100=test.css_s100)


__all__ = ["KIND", "CssTest", "capacity", "check", "from_result", "make", "pace_str", "parse_time", "to_result"]
