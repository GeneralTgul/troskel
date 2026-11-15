"""The calendar file follows RFC 5545 closely enough for phones and calendar programs to import it."""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.plan import build_plan, ics

from . import cases as c


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.fixture(scope="module")
def plan():
    return build_plan(c.CASES["half_12"], plan_id="p1")


def unfold(text: str) -> list[str]:
    return text.replace("\r\n ", "").split("\r\n")


def events(text: str) -> list[dict[str, str]]:
    out, cur = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            out.append(cur)
            cur = None
        elif cur is not None:
            name, _, value = line.partition(":")
            assert name not in cur, f"{name} twice in one event"
            cur[name] = value
    return out


def test_structure(plan):
    text = ics.to_ics(plan)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")       # CRLF only
    lines = text.split("\r\n")[:-1]
    assert all(len(line.encode("utf-8")) <= 75 for line in lines)                # folded at 75 octets
    assert all(line for line in lines)
    flat = unfold(text)
    assert flat[0] == "BEGIN:VCALENDAR" and flat[-2] == "END:VCALENDAR"
    assert "VERSION:2.0" in flat and any(x.startswith("PRODID:") for x in flat)
    assert flat.count("BEGIN:VEVENT") == flat.count("END:VEVENT") == len(plan.workouts)
    for line in flat[:-1]:
        assert re.match(r"^[A-Z][A-Z0-9-]*(;[A-Z-]+=[^:;]+)*:", line), line        # name[;param=value]:value


def test_events(plan):
    evs = events(ics.to_ics(plan))
    uids = [e["UID"] for e in evs]
    assert len(set(uids)) == len(uids) and uids == [f"{w.key}@lopanalys" for w in plan.workouts]
    for e, w in zip(evs, plan.workouts):
        assert re.fullmatch(r"\d{8}T\d{6}Z", e["DTSTAMP"])
        assert e["SUMMARY"] and e["DESCRIPTION"]
        if w.start and w.seconds:
            a, b = (datetime.strptime(e[k], "%Y%m%dT%H%M%S") for k in ("DTSTART", "DTEND"))   # floating local time
            assert a == datetime.combine(w.date, w.start) and (b - a).total_seconds() == w.seconds
        else:
            assert e["DTSTART;VALUE=DATE"] == f"{w.date:%Y%m%d}" and int(e["DTEND;VALUE=DATE"]) > int(e["DTSTART;VALUE=DATE"])
    thr = next(e for e, w in zip(evs, plan.workouts) if w.template_id == "threshold")
    assert thr["SUMMARY"].startswith("Tröskelintervaller · ")
    assert "170–174 slag/min · 4:21–4:10 min/km" in thr["DESCRIPTION"] and "\\n" in thr["DESCRIPTION"]
    race = evs[-1]
    assert race["SUMMARY"] == "Halvmaran" and "21.1 km" in race["DESCRIPTION"] and "under 1:45" in race["DESCRIPTION"]


def test_escaping_and_folding():
    assert ics.escape("a,b;c\\d\ne") == r"a\,b\;c\\d\ne"
    long = "DESCRIPTION:" + "å" * 100
    folded = ics.fold(long)
    assert all(len(part.encode("utf-8")) <= 75 for part in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == long
    folded.encode("utf-8").decode("utf-8")       # no character was split


def test_same_plan_gives_the_same_file_and_it_is_written_as_utf8(plan, tmp_path):
    assert ics.to_ics(plan) == ics.to_ics(build_plan(c.CASES["half_12"], plan_id="p1"))
    ics.write_ics(plan, str(tmp_path / "plan.ics"))
    raw = (tmp_path / "plan.ics").read_bytes()
    assert raw.decode("utf-8") == ics.to_ics(plan) and b"\r\n" in raw


def test_english(plan):
    set_language("en")
    evs = events(ics.to_ics(plan))
    assert any(e["SUMMARY"].startswith("Threshold intervals") for e in evs)
    assert not any("plan." in e["SUMMARY"] + e["DESCRIPTION"] for e in evs)
