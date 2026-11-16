"""Fixed requests for the plan tests: the same input always gives the same plan."""
from __future__ import annotations

from datetime import date, time

from lopanalys.assessments.zone_systems import Anchors
from dataclasses import replace

from lopanalys.plan import RUNNING, Capacity, Goal, PlanRequest, Slot, SportRole

START = date(2026, 11, 16)   # a Monday
TESTED = Capacity(RUNNING, Anchors(192, 46, 165, 174, 13.2, 14.4), date(2026, 11, 13), "Laktattrappa")
FIVE = (Slot(0, 45, start=time(18)), Slot(1, 75, start=time(18)), Slot(3, 75, start=time(18)),
        Slot(5, 60, start=time(9)), Slot(6, 120, start=time(9)))
THREE = (Slot(1, 60), Slot(3, 60), Slot(6, 90))
# The bike's and the SkiErg's tests (#666, step 2): LT1 and LT2 in watt.
BIKE_TEST = Capacity("bike", Anchors(185, 48, 140, 160, 180.0, 240.0, sport="bike"), date(2026, 11, 1), "Laktattrappa")
SKIERG_TEST = Capacity("skierg", Anchors(188, 48, 150, 168, 160.0, 210.0, sport="skierg"), date(2026, 11, 1), "Laktattrappa")
SEVEN = tuple(Slot(d, 90 if d < 5 else 180) for d in range(7))


def request(slots=FIVE, *, goals=(), weeks=None, level="recreational", recent=200, capacity=(TESTED,),
            start=START, roles=()) -> PlanRequest:
    return PlanRequest("Exempel Löpare", start, slots, capacity, goals=tuple(goals), duration_weeks=weeks,
                       level=level, recent_weekly_minutes=recent, roles=tuple(roles))


def for_sports(slots, *sports: str) -> tuple:
    """The slots with every one of the sports allowed."""
    return tuple(replace(s, sports=sports) for s in slots)


RUN_BIKE = (SportRole(RUNNING, "main"), SportRole("bike", "support"))
FOUR = (SportRole(RUNNING, "main"), SportRole("bike", "support"), SportRole("ski", "support", 20),
        SportRole("swim", "maintenance"))
RUN_BIKE_ERG = (SportRole(RUNNING, "main"), SportRole("bike", "support"), SportRole("skierg", "support", 20))


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
    # Several sports (#666): running and cycling in every slot, and four sports with a maintenance sport.
    "run_bike_12": request(for_sports(FIVE, RUNNING, "bike"), goals=(HALF, SEEDING, PARKRUN), roles=RUN_BIKE),
    "four_sports_marathon": request(for_sports(SEVEN, RUNNING, "bike", "ski", "swim"), goals=(MARATHON,),
                                    level="trained", recent=320, roles=FOUR),
    # The bike's and the SkiErg's own workouts with their tests, the bike alone, and skis as the main sport.
    "bike_skierg_tested": request(for_sports(FIVE, RUNNING, "bike", "skierg"), goals=(HALF, SEEDING, PARKRUN),
                                  capacity=(TESTED, BIKE_TEST, SKIERG_TEST), roles=RUN_BIKE_ERG),
    "bike_alone_26": request(for_sports(FIVE, "bike"), weeks=26, level="beginner", recent=None, capacity=(BIKE_TEST,),
                             roles=(SportRole("bike"),)),
    "ski_main": request(for_sports(SEVEN, "ski", "skierg", RUNNING), level="trained", recent=None,
                        goals=(Goal("Skidlopp", date(2027, 3, 6), sport="ski", distance_km=45),),
                        capacity=(TESTED, SKIERG_TEST),
                        roles=(SportRole("ski", "main"), SportRole("skierg", "support"), SportRole(RUNNING, "maintenance"))),
}
