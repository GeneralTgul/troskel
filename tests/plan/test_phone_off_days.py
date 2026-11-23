"""The dates that do not work for the athlete (#846), from the phone to Tröskel and back (plan.phone, Plan.unavailable).

Once *Mina tider* in the app rebuilt the plan without them they are no longer waiting times, and the payload carries
them as ``off``. Tröskel keeps them with the plan it saves, and *Förnya*, a new test and the coach's form drop their
workouts again in the weeks they detail."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

from lopanalys.plan import codec, demo, link, phone, resolve, rolling
from lopanalys.plan.engine import build_plan
from lopanalys.ui import services

BUILT = date(2026, 11, 18)


def _rolling():
    return build_plan(demo.demo_request(20), plan_id="demo", today=BUILT)


def _payload(plan, **extra) -> str:
    return json.dumps({"kind": phone.KIND, "format": phone.FORMAT, "exported": "2026-11-25",
                       "plan": link.stored_plan(plan), "marks": {}, "extras": [], **extra})


def _sketch_day(p) -> date:
    """A day in the first sketched week that *Förnya* gives a workout (not a race)."""
    rolled = rolling.roll_forward(p, date(2026, 12, 1))
    week = next(w for w in rolled.weeks if w.index == p.horizon + 1)
    return next(w.date for w in week.workouts if w.goal is None)


def _on(plan, day: date) -> list:
    return [w for w in plan.workouts if w.date == day and w.goal is None]


def test_the_days_the_phone_rebuilt_without_stay_with_the_plan_troskel_saves():
    p = _rolling()
    day = _sketch_day(p)
    got = phone.read(_payload(p, off=[day.isoformat(), "2026-11-20", "not a day", 7]))
    assert got.off == ("2026-11-20", day.isoformat())
    assert got.log()["off"] == list(got.off)
    plan = phone.to_plan(got, "", date(2026, 11, 25))
    assert plan.unavailable == (day,)                   # the day that passed is left out
    again = codec.plan_from_dict(codec.plan_to_dict(plan), plan.request)
    assert again.unavailable == (day,)
    # a payload from an app before #846 has none, and the stored plan is as before
    old = phone.to_plan(phone.read(_payload(p)), "", date(2026, 11, 25))
    assert old.unavailable == () and "unavailable" not in codec.plan_to_dict(old)


def test_renew_and_a_new_test_on_the_pc_give_no_workout_on_the_days():
    p = _rolling()
    day = _sketch_day(p)
    mine = phone.to_plan(phone.read(_payload(p, off=[day.isoformat()])), "", date(2026, 11, 25))
    later = date(2026, 12, 1)
    assert not _on(rolling.roll_forward(mine, later), day)                         # *Förnya*
    cap = replace(mine.request.capacity_for("run"), tested=later)
    assert not _on(rolling.roll_forward(resolve.retest(mine, cap, from_date=later), later), day)   # a new test
    assert _on(rolling.roll_forward(replace(mine, unavailable=()), later), day)    # without them: a workout


def test_the_waiting_times_own_list_wins_when_times_wait():
    p = _rolling()
    day = _sketch_day(p)
    times = {"slots": link.stored_plan(p)["request"]["availability"], "unavailable": ["2026-12-03"],
             "saved": "2026-11-25"}
    got = phone.read(_payload(p, off=[day.isoformat()], times=times))
    plan = phone.to_plan(got, "", date(2026, 11, 25))
    assert plan.unavailable == (date(2026, 12, 3),)     # the athlete took the old day away under Mina tider


def test_the_coachs_form_keeps_the_days_on_the_same_plan():
    p = _rolling()
    day = next(w.date for w in p.weeks[1].workouts if w.goal is None)
    saved = replace(p, unavailable=(day,))
    fresh = build_plan(p.request, plan_id="demo", today=BUILT)
    assert _on(fresh, day)
    out = services.with_saved_edits(saved, fresh)
    assert out.unavailable == (day,) and not _on(out, day)
    assert services.with_saved_edits(saved, replace(fresh, id="other")).unavailable == ()


def test_the_link_back_to_the_app_holds_the_days():
    p = _rolling()
    day = _sketch_day(p)
    mine = phone.to_plan(phone.read(_payload(p, off=[day.isoformat()])), "", date(2026, 11, 25))
    stored = link.stored_plan(mine)
    assert stored["plan"]["unavailable"] == [day.isoformat()]
    # the day lies in a sketched week, so the engine builds the same plan: the short link carries the day in ``x``
    short = link.short_plan(stored)
    assert short is not None and short["x"]["unavailable"] == [day.isoformat()]
    for payload in (short, stored):
        assert link.expand(link.read_link(link.plan_link(payload)))["plan"]["unavailable"] == [day.isoformat()]
