"""The athlete's changes of single workouts (#777, step 3): moved, swapped and shortened workouts are kept as a list on
the plan (``Plan.edits``) and laid on the weeks again after a rebuild with new times, *Förnya*, a new test and the
coach's form, so that they never disappear (#776)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from lopanalys.plan import codec, demo, edits, link, phone
from lopanalys.plan.engine import build_plan
from lopanalys.plan.models import Edit, Part
from lopanalys.plan.rolling import roll_forward
from lopanalys.ui import services


def _plan(weeks: int = 12):
    return demo.demo_plan(weeks)


def _by_key(plan):
    return {w.key: w for wk in plan.weeks for w in wk.workouts}


def test_a_move_a_swap_and_a_shortening_are_laid_on_the_week():
    p = _plan()
    wk = p.weeks[2]
    mon, tue, thu = wk.workouts[0], wk.workouts[1], wk.workouts[2]
    q = edits.apply_edits(replace(p, edits=(Edit(mon.key, tue.date), Edit(tue.key, mon.date), Edit(thu.key, None, 40),
                                            Edit(wk.workouts[3].key, wk.monday + timedelta(days=4)))))
    got = _by_key(q)
    assert got[mon.key].date == tue.date and got[tue.key].date == mon.date
    assert got[thu.key].minutes == 40 and got[thu.key].template_id == "threshold"
    assert got[wk.workouts[3].key].date == wk.monday + timedelta(days=4)
    assert got[wk.workouts[3].key].start is None          # Friday has no slot in the demo
    assert [w.date for w in q.weeks[2].workouts] == sorted(w.date for w in q.weeks[2].workouts)
    assert edits.apply_edits(q) == q                       # laying them again changes nothing
    assert len(q.edits) == 4


def test_the_clock_time_follows_the_new_day():
    p = _plan()
    wk = p.weeks[2]
    sat = next(w for w in wk.workouts if w.date.weekday() == 5)
    q = edits.apply_edits(replace(p, edits=(Edit(sat.key, wk.monday + timedelta(days=1)),)))
    assert _by_key(q)[sat.key].start == next(s.start for s in p.request.availability if s.weekday == 1)


def test_an_edit_outside_the_week_a_race_and_a_lost_key_are_left_alone():
    p = _plan()
    wk = p.weeks[2]
    w = wk.workouts[0]
    race = next(x for x in p.workouts if x.goal is not None)
    q = edits.apply_edits(replace(p, edits=(Edit(w.key, wk.monday + timedelta(days=9)), Edit(race.key, None, 10),
                                            Edit("demo-9901-mon", None, 10))))
    assert _by_key(q)[w.key] == w and _by_key(q)[race.key] == race
    assert [e.key for e in q.edits] == [w.key]


def test_shorten_cuts_steady_parts_then_the_ends_then_repeats():
    easy = [Part("steady", 60 * 60, "Z2", 3)]
    assert sum(x.total_s for x in edits.shorten(easy, 35)) == 35 * 60
    assert edits.shorten(easy, 2)[0].duration_s == edits.MIN_PART_S
    sets = [Part("warmup", 15 * 60, "Z2", 3), Part("work", 6 * 60, "Z4", 7, repeat=4, rest_s=60),
            Part("cooldown", 10 * 60, "Z2", 2)]
    for m in (50, 45, 40, 30, 20):
        cut = edits.shorten(sets, m)
        assert sum(x.total_s for x in cut) == m * 60, m
    assert edits.shorten(sets, 45)[1].repeat == 3 and edits.shorten(sets, 30)[1].repeat == 1
    assert edits.shorten(sets, 90) == sets
    long_prog = [Part("steady", 70 * 60, "Z2", 4), Part("steady", 20 * 60, "Z3", 6)]
    assert [x.duration_s // 60 for x in edits.shorten(long_prog, 60)] == [40, 20]


def test_the_edits_survive_the_codec_and_the_short_link():
    p = _plan()
    wk = p.weeks[1]
    q = edits.apply_edits(replace(p, edits=(Edit(wk.workouts[0].key, wk.workouts[1].date),
                                            Edit(wk.workouts[1].key, None, 30))))
    assert codec.plan_from_dict(codec.plan_to_dict(q), q.request) == q
    assert "edits" not in codec.plan_to_dict(p)
    stored = link.stored_plan(q)
    short = link.short_plan(stored)
    assert short is not None and "edits" in short["x"]
    assert link.expand(short) == stored


def _edited_rolling():
    p = build_plan(demo.demo_request(20), plan_id="demo", today=date(2026, 11, 18))
    wk = p.weeks[1]
    a, b = wk.workouts[0], wk.workouts[1]
    later = next(x for x in p.weeks[3].workouts if x.goal is None)
    return edits.apply_edits(replace(p, edits=(Edit(a.key, b.date), Edit(b.key, a.date), Edit(later.key, None, 25)))), a, b, later


def test_new_times_on_the_phone_keep_the_edits():
    p, a, b, later = _edited_rolling()
    stored = link.stored_plan(p)
    slots = stored["request"]["availability"]
    out = phone.rebuild(stored, [{**s, "max_minutes": s["max_minutes"] + 15} for s in slots], [], "2026-11-21")
    plan = codec.plan_from_dict(out["plan"], codec.request_from_dict(out["request"]))
    got = _by_key(plan)
    assert got[a.key].date == b.date and got[b.key].date == a.date
    assert got[later.key].minutes <= 25
    assert plan.edits == p.edits


def test_renew_and_a_new_test_keep_the_edits():
    p, a, b, later = _edited_rolling()
    rolled = roll_forward(p, date(2026, 12, 20))
    assert rolled.horizon > p.horizon and rolled.edits == p.edits
    assert _by_key(rolled)[a.key].date == b.date and _by_key(rolled)[later.key].minutes <= 25


def test_the_coachs_form_keeps_the_athletes_edits_on_the_same_plan():
    p, a, b, later = _edited_rolling()
    fresh = build_plan(p.request, plan_id="demo", today=date(2026, 11, 18))
    assert fresh.edits == ()
    out = services.with_saved_edits(p, fresh)
    assert out.edits == p.edits and _by_key(out)[a.key].date == b.date
    assert services.with_saved_edits(p, replace(fresh, id="other")).edits == ()
    assert services.with_saved_edits(None, fresh) is fresh
