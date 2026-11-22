"""Analysis of a recorded clip (e.g. a 120 fps GoPro file) into a ``GaitAnalysis`` with key frames.

Two passes over the video, so memory stays small even for long 4K clips:
  1. every frame through the pose estimator -> poses -> filters -> gait events -> metrics -> findings,
  2. read again and keep only the 4-8 frames chosen as key frames, draw and save them as PNG.

``analyze_poses`` is the same analysis without video (used by tests, the live window and demo).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from lopanalys.core.i18n import t
from lopanalys.pose import annotate, reliability
from lopanalys.pose.filters import prepare_pair
from lopanalys.pose.findings import analysis_ranges, evaluate, num
from lopanalys.pose.gait import Contact, GaitEvents, detect_events, event_quality
from lopanalys.pose.metrics import estimate_scale, step_metrics, summarize
from lopanalys.pose.series import PoseSeries, is_side_view, near_side
from lopanalys.pose.types import Finding, GaitAnalysis, KeyFrame, Pose, Side, StepMetrics, View

log = logging.getLogger(__name__)

ProgressCb = Callable[..., None]    # (fraction 0..1, localised message) or just (fraction)


class AnalysisError(RuntimeError):
    """The clip could not be analysed; the message is localised for the coach."""


@dataclass
class CoreResult:
    """Analysis plus the internals needed to pick and draw key frames."""
    analysis: GaitAnalysis
    kin: PoseSeries | None
    events: GaitEvents | None
    events_series: PoseSeries | None = None     # the series the events were found in (review tool)


def _row_frame(s: PoseSeries, row: float) -> int:
    return int(round(float(np.interp(row, np.arange(len(s)), s.frame_index))))


def analyze_core(poses: Sequence[Pose | None], height_cm: float | None, view: View | str,
                 speed_kmh: float | None = None, times: Sequence[float] | None = None,
                 frames_total: int | None = None) -> CoreResult:
    """The whole measurement chain on a pose sequence (no images)."""
    v = View(view)
    found = [p for p in poses if p is not None]
    n = frames_total if frames_total is not None else len(poses)
    warnings: list[str] = []
    empty = GaitAnalysis(None, [], [], [], v.value, height_cm=height_cm, frames=n,
                         frames_with_pose=len(found), speed_kmh=speed_kmh, warnings=warnings)
    if len(found) < 10:
        warnings.append(t("pose.error.no_runner"))
        return CoreResult(empty, None, None)
    raw = PoseSeries.from_poses(list(poses), times=times)
    # drop leading/trailing frames without a pose
    have = ~np.isnan(raw.xy[:, 23, 0])
    i0, i1 = int(np.argmax(have)), len(have) - int(np.argmax(have[::-1]))
    raw = raw.slice(i0, i1)
    ev_s, kin = prepare_pair(raw)
    # achieved rate over the clip: with uneven timestamps (a live camera) the median frame interval
    # overstates it (real hardware 2026-11-14: median 35 ms = 28.3 fps, really 24.8 fps)
    dur = float(raw.t[-1] - raw.t[0])
    fps = (len(raw) - 1) / dur if dur > 0 else raw.fps
    events = detect_events(ev_s, v)
    scale = estimate_scale(kin, height_cm, v)
    steps = step_metrics(kin, events, scale)
    summary = summarize(steps, events, kin, speed_kmh=speed_kmh) if steps else None
    # values the frame rate cannot carry are left out before anything is judged on them
    summary, rel = reliability.apply(summary, fps)
    findings = reliability.mark(evaluate(summary, steps, v, height_cm, speed_kmh), rel, summary)
    # warnings for the coach
    pct = 100.0 * len(found) / max(1, n)
    if pct < 80:
        warnings.append(t("pose.warn.low_detection", pct=num(pct)))
    warnings += reliability.warnings(fps)
    if not height_cm:
        warnings.append(t("pose.warn.no_height"))
    elif scale is not None:
        warnings.append(t("pose.warn.scale", unc=num(scale.uncertainty_pct)))
    if len(steps) < 10:
        warnings.append(t("pose.warn.few_steps", n=len(steps)))
    if is_side_view(v):
        expected = -1 if v == View.SIDE_LEFT else 1
        if events.facing != expected:
            warnings.append(t("pose.warn.facing"))
        warnings += landing_warnings(events, ev_s)
    else:
        warnings.append(t("pose.warn.rear_timing"))
    analysis = GaitAnalysis(
        summary=summary, steps=steps, findings=findings, keyframes=[], view=v.value, height_cm=height_cm,
        fps=round(fps, 2), duration_s=round(float(raw.t[-1] - raw.t[0]), 2), frames=n,
        frames_with_pose=len(found), facing=events.facing if is_side_view(v) else None, scale=scale,
        speed_kmh=speed_kmh, warnings=warnings, reliability=rel,
        ranges={k: r.to_dict() for k, r in analysis_ranges(summary, steps, v, height_cm, speed_kmh).items()})
    return CoreResult(analysis, kin, events, ev_s)


# Plausible running contact times (ms): elite sprinting to a slow jog. Outside this range the landings
# were found wrongly, not measured (real hardware 2026-11-14: 110 ms from a clip where the true value
# was about 300 ms).
GCT_PLAUSIBLE_MS = (120.0, 450.0)


def landing_warnings(events: GaitEvents, s: PoseSeries) -> list[str]:
    """Warnings when the landings, and so every value timed by them, are uncertain (side view).

    The coach must not get numbers that look exact but are not (if the analysis is not
    trustworthy, the whole idea fails).
    """
    out: list[str] = []
    q = event_quality(events)
    swaps = float(s.meta.get("leg_swap_share", 0.0))
    if swaps > 0.3:          # 13 % in the first real clip, where the correction held
        out.append(t("pose.warn.leg_swaps", pct=num(100 * swaps)))
    if q.get("coverage", 1.0) < 0.8:
        out.append(t("pose.warn.landings_missing", pct=num(100 * q["coverage"])))
    if q.get("position_share", 0.0) > 0.2:
        out.append(t("pose.warn.landings_position", pct=num(100 * q["position_share"])))
    gct = q.get("gct_ms")
    lo, hi = GCT_PLAUSIBLE_MS
    if gct is not None and not lo <= gct <= hi:
        out.append(t("pose.warn.contact_implausible", ms=num(gct), lo=num(lo), hi=num(hi)))
    elif q.get("gct_spread", 0.0) > 0.45:
        g = [c.gct_ms for c in events.contacts if c.gct_ms is not None]
        out.append(t("pose.warn.contact_spread", lo=num(float(np.percentile(g, 25))),
                     hi=num(float(np.percentile(g, 75)))))
    return out


def analyze_poses(poses: Sequence[Pose | None], height_cm: float | None, view: View | str,
                  speed_kmh: float | None = None, times: Sequence[float] | None = None) -> GaitAnalysis:
    """``GaitAnalysis`` (without key frames) from a pose sequence."""
    return analyze_core(poses, height_cm, view, speed_kmh, times).analysis


# --- key frames ----------------------------------------------------------------------------------------

@dataclass
class KeyPlan:
    kind: str                 # contact | midstance | toe_off | flight | sequence
    side: Side
    row: float               # row in the kinematic series
    frame_index: int
    step: StepMetrics | None
    findings: list[Finding]
    rows: list[tuple[str, float]] | None = None   # for the sequence strip: (label, row)


_KIND_OF = {  # finding code -> key frame kind that shows it
    "overstride": "contact", "knee_stiff_contact": "contact", "foot_strike": "contact", "cadence": "contact",
    "knee_flex_low": "midstance", "knee_flex_high": "midstance", "trunk": "midstance",
    "trunk_upright": "midstance", "trunk_forward": "midstance", "vertical_osc": "midstance",
    "hip_ext_low": "toe_off", "asymmetry": "contact",
    "pelvic_drop": "midstance", "knee_valgus": "midstance", "step_width": "midstance",
    "arm_cross": "midstance", "heel_whip": "toe_off",
}


def _rep_contact(core: CoreResult, side: Side, key: str | None) -> tuple[Contact, StepMetrics] | None:
    ev = core.events
    assert ev is not None
    pairs = [(c, s) for c, s in zip(ev.contacts, core.analysis.steps)
             if c.side == side and c.i_to is not None and c.i_mid is not None]
    nxt_ok = {id(c): k + 1 < len(ev.contacts) for k, c in enumerate(ev.contacts)}
    pairs = [p for p in pairs if nxt_ok[id(p[0])]]
    if len(pairs) > 4:
        pairs = pairs[1:-1]            # avoid the first and last contact of the clip
    if not pairs:
        return None
    if key:
        vals = [getattr(s, key) for _, s in pairs]
        good = [v for v in vals if v is not None]
        if good:
            med = float(np.median(good))
            return min(pairs, key=lambda p: abs(getattr(p[1], key) - med) if getattr(p[1], key) is not None else 1e9)
    return pairs[len(pairs) // 2]


def plan_keyframes(core: CoreResult) -> list[KeyPlan]:
    """Choose 4-8 key frames: phases of a representative step, plus frames for side-specific findings."""
    a, ev, kin = core.analysis, core.events, core.kin
    if ev is None or kin is None or not a.steps:
        return []
    v = View(a.view)
    plans: list[KeyPlan] = []

    def fnd(kind: str, side: Side | None = None) -> list[Finding]:
        def fside(f: Finding) -> str | None:
            if f.code == "arm_cross" and f.side:
                return Side.RIGHT.value if f.side == Side.LEFT.value else Side.LEFT.value
            return f.side
        return [f for f in a.findings if _KIND_OF.get(f.code) == kind
                and (side is None or fside(f) is None or fside(f) == side.value)]

    def next_contact(c: Contact) -> Contact | None:
        k = ev.contacts.index(c)
        return ev.contacts[k + 1] if k + 1 < len(ev.contacts) else None

    if is_side_view(v):
        side = near_side(v) or Side.LEFT
        rep = _rep_contact(core, side, "shin_angle_deg") or _rep_contact(
            core, Side.RIGHT if side == Side.LEFT else Side.LEFT, "shin_angle_deg")
        if rep is None:
            return []
        c, st = rep
        side = c.side
        nxt = next_contact(c)
        flight_row = (c.i_to + nxt.i_ic) / 2 if nxt is not None else c.i_to + 2   # type: ignore[operator]
        rows = [("contact", c.i_ic), ("midstance", float(c.i_mid)), ("toe_off", float(c.i_to)),   # type: ignore[arg-type]
                ("flight", float(flight_row))]
        for kind, row in rows:
            plans.append(KeyPlan(kind, side, row, _row_frame(kin, row), st, fnd(kind)))
        plans.append(KeyPlan("sequence", side, rows[0][1], _row_frame(kin, rows[0][1]), st, [],
                             rows=[(t(f"pose.kf.{k}"), r) for k, r in rows]))
    else:
        sides = [Side.LEFT, Side.RIGHT]
        for side in sides:
            rep = _rep_contact(core, side, "pelvic_drop_deg")
            if rep is None:
                continue
            c, st = rep
            plans.append(KeyPlan("midstance", side, float(c.i_mid), _row_frame(kin, float(c.i_mid)), st,   # type: ignore[arg-type]
                                 fnd("midstance", side)))
        whip = [f for f in a.findings if f.code == "heel_whip"]
        if whip and whip[0].side:
            side = Side(whip[0].side)
            rep = _rep_contact(core, side, "heel_whip_deg")
            if rep is not None:
                c, st = rep
                row = float(c.i_to) + 0.3 * (float(c.i_to) - c.i_ic)   # type: ignore[arg-type]
                plans.append(KeyPlan("toe_off", side, row, _row_frame(kin, row), st, whip))
        worst = next((Side(f.side) for f in a.findings if f.side and f.code in ("pelvic_drop", "knee_valgus")),
                     Side.LEFT)
        rep = _rep_contact(core, worst, "pelvic_drop_deg")
        if rep is not None:
            c, st = rep
            nxt = next_contact(c)
            rows = [("contact", c.i_ic), ("midstance", float(c.i_mid)), ("toe_off", float(c.i_to))]  # type: ignore[arg-type]
            if nxt is not None:
                rows.append(("flight", (float(c.i_to) + nxt.i_ic) / 2))   # type: ignore[arg-type]
            plans.append(KeyPlan("sequence", worst, rows[0][1], _row_frame(kin, rows[0][1]), st, [],
                                 rows=[(t(f"pose.kf.{k}"), r) for k, r in rows]))
    # point each finding at the key frame that shows it
    for f in a.findings:
        kind = _KIND_OF.get(f.code)
        fside = f.side
        if f.code == "arm_cross" and fside:   # an arm crosses while the opposite foot is on the belt
            fside = Side.RIGHT.value if fside == Side.LEFT.value else Side.LEFT.value
        match = [p for p in plans if p.kind == kind and (fside is None or p.side.value == fside)] or \
                [p for p in plans if p.kind == kind]
        if match:
            f.frame_index = match[0].frame_index
            if f not in match[0].findings:
                match[0].findings.append(f)
    return plans[:8]


def render_keyframes(core: CoreResult, plans: list[KeyPlan], frames: dict[int, np.ndarray],
                     out_dir: str | Path, stem: str = "kf") -> list[KeyFrame]:
    """Draw and save the planned key frames. ``frames`` maps source frame index -> BGR image."""
    a, kin = core.analysis, core.kin
    assert kin is not None
    v = View(a.view)
    out_dir = Path(out_dir)
    out: list[KeyFrame] = []
    facing = a.facing or 1
    side_view = is_side_view(v)
    # one crop for all frames so they line up in the report
    rows_all = [p.row for p in plans if p.kind != "sequence"]
    crop_poses = [kin.pose_at(int(round(r))) for r in rows_all] or [kin.pose_at(0)]
    ref_img = next(iter(frames.values()), None)
    size = (ref_img.shape[1], ref_img.shape[0]) if ref_img is not None else None
    crop = annotate.crop_box(crop_poses, aspect=0.8 if side_view else 0.66, image_size=size)
    for k, p in enumerate(plans):
        try:
            if p.kind == "sequence":
                items = []
                for label, row in p.rows or []:
                    fi = _row_frame(kin, row)
                    if fi in frames:
                        items.append((frames[fi], _pose_for_frame(kin, row, fi), label))
                if len(items) < 2:
                    continue
                img = annotate.render_sequence(items, v, side=p.side)
                caption = t("pose.kf.caption.sequence", side=t(f"pose.side.{p.side.value}"))
                title = t("pose.kf.sequence")
            else:
                if p.frame_index not in frames:
                    continue
                pose = _pose_for_frame(kin, p.row, p.frame_index)
                img = annotate.render_keyframe(frames[p.frame_index], pose, v, p.kind, side=p.side,
                                               facing=facing, step=p.step, findings=p.findings, crop=crop,
                                               all_findings=a.findings)
                title = t("pose.kf.side_label", phase=t(f"pose.kf.{p.kind}"), side=t(f"pose.side.{p.side.value}"))
                caption = (t(f"pose.kf.caption.{p.kind}", side=t(f"pose.side.{p.side.value}")) if side_view else
                           t("pose.kf.caption.frontal", phase=t(f"pose.kf.{p.kind}"), side=t(f"pose.side.{p.side.value}")))
            path = annotate.save_png(img, out_dir / f"{stem}_{k + 1:02d}_{p.kind}_{p.side.value}.png")
            out.append(KeyFrame(label=title, frame_index=p.frame_index,
                                t=float(np.interp(p.row, np.arange(len(kin)), kin.t)), image_path=path,
                                findings=[f.code for f in p.findings], kind=p.kind, caption=caption))
        except Exception:   # one bad drawing must not lose the analysis
            log.exception("drawing key frame %s failed", p.kind)
    return out


def _pose_for_frame(kin: PoseSeries, row: float, frame_index: int) -> Pose:
    """The filtered pose of the row nearest the source frame (so the drawing matches the image)."""
    r = int(np.argmin(np.abs(kin.frame_index - frame_index)))
    return kin.pose_at(r)


# --- video ------------------------------------------------------------------------------------------------

def _open(path: Path):  # noqa: ANN202 - cv2.VideoCapture
    import cv2
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise AnalysisError(t("pose.error.video_open", name=path.name))
    return cap


def grab_frames(path: str | Path, indices: set[int]) -> dict[int, np.ndarray]:
    """Read the given frame indices by decoding sequentially (seeking is unreliable in MP4/HEVC)."""
    if not indices:
        return {}
    cap = _open(Path(path))
    out: dict[int, np.ndarray] = {}
    last = max(indices)
    i = 0
    try:
        while i <= last:
            if i in indices:
                ok, img = cap.read()
                if not ok:
                    break
                out[i] = img
            else:
                if not cap.grab():
                    break
            i += 1
    finally:
        cap.release()
    return out


def recorded_times(path: str | Path, frame_count: int | None = None) -> list[float] | None:
    """The true time of every frame from the recorder's sidecar (``<video>.json``), or None.

    A live camera rarely delivers its nominal rate (the GoPro HERO8 over USB gave 24.8 fps against
    a nominal 30 on real hardware, 2026-11-14), but the MP4 is written at the nominal rate, so the
    container's timestamps run fast and every rate (cadence) comes out too high by the same factor.
    The sidecar keeps the clock time of each written frame; it wins over the container.
    """
    import json

    from lopanalys.camera.file_source import sidecar_path

    p = sidecar_path(Path(path))
    if not p.is_file():
        return None
    try:
        ts = [float(x) for x in json.loads(p.read_text(encoding="utf-8")).get("t", [])]
    except (ValueError, TypeError, OSError, AttributeError):
        log.warning("bad sidecar %s, using container timestamps", p.name)
        return None
    if len(ts) < 2 or any(b <= a for a, b in zip(ts, ts[1:])):
        log.warning("sidecar %s is not increasing, using container timestamps", p.name)
        return None
    if frame_count and abs(len(ts) - frame_count) > 2:
        log.warning("sidecar %s has %d stamps for %d frames, ignored", p.name, len(ts), frame_count)
        return None
    return [x - ts[0] for x in ts]


def analyze_video(path: str | Path, height_cm: float | None, view: View | str,
                  progress_cb: ProgressCb | None = None, keyframes_dir: str | Path | None = None, *,
                  estimator: "object | None" = None, speed_kmh: float | None = None, model_variant: str = "full",
                  max_seconds: float | None = None, cancel: threading.Event | None = None) -> GaitAnalysis:
    """Analyse a video file. Raises AnalysisError (localised) when the file cannot be read.

    ``estimator``: a ``pose.estimator.PoseEstimator`` (default: MediaPipe, ``model_variant``).
    ``keyframes_dir``: where key frame PNGs go (default ``core.paths.keyframes_dir()/<video name>``).
    ``cancel``: set it to stop; raises AnalysisError("cancelled").
    """
    import cv2

    from lopanalys.core import paths
    from lopanalys.pose.estimator import PoseEstimator, make_estimator
    from lopanalys.pose.models import call_progress

    path = Path(path)
    v = View(view)

    def report(frac: float, msg: str) -> None:
        call_progress(progress_cb, min(max(frac, 0.0), 1.0), msg)

    if not path.is_file():
        raise AnalysisError(t("pose.error.video_open", name=path.name))
    own = estimator is None
    est: PoseEstimator = estimator if estimator is not None else make_estimator(  # type: ignore[assignment]
        model_variant, "video", progress_cb=lambda d, tot: report(0.0, t("pose.progress.model")))
    report(0.0, t("pose.progress.analyze", i=0, n="…"))
    try:
        cap = _open(path)
    except BaseException:
        if own:
            est.close()          # the model is loaded already: a film that cannot be read must not keep it
        raise
    fps_hdr = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    stamps = recorded_times(path, total)
    if max_seconds and fps_hdr and stamps is None:
        total = min(total or 10**9, int(max_seconds * fps_hdr))
    poses: list[Pose | None] = []
    times: list[float] = []
    t_first: float | None = None
    i = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise AnalysisError(t("pose.error.cancelled"))
            if total and i >= total and max_seconds:
                break
            if max_seconds and stamps is not None and i < len(stamps) and stamps[i] > max_seconds:
                break
            ok, img = cap.read()
            if not ok:
                break
            if stamps is not None and i < len(stamps):
                tt = stamps[i]               # the real clock time, not the nominal frame rate
            else:
                pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                tt = pts if (pts > 0 or i == 0) else (i / fps_hdr if fps_hdr else i / 30.0)
            if t_first is None:
                t_first = tt
            tt -= t_first
            if times and tt <= times[-1]:
                tt = times[-1] + (1.0 / fps_hdr if fps_hdr else 1 / 30.0)
            pose = est.process(img, tt, i)
            poses.append(pose)
            times.append(tt)
            i += 1
            if i % 15 == 0:
                report(0.9 * i / max(total, i + 1), t("pose.progress.analyze", i=i, n=total or "?"))
    finally:
        cap.release()
        if own:
            est.close()
    core = analyze_core(poses, height_cm, v, speed_kmh, times=times, frames_total=len(poses))
    core.analysis.video_path = str(path)
    if core.kin is None:
        raise AnalysisError(t("pose.error.no_runner"))
    report(0.92, t("pose.progress.keyframes"))
    plans = plan_keyframes(core)
    needed: set[int] = set()
    for p in plans:
        if p.kind == "sequence":
            needed |= {_row_frame(core.kin, r) for _, r in p.rows or []}
        else:
            needed.add(p.frame_index)
    frames = grab_frames(path, needed)
    out_dir = Path(keyframes_dir) if keyframes_dir else paths.keyframes_dir() / path.stem
    core.analysis.keyframes = render_keyframes(core, plans, frames, out_dir, stem=path.stem[:40] or "kf")
    report(1.0, "")
    return core.analysis
