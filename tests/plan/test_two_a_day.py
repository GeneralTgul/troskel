"""Two workouts a day (#666, docs/TRANINGSPLAN.md, *Två pass samma dag*): a day's second slot at another clock time
gets an easy workout in another sport, only when every day already has one and the week wants more sessions."""
from __future__ import annotations

from dataclasses import replace
from datetime import time, timedelta

from lopanalys.plan import RUNNING, Slot, build_plan
from lopanalys.plan.periodize import second_slots, week_capacity

from . import cases as c


def _doubles(plan):
    days: dict = {}
    for w in plan.workouts:
        if not w.strength and w.goal is None:
            days.setdefault(w.date, []).append(w)
    return {d: ws for d, ws in days.items() if len(ws) == 2}


def test_a_morning_on_the_bike_beside_the_evening_run():
    plan = build_plan(c.CASES["two_a_day"], plan_id="t")
    doubles = _doubles(plan)
    assert len(doubles) >= 10
    for d, ws in doubles.items():
        second = next(w for w in ws if w.key.endswith("-2"))
        first = next(w for w in ws if w is not second)
        assert second.sport == "bike" and first.sport == RUNNING
        assert second.start == time(7) and not second.quality
        assert second.family in ("easy", "recovery")
        if first.quality:          # a hard day keeps its second workout easy: a recovery spin
            assert second.family == "recovery"


def test_never_on_the_long_run_day_nor_around_a_race():
    plan = build_plan(c.CASES["two_a_day"], plan_id="t")
    races = [w.goal for w in plan.workouts if w.goal]
    for d, ws in _doubles(plan).items():
        assert not any(w.family == "long" for w in ws), d
        assert all(abs((d - g.date).days) > (2 if g.priority in ("A", "B") else 1) for g in races), d


def test_only_when_every_day_has_a_workout_and_more_are_wanted():
    # A recreational athlete has at most five sessions: five days are enough, no second workout in a week without a
    # race. A race takes a day, and a fifth session may then be a second workout.
    plan = build_plan(replace(c.CASES["two_a_day"], level="recreational"), plan_id="t")
    for wk in plan.weeks:
        if not any(w.goal for w in wk.workouts):
            assert not any(w.key.endswith("-2") for w in wk.workouts), wk.index
    # Fewer days than sessions in the taper and the race week: never a second workout there.
    trained = build_plan(c.CASES["two_a_day"], plan_id="t")
    for wk in trained.weeks:
        if wk.kind in ("taper", "race"):
            assert not any(w.key.endswith("-2") for w in wk.workouts), wk.index


def test_one_sport_never_gets_two_workouts_a_day():
    req = c.request(c.FIVE + tuple(Slot(d, 45, (RUNNING,), time(7)) for d in (1, 3, 5)), goals=(c.HALF,),
                    level="trained", recent=330)
    assert second_slots(req) == []
    assert _doubles(build_plan(req, plan_id="t")) == {}


def test_the_second_slot_needs_another_clock_time_and_another_sport():
    roles = c.RUN_BIKE
    same_time = c.request(c.FIVE + tuple(Slot(d, 45, ("bike",), time(18)) for d in (1, 3)), roles=roles)
    assert second_slots(same_time) == []
    no_time = c.request(c.FIVE + tuple(Slot(d, 45, ("bike",)) for d in (1, 3)), roles=roles)
    assert second_slots(no_time) == []
    only_run = c.request(c.for_sports(c.FIVE, RUNNING, "bike") + (Slot(1, 45, (RUNNING,), time(7)),), roles=roles)
    assert [s.weekday for s in second_slots(only_run)] == [1]      # the evening allows the bike
    strength = c.request(c.FIVE + c.STRENGTH_SLOTS, roles=roles)
    assert second_slots(strength) == []                             # strength has its own sessions


def test_the_week_holds_the_second_slots():
    req = c.CASES["two_a_day"]
    assert week_capacity(req) > week_capacity(replace(req, availability=c.FIVE))


def test_strength_counts_a_day_with_two_workouts_as_hard():
    """Strength takes a day with one easy workout before a day with two easy ones (engine._Filler._strength)."""
    plan = build_plan(c.CASES["two_a_day"], plan_id="t")
    doubles = _doubles(plan)
    heavy = {w.date for w in plan.workouts if w.quality or w.family == "long" or w.goal}
    for wk in plan.weeks:
        for s in (w for w in wk.workouts if w.strength):
            if s.date in doubles and not any(w.quality for w in doubles[s.date]):
                singles = [w.date for w in wk.endurance if w.date not in doubles and not w.quality and w.family != "long"
                           and w.date + timedelta(days=1) not in heavy and w.goal is None]
                assert singles == [], (wk.index, s.date, singles)
