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
