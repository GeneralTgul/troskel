"""The plan grows from what the athlete trains now towards the time the days hold, and the overview
shows the progression: the longest run and the hard minutes per week (docs/TRANINGSPLAN.md)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.plan import Goal, Slot, build_plan
from lopanalys.plan.periodize import LEVEL, MAX_WEEKLY_INCREASE, first_week_minutes, week_capacity
from lopanalys.plan.text import start_line
from lopanalys.report.plan_builder import overview_svg

from . import cases as c

SIX_HOURS = tuple(Slot(d, 60) for d in range(6))                        # six days of an hour
ROOMY = tuple(Slot(d, 75) for d in range(5)) + (Slot(5, 60), Slot(6, 150))
MARATHON_33 = Goal("Maraton", c.START + timedelta(weeks=32, days=5), distance_km=42.2)


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _loads(plan):
    """The load weeks before the peak without a race, where the volume grows."""
    return [wk for wk in plan.weeks if wk.kind == "load" and wk.block in ("base", "build")
            and not any(w.goal for w in wk.workouts)]


def _plan(slots, level, recent):
    return build_plan(c.request(slots, goals=(MARATHON_33,), level=level, recent=recent))


def test_the_plan_does_not_start_at_the_full_week():
    """The case that started it: 33 weeks, six days of an hour, Vältränad and Tränar nu empty. The plan
    used to open at the level's 5 h and stand at about 5.7 h from week 3 to the end of the base."""
    plan = _plan(SIX_HOURS, "trained", None)
    cap = week_capacity(plan.request)
    loads = _loads(plan)
    assert len(plan.weeks) == 33 and cap == 360
    assert first_week_minutes(plan.request) == (250, "default")
    assert loads[0].target_minutes <= 0.7 * cap
    # it grows all through base and build and reaches the full week only at the end of build
    targets = [wk.target_minutes for wk in loads]
    assert targets == sorted(targets) and targets[-1] == cap
    assert targets.index(cap) >= len(targets) - 2
    base = [wk.minutes for wk in loads if wk.block == "base"]
    assert base[-1] - base[0] >= 60, base


@pytest.mark.parametrize("level,recent", [("recreational", 120), ("trained", None), ("trained", 330),
                                          ("beginner", None), ("recreational", 200)])
@pytest.mark.parametrize("slots", [SIX_HOURS, ROOMY, c.SEVEN], ids=["six_hours", "roomy", "seven"])
def test_the_volume_grows_at_most_the_level_rate_from_what_the_athlete_trains(slots, level, recent):
    plan = _plan(slots, level, recent)
    cap = week_capacity(plan.request)
    start, source = first_week_minutes(plan.request)
    loads = _loads(plan)
    assert loads[0].index == 1 and loads[0].target_minutes == start <= cap
    if recent:
        assert source == "recent" and start == min(recent, cap)
    else:
        assert source == "default" and start <= 0.7 * cap
    rate = min(LEVEL[level].growth, MAX_WEEKLY_INCREASE)
    for a, b in zip(loads, loads[1:]):          # +5: the targets are rounded to five minutes
        assert a.target_minutes <= b.target_minutes <= a.target_minutes * (1 + rate) + 5, (a.index, b.index)
    assert all(wk.target_minutes <= cap for wk in plan.weeks)


def test_far_below_the_full_week_the_plan_grows_at_the_level_rate_for_as_long_as_it_needs():
    plan = _plan(c.SEVEN, "recreational", 60)
    loads = _loads(plan)
    targets = [wk.target_minutes for wk in loads]
    assert targets[:3] == [60, 65, 70]                           # 7 % a load week, rounded to five minutes
    assert targets[-1] < week_capacity(plan.request)             # 33 weeks are not enough to fill seven days
    assert all(b >= a * 1.07 - 5 for a, b in zip(targets, targets[1:]))   # so it grows at the full rate all through


def test_the_longest_run_and_the_hard_minutes_grow_over_the_load_weeks():
    for slots, level, recent in ((ROOMY, "recreational", 180), (c.SEVEN, "trained", 320), (SIX_HOURS, "trained", None)):
        loads = _loads(_plan(slots, level, recent))
        first, last = loads[:3], loads[-3:]
        hard = [sum(wk.hard_minutes for wk in part) for part in (first, last)]
        assert hard[1] > 1.5 * hard[0], (slots, hard)
        longest = [max(wk.longest_minutes for wk in part) for part in (first, last)]
        if max(s.max_minutes for s in slots) > 60:              # an hour a day holds no longer run
            assert longest[1] > longest[0], (slots, longest)


def test_week_numbers_for_the_overview_lines():
    plan = _plan(ROOMY, "recreational", 180)
    race = plan.weeks[-1]
    assert race.longest_minutes == max(w.minutes for w in race.workouts if w.goal is None)   # the race is left out
    wk = plan.weeks[0]
    zm = wk.zone_minutes()
    assert wk.hard_minutes == pytest.approx(zm.get("Z3", 0) + zm.get("Z4", 0) + zm.get("Z5", 0)) and wk.hard_minutes > 0


def test_the_overview_says_where_the_plan_starts():
    assert start_line(_plan(SIX_HOURS, "trained", None)) == \
        "Börjar på 4 h 10 min i veckan, nivåns förval eftersom Tränar nu är tomt. Tiden växer till 6 h i vecka 29."
    assert start_line(_plan(ROOMY, "recreational", 200)).startswith("Börjar på 3 h 20 min i veckan, det du tränar nu.")
    capped = start_line(_plan(SIX_HOURS, "recreational", 600))
    assert capped == ("Börjar på 5 h i veckan, all tid som dagarna rymmer. Tiden står still, så ökningen ligger i passen: "
                      "det längsta passet och de hårda minuterna.")


def test_the_printed_overview_draws_both_lines():
    plan = _plan(SIX_HOURS, "trained", None)
    svg = overview_svg(plan)
    assert svg.count("<polyline") == 2
    assert "Längsta passet (min)" in svg and "Hårda minuter (zon 3–5)" in svg
    assert svg.count('stroke-width="2"/>') >= 2 * len(plan.weeks)      # one marker per week and line
