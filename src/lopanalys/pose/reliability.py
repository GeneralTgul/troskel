"""Which gait values hold at a given frame rate: shown, marked uncertain, or left out.

The principle: the tests should be as good as possible, not as many half-good tests
as possible. A value the frame rate cannot carry is not shown as if it were exact.

Every metric has two limits on the achieved frame rate of the clip:

* ``show_fps``: below it the value is left out of the summary, so no finding, key frame or report row
  is built on it (the per-step values stay in ``steps`` for the review tools);
* ``sure_fps``: below it the value is shown but marked uncertain.

**The limits are provisional.** They come from the synthetic runner (``pose.synthetic``) analysed at
120 fps and decimated to 60, 30 and 24 fps (2026-11-14): cadence, vertical oscillation, trunk lean,
knee flexion at midstance and overstride stayed within 1 unit (overstride 2 cm); contact and flight
time within 6 ms on average; but the angles at landing and toe-off moved with the frame (knee flexion
at contact +5 deg at 30 fps and +10 deg at 24 fps, shin angle -3 to -6 deg, hip extension at toe-off
-3 deg, foot angle -2 deg), because the landing falls between two frames. The synthetic runner has no
motion blur, so real video is worse, not better. So: everything timed by the landing or the toe-off,
and every angle read in that frame, is marked uncertain below 50 fps, and left out below 20 fps
(the second view's live rate can fall to 6 fps). The limits are to be set from a real run in 120 fps
with HRM-Pro+ (``tools/jamfor_garmin.py``) and then this docstring rewritten.

``decimate`` gives the same clip at a lower rate (every n-th pose), for the downsampling test.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from lopanalys.pose.types import GaitSummary, Pose

OK, UNCERTAIN, HIDDEN = "ok", "uncertain", "hidden"


@dataclass(frozen=True)
class Requirement:
    metric: str
    show_fps: float      # below: left out
    sure_fps: float      # below: shown, marked uncertain
    basis: str           # where the limits come from


_SYN = "synthetic runner 120 -> 60/30/24 fps, 2026-11-14 (provisional until the HRM-Pro+ check)"
_TIMING = "landings between frames; real blur unknown: 60 fps recommended since 2026-11-14 (provisional)"

REQUIREMENTS: dict[str, Requirement] = {r.metric: r for r in (
    # robust: medians of positions or of angles at midstance
    Requirement("cadence_spm", 0, 20, _SYN),
    Requirement("vertical_oscillation_cm", 0, 20, _SYN),
    Requirement("trunk_lean_deg", 0, 20, _SYN),
    Requirement("knee_flex_midstance_deg", 0, 20, _SYN),
    Requirement("overstride_cm", 0, 20, _SYN),
    Requirement("pelvic_drop_deg", 0, 20, _SYN),
    Requirement("knee_valgus_deg", 0, 20, _SYN),
    Requirement("step_width_cm", 0, 20, _SYN),
    Requirement("elbow_angle_deg", 0, 20, _SYN),
    Requirement("arm_swing_deg", 0, 20, _SYN),
    Requirement("arm_cross_cm", 0, 20, _SYN),
    # timed by the landing and the toe-off: shown, uncertain below 50 fps
    Requirement("ground_contact_ms", 20, 50, _TIMING),
    Requirement("flight_ms", 20, 50, _TIMING),
    Requirement("duty_factor_pct", 20, 50, _TIMING),
    # angles in the frame of landing or toe-off: uncertain below 50 fps (whether they are to be left
    # out instead is decided from the HRM-Pro+ run)
    Requirement("knee_flex_contact_deg", 20, 50, _SYN),
    Requirement("shin_angle_deg", 20, 50, _SYN),
    Requirement("foot_angle_deg", 20, 50, _SYN),
    Requirement("hip_extension_toe_off_deg", 20, 50, _SYN),
    Requirement("heel_whip_deg", 20, 50, _SYN),
)}

# summary fields that follow another metric (foot_strike is read from foot_angle_deg)
FOLLOWS = {"foot_strike": "foot_angle_deg"}


def level(metric: str, fps: float | None) -> str:
    """OK, UNCERTAIN or HIDDEN for ``metric`` at the achieved ``fps`` (None = unknown: OK, as before)."""
    r = REQUIREMENTS.get(FOLLOWS.get(metric, metric))
    if r is None or fps is None:
        return OK
    if fps < r.show_fps:
        return HIDDEN
    if fps < r.sure_fps:
        return UNCERTAIN
    return OK


def levels(fps: float | None) -> dict[str, str]:
    """Every metric that is not OK at ``fps``: metric -> UNCERTAIN | HIDDEN."""
    out = {m: level(m, fps) for m in list(REQUIREMENTS) + list(FOLLOWS)}
    return {m: v for m, v in out.items() if v != OK}


def apply(summary: GaitSummary | None, fps: float | None) -> tuple[GaitSummary | None, dict[str, str]]:
    """The summary with the HIDDEN metrics set to None (and their asymmetry removed), and the levels."""
    lv = levels(fps)
    if summary is None:
        return None, lv
    hidden = {m for m, v in lv.items() if v == HIDDEN}
    if not hidden:
        return summary, lv
    names = set(GaitSummary.__dataclass_fields__)
    kw = {m: None for m in hidden if m in names}
    kw["asymmetry"] = {k: v for k, v in summary.asymmetry.items() if k not in hidden}
    return replace(summary, **kw), lv


# finding code -> the summary values it is judged on (asymmetry: the one it reports, found by value)
FINDING_METRICS: dict[str, tuple[str, ...]] = {
    "overstride": ("overstride_cm", "shin_angle_deg"), "knee_stiff_contact": ("knee_flex_contact_deg",),
    "knee_flex_low": ("knee_flex_midstance_deg",), "knee_flex_high": ("knee_flex_midstance_deg",),
    "foot_strike": ("foot_angle_deg",), "hip_ext_low": ("hip_extension_toe_off_deg",),
    "heel_whip": ("heel_whip_deg",), "cadence": ("cadence_spm",), "vertical_osc": ("vertical_oscillation_cm",),
    "trunk": ("trunk_lean_deg",), "trunk_upright": ("trunk_lean_deg",), "trunk_forward": ("trunk_lean_deg",),
    "pelvic_drop": ("pelvic_drop_deg",), "knee_valgus": ("knee_valgus_deg",), "step_width": ("step_width_cm",),
    "elbow": ("elbow_angle_deg",), "arm_cross": ("arm_cross_cm",),
}


def mark(findings: list, lv: dict[str, str], summary: GaitSummary | None) -> list:
    """Sets ``Finding.uncertain`` on the findings judged on an UNCERTAIN value (in place; returned)."""
    if not lv:
        return findings
    for f in findings:
        ms: tuple[str, ...] = FINDING_METRICS.get(f.code, ())
        if f.code == "asymmetry" and summary is not None and f.value is not None:
            ms = tuple(k for k, v in summary.asymmetry.items() if v is not None and abs(abs(v) - f.value) < 0.06)
        f.uncertain = any(lv.get(m) == UNCERTAIN for m in ms)
    return findings


def _names(metrics: Sequence[str]) -> str:
    from lopanalys.core.i18n import t
    words = [t(f"pose.rel.{m}") for m in metrics]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} {t('pose.rel.and')} {words[-1]}"


def warnings(fps: float | None) -> list[str]:
    """The coach's warnings for ``fps``: which values are left out and which are uncertain."""
    from lopanalys.core.i18n import t
    from lopanalys.pose.findings import num
    lv = levels(fps)
    out: list[str] = []
    for kind, key in ((HIDDEN, "pose.warn.fps_hidden"), (UNCERTAIN, "pose.warn.low_fps")):
        ms = [m for m in REQUIREMENTS if lv.get(m) == kind]
        if ms:
            need = max(REQUIREMENTS[m].show_fps if kind == HIDDEN else REQUIREMENTS[m].sure_fps for m in ms)
            names = _names(ms)
            out.append(t(key, fps=num(fps), need=num(need), metrics=names[:1].upper() + names[1:]
                         if kind == UNCERTAIN else names))
    return out


def min_fps_for_all(metrics: Sequence[str] | None = None) -> float:
    """The frame rate at which every metric (or the given ones) is OK."""
    ms = metrics or list(REQUIREMENTS)
    return max(REQUIREMENTS[FOLLOWS.get(m, m)].sure_fps for m in ms if FOLLOWS.get(m, m) in REQUIREMENTS)


def decimate(poses: Sequence[Pose | None], every: int, offset: int = 0,
             renumber: bool = True) -> list[Pose | None]:
    """Every ``every``-th pose from ``offset`` on: the clip as a camera with 1/every the rate would have
    given it (same times, so the analysis measures the lower rate itself). ``renumber=False`` keeps the
    source frame numbers, so key frames and review sheets still read the right frame of the film."""
    if every < 1:
        raise ValueError("every must be >= 1")
    out: list[Pose | None] = []
    for j, p in enumerate(poses[offset::every]):
        fi = j if renumber else p.frame_index if p is not None else 0
        out.append(None if p is None else Pose(p.t, fi, p.xy, p.visibility, p.width, p.height))
    return out
