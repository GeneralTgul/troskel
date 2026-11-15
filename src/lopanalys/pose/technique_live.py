"""The bike fit's and the double poling's most important values live on the TV, while the athlete pedals
or poles.

About once a second the live analyzer hands the last ``window_s(kind)`` seconds of poses to
``summarize``. That runs the same chain as the analysis after the test (``pose.bike.analysis`` and
``pose.ski.analysis``: the revolutions or strokes, the angles per revolution or stroke), and takes the
median of each value over the last ``LAST_CYCLES`` revolutions or strokes. No formula is repeated here:
the values, their ranges and their levels are those of the report. Until ``MIN_CYCLES`` revolutions or
strokes are in the window a value is None, and the TV shows "–".

The live values steer nothing and are not saved: the analysis of the whole film after the test is the
result.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from lopanalys.pose import technique as tq
from lopanalys.pose.series import is_side_view
from lopanalys.pose.technique import Ref
from lopanalys.pose.types import Pose, Side, View

LAST_CYCLES = 6       # the median over this many of the newest revolutions or strokes
MIN_CYCLES = 3        # fewer than this: no value yet

# The tiles per test and view, in order: (live key, metric in the analysis' rows, the side for a
# front view). The front view of a bike fit has no angles: the knees' track per leg.
METRICS: dict[tuple[str, str], list[tuple[str, str, str | None]]] = {
    (tq.BIKEFIT, "side"): [("knee_flex_bottom_deg", "knee_flex_bottom_deg", None),
                           ("hip_angle_top_deg", "hip_angle_top_deg", None),
                           ("trunk_angle_deg", "trunk_angle_deg", None),
                           ("elbow_flex_deg", "elbow_flex_deg", None)],
    (tq.BIKEFIT, "front"): [("knee_lateral_left_cm", "knee_lateral_cm", Side.LEFT.value),
                            ("knee_lateral_right_cm", "knee_lateral_cm", Side.RIGHT.value)],
    (tq.SKI, "side"): [("elbow_angle_start_deg", "elbow_angle_start_deg", None),
                       ("trunk_lean_end_deg", "trunk_lean_end_deg", None),
                       ("hip_flex_end_deg", "hip_flex_end_deg", None),
                       ("knee_flex_end_deg", "knee_flex_end_deg", None)],
}


def _group(kind: str, view: View | str) -> tuple[str, str]:
    return kind, "side" if is_side_view(view) else "front"


def metrics(kind: str, view: View | str) -> list[str]:
    """The live keys shown for this test and view (empty when there are none, e.g. double poling from the front)."""
    return [k for k, _, _ in METRICS.get(_group(kind, view), [])]


def window_s(kind: str) -> float:
    """Seconds of poses each live analysis looks at: room for ``LAST_CYCLES`` at a slow cadence or stroke rate."""
    return 20.0 if kind == tq.SKI else 12.0


def refs(kind: str, view: View | str, fps: float | None = None) -> dict[str, Ref]:
    """live key -> the ``Ref`` the report judges that value with."""
    if kind == tq.BIKEFIT:
        from lopanalys.pose.bike.analysis import refs as bike_refs
        R = bike_refs(View(view))
    else:
        from lopanalys.pose.ski.analysis import refs as ski_refs
        R = ski_refs(fps)
    return {key: R[metric] for key, metric, _ in METRICS.get(_group(kind, view), []) if metric in R}


@dataclass
class TechniqueLive:
    """The rolling values of one view: per live key the median over the newest revolutions or strokes."""
    kind: str
    view: str
    t_end: float
    cycles: int = 0                                         # revolutions or strokes in the window
    values: dict[str, float | None] = field(default_factory=dict)
    status: dict[str, str | None] = field(default_factory=dict)   # "ok" | "watch" | "fix" | None
    cadence: float | None = None                            # rpm or strokes/min from the film

    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


def summarize(kind: str, poses: Sequence[Pose | None], times: Sequence[float], height_cm: float | None,
              view: View | str) -> TechniqueLive:
    """The live values from a window of poses, through the analysis after the test."""
    v = View(view)
    out = TechniqueLive(kind, v.value, float(times[-1]) if len(times) else 0.0)
    spec = METRICS.get(_group(kind, v), [])
    if not spec or sum(p is not None for p in poses) < 20:
        out.values = {k: None for k, _, _ in spec}
        return out
    if kind == tq.BIKEFIT:
        from lopanalys.pose.bike.analysis import analyze_core
    else:
        from lopanalys.pose.ski.analysis import analyze_core  # type: ignore[assignment]
    a = analyze_core(poses, height_cm, v, times=times).analysis
    rows = a.cycles or []
    out.cycles = len(rows)
    s = a.summary or {}
    out.cadence = s.get("cadence_rpm") if kind == tq.BIKEFIT else s.get("stroke_rate_spm")
    R = refs(kind, v, a.fps)
    for key, metric, side in spec:
        mine = [r for r in rows if side is None or r.get("side") == side]
        vals = [r.get(metric) for r in mine[-LAST_CYCLES:]]
        have = [x for x in vals if x is not None]
        val = tq.median(have) if len(have) >= MIN_CYCLES else None
        out.values[key] = val
        sev = R[key].classify(val) if key in R else None
        out.status[key] = sev.value if sev is not None else None
    return out
