"""The plan follows the tests (#776): a new test gives the athlete a new link, and nothing else changes the workouts.

The chain end to end at the plan level, with two tests on the same athlete: the plan is built on the first test and
sent as a link; each new test lays the rest of the plan on its values (resolve.retest), details the next weeks
(rolling.roll_forward, as apply_retest in ui/services.py) and is sent as a new link. The link keeps the plan's id and
the workouts' keys, so the app keeps what the athlete ticked off (keepFor in app/src/model/log.ts); the workouts
already handed out keep their days, and before the test day their targets.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language
from lopanalys.plan import RUNNING, Capacity, build_plan, link, resolve, rolling

from . import cases as c

FIRST_DAY = c.START                                  # the plan is built and sent on the first test's Monday
SECOND = Capacity(RUNNING, Anchors(192, 45, 168, 177, 13.6, 14.8), date(2026, 12, 9), "Laktattrappa")
THIRD = Capacity(RUNNING, Anchors(191, 44, 170, 179, 14.0, 15.2), date(2027, 1, 13), "Laktattrappa")


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _phone(plan):
    """What the phone reads: the link's payload, expanded to the whole plan as the app does."""
    return link.expand(link.read_link(link.plan_link(link.link_payload(link.stored_plan(plan, "")))))


def _retest(plan, cap, day):
    """apply_retest without the database: the new targets from ``day``, the next weeks detailed."""
    return rolling.roll_forward(resolve.retest(plan, cap, from_date=day), day)


def _workouts(stored):
    return {w["key"]: w for wk in stored["plan"]["weeks"] for w in wk["workouts"]}


def test_two_new_tests_give_new_links_with_the_same_plan():
    plan = build_plan(c.CASES["half_12"], plan_id="p7", today=FIRST_DAY)
    sent = [_phone(plan)]
    for cap in (SECOND, THIRD):
        day = cap.tested
        plan = _retest(plan, cap, day)
        sent.append(_phone(plan))
        old, new = sent[-2], sent[-1]
        assert new["plan"]["id"] == old["plan"]["id"] == "p7"
        # Every workout handed out before keeps its key and day, and before the test day its parts and status.
        before, after = _workouts(old), _workouts(new)
        assert set(before) <= set(after)
        for k, w in before.items():
            assert after[k]["date"] == w["date"]
            if w["date"] < day.isoformat():
                assert after[k] == w
        # The new values reach the phone: the targets now are the new test's, the old ones stand until the test day.
        assert new["targets"][RUNNING] == link.zone_targets(cap, plan.zone_system)
        assert new["targets"][RUNNING] != old["targets"][RUNNING]
        assert new["targets_log"][-1]["until"] == day.isoformat()
        assert new["targets_log"][-1]["targets"] == old["targets"][RUNNING]
        # And the weeks after the old horizon got their workouts on the new test's day (as Förnya).
        assert new["plan"]["horizon"] >= old["plan"]["horizon"]
    assert [e["until"] for e in sent[-1]["targets_log"]] == [SECOND.tested.isoformat(), THIRD.tested.isoformat()]


def test_without_a_new_test_the_link_is_the_same():
    """The plan does not change by itself: the same stored plan gives the same link on any later day."""
    plan = build_plan(c.CASES["half_12"], plan_id="p7", today=FIRST_DAY)
    first = link.plan_link(link.link_payload(link.stored_plan(plan, "")))
    later = json.loads(json.dumps(link.stored_plan(plan, "")))
    assert link.plan_link(link.link_payload(later)) == first
    # A retest without a newer day moves no workout either: only the targets follow the new values.
    moved = resolve.retest(plan, replace(SECOND, tested=FIRST_DAY), from_date=FIRST_DAY)
    assert moved.weeks == plan.weeks


def test_the_phone_reads_a_retested_plan_from_the_short_link_or_the_whole():
    plan = _retest(build_plan(c.CASES["half_12"], plan_id="p7", today=FIRST_DAY), SECOND, SECOND.tested)
    whole = json.loads(json.dumps(link.stored_plan(plan, "")))
    assert _phone(plan) == whole
    assert any(w["date"] >= (SECOND.tested + timedelta(days=1)).isoformat() for w in _workouts(whole).values())
