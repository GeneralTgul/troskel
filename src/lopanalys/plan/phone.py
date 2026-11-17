"""The athlete's plan from the phone (#781): what the app sends the coach, read in Tröskel. No Qt.

The app's *Send the plan to your coach* (app/src/model/share.ts) gives a ``.troskel`` file or a link
``<app>#a=<data>`` with the payload ``troskel.athlete`` (format 1): the whole stored plan as the phone has it
(``link.stored_plan``'s format 1, with the athlete's times and what the app's engine rebuilt), the marks
(``{"status": "done" | "partial" | "missed", "minutes"?, "auto"?}`` per workout key, ``auto`` when the health data
ticked it off), the sessions outside the plan, the times still waiting under *Mina tider* and the day of the export.
The nights and the watch's workouts never leave the phone.

``rebuild`` is the rule the app's ``rebuild`` follows (app/src/engine/index.ts), with the Python engine: Tröskel uses it
to lay times that wait on the phone on the plan, and app/scripts/make_fixtures.py checks the app's engine against it.
"""
from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from . import codec, link
from .engine import ENGINE_VERSION, build_plan, sketch_of
from .models import Plan

KIND = "troskel.athlete"
FORMAT = 1
STATUSES = ("done", "partial", "missed")


class NotFromPhone(ValueError):
    """The text is not the athlete's plan from the phone. ``code`` says why, for the message on the plan page
    (i18n ``ui.plan.phone.err.<code>``): ``coach_plan`` (a plan from Tröskel, the ``#p=`` link or its file), ``none``
    (nothing from the app), ``newer`` (a newer app's format) or ``broken``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhonePlan:
    """What the phone sent: ``stored`` is the plan's payload (format 1), the rest the athlete's log."""
    stored: dict[str, Any]
    exported: date
    marks: dict[str, dict[str, Any]] = field(default_factory=dict)
    extras: list[dict[str, Any]] = field(default_factory=list)
    times: dict[str, Any] | None = None       # PendingTimes: {"slots", "unavailable", "saved", "kept"?}

    @property
    def plan_id(self) -> str:
        return str(self.stored["plan"]["id"])

    @property
    def athlete(self) -> str:
        """The name the plan on the phone carries ("" when the link from the coach left it out)."""
        return str(self.stored.get("athlete") or "")

    def counts(self) -> dict[str, int]:
        """How many workouts are done, partly done and missed, and how many sessions are outside the plan."""
        out = {s: 0 for s in STATUSES}
        for m in self.marks.values():
            out[m["status"]] += 1
        return {**out, "extras": len(self.extras)}

    def log(self) -> dict[str, Any]:
        """The log as Tröskel keeps it in the plan's event log (``phone``)."""
        return {"exported": self.exported.isoformat(), "marks": self.marks, "extras": self.extras,
                **({"times": self.times} if self.times else {})}


_LINK = re.compile(r"[#&]a=([A-Za-z0-9_-]+)")


def _payload(text: str) -> dict[str, Any]:
    """The JSON of a file, or of the link's ``a=`` data; the link may stand in a message pasted as it is."""
    s = text.strip().lstrip("\ufeff")
    if s.startswith("{"):
        return json.loads(s)
    m = _LINK.search(s)
    if m:
        data = m.group(1)
        return json.loads(zlib.decompress(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))).decode("utf-8"))
    if "#p=" in s or s.startswith("p="):
        raise NotFromPhone("coach_plan")
    raise NotFromPhone("none")


def athlete_link(payload: dict[str, Any], base: str = link.APP_URL) -> str:
    """The link the app's *Kopiera länken* gives (share.athleteLink): ``<app>#a=<data>``, coded as the plan's link.
    For the tests and the app's fixtures: Tröskel only reads these."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{base}#a=" + base64.urlsafe_b64encode(zlib.compress(data, 9)).rstrip(b"=").decode("ascii")


def read(text: str) -> PhonePlan:
    """The athlete's plan from the contents of the app's file or a pasted link. ValueError when it is not one."""
    try:
        d = _payload(text)
    except NotFromPhone:
        raise
    except Exception as e:                     # broken base64, deflate or JSON
        raise NotFromPhone("broken") from e
    if not isinstance(d, dict) or d.get("kind") != KIND:
        if isinstance(d, dict) and d.get("kind") == link.KIND:
            raise NotFromPhone("coach_plan")
        raise NotFromPhone("none")
    if not isinstance(d.get("format"), int) or d["format"] > FORMAT:
        raise NotFromPhone("newer")
    try:
        stored = link.expand(d["plan"])
        exported = date.fromisoformat(d["exported"])
    except Exception as e:
        raise NotFromPhone("broken") from e
    if stored.get("kind") != link.KIND or not isinstance(stored.get("plan"), dict) or "request" not in stored:
        raise NotFromPhone("broken")
    marks = {str(k): {"status": m["status"], **{x: m[x] for x in ("minutes", "auto") if m.get(x) is not None}}
             for k, m in (d.get("marks") or {}).items() if isinstance(m, dict) and m.get("status") in STATUSES}
    extras = [x for x in d.get("extras") or [] if isinstance(x, dict) and isinstance(x.get("date"), str)]
    times = d.get("times") if isinstance(d.get("times"), dict) and isinstance(d["times"].get("slots"), list) else None
    return PhonePlan(stored, exported, marks, extras, times)


def rebuild(stored: dict[str, Any], slots: list[dict[str, Any]], unavailable: list[str], from_day: str) -> dict[str, Any]:
    """The stored plan with new times from ``from_day`` on: what ``rebuild`` in app/src/engine/index.ts does.

    The request gets the new slots and the engine builds the whole plan again (same id, same ``created``),
    so the blocks stay and the weekly time follows what the new slots hold. A week that ends before
    ``from_day`` is kept as it was saved; in the week of ``from_day`` the workouts before it are kept (with
    their status) and the rest are new. The horizon of a rolling plan stays: the weeks after it are sketches
    of the new plan. On the dates in ``unavailable`` the workouts are dropped (races stay). Raises ValueError
    when the engine cannot make a plan."""
    day = date.fromisoformat(from_day)
    off = {date.fromisoformat(d) for d in unavailable}
    request = codec.request_from_dict({**stored["request"], "availability": slots}, stored["athlete"])
    old = codec.plan_from_dict(stored["plan"], codec.request_from_dict(stored["request"], stored["athlete"]))
    fresh = build_plan(request, plan_id=old.id, created=old.created)
    if len(fresh.weeks) != len(old.weeks):
        raise ValueError("the rebuilt plan has another length")
    weeks = []
    for was, new in zip(old.weeks, fresh.weeks):
        if was.sunday < day:
            weeks.append(was)
        elif old.horizon is not None and new.index > old.horizon:
            weeks.append(sketch_of(new))
        else:
            kept = [w for w in was.workouts if w.date < day]
            made = [w for w in new.workouts if w.date >= day and (w.goal is not None or w.date not in off)]
            weeks.append(replace(new, workouts=sorted(kept + made, key=lambda w: (w.date, w.key))))
    plan = replace(old, request=request, blocks=fresh.blocks, notes=fresh.notes, weeks=weeks,
                   engine_version=ENGINE_VERSION)
    return {**stored, "request": codec.request_to_dict(request), "plan": codec.plan_to_dict(plan)}


def to_plan(phone: PhonePlan, athlete_name: str = "", today: date | None = None) -> Plan:
    """The phone's plan as Tröskel saves it: the times that wait on the phone laid on it from tomorrow (as the app's
    *Spara* would), and what is ticked off as the workouts' status (done, missed; a partly done workout stays
    planned, its minutes are in the log). The targets are not taken from the phone: Tröskel works them out from
    the tests, as for its own plans."""
    stored = phone.stored
    if phone.times and not stored["plan"].get("source"):
        frm = (today or date.today()).toordinal() + 1
        later = [d for d in phone.times.get("unavailable", []) if date.fromisoformat(d).toordinal() >= frm]
        stored = rebuild(stored, phone.times["slots"], later, date.fromordinal(frm).isoformat())
    req = codec.request_from_dict(stored["request"], athlete_name or stored.get("athlete") or "")
    plan = codec.plan_from_dict(stored["plan"], req)
    status = {"done": "done", "missed": "missed"}

    def marked(w: Any) -> Any:
        m = phone.marks.get(w.key)
        return replace(w, status=status[m["status"]]) if m and m["status"] in status else w

    return replace(plan, weeks=[replace(wk, workouts=[marked(w) for w in wk.workouts]) for wk in plan.weeks])


__all__ = ["FORMAT", "KIND", "NotFromPhone", "PhonePlan", "athlete_link", "read", "rebuild", "to_plan"]
