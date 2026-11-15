"""Running technique measures per ground contact (``StepMetrics``) and over a window (``GaitSummary``).

Side view (sagittal plane), all at initial contact unless named otherwise:
  overstride_cm        heel ahead of the hip (pelvis centre, a centre-of-mass proxy), horizontal
  shin_angle_deg       tibia (knee->ankle) against the vertical, + = ankle ahead of the knee
  knee_flex_*          180 - hip/knee/ankle angle; midstance = peak flexion during contact
  trunk_lean_deg       pelvis->shoulders against the vertical, + = forward, mean over contact
  hip_extension_...    thigh (hip->knee) behind the vertical at toe-off
  foot_angle_deg       heel->toe line at contact minus the same line at flat foot (midstance),
                       + = toe up; strike type after Altman & Davis 2012 (Gait Posture 35:298-300):
                       > 8 deg heel, -1.6..8 midfoot, < -1.6 forefoot
  vertical_osc...      pelvis rise and fall from this contact to the next
Rear/front view (frontal plane):
  pelvic_drop_deg      contralateral pelvic drop during contact, relative to the clip's median pelvis
                       tilt (removes a tilted camera), as in Bramah et al. 2018 (AJSM 46:3406)
  knee_valgus_deg      frontal plane projection angle (FPPA), + = knee inside the hip-ankle line
  step_width_cm        ankle lateral distance between consecutive contacts, - = crossover
  heel_whip_deg        heel swinging inwards (+) or outwards just after toe-off
  arm_cross_cm         wrist past the body midline, + = crosses

Pixel to centimetre: from the athlete's height. Standing height in pixels is estimated from the
segment lengths (thigh + shank = 0.491 x height, pelvis to shoulders = 0.288 x height; Winter,
"Biomechanics and Motor Control of Human Movement", 4th ed., table 4.1). People differ from these
proportions by a few percent and the camera is not perfectly square to the runner, so every
*_cm value carries roughly +/-5-8 % uncertainty (``ScaleInfo.uncertainty_pct``).
"""
from __future__ import annotations

import math
from collections import Counter

import numpy as np

from lopanalys.pose.gait import Contact, GaitEvents
from lopanalys.pose.series import (PoseSeries, angle_from_vertical, interior_angle, interp_track,
                                   is_side_view, lean_from_vertical, nanmedian)
from lopanalys.pose.types import GaitSummary, ScaleInfo, Side, StepMetrics, View

THIGH_SHANK_RATIO = 0.491
TRUNK_RATIO = 0.288


def lateral_sign(view: View | str) -> int:
    """+1 when the athlete's left is on image left (rear view), -1 when mirrored (front view)."""
    return -1 if View(view) == View.FRONT else 1


# --- scale ----------------------------------------------------------------------------------------

def _segment_len(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = np.linalg.norm(a - b, axis=1)
    return d[~np.isnan(d)]


def estimate_scale(s: PoseSeries, height_cm: float | None, view: View | str) -> ScaleInfo | None:
    """Pixels per cm from the athlete's height and the pose's segment lengths."""
    if not height_cm or height_cm < 80 or len(s) < 5:
        return None
    pct = 50 if is_side_view(view) else 85   # frontal views foreshorten flexed segments
    legs = []
    for side in (Side.LEFT, Side.RIGHT):
        th = _segment_len(s.side_lm(side, "hip"), s.side_lm(side, "knee"))
        sh = _segment_len(s.side_lm(side, "knee"), s.side_lm(side, "ankle"))
        if th.size > 5 and sh.size > 5:
            legs.append(np.percentile(th, pct) + np.percentile(sh, pct))
    trunk = _segment_len(s.mid("hip"), s.mid("shoulder"))
    est = []
    if legs:
        est.append((max(legs) / THIGH_SHANK_RATIO, 0.7))
    if trunk.size > 5:
        est.append((np.percentile(trunk, pct) / TRUNK_RATIO, 0.3))
    if not est:
        return None
    w = sum(e[1] for e in est)
    stature = sum(e[0] * e[1] for e in est) / w
    spread = (abs(est[0][0] - est[1][0]) / stature * 100 / 2) if len(est) == 2 else 5.0
    unc = math.sqrt(5.0**2 + spread**2) + (0 if is_side_view(view) else 3.0)
    return ScaleInfo(px_per_cm=float(stature / height_cm), uncertainty_pct=round(float(unc), 1),
                     stature_px=float(stature))


# --- per step ---------------------------------------------------------------------------------------

def _at(s: PoseSeries, track: np.ndarray, t: float) -> np.ndarray:
    return interp_track(s.t, track, t)


def _cm(px: float | None, scale: ScaleInfo | None) -> float | None:
    if px is None or scale is None or not np.isfinite(px):
        return None
    return float(px / scale.px_per_cm)


def _f(x: float | np.floating | None) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def classify_strike(foot_angle_deg: float | None) -> str | None:
    """Altman & Davis 2012 thresholds on the foot strike angle."""
    if foot_angle_deg is None:
        return None
    if foot_angle_deg > 8.0:
        return "heel"
    if foot_angle_deg < -1.6:
        return "forefoot"
    return "midfoot"


def _foot_angle(s: PoseSeries, side: Side, facing: int, rows: slice | np.ndarray | int) -> np.ndarray:
    heel, toe = s.side_lm(side, "heel")[rows], s.side_lm(side, "foot")[rows]
    d = toe - heel
    return np.degrees(np.arctan2(-d[..., 1], facing * d[..., 0]))


def _sagittal_step(s: PoseSeries, c: Contact, nxt: Contact | None, ev: GaitEvents,
                   scale: ScaleInfo | None) -> StepMetrics:
    f = ev.facing
    side = c.side
    hip_s, knee, ankle = (s.side_lm(side, k) for k in ("hip", "knee", "ankle"))
    heel = s.side_lm(side, "heel")
    hip_c, sh_c = s.mid("hip"), s.mid("shoulder")
    t = c.t_ic
    heel_ic, hipc_ic = _at(s, heel, t), _at(s, hip_c, t)
    knee_ic, ankle_ic, hip_ic = _at(s, knee, t), _at(s, ankle, t), _at(s, hip_s, t)
    overstride = _cm(f * (heel_ic[0] - hipc_ic[0]), scale)
    shin = _f(angle_from_vertical(knee_ic, ankle_ic, f))
    kf_ic = _f(180 - interior_angle(hip_ic, knee_ic, ankle_ic))
    kf_mid = trunk = hip_ext = foot_angle = osc = None
    if c.i_to is not None:
        i0, i1 = int(np.ceil(c.i_ic)), int(np.floor(c.i_to)) + 1
        flex = 180 - interior_angle(hip_s[i0:i1], knee[i0:i1], ankle[i0:i1])
        kf_mid = _f(np.nanmax(flex)) if np.any(~np.isnan(flex)) else None
        lean = lean_from_vertical(hip_c[i0:i1], sh_c[i0:i1], f)
        trunk = _f(np.nanmean(lean)) if np.any(~np.isnan(lean)) else None
        hip_to, knee_to = _at(s, hip_s, c.t_to), _at(s, knee, c.t_to)
        hip_ext = _f(-angle_from_vertical(hip_to, knee_to, f))
        # flat-foot reference: 25-45 % of contact
        n = c.i_to - c.i_ic
        r = np.arange(int(np.ceil(c.i_ic + 0.25 * n)), int(np.floor(c.i_ic + 0.45 * n)) + 1)
        if r.size:
            flat = nanmedian(_foot_angle(s, side, f, r).tolist())
            heel_t, toe_t = heel_ic, _at(s, s.side_lm(side, "foot"), t)
            d = toe_t - heel_t
            ic_ang = math.degrees(math.atan2(-d[1], f * d[0]))
            if flat is not None and np.isfinite(ic_ang):
                foot_angle = ic_ang - flat
    if nxt is not None:
        i0, i1 = int(np.floor(c.i_ic)), int(np.ceil(nxt.i_ic)) + 1
        y = hip_c[i0:i1, 1]
        if np.sum(~np.isnan(y)) > 2:
            osc = _cm(float(np.nanmax(y) - np.nanmin(y)), scale)
    return StepMetrics(
        side=side, t_contact=c.t_ic, t_toe_off=c.t_to,
        contact_frame=int(round(float(np.interp(c.i_ic, np.arange(len(s)), s.frame_index)))),
        ground_contact_ms=c.gct_ms, overstride_cm=overstride, shin_angle_deg=shin,
        knee_flex_contact_deg=kf_ic, knee_flex_midstance_deg=kf_mid, trunk_lean_deg=trunk,
        hip_extension_toe_off_deg=hip_ext, foot_strike=classify_strike(foot_angle),
        toe_off_frame=None if c.i_to is None else int(round(float(np.interp(c.i_to, np.arange(len(s)), s.frame_index)))),
        midstance_frame=None if c.i_mid is None else int(round(float(s.frame_index[c.i_mid]))),
        foot_angle_deg=_f(foot_angle), vertical_oscillation_cm=osc,
    )


def pelvic_tilt(s: PoseSeries, view: View | str) -> np.ndarray:
    """Pelvis tilt per frame in a frontal view, degrees, + = right hip lower in the image."""
    m = lateral_sign(view)
    lh, rh = s.lm("l_hip"), s.lm("r_hip")
    return np.degrees(np.arctan2(rh[:, 1] - lh[:, 1], m * (rh[:, 0] - lh[:, 0])))


def fppa(s: PoseSeries, side: Side, view: View | str, rows: slice | np.ndarray) -> np.ndarray:
    """Frontal plane projection angle of the knee, + = knee medial of the hip-ankle line."""
    m = lateral_sign(view)
    hip, knee, ankle = (s.side_lm(side, k)[rows] for k in ("hip", "knee", "ankle"))
    ang = 180 - interior_angle(hip, knee, ankle)
    # which side of the hip->ankle line is the knee on? medial = towards the other leg
    d = ankle - hip
    k = knee - hip
    cross = d[..., 0] * k[..., 1] - d[..., 1] * k[..., 0]
    # rear view (m=+1), left leg: medial is image right (+x); with y down, knee to the right of a
    # downward line gives cross < 0
    medial_sign = -1.0 if side == Side.LEFT else 1.0
    return ang * np.sign(cross * medial_sign * m)


def _midline_x(s: PoseSeries, rows: np.ndarray | slice, at_y: np.ndarray) -> np.ndarray:
    hip, sh = s.mid("hip")[rows], s.mid("shoulder")[rows]
    with np.errstate(invalid="ignore", divide="ignore"):
        a = (at_y - hip[:, 1]) / (sh[:, 1] - hip[:, 1])
    return hip[:, 0] + a * (sh[:, 0] - hip[:, 0])


def _frontal_step(s: PoseSeries, c: Contact, prev_other: Contact | None, nxt: Contact | None,
                  ev: GaitEvents, scale: ScaleInfo | None, tilt0: float) -> StepMetrics:
    view = ev.view
    m = lateral_sign(view)
    side = c.side
    drop = valgus = width = whip = cross = osc = None
    if c.i_to is not None:
        i0, i1 = int(np.ceil(c.i_ic)), int(np.floor(c.i_to)) + 1
        tilt = pelvic_tilt(s, view)[i0:i1] - tilt0
        if np.any(~np.isnan(tilt)):
            # left stance: right hip should not drop (tilt +); right stance: left hip (tilt -)
            drop = _f(np.nanmax(tilt if side == Side.LEFT else -tilt))
        fp = fppa(s, side, view, slice(i0, i1))
        if np.any(~np.isnan(fp)):
            valgus = _f(np.nanmax(fp))
        # heel whip: sideways heel movement relative to the knee in the first 40 % of swing,
        # compared with midstance, as an angle of the shank (asin of offset / shank length)
        if c.i_mid is not None:
            swing_end = c.i_to + 0.85 * (c.i_to - c.i_ic)   # swing is about 2.1 x contact
            j0, j1 = int(np.ceil(c.i_to)), min(len(s), int(np.floor(swing_end)) + 1)
            shank = _segment_len(s.side_lm(side, "knee")[i0:i1], s.side_lm(side, "ankle")[i0:i1])
            if j1 > j0 and shank.size:
                inward = (1.0 if side == Side.LEFT else -1.0) * m

                def lateral(rows: slice | np.ndarray) -> np.ndarray:
                    return inward * (s.side_lm(side, "heel")[rows, 0] - s.side_lm(side, "knee")[rows, 0])

                dev = (lateral(slice(j0, j1)) - lateral(np.array([c.i_mid]))[0]) / float(np.max(shank))
                if np.any(~np.isnan(dev)):
                    k = int(np.nanargmax(np.abs(dev)))
                    whip = _f(np.degrees(np.arcsin(np.clip(dev[k], -1, 1))))
    if c.i_mid is not None and prev_other is not None and prev_other.i_mid is not None:
        a = s.side_lm(side, "ankle")[c.i_mid, 0]
        b = s.side_lm(prev_other.side, "ankle")[prev_other.i_mid, 0]
        lx, rx = (a, b) if side == Side.LEFT else (b, a)
        width = _cm(m * (rx - lx), scale)
    if nxt is not None:
        i0, i1 = int(np.floor(c.i_ic)), int(np.ceil(nxt.i_ic)) + 1
        y = s.mid("hip")[i0:i1, 1]
        if np.sum(~np.isnan(y)) > 2:
            osc = _cm(float(np.nanmax(y) - np.nanmin(y)), scale)
        # the opposite arm swings forward during this contact
        other = Side.RIGHT if side == Side.LEFT else Side.LEFT
        wr = s.side_lm(other, "wrist")[i0:i1]
        mid = _midline_x(s, slice(i0, i1), wr[:, 1])
        sign = m * (1.0 if other == Side.LEFT else -1.0)
        over = sign * (wr[:, 0] - mid)
        if np.any(~np.isnan(over)):
            cross = _cm(float(np.nanmax(over)), scale)
    return StepMetrics(
        side=side, t_contact=c.t_ic, t_toe_off=c.t_to,
        contact_frame=int(round(float(np.interp(c.i_ic, np.arange(len(s)), s.frame_index)))),
        ground_contact_ms=c.gct_ms, overstride_cm=None, shin_angle_deg=None,
        knee_flex_contact_deg=None, knee_flex_midstance_deg=None, trunk_lean_deg=None,
        hip_extension_toe_off_deg=None, foot_strike=None,
        pelvic_drop_deg=drop, knee_valgus_deg=valgus, step_width_cm=width,
        toe_off_frame=None if c.i_to is None else int(round(float(np.interp(c.i_to, np.arange(len(s)), s.frame_index)))),
        midstance_frame=None if c.i_mid is None else int(round(float(s.frame_index[c.i_mid]))),
        vertical_oscillation_cm=osc, heel_whip_deg=whip, arm_cross_cm=cross,
    )


def step_metrics(s: PoseSeries, ev: GaitEvents, scale: ScaleInfo | None) -> list[StepMetrics]:
    """One ``StepMetrics`` per detected contact, in time order."""
    out = []
    cs = ev.contacts
    side_view = is_side_view(ev.view)
    tilt0 = 0.0
    if not side_view:
        tl = pelvic_tilt(s, ev.view)
        tilt0 = float(np.nanmedian(tl)) if np.any(~np.isnan(tl)) else 0.0
    for k, c in enumerate(cs):
        nxt = cs[k + 1] if k + 1 < len(cs) else None
        if side_view:
            out.append(_sagittal_step(s, c, nxt, ev, scale))
        else:
            prev_other = next((p for p in reversed(cs[:k]) if p.side != c.side), None)
            out.append(_frontal_step(s, c, prev_other, nxt, ev, scale, tilt0))
    return out


# --- summary --------------------------------------------------------------------------------------

ASYM_KEYS = {  # metric -> smallest mean magnitude for which a percentage is meaningful
    "ground_contact_ms": 50.0, "overstride_cm": 3.0, "shin_angle_deg": 4.0,
    "knee_flex_contact_deg": 12.0, "knee_flex_midstance_deg": 15.0, "hip_extension_toe_off_deg": 5.0,
    "pelvic_drop_deg": 2.0, "knee_valgus_deg": 3.0, "vertical_oscillation_cm": 3.0,
}
# From the side the far leg is hidden behind the near one, so its angles and distances are guesses.
# Only the contact time (from the foot events, which both feet give) is compared there.
SIDE_VIEW_ASYM_KEYS = {"ground_contact_ms"}
ASYM_MAX_PCT = 100.0   # a larger difference is a measurement error, not a runner


def asymmetry(steps: list[StepMetrics], side_view: bool = False) -> dict[str, float]:
    """Left/right difference in percent of the mean, + = left larger (only where meaningful).

    Left out: values with opposite signs (the percentage is then 200 % by construction, real
    hardware 2026-11-14), differences over ASYM_MAX_PCT, and in a side view everything but the contact time.
    """
    out: dict[str, float] = {}
    for key, floor in ASYM_KEYS.items():
        if side_view and key not in SIDE_VIEW_ASYM_KEYS:
            continue
        lv = nanmedian(getattr(s, key) for s in steps if s.side == Side.LEFT)
        rv = nanmedian(getattr(s, key) for s in steps if s.side == Side.RIGHT)
        if lv is None or rv is None or lv * rv < 0:
            continue
        mean = (abs(lv) + abs(rv)) / 2
        if mean < floor:
            continue
        pct = 100.0 * (lv - rv) / mean
        if abs(pct) > ASYM_MAX_PCT:
            continue
        out[key] = round(pct, 1)
    return out


def arm_swing(s: PoseSeries, view: View | str) -> float | None:
    """Swing of the near upper arm in degrees: the range (5th to 95th percentile) of its angle to the
    trunk, forward and back together. Side view only."""
    side = Side.LEFT if View(view) == View.SIDE_LEFT else Side.RIGHT
    sh, el, hip = s.side_lm(side, "shoulder"), s.side_lm(side, "elbow"), s.side_lm(side, "hip")
    arm, trunk = np.asarray(el, float) - sh, np.asarray(hip, float) - sh
    with np.errstate(invalid="ignore"):
        ang = np.degrees(np.arctan2(trunk[:, 0] * arm[:, 1] - trunk[:, 1] * arm[:, 0],
                                    np.sum(trunk * arm, axis=-1)))
    ang = ang[~np.isnan(ang)]
    if ang.size < 10:
        return None
    return float(np.percentile(ang, 95) - np.percentile(ang, 5))


def elbow_angle(s: PoseSeries, view: View | str) -> float | None:
    """Median elbow angle of the arm nearest the camera (side view)."""
    side = Side.LEFT if View(view) == View.SIDE_LEFT else Side.RIGHT
    a = interior_angle(s.side_lm(side, "shoulder"), s.side_lm(side, "elbow"), s.side_lm(side, "wrist"))
    a = a[~np.isnan(a)]
    return float(np.median(a)) if a.size else None


def summarize(steps: list[StepMetrics], ev: GaitEvents, s: PoseSeries | None = None,
              t_from: float | None = None, t_to: float | None = None,
              speed_kmh: float | None = None) -> GaitSummary:
    """Medians over the steps (and cadence from the contact times) for a window."""
    if t_from is not None or t_to is not None:
        lo = -np.inf if t_from is None else t_from
        hi = np.inf if t_to is None else t_to
        steps = [x for x in steps if lo <= x.t_contact <= hi]
        cs = [c for c in ev.contacts if lo <= c.t_ic <= hi]
        ev = GaitEvents(cs, ev.facing, ev.view, ev.ground_y, ev.threshold_px)
    times = [x.t_contact for x in steps]
    cad = ev.cadence_spm()
    gct = nanmedian(x.ground_contact_ms for x in steps)
    flight = duty = None
    if cad and gct:
        step_ms = 60000.0 / cad
        flight = step_ms - gct
        duty = 100.0 * gct / (2 * step_ms)
    strikes = Counter(x.foot_strike for x in steps if x.foot_strike)
    side_view = is_side_view(ev.view)

    def med(key: str) -> float | None:
        return nanmedian(getattr(x, key) for x in steps)

    return GaitSummary(
        t_from=float(t_from if t_from is not None else (min(times) if times else 0.0)),
        t_to=float(t_to if t_to is not None else (max(times) if times else 0.0)),
        steps=len(steps), cadence_spm=cad, ground_contact_ms=gct,
        vertical_oscillation_cm=med("vertical_oscillation_cm"),
        overstride_cm=med("overstride_cm"), shin_angle_deg=med("shin_angle_deg"),
        knee_flex_contact_deg=med("knee_flex_contact_deg"),
        knee_flex_midstance_deg=med("knee_flex_midstance_deg"),
        trunk_lean_deg=med("trunk_lean_deg"),
        hip_extension_toe_off_deg=med("hip_extension_toe_off_deg"),
        foot_strike=strikes.most_common(1)[0][0] if strikes else None,
        pelvic_drop_deg=med("pelvic_drop_deg"), knee_valgus_deg=med("knee_valgus_deg"),
        step_width_cm=med("step_width_cm"), asymmetry=asymmetry(steps, side_view),
        foot_angle_deg=med("foot_angle_deg"), flight_ms=flight, duty_factor_pct=duty,
        elbow_angle_deg=elbow_angle(s, ev.view) if (s is not None and side_view) else None,
        arm_swing_deg=arm_swing(s, ev.view) if (s is not None and side_view) else None,
        arm_cross_cm=med("arm_cross_cm"), heel_whip_deg=med("heel_whip_deg"),
        speed_kmh=speed_kmh, view=View(ev.view).value,
    )
