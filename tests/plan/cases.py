"""Fixed requests for the plan tests: the same input always gives the same plan."""
from __future__ import annotations

from datetime import date, time

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.plan import RUNNING, Capacity, Goal, PlanRequest, Slot

START = date(2026, 11, 16)   # a Monday
TESTED = Capacity(RUNNING, Anchors(192, 46, 165, 174, 13.2, 14.4), date(2026, 11, 13), "Laktattrappa")
FIVE = (Slot(0, 45, start=time(18)), Slot(1, 75, start=time(18)), Slot(3, 75, start=time(18)),
        Slot(5, 60, start=time(9)), Slot(6, 120, start=time(9)))
THREE = (Slot(1, 60), Slot(3, 60), Slot(6, 90))
SEVEN = tuple(Slot(d, 90 if d < 5 else 180) for d in range(7))


def request(slots=FIVE, *, goals=(), weeks=None, level="recreational", recent=200, capacity=(TESTED,),
            start=START) -> PlanRequest:
    return PlanRequest("Exempel Löpare", start, slots, capacity, goals=tuple(goals), duration_weeks=weeks,
                       level=level, recent_weekly_minutes=recent)


HALF = Goal("Halvmaran", date(2027, 2, 6), distance_km=21.1, target="under 1:45")       # Saturday of week 12
SEEDING = Goal("Seedningslopp", date(2027, 1, 2), distance_km=10, priority="B")          # Saturday of week 7
PARKRUN = Goal("Parkrun", date(2026, 12, 12), distance_km=5, priority="C")               # Saturday of week 4
MARATHON = Goal("Maraton", date(2027, 6, 5), distance_km=42.2)                           # week 29

CASES = {
    "half_12": request(goals=(HALF, SEEDING, PARKRUN)),
    "no_goal_12": request(weeks=12),
    "beginner_26": request(weeks=26, level="beginner", recent=None),
    "marathon_trained": request(SEVEN, goals=(MARATHON,), level="trained", recent=320),
    "three_days_52": request(THREE, weeks=52),
    "short_5": request(goals=(Goal("Milen", date(2026, 12, 19), distance_km=10),)),
}
