"""Cadence from the heart rate strap (RSC) before the camera's.

A strap with Running Speed and Cadence (Garmin HRM-Pro/HRM-Pro Plus) counts every step from the
torso's movement, which is more exact than counting foot contacts in a 25-30 fps video. When the
1 Hz samples of a test carry the strap's cadence, it replaces the camera's in the gait analysis
that the results view and the report show, and the cadence finding is judged on it. The source is
kept in ``GaitSummary.cadence_source`` so every place can say where the number comes from.
"""
from __future__ import annotations

import statistics
from typing import Any, Iterable, Mapping

from lopanalys.core.i18n import t

MIN_RUNNING_KMH = 3.0      # samples at walking speed or standing still do not count
MIN_SAMPLES = 10           # at least 10 s of strap cadence


def strap_cadence(samples: Iterable[Any], t_from: float | None = None, t_to: float | None = None) -> float | None:
    """Median strap cadence (steps/min) over the running samples, or None without enough of them."""
    vals = []
    for s in samples:
        if getattr(s, "cadence_source", None) != "strap" or not getattr(s, "cadence_spm", None):
            continue
        sp = getattr(s, "speed_kmh", None)
        if sp is not None and sp < MIN_RUNNING_KMH:
            continue
        ts = getattr(s, "t_s", None)
        if ts is not None and ((t_from is not None and ts < t_from) or (t_to is not None and ts > t_to)):
            continue
        vals.append(float(s.cadence_spm))
    return float(statistics.median(vals)) if len(vals) >= MIN_SAMPLES else None


def cadence_label(source: str | None, key: str) -> str:
    """The localised name of the cadence metric, with "(pulsbandet)" when the strap gave it."""
    base = t(key)
    return t("assessments.cadence.strap_label", name=base) if source == "strap" else base


def with_strap_cadence(gait: Mapping[str, Any] | Any, samples: Iterable[Any],
                       height_cm: float | None = None) -> Any:
    """``gait`` (a ``GaitAnalysis`` or its dict) with the strap's cadence when the samples have it.

    Returns a new dict (the input is not changed), or ``gait`` itself when there is no strap
    cadence. The summary's cadence, the cadence range (value and level) and the cadence finding
    are replaced; the camera's value is kept as ``summary["cadence_camera_spm"]``.
    """
    from lopanalys.pose.findings import evaluate, reference_ranges
    from lopanalys.pose.types import GaitAnalysis, GaitSummary

    if gait is None:
        return None
    cad = strap_cadence(samples)
    if cad is None:
        return gait
    if hasattr(gait, "to_dict"):
        d = gait.to_dict()
    elif isinstance(gait, Mapping):
        d = dict(gait)
    else:
        return gait          # some other analysis-like object: shown as it is
    summ = d.get("summary")
    if not summ:
        return gait
    if not isinstance(summ, Mapping):
        summ = summ.to_dict() if hasattr(summ, "to_dict") else vars(summ)
    summ = dict(summ)
    if summ.get("cadence_source") != "strap":
        summ["cadence_camera_spm"] = summ.get("cadence_spm")
    summ["cadence_spm"], summ["cadence_source"] = cad, "strap"
    d["summary"] = summ
    view, speed = d.get("view"), d.get("speed_kmh") if d.get("speed_kmh") is not None else summ.get("speed_kmh")
    height = height_cm if height_cm is not None else d.get("height_cm")
    # the cadence range, judged on the strap's value (analyses without stored ranges are judged
    # on the summary by the report anyway)
    ranges = dict(d.get("ranges") or {})
    r = reference_ranges(view, height, speed).get("cadence_spm") if ranges else None
    if r is not None:
        sev = r.classify(cad)
        ranges["cadence_spm"] = dict(r.to_dict(), value=cad, severity=sev.value if sev is not None else None)
        d["ranges"] = ranges
    # the cadence finding, judged again; it keeps the key frame the old one pointed at
    findings = [dict(f) for f in d.get("findings") or []]
    old = next((f for f in findings if f.get("code") == "cadence"), None)
    try:
        a = GaitAnalysis.from_dict(d)
        new = next((f for f in evaluate(GaitSummary.from_dict(summ), a.steps, view or "side_left", height, speed)
                    if f.code == "cadence"), None)
    except (KeyError, ValueError, TypeError):
        new = None
    if new is not None:
        nd = new.to_dict()
        nd["title"] = cadence_label("strap", "pose.finding.cadence.title")
        if old is not None:
            nd["frame_index"] = old.get("frame_index")
            findings[findings.index(old)] = nd
        else:
            findings.append(nd)
        order = {"fix": 0, "watch": 1, "ok": 2}
        findings.sort(key=lambda f: order.get(str(f.get("severity")), 3))
        d["findings"] = findings
    return d
