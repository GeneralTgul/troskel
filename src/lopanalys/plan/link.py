"""A plan for the athlete's app: one JSON payload, sent as a link, a QR code or a ``.troskel`` file. No Qt.

The payload is the stored plan (``codec.request_to_dict`` and ``codec.plan_to_dict``) with what the app
cannot work out by itself: the athlete's name and every zone's targets (heart rate, speed), resolved here
from the tests the plan is built on. The app has no zone systems, so it shows the targets as they were
when the coach sent the plan; a retest sends a new link.

The link is ``APP_URL#p=<data>``: the JSON compressed with deflate (zlib) and coded as base64url without
padding. A browser never sends the fragment (``#...``) to a server, so the plan stays on the phone.
The file is the same JSON as UTF-8 text.
"""
from __future__ import annotations

import base64
import json
import zlib
from typing import Any
from urllib.parse import urlsplit

from . import codec
from .models import Capacity, Plan
from .resolve import Target, targets

APP_URL = "https://generaltgul.github.io/troskel/app/"
KIND = "troskel.plan"
FORMAT = 1
FILE_SUFFIX = ".troskel"
_TARGET_FIELDS = ("hr_low", "hr_high", "speed_low_kmh", "speed_high_kmh", "estimated", "speed_low_approx",
                  "speed_high_approx", "speed_from_hr")


# The bike's and the SkiErg's watt (#666, step 2): only in a target that has them, so a running plan's payload
# is as before and an app without the fields reads it as before.
_POWER_FIELDS = ("power_low_w", "power_high_w")


def _target_dict(tg: Target) -> dict[str, Any]:
    d = {k: getattr(tg, k) for k in _TARGET_FIELDS}
    if tg.power_low_w or tg.power_high_w:
        d.update({k: getattr(tg, k) for k in _POWER_FIELDS})
    return d


def zone_targets(capacity: Capacity | None, system: str, sport: str | None = None) -> dict[str, dict[str, Any]]:
    """Zone code -> its targets as a dict, for workouts in ``sport`` (None: the test's); {} when nothing is known."""
    return {z: _target_dict(tg) for z, tg in targets(capacity, system, sport).items()}


def _targets(plan: Plan) -> dict[str, dict[str, dict[str, Any]]]:
    """Targets per sport: each test's, and a sport without its own test with the test it borrows (skis: the
    SkiErg's heart rates, without its watt)."""
    out = {c.sport: zone_targets(c, plan.zone_system) for c in plan.request.capacity}
    for sport in plan.request.plan_sports:
        if sport not in out and plan.request.capacity_for(sport).anchors is not None:
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


__all__ = ["APP_URL", "FILE_SUFFIX", "FORMAT", "KIND", "decode", "encode", "plan_file", "plan_link", "read_link",
           "stored_plan", "zone_targets"]
