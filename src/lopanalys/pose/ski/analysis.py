"""Double poling on the Concept2 SkiErg, filmed from the side: the measured values and the feedback.

The technique gets feedback with marks in the pictures already at the
first test, as running does, and every value is compared with the athlete's previous test.

**Every range in degrees here is a rule of thumb.** The research is on snow and roller skis, not on
the SkiErg, and gives directions, not angles (docs/sporter/bikefit-och-stakteknik.md):
  [Holmberg05]  Holmberg HC et al. Biomechanical analysis of double poling in elite cross-country
                skiers. Med Sci Sports Exerc 2005;37:807-818: the fastest skiers had a smaller minimum
                elbow and hip angle (more flexion), faster flexion and a relatively longer recovery.
  [Concept2]    Concept2, "5 Common Errors on the SkiErg" and the technique page: hands start just
                above eye level, hinge at the hips before the knees, finish at the thighs, and the
                recovery takes about twice as long as the drive.
So each range is marked *erfarenhetsvärde* wherever it is shown, and the report leans on the change
against the previous test.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from lopanalys.core.i18n import t
from lopanalys.pose import technique as tq
from lopanalys.pose.findings import num
from lopanalys.pose.metrics import estimate_scale
from lopanalys.pose.series import PoseSeries, interior_angle, is_side_view, lean_from_vertical
from lopanalys.pose.ski.strokes import Stroke, StrokeCycles, detect
from lopanalys.pose.technique import EXPERIENCE, NO_VERDICT, Ref, TechniqueAnalysis
from lopanalys.pose.types import Finding, Pose, Severity, Side, View

log = logging.getLogger(__name__)

KIND = tq.SKI
UNC_ANGLE = 5.0       # deg, hip and knee from the side
UNC_ARM = 6.0         # deg, the elbow
MIN_FPS = 50.0        # the drive takes about 0.6 s: at 30 fps the start and the finish fall between frames
ONSET = 0.2           # a joint "starts" when it has moved this share of its travel in the drive

_SRC_C2 = "Concept2"
_SRC_HOLMBERG = "Holmberg et al. 2005"


def refs(fps: float | None = None) -> dict[str, Ref]:
    """metric -> ``Ref``. ``fps`` sets how well the timing can be measured."""
    spm = t("technique.unit.strokes")
    frame_ms = 1000.0 / fps if fps else 17.0
    out = [
        Ref("stroke_rate_spm", spm, basis=NO_VERDICT),
        Ref("drive_ms", "ms", basis=NO_VERDICT, uncertainty=round(frame_ms)),
        Ref("recovery_ms", "ms", basis=NO_VERDICT, uncertainty=round(frame_ms)),
        Ref("recovery_ratio", "", ok_lo=1.2, fix_lo=0.9, decimals=1, basis=EXPERIENCE, source=_SRC_C2, uncertainty=0.1),
        Ref("hand_height_start_cm", "cm", ok_lo=0, ok_hi=20, fix_lo=-15, fix_hi=35, basis=EXPERIENCE,
            source=_SRC_C2, uncertainty=4.0),
        Ref("elbow_angle_start_deg", ok_lo=80, ok_hi=110, fix_hi=140, basis=EXPERIENCE, source=_SRC_HOLMBERG,
            uncertainty=UNC_ARM),
        Ref("trunk_lean_end_deg", ok_lo=40, ok_hi=60, fix_lo=25, fix_hi=80, basis=EXPERIENCE, source=_SRC_C2,
            uncertainty=UNC_ANGLE),
        Ref("hip_flex_end_deg", ok_lo=50, ok_hi=80, fix_lo=30, basis=EXPERIENCE, source=_SRC_HOLMBERG,
            uncertainty=UNC_ANGLE),
        Ref("knee_flex_end_deg", ok_lo=20, ok_hi=50, fix_hi=70, basis=EXPERIENCE, source=_SRC_C2,
            uncertainty=UNC_ANGLE),
        Ref("trunk_lead_ms", "ms", ok_lo=0, fix_lo=-100, basis=EXPERIENCE, source=_SRC_C2,
            uncertainty=round(1.5 * frame_ms)),
        Ref("elbow_angle_min_deg", basis=NO_VERDICT, source=_SRC_HOLMBERG, uncertainty=UNC_ARM),
        Ref("vertical_oscillation_cm", "cm", basis=NO_VERDICT, uncertainty=2.0),
        Ref("heel_raise_cm", "cm", decimals=1, basis=NO_VERDICT, uncertainty=2.0),
        Ref("rhythm_cv_pct", "%", decimals=1, basis=NO_VERDICT),
    ]
    return {r.metric: r for r in out}


@dataclass
class SkiCore:
    """The analysis plus what the key frames and the review tool need."""
    analysis: TechniqueAnalysis
    kin: PoseSeries | None = None
    cycles: StrokeCycles | None = None
    rep: Stroke | None = None                       # the stroke the key frames show
    series: dict[str, np.ndarray] | None = None     # angle per row


def _at(arr: np.ndarray, row: float) -> float | None:
    v = float(np.interp(row, np.arange(len(arr)), arr))
    return None if v != v else v


def _onset(arr: np.ndarray, t: np.ndarray, i0: int, i1: int) -> float | None:
    """The time when ``arr`` has risen ``ONSET`` of its travel between rows i0 and i1 (None when it barely moves)."""
    seg = arr[i0:i1 + 1]
    if len(seg) < 4 or np.isnan(seg).any():
        return None
    base, travel = float(seg[:2].min()), float(seg.max() - seg[:2].min())
    if travel < 10.0:
        return None
    level = base + ONSET * travel
    above = np.flatnonzero(seg >= level)
    if not len(above) or above[0] == 0:
        return float(t[i0])
    j = int(above[0])
    a, b = seg[j - 1], seg[j]
    f = (level - a) / (b - a) if b != a else 0.0
    return float(t[i0 + j - 1] + f * (t[i0 + j] - t[i0 + j - 1]))


def _findings(s: dict[str, Any], R: dict[str, Ref], sd: Side) -> list[Finding]:
    pre = "l_" if sd == Side.LEFT else "r_"
    out: list[Finding] = []

    def two_sided(code: str, key: str, low: str, high: str, hl: list[str]) -> None:
        v = s.get(key)
        if v is None:
            return
        dev = R[key].deviation(v)
        out.append(tq.make_finding(KIND, code, R[key], v, highlight=[pre + h for h in hl],
                                   variant=None if dev == 0 else (f"problem_{high}" if dev > 0 else f"problem_{low}")))

    def one_sided(code: str, key: str, hl: list[str], always: bool = False, **fmt: str) -> None:
        v = s.get(key)
        if v is None or (not always and R[key].classify(v) == Severity.OK):
            return
        out.append(tq.make_finding(KIND, code, R[key], v, highlight=[pre + h for h in hl], **fmt))

    two_sided("hand_height", "hand_height_start_cm", "low", "high", ["wrist"])
    two_sided("elbow_start", "elbow_angle_start_deg", "tight", "straight", ["shoulder", "elbow", "wrist"])
    two_sided("trunk", "trunk_lean_end_deg", "shallow", "deep", ["shoulder", "hip"])
    one_sided("hip_hinge", "hip_flex_end_deg", ["hip"], knee=num(s.get("knee_flex_end_deg")))
    one_sided("knee_squat", "knee_flex_end_deg", ["knee"], hip=num(s.get("hip_flex_end_deg")))
    one_sided("sequence", "trunk_lead_ms", ["elbow", "hip"], always=True,
              lead=num(abs(s.get("trunk_lead_ms") or 0.0)))
    one_sided("rhythm", "recovery_ratio", [], always=True, drive=num(s.get("drive_ms")),
              recovery=num(s.get("recovery_ms")), rate=num(s.get("stroke_rate_spm")))
    return out


def analyze_core(poses: Sequence[Pose | None], height_cm: float | None, view: View | str = View.SIDE_LEFT, *,
                 times: Sequence[float] | None = None, frames_total: int | None = None,
                 previous: dict[str, Any] | None = None) -> SkiCore:
    """The whole measurement chain on a pose sequence (no images)."""
    v = View(view)
    found = sum(p is not None for p in poses)
    n = frames_total if frames_total is not None else len(poses)
    raw = tq.series_from(poses, times)

    def empty(reason: str) -> SkiCore:
        return SkiCore(TechniqueAnalysis(KIND, v.value, height_cm=height_cm, frames=n, frames_with_pose=found,
                                         warnings=[reason], quality=tq.quality("poor", [reason])))

    if not is_side_view(v):
        return empty(t("technique.ski.warn.side_only"))
    if raw is None:
        return empty(t("technique.ski.warn.no_skier"))
    kin = tq.prepare(raw)
    fps = tq.achieved_fps(raw)
    sd = tq.near(v)
    pre = "l_" if sd == Side.LEFT else "r_"
    facing = tq.body_facing(kin)
    scale = estimate_scale(kin, height_cm, v)
    ppc = scale.px_per_cm if scale else None
    cyc = detect(kin, sd, scale.stature_px if scale else None)
    hip, knee, ankle, heel = (kin.side_lm(sd, k) for k in ("hip", "knee", "ankle", "heel"))
    sh, el, wr = (kin.side_lm(sd, k) for k in ("shoulder", "elbow", "wrist"))
    nose = kin.lm("nose")
    ser = {
        "elbow": interior_angle(sh, el, wr),
        "trunk_lean": lean_from_vertical(hip, sh, facing),
        "hip_flex": 180.0 - interior_angle(sh, hip, knee),
        "knee_flex": 180.0 - interior_angle(hip, knee, ankle),
    }
    floor = float(np.nanpercentile(heel[:, 1], 95)) if (~np.isnan(heel[:, 1])).any() else None
    rows_out = []
    for k, st in enumerate(cyc.strokes):
        i0, i1 = int(np.floor(st.i_start)), int(np.ceil(st.i_bottom))
        full = np.arange(int(np.ceil(st.i_start)), min(len(kin), int(np.floor(st.i_next)) + 1))
        if i1 - i0 < 3 or len(full) < 4:
            continue
        t_hip, t_el = _onset(ser["hip_flex"], kin.t, i0, i1), _onset(ser["elbow"], kin.t, i0, i1)
        r: dict[str, Any] = {
            "index": k, "t": round(st.t_start, 3),
            "row_top": st.i_top, "row_start": st.i_start, "row_bottom": st.i_bottom, "row_end": st.i_end,
            "row_next": st.i_next,
            "drive_ms": 1000 * st.drive_s, "recovery_ms": 1000 * st.recovery_s,
            "recovery_ratio": st.recovery_s / st.drive_s if st.drive_s > 0 else None,
            "stroke_rate_spm": 60.0 / st.period_s,
            "elbow_angle_start_deg": _at(ser["elbow"], st.i_start),
            "elbow_angle_min_deg": float(np.nanmin(ser["elbow"][i0:i1 + 1])),
            "trunk_lean_end_deg": _at(ser["trunk_lean"], st.i_bottom),
            "hip_flex_end_deg": _at(ser["hip_flex"], st.i_bottom),
            "knee_flex_end_deg": _at(ser["knee_flex"], st.i_bottom),
            # + = the trunk starts before the elbow extends. An elbow that stays bent the whole drive
            # (no onset) also means the trunk leads.
            "trunk_lead_ms": None if t_hip is None else 1000 * ((t_el if t_el is not None else st.t_end) - t_hip),
        }
        if ppc:
            wy, ny = _at(wr[:, 1], st.i_top), _at(nose[:, 1], st.i_top)
            r["hand_height_start_cm"] = None if wy is None or ny is None else (ny - wy) / ppc
            hy = kin.mid("hip")[full, 1]
            r["vertical_oscillation_cm"] = float((np.nanmax(hy) - np.nanmin(hy)) / ppc)
            hz = _at(heel[:, 1], st.i_top)
            r["heel_raise_cm"] = None if hz is None or floor is None else max(0.0, (floor - hz) / ppc)
        rows_out.append({k2: (None if isinstance(x, float) and x != x else x) for k2, x in r.items()})
    R = refs(fps)
    summary: dict[str, Any] = {"strokes": len(rows_out), "side": sd.value,
                               "rhythm_cv_pct": round(100 * cyc.rhythm_cv, 1)}
    for key in R:
        if key != "rhythm_cv_pct":
            summary[key] = tq.median([r.get(key) for r in rows_out])
    # --- how far the measurement can be trusted
    warn: list[str] = []
    level = "good"
    arm = tq.visible_share(raw, [pre + "shoulder", pre + "elbow", pre + "wrist"])
    if len(rows_out) < 4 or cyc.missing_share > 0.3:
        level = "poor"
        warn.append(t("technique.ski.warn.no_strokes", n=len(rows_out)))
    else:
        if len(rows_out) < 8:
            level = "uncertain"
            warn.append(t("technique.ski.warn.few_strokes", n=len(rows_out)))
        if cyc.rhythm_cv > 0.1:
            level = "uncertain"
            warn.append(t("technique.ski.warn.uneven", pct=num(100 * cyc.rhythm_cv)))
        if arm < 0.8:
            level = "uncertain"
            warn.append(t("technique.ski.warn.arm_hidden", pct=num(100 * arm)))
        if scale is not None and cyc.travel_px < 0.25 * scale.stature_px:
            level = "uncertain"
            warn.append(t("technique.ski.warn.short_travel"))
    if tq.too_slow(fps, MIN_FPS) and level == "good":
        level = "uncertain"
    warnings = tq.common_warnings(KIND, raw, n, found, fps, MIN_FPS) + warn
    if 100.0 * found / max(1, n) < 60:
        level = "poor"
    if not height_cm:
        warnings.append(t("pose.warn.no_height"))
    elif scale is not None:
        warnings.append(t("pose.warn.scale", unc=num(scale.uncertainty_pct)))
    if facing != (-1 if v == View.SIDE_LEFT else 1) and level != "poor":
        warnings.append(t("technique.ski.warn.facing"))
    findings = tq.sort_findings(_findings(summary, R, sd)) if level != "poor" else []
    a = TechniqueAnalysis(
        KIND, v.value, summary=summary, cycles=rows_out, findings=findings,
        ranges={k: r.judged_dict(summary.get(k)) for k, r in R.items() if summary.get(k) is not None},
        warnings=warnings, quality=tq.quality(level, warn), height_cm=height_cm, fps=round(fps, 2),
        duration_s=round(float(raw.t[-1] - raw.t[0]), 2), frames=n, frames_with_pose=found, facing=facing, scale=scale)
    a.comparison = tq.compare(a, previous)
    rep = None
    if rows_out and level != "poor":
        cand = rows_out[1:-1] if len(rows_out) > 4 else rows_out
        target = summary.get("trunk_lean_end_deg") or 0.0
        best = min(cand, key=lambda r: abs((r.get("trunk_lean_end_deg") or 1e9) - target))
        rep = next((s for s in cyc.strokes if abs(s.i_start - best["row_start"]) < 1e-6), None)
    return SkiCore(a, kin, cyc, rep, ser)


def analyze_poses(poses: Sequence[Pose | None], height_cm: float | None, view: View | str = View.SIDE_LEFT,
                  **kw: Any) -> TechniqueAnalysis:
    """``TechniqueAnalysis`` (without key frames) from a pose sequence."""
    return analyze_core(poses, height_cm, view, **kw).analysis


def analyze_video(path: str | Path, height_cm: float | None, view: View | str = View.SIDE_LEFT,
                  progress_cb: tq.ProgressCb | None = None, keyframes_dir: str | Path | None = None, *,
                  estimator: Any = None, model_variant: str = "full", previous: dict[str, Any] | None = None,
                  max_seconds: float | None = None, cancel: threading.Event | None = None) -> TechniqueAnalysis:
    """Analyse a film of an athlete on the SkiErg. Raises ``TechniqueError`` (localised)."""
    from lopanalys.core import paths
    from lopanalys.pose.models import call_progress
    from lopanalys.pose.offline import grab_frames
    from lopanalys.pose.ski import draw

    path = Path(path)
    poses, times = tq.read_poses(path, estimator=estimator, model_variant=model_variant, progress_cb=progress_cb,
                                 max_seconds=max_seconds, cancel=cancel)
    core = analyze_core(poses, height_cm, view, times=times, frames_total=len(poses), previous=previous)
    core.analysis.video_path = str(path)
    if core.kin is None:
        raise tq.TechniqueError(core.analysis.warnings[0] if core.analysis.warnings else t("technique.ski.warn.no_skier"))
    call_progress(progress_cb, 0.92, t("pose.progress.keyframes"))
    plans = draw.plan(core)
    frames = grab_frames(path, {f for p in plans for f in p.frames})
    out_dir = Path(keyframes_dir) if keyframes_dir else paths.keyframes_dir() / path.stem
    core.analysis.keyframes = draw.render_all(core, plans, frames, out_dir, stem=path.stem[:40] or "kf")
    call_progress(progress_cb, 1.0, "")
    return core.analysis


