"""What the plan counts on, and how much of it was done (#778): plan.expected."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from lopanalys.plan import RUNNING, build_plan
from lopanalys.plan import expected as ex

from . import cases as c

BEFORE = date(2026, 11, 13)          # the test the plan is built on (cases.TESTED), the Friday before the start


@pytest.fixture(scope="module")
def plan():
    return build_plan(c.CASES["half_12"], plan_id="p7", today=c.START)


def test_the_gain_follows_the_weeks_their_kind_and_the_level(plan):
    # Four loaded weeks of the plan, then an easy week: the plan's own weeks between the tests.
    kinds = [wk.kind for wk in plan.weeks[:4]]
    assert kinds == ["load", "load", "load", "easy"]
    e = ex.expected(plan, RUNNING, BEFORE, date(2026, 12, 14))        # the Monday of week 5
    assert e.role == "main" and e.level == "recreational"
    assert e.weeks == pytest.approx(3.5)                                # 3 loaded weeks and half of the easy one
    assert e.gain_pct == pytest.approx(3.5 * ex.RATE_PCT["recreational"])
    # Days before the plan count for nothing: a test long before it gives the same expectation.
    assert ex.expected(plan, RUNNING, date(2026, 6, 1), date(2026, 12, 14)) == replace(e, start=date(2026, 6, 1))
    # A beginner gains more on the same weeks, a high-volume athlete less.
    gains = [ex.expected(replace(plan, request=replace(plan.request, level=lv)), RUNNING, BEFORE,
                         date(2026, 12, 14)).gain_pct for lv in ("beginner", "recreational", "trained", "high")]
    assert gains == sorted(gains, reverse=True)


def test_the_gain_flattens_after_twelve_weeks(plan):
    long = replace(plan, weeks=[replace(wk, kind="load") for wk in plan.weeks])
    assert len(long.weeks) == 12
    twelve = ex.expected(long, RUNNING, BEFORE, long.end + timedelta(days=1))
    assert twelve.weeks == pytest.approx(12)
    assert twelve.gain_pct == pytest.approx(12 * ex.RATE_PCT["recreational"])
    rate = ex.RATE_PCT["recreational"]
    many = replace(long, weeks=long.weeks + [replace(wk, monday=wk.monday + timedelta(weeks=12))
                                             for wk in long.weeks])
    e = ex.expected(many, RUNNING, BEFORE, many.end + timedelta(days=1))
    assert e.gain_pct == pytest.approx(12 * rate + 12 * rate / 2)


def test_the_role_weighs_the_gain_and_a_sport_outside_the_plan_has_none():
    plan = build_plan(c.request(c.for_sports(c.FIVE, RUNNING, "bike"), weeks=8, roles=c.RUN_BIKE,
                                capacity=(c.TESTED, c.BIKE_TEST)), plan_id="p8", today=c.START)
    run = ex.expected(plan, RUNNING, BEFORE, date(2026, 12, 7))
    bike = ex.expected(plan, "bike", BEFORE, date(2026, 12, 7))
    assert bike.role == "support" and bike.gain_pct == pytest.approx(run.gain_pct / 2)
    assert ex.expected(plan, "skierg", BEFORE, date(2026, 12, 7)) is None


def test_the_measured_value_against_the_expected_with_the_measurement_error(plan):
    e = ex.expected(plan, RUNNING, BEFORE, date(2026, 12, 14))
    want = e.value(14.4)
    assert e.verdict(14.4, want) == "on"
    assert e.verdict(14.4, want + 14.4 * 0.01) == "on"                # within 1.5 %
    assert e.verdict(14.4, want + 14.4 * 0.02) == "ahead"
    assert e.verdict(14.4, 14.4) == "behind"


def _week1(plan):
    return [w for w in plan.workouts if w.date < date(2026, 11, 23) and w.goal is None]


def test_done_comes_from_the_status_and_the_phones_logs(plan):
    end = date(2026, 11, 23)                                            # the Monday of week 2
    wk = _week1(plan)
    assert len(wk) >= 4
    # Nothing known: no status, no log.
    d = ex.done(plan, BEFORE, end)
    assert d.share is None and d.known_min == 0 and d.planned_min == sum(w.minutes for w in wk)
    # The coach ticked the first one off in Tröskel; the phone's logs mark the others (the later log wins).
    first, second, third, fourth = wk[:4]
    marked = replace(plan, weeks=[replace(plan.weeks[0], workouts=[replace(w, status="done") if w is first else w
                                                                   for w in plan.weeks[0].workouts]),
                                  *plan.weeks[1:]])
    logs = [{"exported": "2026-11-19", "marks": {second.key: {"status": "missed"}}, "extras": []},
            {"exported": "2026-11-22", "marks": {second.key: {"status": "partial", "minutes": 20},
                                                 third.key: {"status": "missed"}},
             "extras": [{"date": "2026-11-21", "sport": "other", "minutes": 45, "name": "Yoga", "id": "x"},
                        {"date": "2026-11-30", "sport": "other", "minutes": 30, "name": "Yoga", "id": "y"}]}]
    d = ex.done(marked, BEFORE, end, logs)
    assert d.logged_until == date(2026, 11, 22)
    # Every workout of the week lies before the last log: the unmarked ones count as not done, none is unknown.
    assert d.known_min == d.planned_min
    assert d.done_min == first.minutes + 20
    assert d.share == pytest.approx((first.minutes + 20) / d.planned_min)
    assert (d.extras, d.extras_min) == (1, 45)                         # the session after the new test is left out
    q = [w for w in wk if w.quality]
    assert d.quality_planned == len(q)
    # A partly done workout without minutes counts half.
    half = ex.done(plan, BEFORE, end, [{"exported": "2026-11-17", "marks": {fourth.key: {"status": "partial"}}}])
    assert half.done_min == round(fourth.minutes * ex.PARTIAL_SHARE)
    # Workouts after the last log, without a status or a mark, are unknown, not missed.
    assert half.known_min == sum(w.minutes for w in wk if w.date <= date(2026, 11, 17) or w is fourth)
    assert half.known_min < half.planned_min


def test_the_followup_needs_days_of_the_plan_between_the_tests(plan):
    assert ex.followup(plan, RUNNING, BEFORE, date(2026, 11, 16)) is None          # the plan begins on the test day
    assert ex.followup(plan, RUNNING, date(2027, 3, 1), date(2027, 4, 1)) is None   # after the plan
    f = ex.followup(plan, RUNNING, BEFORE, date(2026, 12, 9))
    d = f.to_dict()
    assert d["expected"]["sport"] == RUNNING and d["expected"]["start"] == "2026-11-13"
    assert d["done"]["sports"][0]["sport"] == RUNNING and d["plan_start"] == "2026-11-16"
