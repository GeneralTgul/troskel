"""Tests run with a simulated treadmill or heart rate outside demo mode ("Övning").

The simulators are offered last in the wizard, clearly marked and never preselected, so the coach
can try the program without hardware. Such a test is marked in ``Session.result["simulated"]`` and
the operator page, the result page and the report say so, so it is never taken for a real test.
Demo mode is all simulated anyway and is not marked. No Qt here.
"""
from __future__ import annotations

from typing import Any

from lopanalys.core.i18n import t

TREADMILL = "treadmill"
HEART_RATE = "heart_rate"


def from_plan(treadmill_mode: str | None, hr_device: Any) -> list[str]:
    """The simulated devices of a test plan: [] for a real test."""
    out = []
    if treadmill_mode == "sim":
        out.append(TREADMILL)
    if hr_device == "sim" or getattr(hr_device, "address", None) == "sim":
        out.append(HEART_RATE)
    return out


def of_session(session: Any) -> list[str]:
    """The simulated devices a saved test ran with ([] for a real test or one from demo mode)."""
    res = getattr(session, "result", None) or {}
    sim = res.get("simulated") if isinstance(res, dict) else None
    return [s for s in sim if s in (TREADMILL, HEART_RATE)] if isinstance(sim, list) else []


def label(devices: list[str], sport: Any = None) -> str:
    """'Övning: simulerad puls', 'Övning: simulerat band' (or trainer, or SkiErg, by the test's
    sport), both, or '' for a real test."""
    sp = str(getattr(sport, "value", sport) or "run")

    def key(d: str) -> str:
        return f"report.simulated.{sp}" if d == TREADMILL and sp != "run" else f"report.simulated.{d}"
    return " · ".join(t(key(d)) for d in devices)
