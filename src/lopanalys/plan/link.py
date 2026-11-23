"""A plan for the athlete's app: one JSON payload, sent as a link, a QR code or a ``.troskel`` file. No Qt.

The payload is the stored plan (``codec.request_to_dict`` and ``codec.plan_to_dict``) with what the app
cannot work out by itself: the athlete's name and every zone's targets (heart rate, speed), resolved here
from the tests the plan is built on. The app shows a test's targets as they were when the coach sent the plan; a
retest sends a new link. Only for the athlete's own values (#777, ``source`` "self") does the app work the targets out
itself, with a port of resolve.py (app/src/engine/zones.ts).

The link is ``APP_URL#p=<data>``: the JSON compressed with deflate (zlib) and coded as base64url without
padding. A browser never sends the fragment (``#...``) to a server, so the plan stays on the phone.
The file is the same JSON as UTF-8 text.

Format 2, the short payload (``short_plan``), holds the plan's request instead of its workouts: the app builds the
plan with its own engine (app/src/engine), the same rules as engine.py, so a plan with several sports and two
workouts a day fits in one QR code. It holds what the engine needs (the request, the plan's id, ``created`` and
horizon), the engine version that built it, the fields of the plan that the engine would not give back as they
are stored (``x``: notes, the log of earlier tests, a status, the athlete's changes of single workouts, which the
engine lays on the plan it built: plan.edits, the day of the next test: plan.next_test, the dates that do not work:
plan.unavailable), and the targets. Keys are short and a default is left
out. ``short_plan`` checks itself: it builds the plan from the short payload and gives None when that is not the
stored plan (a workout with a status, weeks rolled on by older rules), and the link then holds the whole plan.
The app reads both formats; with format 2 and another engine version it asks for an update instead of building
another plan. The ``.troskel`` file always holds the whole plan (format 1).

Format 3 is format 2 with the coach's choices in the request (``CHOICES``: the keys ``q``, ``ed``, ``sl``, ``ew``,
``mx`` and a fixed day's minutes). It has its own number because an app from before the choices reads format 2 and
would drop keys it does not know and build another plan, without a word; a format it does not know it turns down
and asks for an update. The engine version stays, so the links of every plan without a choice are as before, in
both directions (an older Tröskel with a newer app, and the other way round).
"""
from __future__ import annotations

import base64
import json
import zlib
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from . import codec
from .edits import apply_edits
from .engine import ENGINE_VERSION, build_plan, sketch_of
from .models import Capacity, Plan
from .resolve import Target, band_targets, targets

APP_URL = "https://generaltgul.github.io/troskel/app/"
KIND = "troskel.plan"
FORMAT = 1
SHORT_FORMAT = 2              # the request instead of the workouts: the app builds the plan (short_plan)
CHOICES_FORMAT = 3            # the same with the coach's choices in the request, which an older app cannot read
SHORT_FORMATS = (SHORT_FORMAT, CHOICES_FORMAT)
FILE_SUFFIX = ".troskel"
# The coach's choices in the request: a short payload with one of them (or with a fixed day's minutes) is format 3
# (``has_choices``), which the app reads from the version that unpacks them (app/src/model/short.ts).
CHOICES = ("quality_sessions", "easy_days", "second_long", "easy_weeks", "max_weekly_minutes")
# The guard: until the app that reads format 3 is out (in the stores and on GitHub Pages), a plan with a choice is
# sent whole (format 1), which every app reads as it is; an installed older app would only ask for an update that
# does not exist yet. Set to False when the new app is out, and the same plans get the short link (format 3).
APP_LACKS = True
_TARGET_FIELDS = ("hr_low", "hr_high", "speed_low_kmh", "speed_high_kmh", "estimated", "speed_low_approx",
                  "speed_high_approx", "speed_from_hr")


# The bike's and the SkiErg's watt (#666, step 2): only in a target that has them, so a running plan's payload
# is as before and an app without the fields reads it as before.
_POWER_FIELDS = ("power_low_w", "power_high_w")
# Swimming's pace per 100 m from a CSS test (#666, step 3), in the same way: only in a swimming target.
_SWIM_FIELDS = ("swim_low_s", "swim_high_s")


def _target_dict(tg: Target) -> dict[str, Any]:
    d = {k: getattr(tg, k) for k in _TARGET_FIELDS}
    if tg.power_low_w or tg.power_high_w:
        d.update({k: getattr(tg, k) for k in _POWER_FIELDS})
    if tg.swim_low_s or tg.swim_high_s:
        d.update({k: getattr(tg, k) for k in _SWIM_FIELDS})
    return d


def zone_targets(capacity: Capacity | None, system: str, sport: str | None = None) -> dict[str, dict[str, Any]]:
    """Zone code -> its targets as a dict, for workouts in ``sport`` (None: the test's); {} when nothing is known.
    A band's target (the bike's sweet spot, resolve.BANDS) lies beside the zones under the band's name."""
    both = {**targets(capacity, system, sport), **band_targets(capacity, system, sport)}
    return {z: _target_dict(tg) for z, tg in both.items()}


def _targets(plan: Plan) -> dict[str, dict[str, dict[str, Any]]]:
    """Targets per sport: each test's, and a sport without its own test with the test it borrows (skis: the
    SkiErg's heart rates, without its watt)."""
    out = {c.sport: zone_targets(c, plan.zone_system) for c in plan.request.capacity}
    for sport in plan.request.plan_sports:
        if sport not in out and plan.request.capacity_for(sport).known:
            out[sport] = zone_targets(plan.request.capacity_for(sport), plan.zone_system, sport)
    return out


def stored_plan(plan: Plan, athlete_name: str | None = None) -> dict[str, Any]:
    """The payload for the app. ``athlete_name`` None: the plan's own name; "" leaves the name out."""
    name = plan.request.athlete_name if athlete_name is None else athlete_name
    return {
        "kind": KIND, "format": FORMAT, "athlete": name,
        "request": codec.request_to_dict(plan.request), "plan": codec.plan_to_dict(plan),
        "targets": _targets(plan),
        # Targets of the earlier tests: a workout dated before ``until`` is shown with them (Plan.capacity_on).
        "targets_log": [{"until": until.isoformat(), "sport": c.sport, "targets": zone_targets(c, plan.zone_system)}
                        for until, c in plan.capacity_log],
    }


# ---- format 2: the short payload ----------------------------------------------------------------------------------

_ANCHORS = ("max_hr", "resting_hr", "lt1_hr", "lt2_hr", "lt1_speed_kmh", "lt2_speed_kmh")
_FLAGS = ("estimated", "speed_low_approx", "speed_high_approx", "speed_from_hr")      # bit 1, 2, 4, 8
_PLAN_FIELDS = ("engine_version", "zone_system", "status", "based_on", "notes", "capacity_log", "blocks", "edits", "next_test",
                "unavailable")


def _trim(row: list[Any], defaults: tuple[Any, ...]) -> list[Any]:
    """``row`` without the trailing values that are their defaults (``defaults`` lines up with its end)."""
    out = list(row)
    while out and len(out) > len(row) - len(defaults) and out[-1] == defaults[len(out) - len(row) - 1]:
        out.pop()
    return out


def _hm(v: str | None) -> str | None:
    return v[:5] if v and v.endswith(":00") and len(v) == 8 else v


def _pack_capacity(c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"s": c["sport"]}
    a = c.get("anchors")
    if a is not None:
        out["a"] = [a.get(k) for k in _ANCHORS] + [a.get("sport", "run")]
        out["a"] = _trim(out["a"], (None,) * 6 + (c["sport"],))
    for k, short in (("tested", "t"), ("source", "o"), ("speed_line", "l"), ("css_s100", "c")):
        if c.get(k) not in (None, ""):
            out[short] = c[k]
    return out


def _unpack_capacity(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"sport": d["s"], "anchors": None, "tested": d.get("t"), "source": d.get("o", "")}
    if "a" in d:
        a = list(d["a"]) + [None] * (6 - min(6, len(d["a"])))
        out["anchors"] = {**dict(zip(_ANCHORS, a[:6])), "sport": a[6] if len(a) > 6 else d["s"]}
    if "l" in d:
        out["speed_line"] = d["l"]
    if "c" in d:
        out["css_s100"] = d["c"]
    return out


def _pack_goal(g: dict[str, Any]) -> list[Any]:
    return _trim([g["name"], g["date"], g["sport"], g["distance_km"], g["priority"], g["target"]],
                 ("run", None, "A", ""))


def _unpack_goal(g: list[Any]) -> dict[str, Any]:
    g = list(g) + [None] * (6 - len(g))
    return {"name": g[0], "date": g[1], "sport": g[2] or "run", "distance_km": g[3], "priority": g[4] or "A",
            "target": g[5] or ""}


def pack_request(r: dict[str, Any]) -> dict[str, Any]:
    """``codec.request_to_dict`` with short keys and without its defaults (``unpack_request`` gives it back)."""
    sports = r["sports"]
    out: dict[str, Any] = {"s": r["start"]}
    if sports != ["run"]:
        out["sp"] = sports
    out["a"] = [_trim([x["weekday"], x["max_minutes"], _hm(x["start"]), None if x["sports"] == sports else x["sports"],
                       x["role"]], (None, None, "core")) for x in r["availability"]]
    if r["capacity"]:
        out["c"] = [_pack_capacity(c) for c in r["capacity"]]
    if r["goals"]:
        out["g"] = [_pack_goal(g) for g in r["goals"]]
    if r["roles"]:
        out["ro"] = [_trim([x["sport"], x["role"], x["share"]], ("main", 0)) for x in r["roles"]]
    for k, short, default in (("duration_weeks", "w", None), ("level", "l", "recreational"),
                              ("recent_weekly_minutes", "m", None), ("strength_sessions", "st", None)):
        if r.get(k, default) != default:
            out[short] = r[k]
    if r.get("hard_days"):        # the fixed hard days: [weekday], [weekday, sport] or [weekday, sport, minutes]
        out["hd"] = [_pack_day(h) for h in r["hard_days"]]
    # The coach's choices (CHOICES): each only when set.
    if r.get("quality_sessions"):
        out["q"] = r["quality_sessions"]
    if r.get("easy_days"):
        out["ed"] = [_pack_day(h) for h in r["easy_days"]]
    if r.get("second_long"):
        out["sl"] = 1
    if r.get("easy_weeks"):
        out["ew"] = list(r["easy_weeks"])
    if r.get("max_weekly_minutes"):
        out["mx"] = r["max_weekly_minutes"]
    return out


def _pack_day(h: dict[str, Any]) -> list[Any]:
    return _trim([h["weekday"], h.get("sport") or "", h.get("minutes") or 0], ("", 0))


def _unpack_day(h: list[Any]) -> dict[str, Any]:
    return {"weekday": h[0], **({"sport": h[1]} if len(h) > 1 and h[1] else {}),
            **({"minutes": h[2]} if len(h) > 2 and h[2] else {})}


def unpack_request(d: dict[str, Any]) -> dict[str, Any]:
    sports = d.get("sp", ["run"])
    slots = []
    for x in d["a"]:
        x = list(x) + [None] * (5 - len(x))
        start = x[2] + ":00" if x[2] and len(x[2]) == 5 else x[2]
        slots.append({"weekday": x[0], "max_minutes": x[1], "sports": list(x[3] or sports), "start": start,
                      "role": x[4] or "core"})
    out = {"format": codec.FORMAT, "start": d["s"], "sports": list(sports), "availability": slots,
           "capacity": [_unpack_capacity(c) for c in d.get("c", [])], "goals": [_unpack_goal(g) for g in d.get("g", [])],
           "duration_weeks": d.get("w"), "level": d.get("l", "recreational"), "recent_weekly_minutes": d.get("m"),
           "roles": [{"sport": x[0], "role": x[1] if len(x) > 1 else "main", "share": x[2] if len(x) > 2 else 0}
                     for x in d.get("ro", [])]}
    if d.get("st"):
        out["strength_sessions"] = d["st"]
    if d.get("hd"):
        out["hard_days"] = [_unpack_day(h) for h in d["hd"]]
    if d.get("q"):
        out["quality_sessions"] = d["q"]
    if d.get("ed"):
        out["easy_days"] = [_unpack_day(h) for h in d["ed"]]
    if d.get("sl"):
        out["second_long"] = True
    if d.get("ew"):
        out["easy_weeks"] = list(d["ew"])
    if d.get("mx"):
        out["max_weekly_minutes"] = d["mx"]
    return out


def pack_targets(ts: dict[str, dict[str, Any]]) -> dict[str, list[Any]]:
    """One zone's targets as a row: the heart rates, the speeds, the flags as bits, the watt and the swim paces."""
    out = {}
    for zone, tg in ts.items():
        flags = sum(1 << i for i, k in enumerate(_FLAGS) if tg[k])
        row = [tg["hr_low"], tg["hr_high"], tg["speed_low_kmh"], tg["speed_high_kmh"], flags,
               tg.get("power_low_w"), tg.get("power_high_w"), tg.get("swim_low_s"), tg.get("swim_high_s")]
        out[zone] = _trim(row, (None, None, 0, None, None, None, None))
    return out


def unpack_targets(d: dict[str, list[Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for zone, row in d.items():
        r = list(row) + [None] * (9 - len(row))
        tg: dict[str, Any] = {"hr_low": r[0], "hr_high": r[1], "speed_low_kmh": r[2], "speed_high_kmh": r[3]}
        tg.update({k: bool((r[4] or 0) >> i & 1) for i, k in enumerate(_FLAGS)})
        if r[5] or r[6]:          # as _target_dict: the watt and the paces only in a target that has them
            tg.update(power_low_w=r[5], power_high_w=r[6])
        if r[7] or r[8]:
            tg.update(swim_low_s=r[7], swim_high_s=r[8])
        out[zone] = tg
    return out


def _default_created(start: str) -> str:
    return f"{start}T00:00:00+00:00"


def _pack(stored: dict[str, Any]) -> dict[str, Any]:
    plan, req = stored["plan"], stored["request"]
    out: dict[str, Any] = {"kind": KIND, "format": CHOICES_FORMAT if has_choices(req) else SHORT_FORMAT,
                           "v": ENGINE_VERSION, "id": plan["id"],
                           "r": pack_request(req)}
    if stored["athlete"]:
        out["n"] = stored["athlete"]
    if plan.get("horizon") is not None:
        out["h"] = plan["horizon"]
    if plan["created"] != _default_created(req["start"]):
        out["c"] = plan["created"]
    built = _build(out, codec.request_from_dict(req))
    x = {k: plan[k] for k in _PLAN_FIELDS if plan.get(k) != built.get(k)}
    if x:
        out["x"] = x
    out["t"] = {sport: pack_targets(ts) for sport, ts in stored["targets"].items()}
    if stored["targets_log"]:
        out["tl"] = [[e["until"], e["sport"], pack_targets(e["targets"])] for e in stored["targets_log"]]
    return out


def _build(short: dict[str, Any], request: Any) -> dict[str, Any]:
    """The stored plan (``codec.plan_to_dict``) the engine builds from a short payload, before ``x``."""
    p = build_plan(request, plan_id=short["id"], created=datetime.fromisoformat(short["c"]) if "c" in short else None)
    h = short.get("h")
    if h is not None:
        p = replace(p, weeks=[w if w.index <= h else sketch_of(w) for w in p.weeks], horizon=h)
    return codec.plan_to_dict(p)


def expand(payload: dict[str, Any]) -> dict[str, Any]:
    """The whole payload (format 1) of a payload of either format. A short one is built with the engine, which
    must be the version that made it (ValueError otherwise, as the app asks for an update)."""
    if payload.get("format") not in SHORT_FORMATS:
        return payload
    if payload.get("v") != ENGINE_VERSION:
        raise ValueError("the plan was made with another engine version")
    req = unpack_request(payload["r"])
    plan = {**_build(payload, codec.request_from_dict(req)), **payload.get("x", {})}
    if plan.get("edits"):           # the athlete's changes, laid on the workouts the engine built (plan.edits)
        plan = codec.plan_to_dict(apply_edits(codec.plan_from_dict(plan, codec.request_from_dict(req))))
    return {"kind": KIND, "format": FORMAT, "athlete": payload.get("n", ""), "request": req, "plan": plan,
            "targets": {sport: unpack_targets(ts) for sport, ts in payload["t"].items()},
            "targets_log": [{"until": u, "sport": s, "targets": unpack_targets(ts)} for u, s, ts in payload.get("tl", [])]}


def has_choices(request: dict[str, Any]) -> bool:
    """True for a request with one of the coach's choices (``CHOICES``) or a fixed hard day with its own length:
    its short payload is format 3, which an app from before the choices turns down instead of misreading."""
    return (any(request.get(k) for k in CHOICES)
            or any(h.get("minutes") for h in request.get("hard_days") or []))


def app_lacks(request: dict[str, Any]) -> bool:
    """True while the guard ``APP_LACKS`` holds and the request has a choice (``has_choices``)."""
    return APP_LACKS and has_choices(request)


def short_plan(stored: dict[str, Any]) -> dict[str, Any] | None:
    """The short payload (format 2, or 3 with the coach's choices) of a whole one from ``stored_plan``, or None when
    the engine would not build the same plan from it (then the link holds the whole plan), or when it holds a
    choice and the app that reads format 3 is not out yet (``app_lacks``)."""
    if app_lacks(stored["request"]):
        return None
    try:
        short = _pack(stored)
        same = expand(json.loads(_json(short))) == json.loads(_json(stored))
    except ValueError:
        return None
    return short if same else None


def link_payload(stored: dict[str, Any]) -> dict[str, Any]:
    """What the link, the QR code and the mail hold: the short payload when it gives the same plan, else the whole."""
    return short_plan(stored) or stored


def _json(stored: dict[str, Any]) -> bytes:
    return json.dumps(stored, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def encode(stored: dict[str, Any]) -> str:
    """The payload as the link's data: deflate (zlib) + base64url without padding."""
    return base64.urlsafe_b64encode(zlib.compress(_json(stored), 9)).rstrip(b"=").decode("ascii")


def decode(data: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    out = json.loads(zlib.decompress(raw).decode("utf-8"))
    if not isinstance(out, dict) or out.get("kind") != KIND:
        raise ValueError("not a Tröskel plan")
    return out


def plan_link(stored: dict[str, Any], base: str = APP_URL) -> str:
    """The link that opens the plan in the app: ``base#p=<data>``."""
    return f"{base}#p={encode(stored)}"


def read_link(link: str) -> dict[str, Any]:
    """The payload of a link from ``plan_link``."""
    frag = urlsplit(link).fragment
    for part in frag.split("&"):
        if part.startswith("p="):
            return decode(part[2:])
    raise ValueError("the link holds no plan")


def plan_file(stored: dict[str, Any]) -> bytes:
    """The payload as the contents of a ``.troskel`` file (readable JSON, UTF-8)."""
    return json.dumps(stored, ensure_ascii=False, indent=1).encode("utf-8") + b"\n"


__all__ = ["APP_LACKS", "APP_URL", "app_lacks", "CHOICES", "CHOICES_FORMAT", "FILE_SUFFIX", "FORMAT", "KIND", "SHORT_FORMAT", "SHORT_FORMATS", "decode",
           "encode", "expand", "has_choices", "link_payload",
           "pack_request", "pack_targets", "plan_file", "plan_link", "read_link", "short_plan", "stored_plan",
           "unpack_request", "unpack_targets", "zone_targets"]
