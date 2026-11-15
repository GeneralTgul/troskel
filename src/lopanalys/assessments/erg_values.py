"""What the coach writes in by hand on a test in watt: a stage's mean power when the ergometer did not
give it (a PM5 that dropped out, manual mode), and the SkiErg's drag factor.

Both live in ``session.result`` next to what the runner noted, so the result page, the report and the
FIT file read them from one place:

- ``result["stage_stats"][i]["manual"]``: the mean power typed in for the stage (W). The stage's
  ``StepRecord.speed_kmh`` takes the same value, so the thresholds are calculated on it.
  ``"auto"`` keeps the load the stage had before, so clearing the entry brings it back.
- ``result["drag_factor"]``: ``{"value": int, "source": "pm5" | "manual"}``. The runner notes the
  median of what the PM5 reported during the working stages; the coach can write it in or correct it.

No Qt, no i18n.
"""
from __future__ import annotations

from typing import Any

from lopanalys.core.models import Session, StepRecord

POWER_RANGE = (1.0, 2500.0)      # W: a stage's mean power outside this is a typing error
DRAG_RANGE = (30, 300)           # PM5 drag factor: a SkiErg is usually 80-150, a rower 90-220
TARGET_BAND = 0.05               # as in the runner: more than 5 % from the target is marked


def _stats(session: Session) -> list[dict[str, Any]]:
    session.result = dict(session.result or {})
    stats = [dict(x) for x in session.result.get("stage_stats") or []]
    session.result["stage_stats"] = stats
    return stats


def stage_stat(session: Session | None, index: int) -> dict[str, Any]:
    """The noted values of one stage ({} when nothing was noted)."""
    for x in ((session.result or {}).get("stage_stats") or []) if session is not None else []:
        if x.get("index") == index:
            return x
    return {}


def is_manual(session: Session | None, index: int) -> bool:
    return stage_stat(session, index).get("manual") is not None


def set_manual_load(session: Session, record: StepRecord, value: float | None) -> None:
    """Write in (or, with None, take back) a stage's mean power. Raises ValueError outside POWER_RANGE."""
    stats = _stats(session)
    st = next((x for x in stats if x.get("index") == record.index), None)
    if st is None:
        st = {"index": record.index, "target": None, "measured": None, "cadence": None,
              "self_paced": False, "free": False}
        stats.append(st)
        stats.sort(key=lambda x: x.get("index", 0))
    if value is None:
        if st.get("manual") is not None:
            record.speed_kmh = float(st.get("auto", record.speed_kmh))
        st.pop("manual", None)
        st.pop("auto", None)
        _band(st, record.speed_kmh)
        return
    v = float(value)
    if not POWER_RANGE[0] <= v <= POWER_RANGE[1]:
        raise ValueError(v)
    if st.get("manual") is None:
        st["auto"] = record.speed_kmh
    st["manual"] = round(v, 1)
    record.speed_kmh = st["manual"]
    _band(st, record.speed_kmh)


def _band(st: dict[str, Any], load: float) -> None:
    """Whether the stage's power (as it now stands) was within the band around its target."""
    target = st.get("target")
    if target and not st.get("free") and (st.get("self_paced") or st.get("manual") is not None):
        st["in_band"] = abs(load - target) <= TARGET_BAND * target
    else:
        st.pop("in_band", None)


def drag_factor(session: Session | None) -> dict[str, Any] | None:
    """``{"value", "source"}`` of the test's drag factor, or None."""
    d = (session.result or {}).get("drag_factor") if session is not None else None
    return d if isinstance(d, dict) and d.get("value") else None


def set_drag_factor(session: Session, value: int | None, source: str = "manual") -> None:
    """Write in (or, with None, take away) the drag factor. Raises ValueError outside DRAG_RANGE."""
    session.result = dict(session.result or {})
    if value is None:
        session.result.pop("drag_factor", None)
        return
    v = int(round(float(value)))
    if not DRAG_RANGE[0] <= v <= DRAG_RANGE[1]:
        raise ValueError(v)
    session.result["drag_factor"] = {"value": v, "source": source}


def median_int(values: list[int]) -> int | None:
    """The middle value (the lower of the two middle ones), None for no values."""
    vs = sorted(v for v in values if v)
    return vs[(len(vs) - 1) // 2] if vs else None


__all__ = ["DRAG_RANGE", "POWER_RANGE", "drag_factor", "is_manual", "median_int", "set_drag_factor",
           "set_manual_load", "stage_stat"]
