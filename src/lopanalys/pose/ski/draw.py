"""Key frames for the double poling report: the start, the middle of the drive, the finish, the
recovery, and the four moments as a strip with the hand's path."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from lopanalys.core.i18n import t
from lopanalys.pose import annotate as an
from lopanalys.pose import technique as tq
from lopanalys.pose import technique_draw as td
from lopanalys.pose.findings import num
from lopanalys.pose.ski.analysis import SkiCore
from lopanalys.pose.types import KeyFrame, Pose, Side, View

PREFIX = "technique.ski"
KIND_OF = {"hand_height": "start", "elbow_start": "start", "sequence": "mid_drive", "trunk": "finish",
           "hip_hinge": "finish", "knee_squat": "finish", "rhythm": "recovery"}


def plan(core: SkiCore) -> list[td.Plan]:
    """The key frames of the stroke closest to the median (none when the film could not be judged)."""
    a, kin, st = core.analysis, core.kin, core.rep
    if kin is None or st is None:
        return []
    row = next((r for r in a.cycles if abs(r["row_start"] - st.i_start) < 1e-6), None)
    if row is None:
        return []
    side = tq.near(a.view)
    # the middle of the drive: the wrist passes shoulder height on its way down
    wy, sy = kin.side_lm(side, "wrist")[:, 1], kin.side_lm(side, "shoulder")[:, 1]
    i0, i1 = int(np.ceil(st.i_start)), int(np.floor(st.i_bottom))
    below = np.flatnonzero(wy[i0:i1 + 1] >= sy[i0:i1 + 1])
    mid = float(i0 + below[0]) if len(below) else (st.i_start + st.i_bottom) / 2
    marks = [("start", st.i_top), ("mid_drive", mid), ("finish", st.i_bottom), ("recovery", (st.i_end + st.i_next) / 2)]
    fr = lambda r: tq.frame_of_row(kin, r)      # noqa: E731
    plans = [td.Plan(k, side, r, [fr(r)], row) for k, r in marks]
    rows = np.arange(int(np.ceil(st.i_top)), min(len(kin), int(np.floor(st.i_next)) + 1))
    plans.append(td.Plan("sequence", side, st.i_top, [fr(r) for _, r in marks], row,
                         rows=[(t(f"{PREFIX}.kf.{k}"), r) for k, r in marks], path=kin.side_lm(side, "wrist")[rows]))
    td.attach(a.findings, plans, KIND_OF)
    return plans


def _draw(core: SkiCore):   # noqa: ANN202
    a, kin = core.analysis, core.kin
    R = a.ranges
    facing = a.facing or 1

    def draw(cv: an.Canvas, pose: Pose, p: td.Plan, chips: td.Chips) -> None:
        pre = "l_" if p.side == Side.LEFT else "r_"
        v, u = p.values, cv.u
        L = lambda key, name, d=0: t(f"{PREFIX}.label.{name}", value=num(v.get(key), d))     # noqa: E731
        hip, sh = cv.lm(pose, pre + "hip"), cv.lm(pose, pre + "shoulder")

        def angle(kind: str, key: str, name: str, parts: tuple[str, str, str], ring: str | None = None) -> None:
            if v.get(key) is None:
                return
            col = td.color(R, key)
            (td.flexion if kind == "flex" else td.interior)(cv, pose, *(pre + x for x in parts), col, L(key, name))
            chips.append((L(key, name), col))
            flag = td.flagged(R, key)
            if ring and flag:
                cv.ring(cv.lm(pose, pre + ring), 62 * u, flag)

        def lean(key: str | None) -> None:
            text = t(f"{PREFIX}.label.trunk", value=num(v.get(key))) if key and v.get(key) is not None else ""
            col = td.color(R, key) if key else an.ACCENT
            td.against(cv, hip, sh, -90.0, col, text) if text else cv.dashed(
                hip, (hip[0], hip[1] - 1.05 * (hip[1] - sh[1])), an.TEXT + (170,), 3 * u)
            if text:
                chips.append((text, col))

        if p.kind == "start":
            angle("int", "elbow_angle_start_deg", "elbow", ("shoulder", "elbow", "wrist"), ring="elbow")
            nose, wrist = cv.lm(pose, "nose"), cv.lm(pose, pre + "wrist")
            cv.dashed((nose[0] - 150 * u, nose[1]), (nose[0] + 150 * u, nose[1]), an.TEXT + (210,), 4 * u)
            if v.get("hand_height_start_cm") is not None:
                col = td.color(R, "hand_height_start_cm")
                text = L("hand_height_start_cm", "hand_height")
                cv.label((wrist[0] + facing * 34 * u, wrist[1]), text, col, size=27, align="left" if facing > 0 else "right")
                chips.append((text, col))
                flag = td.flagged(R, "hand_height_start_cm")
                if flag:
                    cv.ring(wrist, 55 * u, flag)
            if v.get("heel_raise_cm") is not None:
                chips.append((L("heel_raise_cm", "heel", 1), an.MUTED))
        elif p.kind == "mid_drive":
            td.interior(cv, pose, pre + "shoulder", pre + "elbow", pre + "wrist", an.ACCENT, "")
            lean(None)
            if v.get("trunk_lead_ms") is not None:
                col = td.color(R, "trunk_lead_ms")
                key = "lead" if v["trunk_lead_ms"] >= 0 else "lag"
                chips.append((t(f"{PREFIX}.label.{key}", value=num(abs(v["trunk_lead_ms"]))), col))
                flag = td.flagged(R, "trunk_lead_ms")
                if flag:
                    cv.ring(cv.lm(pose, pre + "elbow"), 62 * u, flag)
        elif p.kind == "finish":
            lean("trunk_lean_end_deg")
            angle("flex", "hip_flex_end_deg", "hip", ("shoulder", "hip", "knee"), ring="hip")
            angle("flex", "knee_flex_end_deg", "knee", ("hip", "knee", "ankle"), ring="knee")
            flag = td.flagged(R, "trunk_lean_end_deg")
            if flag:
                cv.ring(((hip[0] + sh[0]) / 2, (hip[1] + sh[1]) / 2), 0.42 * abs(hip[1] - sh[1]) + 40 * u, flag)
        elif p.kind == "recovery" and kin is not None:
            top = tq.pose_for_frame(kin, tq.frame_of_row(kin, v["row_top"]))
            y = cv.lm(top, pre + "hip")[1]
            cv.dashed((hip[0] - 170 * u, y), (hip[0] + 170 * u, y), an.TEXT + (200,), 4 * u)
            for key, name, d in (("drive_ms", "drive", 0), ("recovery_ms", "recovery", 0),
                                 ("recovery_ratio", "ratio", 1)):
                if v.get(key) is not None:
                    chips.append((L(key, name, d), td.color(R, key) if key == "recovery_ratio" else an.MUTED))

    return draw


def render_all(core: SkiCore, plans: list[td.Plan], frames: dict[int, np.ndarray], out_dir: str | Path,
               stem: str = "kf") -> list[KeyFrame]:
    """Draw and save the planned key frames. ``frames`` maps source frame index -> BGR image."""
    if core.kin is None:
        return []
    return td.render_all(core.kin, View(core.analysis.view), plans, frames, out_dir, stem, PREFIX, _draw(core),
                         aspect=0.62)
