"""Which gait values hold at a given frame rate: shown, marked uncertain, or left out.

The principle: the tests should be as good as possible, not as many half-good tests
as possible. A value the frame rate cannot carry is not shown as if it were exact.

Every metric has two limits on the achieved frame rate of the clip:

* ``show_fps``: below it the value is left out of the summary, so no finding, key frame or report row
  is built on it (the per-step values stay in ``steps`` for the review tools);
* ``sure_fps``: below it the value is shown but marked uncertain.

**The limits come from a real run** (2026-11-15): a treadmill run of about six minutes in three
speeds, filmed from the left side with a GoPro in 1080p, 100 fps and the linear lens, analysed at 100
fps and decimated to 50, 33, 25 and 20 fps (``tools/jamfor_garmin.py --every 1,2,3,4,5``), with the
median per 30 s window compared with the full rate (12 windows, tolerances in ``TOLERANCE`` there):

* within tolerance at every rate down to 20 fps: vertical oscillation (0.4 cm), trunk lean (0.2 deg)
  and cadence (2.2 spm at most, bias -2 spm or less); knee flexion at midstance down to 25 fps (2.9
  deg; 5.6 deg at 20 fps);
* within tolerance at 50 fps but not below: overstride (5 cm at 33 fps, 10 at 25), shin angle and knee
  flexion at contact (6-7 deg at 33 fps, 10 at 25, 17-18 at 20);
* off already at 50 fps: ground contact time (+54 ms at 50 fps, +93 at 33, +116 at 25), hip extension
  at toe-off (+4.5 deg) and foot angle (-2.6 deg at 50 fps, -14 at 33, -20 at 25, so the foot strike
  flips). Flight time and duty factor are timed by the same landings and toe-offs.

So a value is sure from the lowest tested rate that held, and left out below the rate where its error
passed about twice the tolerance. The contact values and the two angles held at no lower rate, so they
are sure only at the film's own 100 fps, which is the highest tested and not itself checked: the check
of the absolute values against the HRM-Pro+ strap (ground contact, vertical oscillation, cadence) is
still to come. The values not in the run (front-view values, arm values, heel whip) keep the limits
from the synthetic runner (``pose.synthetic``, 120 fps decimated to 60/30/24, 2026-11-14), which has
no motion blur, so real video is worse, not better.

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


_SYN = "synthetic runner 120 -> 60/30/24 fps, 2026-11-14 (not in the real run)"
_RUN = "real run, GoPro 100 fps decimated to 50/33/25/20, 2026-11-15 (absolute check against the strap to come)"

REQUIREMENTS: dict[str, Requirement] = {r.metric: r for r in (
    # robust: medians of positions or of angles at midstance
    Requirement("cadence_spm", 0, 20, _RUN),
    Requirement("vertical_oscillation_cm", 0, 20, _RUN),
    Requirement("trunk_lean_deg", 0, 20, _RUN),
    Requirement("knee_flex_midstance_deg", 0, 25, _RUN),
    Requirement("pelvic_drop_deg", 0, 20, _SYN),
    Requirement("knee_valgus_deg", 0, 20, _SYN),
    Requirement("step_width_cm", 0, 20, _SYN),
    Requirement("elbow_angle_deg", 0, 20, _SYN),
    Requirement("arm_swing_deg", 0, 20, _SYN),
    Requirement("arm_cross_cm", 0, 20, _SYN),
    # read in the frame of landing: sure from 50 fps, left out below 30
    Requirement("overstride_cm", 30, 50, _RUN),
    Requirement("knee_flex_contact_deg", 30, 50, _RUN),
    Requirement("shin_angle_deg", 30, 50, _RUN),
    # timed by the landing and the toe-off, or read in those frames: off already at 50 fps
    Requirement("ground_contact_ms", 50, 100, _RUN),
    Requirement("flight_ms", 50, 100, _RUN),
    Requirement("duty_factor_pct", 50, 100, _RUN),
    Requirement("foot_angle_deg", 50, 100, _RUN),
    Requirement("hip_extension_toe_off_deg", 20, 100, _RUN),
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
