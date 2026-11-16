"""The rules of the plan engine, checked on fixed requests (docs/TRANINGSPLAN.md)."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.plan import Goal, Slot, build_plan, library, resolve
from lopanalys.plan.periodize import LEVEL, core_slots, first_week_minutes, periodize, second_slots

from . import cases as c


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.fixture(scope="module", params=sorted(c.CASES))
def plan(request):
    return build_plan(c.CASES[request.param], plan_id=request.param)


def kinds(plan):
    return [w.kind for w in plan.weeks]


# ---------------------------------------------------------------- every plan


def test_same_request_gives_the_same_plan(plan):
    assert build_plan(plan.request, plan_id=plan.id) == plan


def test_weeks_are_whole_and_in_order(plan):
    assert plan.start.weekday() == 0
    for a, b in zip(plan.weeks, plan.weeks[1:]):
        assert b.monday - a.monday == timedelta(days=7) and b.index == a.index + 1
    assert sum(b.weeks for b in plan.blocks) == len(plan.weeks)
    for wk in plan.weeks:
        assert all(wk.monday <= w.date <= wk.sunday for w in wk.workouts)


def test_availability_is_respected(plan):
    slots = {s.weekday: s for s in core_slots(plan.request)}
    seconds = {s.weekday: s for s in second_slots(plan.request)}
    for w in plan.workouts:
        if w.goal or w.strength:
            continue
        slot = (seconds if w.key.endswith("-2") else slots)[w.date.weekday()]   # KeyError: a day without a slot
        assert w.minutes <= slot.max_minutes, w
        assert w.start == slot.start
        assert w.sport in slot.sports, w
    days: dict = {}
    for w in plan.workouts:
        if not w.strength:
            days.setdefault(w.date, []).append(w)
    for d, ws in days.items():
        assert len(ws) <= 2, d
        if len(ws) == 2:          # two workouts a day: two sports, one of them on the day's second slot, never two hard
            assert ws[0].sport != ws[1].sport and sum(w.key.endswith("-2") for w in ws) == 1, d
            assert sum(w.quality for w in ws) <= 1 and not any(w.key.endswith("-2") and w.quality for w in ws), d
    assert len({w.key for w in plan.workouts}) == len(plan.workouts)


def _day_kind(ws):
    """A day with two workouts is hard when one of them is."""
    return SimpleNamespace(quality=any(w.quality for w in ws), family="long" if any(w.family == "long" for w in ws)
                           else ws[0].family, goal=next((w.goal for w in ws if w.goal), None), key=ws[0].key)


def test_never_two_hard_days_in_a_row_and_none_before_the_long_run(plan):
    days: dict = {}
    for w in plan.workouts:
        if not w.strength:
            days.setdefault(w.date, []).append(w)
    by_day = {d: _day_kind(ws) for d, ws in days.items()}
    for d, w in by_day.items():
        nxt = by_day.get(d + timedelta(days=1))
        if w.quality and nxt is not None:
            assert not nxt.quality, (w.key, nxt.key)
            assert nxt.family != "long", (w.key, nxt.key)
        if w.goal and w.goal.priority in ("A", "B"):
            near = [by_day.get(d + timedelta(days=k)) for k in (-2, -1, 1, 2)]
            assert not any(x.quality for x in near if x), w.key


def test_progression_is_careful(plan):
    level = LEVEL[plan.request.level]
    start, _ = first_week_minutes(plan.request)
    assert plan.weeks[0].minutes <= start
    biggest = plan.weeks[0].minutes
    for wk in plan.weeks[1:]:
        assert wk.minutes <= 1.10 * biggest, f"week {wk.index}: {wk.minutes} after {biggest}"
        assert wk.minutes <= wk.target_minutes
        biggest = max(biggest, wk.minutes)
    main = plan.request.main_sport           # a supporting sport's smaller long workout is not the long run
    longs = [next((w.minutes for w in wk.workouts if w.family == "long" and w.sport == main), None)
             for wk in plan.weeks if wk.kind == "load"]
    longs = [m for m in longs if m]
    assert all(b - a <= 15 for a, b in zip(longs, longs[1:]))
    assert all(m <= level.long_cap for m in longs)


def test_easy_weeks_come_regularly_and_are_lighter(plan):
    level = LEVEL[plan.request.level]
    run, opened = 0, True
    for wk in plan.weeks:
        run = run + 1 if wk.kind == "load" else 0
        opened = opened and wk.kind == "load"
        # The plan never opens with an easy week, so the first run may be one week longer.
        assert run <= level.load_weeks + (1 if opened else 0), f"week {wk.index}: {run} load weeks in a row"
    for i, wk in enumerate(plan.weeks):
        if wk.kind in ("easy", "test") and i and plan.weeks[i - 1].kind == "load":
            load = max(w.minutes for w in plan.weeks[max(0, i - level.load_weeks):i])
            assert wk.minutes <= 0.75 * load, (wk.index, wk.minutes, load)


def test_mostly_easy(plan):
    assert plan.easy_share >= 0.78
    for wk in plan.weeks:
        assert wk.easy_share >= 0.75, (wk.index, wk.easy_share)
        n = len([w for w in wk.endurance if not w.goal])
        hard = [w for w in wk.workouts if w.quality]
        assert len(hard) <= 2 and (n >= 4 or len(hard) <= 1 or any(w.goal for w in hard))


def test_retests(plan):
    tests = [wk.index for wk in plan.weeks if wk.kind == "test"]
    for wk in plan.weeks:
        has_test = any(w.family == "test" for w in wk.workouts)        # running's test or the sport's own
        assert has_test == (wk.kind == "test"), wk.index
    gaps = [b - a for a, b in zip([0] + tests, tests)]
    assert all(g >= 8 for g in gaps), gaps
    if plan.goal is None:
        assert tests and tests[-1] == len(plan.weeks), "a plan without a goal ends with a test"
        assert all(g <= 13 for g in gaps), gaps
    else:
        assert all(len(plan.weeks) - t >= 4 for t in tests)
        if len(plan.weeks) >= 16:
            assert tests
    if len(plan.weeks) >= 26:
        assert len(tests) >= 2


def test_the_plan_stores_zones_not_numbers(plan):
    for w in plan.workouts:
        assert w.parts
        for p in w.parts:
            assert set(vars(p)) == {"kind", "duration_s", "zone", "rpe", "repeat", "rest_s", "rest_zone", "band"}
            assert p.band in ("", *resolve.BANDS)                    # a band is a name too: its watt is looked up
            assert p.zone in (None, "Z1", "Z2", "Z3", "Z4", "Z5")
            assert 1 <= p.rpe <= 10


def test_every_workout_comes_from_the_library(plan):
    for w in plan.workouts:
        tpl = library.get(w.template_id)
        assert (w.family, w.quality) == (tpl.family, tpl.quality)
        if tpl.steps:
            assert tuple(w.parts) in tpl.steps
        else:
            assert tpl.min_minutes <= w.minutes


# ---------------------------------------------------------------- single cases


def test_blocks_are_counted_backwards_from_the_goal():
    plan = build_plan(c.CASES["half_12"])
    assert [(b.kind, b.weeks) for b in plan.blocks] == [("base", 5), ("build", 4), ("peak", 2), ("taper", 1)]
    assert plan.end >= c.HALF.date >= plan.weeks[-1].monday
    assert kinds(plan) == ["load", "load", "load", "easy", "load", "load", "load", "test", "load", "load", "load", "race"]
    assert all(b.reason.text and "plan." not in b.reason.text for b in plan.blocks)


def test_taper_before_the_goal():
    plan = build_plan(c.CASES["half_12"])
    race_week, peak = plan.weeks[-1], max(w.minutes for w in plan.weeks)
    assert race_week.kind == "race" and race_week.minutes <= 0.6 * peak
    last = plan.workouts[-1]
    assert last.goal == c.HALF and last.date == c.HALF.date and last.template_id == "race"
    before = [w for w in race_week.workouts if not w.goal]
    assert all((c.HALF.date - w.date).days >= 3 for w in before if w.quality)
    assert [w.template_id for w in before if w.quality] == ["opener"]
    assert not any(w.family == "long" for w in before)
    assert all(w.minutes <= 45 for w in before)

    marathon = build_plan(c.CASES["marathon_trained"])
    assert marathon.blocks[-1].kind == "taper" and marathon.blocks[-1].weeks == 2
    assert kinds(marathon)[-2:] == ["taper", "race"]
    assert marathon.weeks[-1].minutes < marathon.weeks[-2].minutes < max(w.minutes for w in marathon.weeks)
    assert any(w.template_id == "long_progressive" for w in marathon.workouts)


def test_three_plus_one_and_test_at_the_end_without_a_goal():
    plan = build_plan(c.CASES["no_goal_12"])
    assert kinds(plan) == ["load"] * 3 + ["easy"] + ["load"] * 3 + ["easy"] + ["load"] * 3 + ["test"]
    assert [(b.kind, b.weeks) for b in plan.blocks] == [("base", 6), ("build", 6)]
    year = build_plan(c.CASES["three_days_52"])
    tests = [w.index for w in year.weeks if w.kind == "test"]
    assert len(tests) == 5 and tests[-1] == 52
    assert all(8 <= b - a <= 12 for a, b in zip([0] + tests, tests))


def test_beginner_two_plus_one_and_nothing_above_lt2_at_first():
    plan = build_plan(c.CASES["beginner_26"])
    assert kinds(plan)[:6] == ["load", "load", "easy", "load", "load", "easy"]
    for wk in plan.weeks[:4]:
        assert not any(p.zone == "Z5" for w in wk.workouts for p in w.parts)
    assert any(p.zone == "Z5" for w in plan.workouts for p in w.parts)
    assert max(len(wk.workouts) for wk in plan.weeks) <= 3


def test_milestone_and_training_race():
    plan = build_plan(c.CASES["half_12"])
    by_day = {w.date: w for w in plan.workouts}
    assert by_day[c.SEEDING.date].goal == c.SEEDING and by_day[c.PARKRUN.date].goal == c.PARKRUN
    for d in (1, 2):   # two easy days on each side of a B race
        for day in (c.SEEDING.date - timedelta(days=d), c.SEEDING.date + timedelta(days=d)):
            assert day not in by_day or not by_day[day].quality
    week7 = plan.weeks[6]
    assert not any(w.family == "long" for w in week7.workouts)
    assert len([w for w in week7.workouts if w.quality and not w.goal]) == 1
    # The C race replaces the week's quality session and nothing else moves.
    without = build_plan(c.request(goals=(c.HALF, c.SEEDING)))
    week4, week4_without = plan.weeks[3], without.weeks[3]
    assert len([w for w in week4.workouts if w.quality and not w.goal]) == \
        len([w for w in week4_without.workouts if w.quality]) - 1
    assert [b.kind for b in plan.blocks] == [b.kind for b in without.blocks]


def test_short_time_to_the_goal_says_so_and_builds_no_peak():
    plan = build_plan(c.CASES["short_5"])
    assert len(plan.weeks) == 5 and "peak" not in [b.kind for b in plan.blocks]
    assert any(n.key == "plan.note.short" for n in plan.notes)
    assert plan.weeks[-1].kind == "race"


def test_volume_never_passes_what_the_slots_hold():
    req = c.request(c.THREE, weeks=12, recent=600)
    plan = build_plan(req)
    assert max(w.minutes for w in plan.weeks) <= 210
    assert any(n.key == "plan.note.capped" for n in plan.notes)


def test_start_moves_to_the_first_monday():
    plan = build_plan(c.request(weeks=12, start=date(2026, 11, 18)))
    assert plan.start == date(2026, 11, 23)


def test_extra_slots_and_other_sports_are_not_filled():
    slots = c.THREE + (Slot(2, 60, role="extra"), Slot(4, 60, sports=("cycling",)))
    plan = build_plan(c.request(slots, weeks=12))
    assert {w.date.weekday() for w in plan.workouts} <= {1, 3, 6}


def test_errors_are_user_facing():
    with pytest.raises(ValueError, match="minst en dag"):
        build_plan(c.request((), weeks=12))
    with pytest.raises(ValueError, match="för nära"):
        build_plan(c.request(goals=(Goal("Lopp", c.START + timedelta(days=3)),)))
    with pytest.raises(ValueError, match="minst 4"):
        build_plan(c.request(weeks=2))
    with pytest.raises(ValueError, match="nivå"):
        build_plan(c.request(weeks=12, level="elite"))


def test_library_has_every_family_and_progressions_grow():
    assert {t.family for t in library.TEMPLATES.values()} >= {
        "easy", "long", "recovery", "threshold", "vo2max", "fartlek", "hills", "test", "race"}
    for tid in ("threshold", "vo2max", "fartlek", "hills"):
        work = [sum(p.work_s for p in step if p.kind == "work") for step in library.get(tid).steps]
        assert work == sorted(work) and len(set(work)) > 1, tid


def test_skeleton_volume_holds_in_the_peak():
    sk = periodize(c.CASES["half_12"])
    peak = [w.target_minutes for w in sk.weeks if w.block == "peak"]
    assert len(set(peak)) == 1
