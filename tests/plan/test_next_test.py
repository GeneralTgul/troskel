"""The plan's next test (#778, step 4, lopanalys.plan.next_test): the default day, what the targets build on, and the
coach's day through the codec, the links, new times, *Förnya*, the athlete's edits and a new test. The app's port
(app/src/model/nexttest.ts) is checked against the same rules with app/test/fixtures/nexttest.json.gz."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from lopanalys.plan import codec, demo, edits, link, next_test, phone
from lopanalys.plan.engine import build_plan
from lopanalys.plan.models import SELF, Capacity, Edit
from lopanalys.plan.rolling import roll_forward

DAY = date(2027, 2, 10)


def _plan(weeks: int = 12, **kw):
    return build_plan(replace(demo.demo_request(weeks), **kw), plan_id="demo")


def test_the_default_is_the_test_week_8_to_12_weeks_after_the_test():
    plan = _plan(12)
    kind, tested = next_test.basis(plan)
    assert kind == "test" and tested == plan.request.capacity_for("run").tested
    day = next_test.default_next_test(plan)
    week = next(wk for wk in plan.weeks if wk.monday <= day <= wk.sunday)
    assert week.kind == "test" and any(w.family == "test" and w.date == day for w in week.workouts)
    start = next_test.anchor(plan)
    assert start == max(tested, plan.start)
    assert start + timedelta(weeks=7) <= day <= start + timedelta(weeks=12)
    assert next_test.next_test(plan) == day and plan.next_test is None


def test_without_a_test_week_in_the_window_it_is_10_weeks_after():
    plan = _plan(5)                                          # a short plan: its test weeks end before 8 weeks
    assert next_test.default_next_test(plan) == next_test.anchor(plan) + timedelta(weeks=10)


def test_an_old_test_counts_from_the_plans_start():
    old = demo.demo_request(12).capacity[0]
    plan = _plan(12, capacity=(replace(old, tested=date(2026, 8, 18)),))
    assert next_test.anchor(plan) == plan.start
    assert plan.start + timedelta(weeks=7) <= next_test.default_next_test(plan) <= plan.start + timedelta(weeks=12)


def test_without_a_test_and_with_own_values_it_counts_from_the_start():
    plan = _plan(12, capacity=())
    assert next_test.basis(plan) == ("none", None) and next_test.anchor(plan) == plan.start
    own = _plan(12, capacity=(Capacity("run", demo.demo_request(12).capacity[0].anchors, None, SELF),))
    assert next_test.basis(own) == ("own", None) and next_test.anchor(own) == own.start


def test_the_coachs_day_is_stored_only_when_set_and_stays_through_the_links():
    plan = _plan(12)
    assert "next_test" not in codec.plan_to_dict(plan)
    chosen = replace(plan, next_test=DAY)
    d = codec.plan_to_dict(chosen)
    assert d["next_test"] == DAY.isoformat()
    assert codec.plan_from_dict(d, plan.request).next_test == DAY
    stored = link.stored_plan(chosen)
    payload = link.link_payload(stored)
    assert payload["format"] == 2 and payload["x"]["next_test"] == DAY.isoformat()
    assert link.expand(payload) == stored
    assert link.expand(link.read_link(link.plan_link(stored)))["plan"]["next_test"] == DAY.isoformat()


def test_the_coachs_day_stays_through_new_times_renew_and_edits():
    plan = replace(build_plan(demo.demo_request(20), plan_id="demo", today=date(2026, 11, 18)), next_test=DAY)
    stored = link.stored_plan(plan)
    slots = [s for s in stored["request"]["availability"] if s["weekday"] != 0]
    assert phone.rebuild(stored, slots, [], "2026-11-24")["plan"]["next_test"] == DAY.isoformat()
    assert roll_forward(plan, date(2026, 12, 20)).next_test == DAY
    w = next(w for w in plan.weeks[1].workouts if edits.editable(w))
    assert edits.apply_edits(replace(plan, edits=(Edit(w.key, None, 25),))).next_test == DAY


def test_a_new_test_moves_the_coachs_day_as_far_after_it():
    before = replace(_plan(12), next_test=None)
    tested = next_test.basis(before)[1]
    cap = replace(before.request.capacity_for("run"), tested=tested + timedelta(weeks=8))
    from lopanalys.plan import resolve
    after = resolve.retest(before, cap, from_date=cap.tested)
    assert next_test.after_retest(before, after).next_test is None      # the default follows the new test itself
    chosen = replace(before, next_test=next_test.anchor(before) + timedelta(weeks=9))
    after = resolve.retest(chosen, cap, from_date=cap.tested)
    assert next_test.anchor(after) == cap.tested
    assert next_test.after_retest(chosen, after).next_test == cap.tested + timedelta(weeks=9)
    bike = Capacity("bike", cap.anchors, cap.tested, "FTP")              # another sport: the day stays
    other = resolve.retest(chosen, bike, from_date=cap.tested)
    assert next_test.after_retest(chosen, other).next_test == chosen.next_test
