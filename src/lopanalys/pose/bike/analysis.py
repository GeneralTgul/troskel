"""Bike fit for a road bike on the trainer: the measured values, what they are judged against, and
what to change on the bike.

Filmed from the side (the near leg, trunk and arm) and from the front (the knees' track). The
saddle height and the cleats are set on site, the stem becomes a
recommendation.

What the limits rest on (docs/sporter/bikefit-och-stakteknik.md has the full list):
  [Holmes94]  Holmes JC, Pruitt AL, Whalen NJ. Lower extremity overuse in bicycling. Clin Sports Med
              1994;13:187-205: knee flexion 25-35 deg at the bottom, measured statically.
  [Bini16]    Bini RR, Hume P. A comparison of static and dynamic measures of lower limb joint angles
              in cycling. 2016: the knee is 8 +- 2 deg more flexed while pedalling than held still at
              6 o'clock, so 25-35 deg static is about 33-43 deg in video.
  [Swart19]   Swart J, Holliday W. Cycling biomechanics optimization: the (r)evolution of bicycle
              fitting. Curr Sports Med Rep 2019;18:490-496: no accepted optimal values exist for the
              hip, trunk, shoulder and elbow.
  [Bini23]    Bini RR et al. J Sports Sci 2023: markerless pose against markers in cycling from the
              side, hip and knee within 4-8 deg, the ankle 15-22 deg.
The knee at the bottom is the only value with a research-based range. Everything else is fitting
practice: it is shown with a cautious range, marked as a rule of thumb, and never as a fault. The
ankle is shown without a verdict.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from lopanalys.core.i18n import t
from lopanalys.pose import technique as tq
from lopanalys.pose.bike.cycle import PedalCycles, Revolution, detect_front, detect_side
from lopanalys.pose.findings import num
from lopanalys.pose.metrics import estimate_scale
from lopanalys.pose.series import PoseSeries, interior_angle, is_side_view
from lopanalys.pose.technique import EXPERIENCE, NO_VERDICT, RESEARCH, Ref, TechniqueAnalysis
from lopanalys.pose.types import Finding, Pose, ScaleInfo, Severity, Side, View

log = logging.getLogger(__name__)

KIND = tq.BIKEFIT
UNC_ANGLE = 5.0        # deg: hip and knee from the side with markerless pose [Bini23]
UNC_ARM = 6.0          # deg: the hands on the bars are often lost, the arm is less certain
UNC_ANKLE = 15.0       # deg [Bini23]: too much to judge
MIN_FPS = 50.0         # at 90 rpm and 30 fps the crank turns 18 deg between two frames
BALL_OF_FOOT = 0.72    # the pedal axle sits under the ball of the foot, this share from heel to toe
SEGMENT_RATIO = 0.2455  # thigh and shank, each as a share of standing height (Winter)
SADDLE_STEP_MM = 5      # change at most this much before measuring again

_SRC_KNEE = "Holmes et al. 1994; Bini & Hume 2016; Swart & Holliday 2019"
_SRC_PRACTICE = "Swart & Holliday 2019"
_W = Severity.WATCH


def refs(view: View | str | None = None) -> dict[str, Ref]:
    """metric -> ``Ref`` for a road bike. ``view`` None gives every metric."""
    rpm = t("technique.unit.rpm")
    side = [
        Ref("cadence_rpm", rpm, basis=NO_VERDICT),
        Ref("knee_flex_bottom_deg", ok_lo=33, ok_hi=43, fix_lo=25, fix_hi=50, basis=RESEARCH, source=_SRC_KNEE,
            uncertainty=UNC_ANGLE),
        Ref("knee_flex_top_deg", ok_hi=115, basis=EXPERIENCE, uncertainty=UNC_ANGLE, max_severity=_W),
        Ref("hip_angle_top_deg", ok_lo=45, basis=EXPERIENCE, source=_SRC_PRACTICE, uncertainty=UNC_ANGLE,
            max_severity=_W),
        Ref("trunk_angle_deg", ok_lo=40, ok_hi=55, basis=EXPERIENCE, uncertainty=UNC_ANGLE, max_severity=_W),
        Ref("shoulder_angle_deg", ok_lo=80, ok_hi=95, basis=EXPERIENCE, source=_SRC_PRACTICE, uncertainty=UNC_ARM,
            max_severity=_W),
        Ref("elbow_flex_deg", ok_lo=10, ok_hi=25, basis=EXPERIENCE, source="Holliday & Swart 2021",
            uncertainty=UNC_ARM, max_severity=_W),
        Ref("hip_bounce_cm", "cm", ok_hi=2, decimals=1, basis=EXPERIENCE, uncertainty=1.5, max_severity=_W),
        Ref("knee_over_pedal_cm", "cm", decimals=1, basis=NO_VERDICT, uncertainty=2.0),
        Ref("ankle_range_deg", basis=NO_VERDICT, source="Bini et al. 2023", uncertainty=UNC_ANKLE),
    ]
    front = [
        Ref("cadence_rpm", rpm, basis=NO_VERDICT),
        Ref("knee_lateral_cm", "cm", ok_hi=3, decimals=1, basis=EXPERIENCE, uncertainty=1.5, max_severity=_W),
    ]
    if view is None:
        return {r.metric: r for r in side + front[1:]}
    return {r.metric: r for r in (side if is_side_view(view) else front)}


@dataclass
class BikeCore:
    """The analysis plus what the key frames and the review tool need."""
    analysis: TechniqueAnalysis
    kin: PoseSeries | None = None
    cycles: PedalCycles | None = None
    rep: Revolution | None = None          # the revolution the key frames show
    series: dict[str, np.ndarray] | None = None   # angle per row: knee_flex, hip_angle, ...


def _at(arr: np.ndarray, row: float) -> float:
    return float(np.interp(row, np.arange(len(arr)), arr))


def _extreme(arr: np.ndarray, rows: np.ndarray, kind: str) -> tuple[float, float] | None:
    """(row, value) of the smallest or largest value among ``rows``, refined between the frames."""
    seg = arr[rows]
    if len(seg) < 3 or np.isnan(seg).all():
        return None
    i = int(rows[int(np.nanargmin(seg) if kind == "min" else np.nanargmax(seg))])
    return tq.parabola_extreme(arr, i)


def harmonic_fit(y: np.ndarray, phase: np.ndarray, harmonics: int = 1) -> np.ndarray | None:
    """``y`` over one revolution as a mean plus ``harmonics`` sine waves of the crank angle.

    A range (max - min) read straight from a noisy track is too large: the noise adds to both ends.
    The fitted curve keeps what repeats with the pedalling and drops the rest.
    """
    ok = ~np.isnan(y)
    if ok.sum() < 2 * harmonics + 4:
        return None
    cols = [np.ones(len(y))]
    for k in range(1, harmonics + 1):
        cols += [np.cos(k * phase), np.sin(k * phase)]
    a = np.stack(cols, axis=1)
    coef, *_ = np.linalg.lstsq(a[ok], y[ok], rcond=None)
    return a @ coef


def _phase(rev: Revolution, rows: np.ndarray) -> np.ndarray:
    return 2 * np.pi * (rows - rev.i_top) / max(rev.i_end - rev.i_top, 1e-9)


def _empty(view: View, height_cm: float | None, frames: int, found: int, reason: str) -> BikeCore:
    a = TechniqueAnalysis(KIND, view.value, height_cm=height_cm, frames=frames, frames_with_pose=found,
                          warnings=[reason], quality=tq.quality("poor", [reason]))
    return BikeCore(a)


# --- side view ----------------------------------------------------------------------------------------

def _side(kin: PoseSeries, raw: PoseSeries, v: View, height_cm: float | None,
          fps: float) -> tuple[dict[str, Any], list[dict[str, Any]], PedalCycles, dict[str, np.ndarray],
                               ScaleInfo | None, list[str], str]:
    sd = tq.near(v)
    pre = "l_" if sd == Side.LEFT else "r_"
    cyc = detect_side(kin, sd)
    facing = cyc.facing
    hip, knee, ankle = (kin.side_lm(sd, k) for k in ("hip", "knee", "ankle"))
    heel, foot = kin.side_lm(sd, "heel"), kin.side_lm(sd, "foot")
    sh, el, wr = (kin.side_lm(sd, k) for k in ("shoulder", "elbow", "wrist"))
    ser = {
        "knee_flex": 180.0 - interior_angle(hip, knee, ankle),
        "hip_angle": interior_angle(sh, hip, knee),
        "trunk": np.degrees(np.arctan2(-(sh[:, 1] - hip[:, 1]), facing * (sh[:, 0] - hip[:, 0]))),
        "shoulder": interior_angle(hip, sh, el),
        "elbow_flex": 180.0 - interior_angle(sh, el, wr),
        "ankle": interior_angle(knee, ankle, foot),
    }
    scale = estimate_scale(kin, height_cm, v)
    ppc = scale.px_per_cm if scale else None
    pedal = heel + BALL_OF_FOOT * (foot - heel)
    arms_seen = tq.visible_share(raw, [pre + "elbow", pre + "wrist"]) >= 0.6
    n = len(kin)
    rows_out = []
    for k, rev in enumerate(cyc.revolutions):
        rows = rev.rows(n)
        if len(rows) < 4:
            continue
        bottom, top = _extreme(ser["knee_flex"], rows, "min"), _extreme(ser["knee_flex"], rows, "max")
        hip_top = _extreme(ser["hip_angle"], rows, "min")
        if bottom is None or top is None:
            continue
        r: dict[str, Any] = {
            "index": k, "t": round(rev.t_top, 3), "period_s": round(rev.period_s, 4),
            "row_top": top[0], "row_bottom": bottom[0], "row_forward": rev.i_forward, "row_back": rev.i_back,
            "row_tdc": rev.i_top, "row_bdc": rev.i_bottom,
            "knee_flex_bottom_deg": bottom[1], "knee_flex_top_deg": top[1],
            "hip_angle_top_deg": hip_top[1] if hip_top else None,
            "trunk_angle_deg": float(np.nanmean(ser["trunk"][rows])),
            "ankle_range_deg": float(np.nanmax(ser["ankle"][rows]) - np.nanmin(ser["ankle"][rows]))
            if not np.isnan(ser["ankle"][rows]).all() else None,
            "shoulder_angle_deg": float(np.nanmean(ser["shoulder"][rows])) if arms_seen else None,
            "elbow_flex_deg": float(np.nanmean(ser["elbow_flex"][rows])) if arms_seen else None,
        }
        if ppc:
            fit = harmonic_fit(hip[rows, 1], _phase(rev, rows))
            r["hip_bounce_cm"] = float((fit.max() - fit.min()) / ppc) if fit is not None else None
            # 3 o'clock is where the pedal is furthest forward. The ankle's circle is turned a little
            # against the crank (the ankle sits behind the axle), so the pedal's own track decides.
            px = harmonic_fit(facing * pedal[rows, 0], _phase(rev, rows))
            if px is not None:
                j, _ = tq.parabola_extreme(px, int(np.argmax(px)))
                fwd = float(rows[0] + j)
                r["row_forward"] = fwd
                r["knee_over_pedal_cm"] = facing * (_at(knee[:, 0], fwd) - _at(pedal[:, 0], fwd)) / ppc
        rows_out.append({k2: (None if isinstance(x, float) and x != x else x) for k2, x in r.items()})
    summary: dict[str, Any] = {"revolutions": len(rows_out), "cadence_rpm": cyc.cadence_rpm,
                               "cadence_spread_pct": round(100 * cyc.cadence_spread, 1), "side": sd.value}
    for key in refs(v):
        if key != "cadence_rpm":
            summary[key] = tq.median([r.get(key) for r in rows_out])
    # --- how far the measurement can be trusted
    warn: list[str] = []
    level = "good"
    legs = tq.visible_share(raw, [pre + "hip", pre + "knee", pre + "ankle"])
    if (len(rows_out) < 5 or cyc.residual > 0.25 or cyc.forward_share < 0.75 or cyc.missing_share > 0.3):
        level = "poor"
        warn.append(t("technique.bikefit.warn.no_revolutions", n=len(rows_out)))
    else:
        if len(rows_out) < 10:
            level = "uncertain"
            warn.append(t("technique.bikefit.warn.few_revolutions", n=len(rows_out)))
        if cyc.residual > 0.12:
            level = "uncertain"
            warn.append(t("technique.bikefit.warn.circle", pct=num(100 * cyc.residual)))
        if cyc.cadence_spread > 0.08:
            level = "uncertain"
            warn.append(t("technique.bikefit.warn.uneven", pct=num(100 * cyc.cadence_spread)))
        if legs < 0.8:
            level = "uncertain"
            warn.append(t("technique.bikefit.warn.leg_hidden", pct=num(100 * legs)))
    if tq.too_slow(fps, MIN_FPS) and level == "good":
        level = "uncertain"
    if tq.body_facing(kin) != facing and level != "poor":
        level = "uncertain"
        warn.append(t("technique.bikefit.warn.direction"))
    elif facing != (-1 if v == View.SIDE_LEFT else 1) and level != "poor":
        warn.append(t("technique.bikefit.warn.facing"))
    if not arms_seen:
        warn.append(t("technique.bikefit.warn.arms_hidden"))
    if not height_cm:
        warn.append(t("pose.warn.no_height"))
    return summary, rows_out, cyc, ser, scale, warn, level


# --- front view ---------------------------------------------------------------------------------------

def knee_offset(kin: PoseSeries, side: Side) -> np.ndarray:
    """How far the knee lies towards the body's midline from the line hip -> ankle, in pixels per row."""
    hip, knee, ankle = (kin.side_lm(side, k) for k in ("hip", "knee", "ankle"))
    mid = kin.mid("hip")[:, 0]
    with np.errstate(invalid="ignore", divide="ignore"):
        f = (knee[:, 1] - hip[:, 1]) / (ankle[:, 1] - hip[:, 1])
    line_x = hip[:, 0] + np.clip(f, 0.0, 1.0) * (ankle[:, 0] - hip[:, 0])
    outward = np.sign(np.nanmedian(ankle[:, 0] - mid)) or 1.0
    return outward * (line_x - knee[:, 0])


def _front(kin: PoseSeries, raw: PoseSeries, v: View, height_cm: float | None,
           fps: float) -> tuple[dict[str, Any], list[dict[str, Any]], PedalCycles, dict[str, np.ndarray],
                                ScaleInfo | None, list[str], str]:
    cyc = detect_front(kin)
    scale = None
    if height_cm:
        shank = [np.hypot(*(kin.side_lm(s, "knee") - kin.side_lm(s, "ankle")).T) for s in (Side.LEFT, Side.RIGHT)]
        vals = [float(np.nanpercentile(x, 90)) for x in shank if (~np.isnan(x)).sum() > 5]
        if vals:   # the shank is least foreshortened near the bottom of the stroke
            px = max(vals) / (0.246 * height_cm)
            scale = ScaleInfo(px_per_cm=px, uncertainty_pct=8.0, stature_px=px * height_cm, method="shank")
    ppc = scale.px_per_cm if scale else None
    ser = {f"offset_{s.value}": knee_offset(kin, s) for s in (Side.LEFT, Side.RIGHT)}
    rows_out = []
    n = len(kin)
    for k, rev in enumerate(cyc.revolutions):
        rows = rev.rows(n)
        off = harmonic_fit(ser[f"offset_{rev.side.value}"][rows], _phase(rev, rows), 2) if rev.side else None
        if off is None or not ppc:
            continue
        down = rows <= rev.i_bottom
        top = (rows - rev.i_top) < 0.2 * (rev.i_end - rev.i_top)
        rows_out.append({
            "index": k, "side": rev.side.value, "t": round(rev.t_top, 3), "period_s": round(rev.period_s, 4),
            "row_tdc": rev.i_top, "row_bdc": rev.i_bottom, "row_forward": rev.i_forward,
            "knee_lateral_cm": float((np.nanmax(off) - np.nanmin(off)) / ppc),
            "knee_in_cm": float(np.nanmax(off[down]) / ppc) if down.any() else None,
            "knee_out_cm": float(-np.nanmin(off[top]) / ppc) if top.any() else None,
        })
    summary: dict[str, Any] = {"revolutions": len(rows_out), "cadence_rpm": cyc.cadence_rpm,
                               "cadence_spread_pct": round(100 * cyc.cadence_spread, 1)}
    per_side = {}
    for s in (Side.LEFT, Side.RIGHT):
        mine = [r for r in rows_out if r["side"] == s.value]
        per_side[s] = tq.median([r["knee_lateral_cm"] for r in mine])
        summary[f"knee_lateral_{s.value}_cm"] = per_side[s]
        summary[f"knee_in_{s.value}_cm"] = tq.median([r["knee_in_cm"] for r in mine])
        summary[f"knee_out_{s.value}_cm"] = tq.median([r["knee_out_cm"] for r in mine])
    have = {s: x for s, x in per_side.items() if x is not None}
    worst = max(have, key=lambda s: have[s]) if have else None
    summary["knee_lateral_cm"] = have[worst] if worst else None      # the worse side is judged
    summary["side"] = worst.value if worst else None
    if worst is not None:
        i, o = summary[f"knee_in_{worst.value}_cm"] or 0.0, summary[f"knee_out_{worst.value}_cm"] or 0.0
        summary["knee_direction"] = "in" if i >= o else "out"
    warn: list[str] = []
    level = "good"
    per_leg = min((sum(r["side"] == s.value for r in rows_out) for s in (Side.LEFT, Side.RIGHT)), default=0)
    if per_leg < 5:
        level = "poor"
        warn.append(t("technique.bikefit.warn.no_revolutions", n=per_leg))
    else:
        if per_leg < 10:
            level = "uncertain"
            warn.append(t("technique.bikefit.warn.few_revolutions", n=per_leg))
        if cyc.missing_share > 0.2:
            level = "uncertain"
            warn.append(t("technique.bikefit.warn.leg_hidden", pct=num(100 * (1 - cyc.missing_share))))
    if not height_cm:
        level = "poor" if level == "poor" else "uncertain"
        warn.append(t("pose.warn.no_height"))
    warn.append(t("technique.bikefit.warn.front_hip"))
    return summary, rows_out, cyc, ser, scale, warn, level


# --- findings and advice ------------------------------------------------------------------------------

def _findings(summary: dict[str, Any], R: dict[str, Ref], v: View) -> list[Finding]:
    out: list[Finding] = []
    mk = lambda code, key, **kw: tq.make_finding(KIND, code, R[key], summary.get(key), **kw)   # noqa: E731
    sd = Side(summary["side"]) if summary.get("side") else None
    pre = "r_" if sd == Side.RIGHT else "l_"
    if not is_side_view(v):
        val = summary.get("knee_lateral_cm")
        if val is not None:
            sev = R["knee_lateral_cm"].classify(val)
            variant = None if sev == Severity.OK else f"problem_{summary.get('knee_direction', 'in')}"
            out.append(mk("knee_track", "knee_lateral_cm", variant=variant, side=sd, highlight=[pre + "knee"],
                          side_word=t(f"pose.side_title.{sd.value}") if sd else ""))
        return out
    kb = summary.get("knee_flex_bottom_deg")
    if kb is not None:
        ref = R["knee_flex_bottom_deg"]
        dev = ref.deviation(kb)
        variant = None if dev == 0 else ("problem_low_saddle" if dev > 0 else "problem_high_saddle")
        out.append(mk("saddle", "knee_flex_bottom_deg", variant=variant, highlight=[pre + "knee"]))
    for code, key, hl in (("knee_top", "knee_flex_top_deg", ["knee"]), ("hip_closed", "hip_angle_top_deg", ["hip"]),
                          ("hip_rock", "hip_bounce_cm", ["hip"])):
        val = summary.get(key)
        if val is not None and R[key].classify(val) not in (None, Severity.OK):
            out.append(mk(code, key, highlight=[pre + h for h in hl]))
    tr = summary.get("trunk_angle_deg")
    if tr is not None:
        dev = R["trunk_angle_deg"].deviation(tr)
        out.append(mk("trunk", "trunk_angle_deg", highlight=[pre + "shoulder", pre + "hip"],
                      variant=None if dev == 0 else ("problem_upright" if dev > 0 else "problem_low")))
    reach = reach_verdict(summary, R)
    if reach is not None:
        key = "elbow_flex_deg" if reach in ("long_elbow", "ok") else "shoulder_angle_deg"
        variant = {"ok": "ok", "long_elbow": "problem_long", "long_shoulder": "problem_long",
                   "short": "problem_short"}[reach]
        out.append(mk("reach", key, variant=variant, highlight=[pre + "shoulder", pre + "elbow", pre + "wrist"],
                      severity=Severity.OK if reach == "ok" else Severity.WATCH,
                      elbow=num(summary.get("elbow_flex_deg")), shoulder=num(summary.get("shoulder_angle_deg"))))
    return out


def reach_verdict(summary: dict[str, Any], R: dict[str, Ref]) -> str | None:
    """"long_elbow" | "long_shoulder" | "short" | "ok", or None when the arm was not seen."""
    el, shd = summary.get("elbow_flex_deg"), summary.get("shoulder_angle_deg")
    if el is None or shd is None:
        return None
    e, s = R["elbow_flex_deg"], R["shoulder_angle_deg"]
    if e.deviation(el) < 0:
        return "long_elbow"                      # locked, straight arms
    if s.deviation(shd) > 0:
        return "long_shoulder"                   # the arm reaches far forward
    if s.deviation(shd) < 0 and e.deviation(el) > 0:
        return "short"                           # cramped: closed shoulder and bent elbows
    return "ok"


def saddle_mm_per_deg(knee_flex_deg: float, height_cm: float | None) -> float:
    """How many mm of saddle height one degree of knee flexion at the bottom corresponds to.

    Geometry only: with thigh and shank of length l the distance hip-ankle is 2 l cos(flex / 2), so
    one degree changes it by l sin(flex / 2) pi / 180. For a 178 cm rider near 38 deg that is about
    2.5 mm per degree (1 cm = 4 deg). In practice the ankle takes up part of a change, which is why the
    advice is to change a few mm at a time and measure again.
    """
    seg_mm = SEGMENT_RATIO * (height_cm or 175.0) * 10.0
    return seg_mm * math.sin(math.radians(knee_flex_deg) / 2) * math.pi / 180.0


def advice(summary: dict[str, Any], view: View | str, height_cm: float | None,
           level: str = "good") -> list[dict[str, Any]]:
    """What to change on the bike: saddle height and cleats (set on site), and the stem (a recommendation)."""
    v = View(view)
    R = refs(v)
    out: list[dict[str, Any]] = []

    def add(part: str, action: str, on_site: bool, certain: bool = True, direction: str | None = None,
            **fmt: Any) -> None:
        out.append({"part": part, "action": action, "on_site": on_site, "certain": certain, "direction": direction,
                    "title": t(f"technique.bikefit.advice.{part}.title"),
                    "text": t(f"technique.bikefit.advice.{part}.{action}", **{k: x for k, x in fmt.items()
                                                                             if isinstance(x, str)}),
                    **{k: x for k, x in fmt.items() if not isinstance(x, str)}})

    if not is_side_view(v):
        val, sd = summary.get("knee_lateral_cm"), summary.get("side")
        if level == "poor" or val is None:
            add("cleat", "unknown", True, certain=False)
        elif R["knee_lateral_cm"].classify(val) == Severity.OK:
            add("cleat", "keep", True, value=num(val, 1))
        else:
            add("cleat", f"check_{summary.get('knee_direction', 'in')}", True,
                certain=not R["knee_lateral_cm"].within_error(val), value=num(val, 1),
                side=t(f"pose.side_title.{sd}") if sd else "")
        return out
    kb = summary.get("knee_flex_bottom_deg")
    ref = R["knee_flex_bottom_deg"]
    if level == "poor" or kb is None:
        add("saddle_height", "unknown", True, certain=False)
    else:
        dev = ref.deviation(kb)
        rock = summary.get("hip_bounce_cm")
        rocking = rock is not None and R["hip_bounce_cm"].classify(rock) != Severity.OK \
            and not R["hip_bounce_cm"].within_error(rock)
        if dev == 0:
            add("saddle_height", "check_rock" if rocking else "keep", True, certain=not rocking, value=num(kb),
                rock=num(rock, 1))
        else:
            per = saddle_mm_per_deg(kb, height_cm)
            mid = (ref.ok_lo + ref.ok_hi) / 2            # type: ignore[operator]
            lo, hi = int(round(abs(dev) * per)), int(round(abs(kb - mid) * per))
            lo = max(lo, 1)
            certain = not ref.within_error(kb) and level == "good"
            action = ("raise" if dev > 0 else "lower") + ("" if certain else "_uncertain")
            add("saddle_height", action, True, certain=certain, value=num(kb), dev=num(abs(dev)),
                lo=str(lo), hi=str(max(hi, lo)), step=str(SADDLE_STEP_MM), unc=num(UNC_ANGLE),
                mm_lo=lo, mm_hi=max(hi, lo), direction="up" if dev > 0 else "down")
    # the fore-aft position of the cleat cannot be read from video: the pedal axle is not a landmark
    add("cleat", "manual", True, certain=False)
    reach = reach_verdict(summary, R)
    hip = summary.get("hip_angle_top_deg")
    hip_closed = hip is not None and R["hip_angle_top_deg"].classify(hip) != Severity.OK \
        and not R["hip_angle_top_deg"].within_error(hip)
    fmt = dict(elbow=num(summary.get("elbow_flex_deg")), shoulder=num(summary.get("shoulder_angle_deg")),
               hip=num(hip))
    if level == "poor" or reach is None:
        add("stem", "unknown", False, certain=False)
    elif reach in ("long_elbow", "long_shoulder"):
        add("stem", "shorter", False, certain=False, **fmt)
    elif reach == "short":
        add("stem", "longer", False, certain=False, **fmt)
    elif hip_closed:
        add("stem", "higher", False, certain=False, **fmt)
    else:
        add("stem", "keep", False, **fmt)
    return out


# --- the whole chain ----------------------------------------------------------------------------------

def analyze_core(poses: Sequence[Pose | None], height_cm: float | None, view: View | str, *,
                 times: Sequence[float] | None = None, frames_total: int | None = None,
                 previous: dict[str, Any] | None = None) -> BikeCore:
    """The whole measurement chain on a pose sequence (no images)."""
    v = View(view)
    found = sum(p is not None for p in poses)
    n = frames_total if frames_total is not None else len(poses)
    raw = tq.series_from(poses, times)
    if raw is None:
        return _empty(v, height_cm, n, found, t("technique.bikefit.warn.no_cyclist"))
    kin = tq.prepare(raw)
    fps = tq.achieved_fps(raw)
    branch = _side if is_side_view(v) else _front
    summary, rows, cyc, ser, scale, warn, level = branch(kin, raw, v, height_cm, fps)
    warnings = tq.common_warnings(KIND, raw, n, found, fps, MIN_FPS) + warn
    if 100.0 * found / max(1, n) < 60:
        level = "poor"
    R = refs(v)
    findings = tq.sort_findings(_findings(summary, R, v)) if level != "poor" else []
    if level != "poor":
        warnings.append(t("technique.bikefit.warn.extremes", unc=num(UNC_ANGLE)))
    a = TechniqueAnalysis(
        KIND, v.value, summary=summary, cycles=rows, findings=findings,
        ranges={k: r.judged_dict(summary.get(k)) for k, r in R.items() if summary.get(k) is not None},
        warnings=warnings, advice=advice(summary, v, height_cm, level), quality=tq.quality(level, warn),
        height_cm=height_cm, fps=round(fps, 2), duration_s=round(float(raw.t[-1] - raw.t[0]), 2), frames=n,
        frames_with_pose=found, facing=cyc.facing if is_side_view(v) else None, scale=scale)
    a.comparison = tq.compare(a, previous)
    rep = None
    if rows and level != "poor":
        key = "knee_flex_bottom_deg" if is_side_view(v) else "knee_lateral_cm"
        cand = rows[1:-1] if len(rows) > 4 else rows
        if not is_side_view(v) and summary.get("side"):
            cand = [r for r in cand if r["side"] == summary["side"]] or cand
        target = summary.get(key)
        best = min(cand, key=lambda r: abs((r.get(key) or 1e9) - (target or 0.0)))
        rep = next((r for r in cyc.revolutions if abs(r.i_top - best["row_tdc"]) < 1e-6), None)
    return BikeCore(a, kin, cyc, rep, ser)


def analyze_poses(poses: Sequence[Pose | None], height_cm: float | None, view: View | str, **kw: Any) -> TechniqueAnalysis:
    """``TechniqueAnalysis`` (without key frames) from a pose sequence."""
    return analyze_core(poses, height_cm, view, **kw).analysis


def analyze_video(path: str | Path, height_cm: float | None, view: View | str, progress_cb: tq.ProgressCb | None = None,
                  keyframes_dir: str | Path | None = None, *, estimator: Any = None, model_variant: str = "full",
                  previous: dict[str, Any] | None = None, max_seconds: float | None = None,
                  cancel: threading.Event | None = None) -> TechniqueAnalysis:
    """Analyse a film of a cyclist on the trainer. Raises ``TechniqueError`` (localised)."""
    from lopanalys.core import paths
    from lopanalys.pose.bike import draw
    from lopanalys.pose.models import call_progress
    from lopanalys.pose.offline import grab_frames

    path = Path(path)
    poses, times = tq.read_poses(path, estimator=estimator, model_variant=model_variant, progress_cb=progress_cb,
                                 max_seconds=max_seconds, cancel=cancel)
    core = analyze_core(poses, height_cm, view, times=times, frames_total=len(poses), previous=previous)
    core.analysis.video_path = str(path)
    if core.kin is None:
        raise tq.TechniqueError(t("technique.bikefit.warn.no_cyclist"))
    call_progress(progress_cb, 0.92, t("pose.progress.keyframes"))
    plans = draw.plan(core)
    frames = grab_frames(path, {f for p in plans for f in p.frames})
    out_dir = Path(keyframes_dir) if keyframes_dir else paths.keyframes_dir() / path.stem
    core.analysis.keyframes = draw.render_all(core, plans, frames, out_dir, stem=path.stem[:40] or "kf")
    call_progress(progress_cb, 1.0, "")
    return core.analysis
