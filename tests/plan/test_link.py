"""The plan as a link, a QR code and a file for the athlete's app, and back."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.plan import build_plan, codec, link

from . import cases as c

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.mark.parametrize("name", sorted(c.CASES))
def test_the_link_decodes_to_the_same_plan(name):
    plan = build_plan(c.CASES[name], plan_id="p1")
    stored = link.stored_plan(plan)
    url = link.plan_link(stored)
    assert url.startswith(link.APP_URL + "#p=")
    back = link.read_link(url)
    assert back == json.loads(json.dumps(stored))
    req = codec.request_from_dict(back["request"], back["athlete"])
    assert codec.plan_from_dict(back["plan"], req) == plan


def test_the_link_has_no_padding_and_only_url_characters():
    data = link.plan_link(link.stored_plan(build_plan(c.CASES["half_12"])))
    tail = data.split("#p=", 1)[1]
    assert "=" not in tail and set(tail) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_the_targets_are_resolved_for_the_app():
    stored = link.stored_plan(build_plan(c.CASES["half_12"]))
    run = stored["targets"]["run"]
    assert sorted(run) == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert run["Z4"]["hr_low"] and run["Z4"]["speed_high_kmh"]


def test_the_file_is_the_same_payload():
    stored = link.stored_plan(build_plan(c.CASES["half_12"]), athlete_name="")
    assert stored["athlete"] == ""
    assert json.loads(link.plan_file(stored).decode("utf-8")) == json.loads(json.dumps(stored))


def test_something_else_is_not_read_as_a_plan():
    with pytest.raises(ValueError):
        link.read_link(link.APP_URL)
    import base64
    import zlib
    other = base64.urlsafe_b64encode(zlib.compress(b'{"kind":"other"}')).decode().rstrip("=")
    with pytest.raises(ValueError):
        link.decode(other)


def test_the_apps_files_from_python_are_up_to_date():
    """app/public/demo.troskel, the app's test fixtures and its plan texts: run app/scripts/make_fixtures.py."""
    spec = importlib.util.spec_from_file_location("make_fixtures", ROOT / "app" / "scripts" / "make_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert [p.relative_to(ROOT).as_posix() for p in mod.stale()] == []


def test_the_engine_fixtures_are_written_in_swedish():
    """The fixtures were once built while the English texts were set, so the running test's source was English."""
    import gzip
    raw = gzip.decompress((ROOT / "app" / "test" / "fixtures" / "engine.json.gz").read_bytes()).decode("utf-8")
    assert "Lactate step test" not in raw and '"source":"Laktattrappa"' in raw


# ---- the short link (format 2): the request instead of the workouts ----------------------------------------------

def _short_link(plan) -> tuple[str, dict]:
    stored = link.stored_plan(plan)
    return link.plan_link(link.link_payload(stored)), stored


@pytest.mark.parametrize("name", sorted(c.CASES))
def test_the_short_link_gives_the_same_plan(name):
    plan = build_plan(c.CASES[name], plan_id="p1", today=c.START)
    if link.app_lacks(codec.request_to_dict(plan.request)):
        # A choice the app's short link does not read yet: the link holds the whole plan (link.APP_LACKS).
        stored = link.stored_plan(plan)
        assert link.short_plan(stored) is None and link.link_payload(stored)["format"] == link.FORMAT
        short = link._pack(stored)        # the short payload itself carries the choices and gives the plan back
        assert link.expand(json.loads(json.dumps(short))) == json.loads(json.dumps(stored))
        return
    url, stored = _short_link(plan)
    payload = link.read_link(url)
    assert payload["format"] == link.SHORT_FORMAT and "plan" not in payload and payload["v"] == link.ENGINE_VERSION
    assert link.expand(payload) == json.loads(json.dumps(stored))


def test_a_plan_with_every_sport_now_fits_in_a_qr_code():
    """The coach's plan with four sports, two workouts a day and 30 weeks was too long for a QR code."""
    from lopanalys.share import qr
    plan = build_plan(c.MANY_SPORTS_30, plan_id="p1", today=c.START)
    assert len(plan.weeks) == 30 and len(plan.request.plan_sports) == 4
    assert len({w.date for w in plan.workouts}) < len([w for w in plan.workouts if w.goal is None])   # two a day
    whole = link.plan_link(link.stored_plan(plan))
    url, stored = _short_link(plan)
    assert not qr.fits(whole) and len(url) < 2000 and qr.fits(url)
    assert link.expand(link.read_link(url)) == json.loads(json.dumps(stored))
    detailed = build_plan(c.MANY_SPORTS_30, plan_id="p1")        # every week with its workouts: the same short link
    assert len(_short_link(detailed)[0]) < 2000


def test_the_short_link_carries_what_the_engine_does_not_give_back():
    """A retest: the log of the earlier test and the new test go in the request and ``x``; the plan is the same."""
    from datetime import date, timedelta

    from lopanalys.assessments.zone_systems import Anchors
    from lopanalys.plan import Capacity, resolve, rolling
    plan = build_plan(c.CASES["half_12"], plan_id="p1", today=c.START)
    day = c.START + timedelta(weeks=3)
    new = Capacity("run", Anchors(192, 46, 167, 176, 13.5, 14.8), day, "Laktattrappa")
    plan = rolling.roll_forward(resolve.retest(plan, new, from_date=day), day)
    url, stored = _short_link(plan)
    payload = link.read_link(url)
    assert payload["format"] == link.SHORT_FORMAT and "capacity_log" in payload["x"] and payload["tl"]
    assert link.expand(payload) == json.loads(json.dumps(stored))
    assert date.fromisoformat(link.expand(payload)["targets_log"][0]["until"]) == day


def test_a_plan_the_engine_would_not_build_again_goes_whole():
    """A workout with a status (the engine makes them planned): the link holds the whole plan, as before."""
    from dataclasses import replace
    plan = build_plan(c.CASES["half_12"], plan_id="p1")
    wk = plan.weeks[0]
    plan = replace(plan, weeks=[replace(wk, workouts=[replace(wk.workouts[0], status="done"), *wk.workouts[1:]]),
                                *plan.weeks[1:]])
    stored = link.stored_plan(plan)
    assert link.short_plan(stored) is None and link.link_payload(stored) is stored
    assert link.read_link(link.plan_link(link.link_payload(stored)))["format"] == link.FORMAT


def test_a_short_link_from_another_engine_is_not_built():
    payload = link.short_plan(link.stored_plan(build_plan(c.CASES["half_12"], plan_id="p1")))
    with pytest.raises(ValueError):
        link.expand({**payload, "v": "0"})
    assert link.expand({**link.stored_plan(build_plan(c.CASES["half_12"]))})["format"] == link.FORMAT   # format 1


def test_the_short_targets_keep_the_watt_and_the_swim_paces():
    plan = build_plan(c.CASES["triathlon_swim"], plan_id="p1")
    stored = json.loads(json.dumps(link.stored_plan(plan)))
    for sport, ts in stored["targets"].items():
        assert link.unpack_targets(json.loads(json.dumps(link.pack_targets(ts)))) == ts, sport
    assert "power_low_w" in stored["targets"]["bike"]["Z3"] and "swim_low_s" in stored["targets"]["swim"]["Z3"]
    assert link.unpack_request(link.pack_request(stored["request"])) == stored["request"]
