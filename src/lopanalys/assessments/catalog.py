"""The tests the program offers, described for the athlete: what each one measures, how it is done,
by which standard, what you get and where its limits are.

The texts are data (i18n keys ``assessments.info.<test>.<field>``), so the page "Testerna" in the
app and the printed sheet "Om testerna" show exactly the same words. The methods named in the texts
are the ones the code uses: lactate.py (LT1 = baseline + 1.0 mmol/L, LT2 = modified Dmax),
max_hr.py (5-s peak and the true-max criteria), hr_zones.py (the stable heart rate of a step, the
line and how far it is extended), zone_systems.py and vo2max.py; for cycling and SkiErg also
power.py (FTP 95 % of 20 min or 75 % of the best minute, Coggan's zones), library_power.py (the
stages) and pose.bike / pose.ski (bike fit and double poling). Change the texts when those
defaults change.

Each sport has its own list, shown one sport at a time on the page and on its own printed sheet.
"""
from __future__ import annotations

from typing import Any

from lopanalys.core.i18n import t
from lopanalys.core.sport import Sport, sport_of

SPORT_TESTS: dict[Sport, tuple[str, ...]] = {
    Sport.RUN: ("lactate", "max_hr", "hr_zones", "gait", "zones", "vo2max"),
    Sport.BIKE: ("bike_lactate", "bike_ramp", "bike_ftp", "bikefit", "bike_zones"),
    Sport.SKIERG: ("skierg_lactate", "skierg_ramp", "ski_technique", "skierg_zones"),
    Sport.SWIM: ("swim_css",),
}
TESTS = SPORT_TESTS[Sport.RUN]
FIELDS = ("measures", "how", "time", "equipment", "standard", "get", "limits")


def tests_info(sport: Any = None) -> list[dict[str, Any]]:
    """One dict per test of a sport (running when none is given): key, title, and the fields as
    {"key", "label", "text"} in reading order."""
    return [{"key": k, "title": t(f"assessments.info.{k}.title"),
             "fields": [{"key": f, "label": t(f"assessments.info.label.{f}"), "text": t(f"assessments.info.{k}.{f}")}
                        for f in FIELDS]} for k in SPORT_TESTS[sport_of(sport)]]
