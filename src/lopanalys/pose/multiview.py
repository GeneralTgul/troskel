"""Two camera views of the same run: independent 2D analyses that share one clock.

Each view has its own source, pose chain and metrics; nothing is calibrated between the cameras.
What they share is time: every live frame is stamped with ``time.perf_counter()`` when it reaches
the program, both recordings keep those stamps in their sidecar files, and so a ground contact seen
from the side and the same contact seen from behind sit at (nearly) the same time.

"Nearly", because a camera's own delay (a phone over Wi-Fi may be a few hundred ms behind a GoPro on
USB) shifts its whole time line. ``match_steps`` therefore estimates that lag from the steps
themselves, the left and right contacts of both views, and pairs the contacts after removing it.
The lag is a measurement of its own: how far behind the second camera is.

Also here: ``balance_second_rate``, which lowers the second view's analysis rate when the computer
cannot keep the main view's analysis up with its camera. No Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

SECOND_VIEW_MAX_FPS = 15.0   # ceiling of the second view's live analysis (its film keeps every frame)
SECOND_VIEW_MIN_FPS = 6.0    # never lower: below this the live steps of the second view get unreliable
MAIN_VIEW_KEEPS_UP = 0.8     # the main analysis should reach this share of its camera's frame rate

Contact = tuple[float, str]  # (time in seconds, "left" | "right")


def balance_second_rate(camera_fps: float, analysis_fps: float, current: float,
                        ceiling: float = SECOND_VIEW_MAX_FPS, floor: float = SECOND_VIEW_MIN_FPS) -> float:
    """The second view's analysis rate for the next period.

    When the main view analyses less than ``MAIN_VIEW_KEEPS_UP`` of what its camera delivers, the
    second view gives way (a quarter less, down to ``floor``). When the main view keeps up with room
    to spare (95 %), the second view gets a little back, up to ``ceiling``.
    """
    if camera_fps <= 0 or analysis_fps <= 0:
        return current
    share = analysis_fps / camera_fps
    if share < MAIN_VIEW_KEEPS_UP:
        return max(floor, round(current * 0.75, 1))
    if share >= 0.95 and current < ceiling:
        return min(ceiling, round(current + 1.0, 1))
    return current


def contacts_of(steps: Iterable[Any], offset_s: float = 0.0) -> list[Contact]:
    """(time, side) of every step (StepMetrics or their dicts), shifted by ``offset_s``, in time order."""
    out: list[Contact] = []
    for s in steps:
        g = s.get if isinstance(s, dict) else (lambda k, s=s: getattr(s, k, None))
        tc, side = g("t_contact"), g("side")
        if tc is None or side is None:
            continue
        out.append((float(tc) + offset_s, str(getattr(side, "value", side))))
    return sorted(out)


@dataclass
class StepMatch:
    """How the contacts of two views fit together."""
    lag_s: float                      # the second view's clock minus the first's (+ = the second is behind)
    matched: int                      # contacts found in both views (same foot, same moment)
    first: int                        # contacts in the first view inside the common time span
    second: int
    spread_ms: float | None           # median distance between the paired contacts after removing the lag
    pairs: list[tuple[float, float, str]] = field(default_factory=list)   # (t first, t second, side)

    @property
    def share(self) -> float:
        n = min(self.first, self.second)
        return self.matched / n if n else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"lag_s": round(self.lag_s, 3), "matched": self.matched, "first": self.first,
                "second": self.second, "spread_ms": None if self.spread_ms is None else round(self.spread_ms, 1),
                "share": round(self.share, 3)}


def _pair(a: Sequence[Contact], b: Sequence[Contact], lag: float, tol: float) -> list[tuple[float, float, str]]:
    """Greedy pairing in time order: each contact of ``a`` with the nearest unused one of ``b`` on the
    same foot within ``tol`` seconds, after moving ``b`` back by ``lag``."""
    pairs: list[tuple[float, float, str]] = []
    for side in ("left", "right"):
        xs = [t for t, s in a if s == side]
        ys = [t for t, s in b if s == side]
        j = 0
        for x in xs:
            while j < len(ys) and ys[j] - lag < x - tol:
                j += 1
            best = None
            for k in (j, j + 1):
                if k < len(ys) and abs(ys[k] - lag - x) <= tol and (best is None or
                                                                   abs(ys[k] - lag - x) < abs(ys[best] - lag - x)):
                    best = k
            if best is not None:
                pairs.append((x, ys[best], side))
                j = best + 1
    return sorted(pairs)


def match_steps(first: Sequence[Contact], second: Sequence[Contact], *, max_lag_s: float = 0.3,
                tol_s: float = 0.06, step_s: float = 0.01) -> StepMatch:
    """Pair the ground contacts of two views on the shared clock.

    The lag is searched in ±``max_lag_s`` (less than half a stride, so the same foot cannot be mistaken
    for its next contact) as the shift that pairs the most contacts, then refined to the median of the
    paired differences. The feet are what makes the lag unambiguous: two views that named left and
    right the other way round throughout would read as a lag of half a stride.
    """
    a, b = sorted(first), sorted(second)
    if not a or not b:
        return StepMatch(0.0, 0, len(a), len(b), None)
    lo, hi = max(a[0][0], b[0][0]) - max_lag_s, min(a[-1][0], b[-1][0]) + max_lag_s
    a = [c for c in a if lo <= c[0] <= hi]
    b = [c for c in b if lo <= c[0] <= hi]
    if not a or not b:
        return StepMatch(0.0, 0, len(a), len(b), None)

    def search(x: Sequence[Contact], y: Sequence[Contact]) -> tuple[float, list[tuple[float, float, str]]]:
        best_lag, best = 0.0, []
        n = int(round(max_lag_s / step_s))
        for i in sorted(range(-n, n + 1), key=abs):          # small lags first: they win a tie
            lag = i * step_s
            pairs = _pair(x, y, lag, tol_s)
            if len(pairs) > len(best):
                best_lag, best = lag, pairs
        if best:
            d = sorted(q - p for p, q, _ in best)
            best_lag = d[len(d) // 2]
            best = _pair(x, y, best_lag, tol_s)
        return best_lag, best

    lag, pairs = search(a, b)
    spread = None
    if pairs:
        d = sorted(abs(q - lag - p) for p, q, _ in pairs)
        spread = d[len(d) // 2] * 1000.0
    return StepMatch(lag, len(pairs), len(a), len(b), spread, pairs)


def match_analyses(first: dict[str, Any] | None, second: dict[str, Any] | None,
                   offset_s: float = 0.0) -> StepMatch | None:
    """``match_steps`` for two stored analyses (GaitAnalysis.to_dict()). ``offset_s`` is the second
    film's start minus the first's on the shared clock (each analysis counts time from its own film)."""
    if not first or not second:
        return None
    a = contacts_of(first.get("steps") or [])
    b = contacts_of(second.get("steps") or [], offset_s)
    if not a or not b:
        return None
    return match_steps(a, b)
