"""Key frames for the bike fit report: the bottom and the top of the stroke, 3 o'clock, the posture,
the four crank positions as a strip, and the knees' track from the front."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from lopanalys.core.i18n import t
from lopanalys.pose import annotate as an
from lopanalys.pose import technique as tq
from lopanalys.pose import technique_draw as td
from lopanalys.pose.bike.analysis import BALL_OF_FOOT, BikeCore
from lopanalys.pose.findings import num
from lopanalys.pose.series import is_side_view
from lopanalys.pose.types import KeyFrame, Pose, Side, View

PREFIX = "technique.bikefit"
KIND_OF = {"saddle": "bottom", "hip_rock": "bottom", "knee_top": "top", "hip_closed": "top", "trunk": "posture",
           "reach": "posture", "knee_track": "front_track"}


def plan(core: BikeCore) -> list[td.Plan]:
    """The key frames of the revolution closest to the median (none when the film could not be judged)."""
    a, kin, rev = core.analysis, core.kin, core.rep
    if kin is None or rev is None:
        return []
    row = next((r for r in a.cycles if abs(r["row_tdc"] - rev.i_top) < 1e-6), None)
    if row is None:
        return []
    fr = lambda r: tq.frame_of_row(kin, r)      # noqa: E731
    if not is_side_view(a.view):
        side = Side(row["side"])
        plans = [td.Plan("front_track", side, rev.i_forward, [fr(rev.i_forward)], row)]
    else:
        side = tq.near(a.view)
        marks = [("bottom", row["row_bottom"]), ("top", row["row_top"]), ("forward", row["row_forward"]),
                 ("posture", rev.i_back)]
        plans = [td.Plan(k, side, r, [fr(r)], row) for k, r in marks]
        strip = [(t(f"{PREFIX}.kf.clock_{h}"), r) for h, r in ((12, rev.i_top), (3, rev.i_forward), (6, rev.i_bottom),
                                                              (9, rev.i_back))]
        rows = rev.rows(len(kin))
        plans.append(td.Plan("sequence", side, rev.i_top, [fr(r) for _, r in strip], row, rows=strip,
                             path=kin.side_lm(side, "ankle")[rows]))
    td.attach(a.findings, plans, KIND_OF)
    return plans


def _draw(core: BikeCore):   # noqa: ANN202
    a, kin = core.analysis, core.kin
    R = a.ranges
    facing = a.facing or 1

    def draw(cv: an.Canvas, pose: Pose, p: td.Plan, chips: td.Chips) -> None:
        pre = "l_" if p.side == Side.LEFT else "r_"
        v, u = p.values, cv.u
        L = lambda key, name, d=0: t(f"{PREFIX}.label.{name}", value=num(v.get(key), d))     # noqa: E731

        def angle(kind: str, key: str, name: str, parts: tuple[str, str, str]) -> None:
            if v.get(key) is None:
                return
            col = td.color(R, key)
            (td.flexion if kind == "flex" else td.interior)(cv, pose, *(pre + x for x in parts), col, L(key, name))
            chips.append((L(key, name), col))

        if p.kind == "bottom":
            angle("flex", "knee_flex_bottom_deg", "knee", ("hip", "knee", "ankle"))
            ring = td.flagged(R, "knee_flex_bottom_deg")
            if ring:
                cv.ring(cv.lm(pose, pre + "knee"), 70 * u, ring)
            if v.get("ankle_range_deg") is not None:
                chips.append((L("ankle_range_deg", "ankle"), an.MUTED))
            if v.get("hip_bounce_cm") is not None:
                chips.append((L("hip_bounce_cm", "hip_bounce", 1), td.color(R, "hip_bounce_cm")))
                ring = td.flagged(R, "hip_bounce_cm")
                if ring:
                    cv.ring(cv.lm(pose, pre + "hip"), 60 * u, ring)
        elif p.kind == "top":
            angle("flex", "knee_flex_top_deg", "knee", ("hip", "knee", "ankle"))
            angle("int", "hip_angle_top_deg", "hip", ("shoulder", "hip", "knee"))
            for key, part in (("knee_flex_top_deg", "knee"), ("hip_angle_top_deg", "hip")):
                ring = td.flagged(R, key)
                if ring:
                    cv.ring(cv.lm(pose, pre + part), 65 * u, ring)
        elif p.kind == "forward":
            knee = cv.lm(pose, pre + "knee")
            pedal = cv.p(td.lm(pose, pre + "heel") + BALL_OF_FOOT * (td.lm(pose, pre + "foot") - td.lm(pose, pre + "heel")))
            cv.dashed(knee, (knee[0], pedal[1] + 20 * u), an.TEXT + (210,), 4 * u)
            cv.joint(pedal, an.MUTED, 8 * u)
            if v.get("knee_over_pedal_cm") is not None:
                text = L("knee_over_pedal_cm", "kops", 1)
                cv.label((knee[0] + facing * 30 * u, (knee[1] + pedal[1]) / 2), text, an.MUTED, size=27,
                         align="left" if facing > 0 else "right")
                chips.append((text, an.MUTED))
        elif p.kind == "posture":
            hip, sh = cv.lm(pose, pre + "hip"), cv.lm(pose, pre + "shoulder")
            if v.get("trunk_angle_deg") is not None:
                col = td.color(R, "trunk_angle_deg")
                td.against(cv, hip, sh, 0.0 if facing > 0 else 180.0, col, L("trunk_angle_deg", "trunk"), share=0.4)
                chips.append((L("trunk_angle_deg", "trunk"), col))
            angle("int", "shoulder_angle_deg", "shoulder", ("hip", "shoulder", "elbow"))
            angle("flex", "elbow_flex_deg", "elbow", ("shoulder", "elbow", "wrist"))
            ring = td.flagged(R, "elbow_flex_deg") or td.flagged(R, "shoulder_angle_deg")
            if ring:
                cv.ring(cv.lm(pose, pre + "elbow"), 60 * u, ring)
        elif p.kind == "front_track" and kin is not None:
            n = len(kin)
            for sd in (Side.LEFT, Side.RIGHT):
                q = "l_" if sd == Side.LEFT else "r_"
                revs = [r for r in (core.cycles.revolutions if core.cycles else []) if r.side == sd][1:11]
                for r in revs:
                    td.track(cv, kin.side_lm(sd, "knee")[r.rows(n)], an.SIDE_COLOR[sd], 3.0)
                cv.dashed(cv.lm(pose, q + "hip"), cv.lm(pose, q + "ankle"), an.TEXT + (190,), 3 * u)
                key = f"knee_lateral_{sd.value}_cm"
                val = a.summary.get(key)
                if val is not None:
                    judged = sd == p.side
                    col = td.color(R, "knee_lateral_cm") if judged else an.MUTED
                    chips.append((t(f"{PREFIX}.label.knee_lateral", side=t(f"pose.side_title.{sd.value}"),
                                    value=num(val, 1)), col))
            ring = td.flagged(R, "knee_lateral_cm")
            if ring:
                cv.ring(cv.lm(pose, pre + "knee"), 65 * u, ring)

    return draw


def render_all(core: BikeCore, plans: list[td.Plan], frames: dict[int, np.ndarray], out_dir: str | Path,
               stem: str = "kf") -> list[KeyFrame]:
    """Draw and save the planned key frames. ``frames`` maps source frame index -> BGR image."""
    if core.kin is None:
        return []
    side = is_side_view(core.analysis.view)
    return td.render_all(core.kin, View(core.analysis.view), plans, frames, out_dir, stem, PREFIX, _draw(core),
                         aspect=1.05 if side else 0.66)
