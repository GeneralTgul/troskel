"""A parametric cyclist on a trainer: synthetic poses (and simple drawn frames) with known truth.

The counterpart of ``pose.synthetic`` for bike fit. The pedal axle moves on a circle around the bottom
bracket with the ball of the foot on it, the hip sits on the saddle, and the knee follows from the two
(a two-link leg). Saddle height is the free parameter, as on a real bike: ``saddle_for_knee`` finds the
height that gives a wanted knee flexion at the bottom, and ``truth`` reads the true angles from the
noise-free model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from lopanalys.pose import synthetic, technique_synth as ts
from lopanalys.pose.series import interior_angle
from lopanalys.pose.types import LM, Pose, Side, View


@dataclass
class CyclistParams(ts.SynthBase):
    """Everything that shapes the synthetic cyclist. Lengths in cm, angles in degrees."""
    cadence_rpm: float = 90.0
    crank_mm: float = 170.0
    saddle_height_cm: float = 78.7       # bottom bracket to the hip joint, along the seat tube
    seat_angle_deg: float = 73.0
    trunk_angle_deg: float = 45.0        # hip -> shoulder against the horizontal
    shoulder_angle_deg: float = 88.0     # hip - shoulder - elbow
    elbow_flex_deg: float = 18.0         # 0 = straight arm
    foot_pitch_deg: float = 22.0         # mean heel-up angle of the foot
    foot_pitch_range_deg: float = 30.0   # how much the foot pitch varies over a revolution
    foot_pitch_phase_deg: float = 110.0  # crank angle where the heel is lowest
    hip_bounce_cm: float = 1.0           # the near hip's rise and fall per revolution
    knee_lateral_cm: float = 1.5         # sideways travel of the knee per revolution (front view)
    knee_track: str = "in"               # "in": falls inwards in the downstroke, "out": outwards at the top
    stance_cm: float = 22.0              # between the feet


class CyclistModel:
    """Kinematics of the synthetic cyclist. ``joints(t)`` returns 3D landmark positions (33, 3)."""

    BB_Z = 0.27       # bottom bracket above the floor, m

    def __init__(self, p: CyclistParams) -> None:
        self.p = p
        H = p.height_cm / 100.0
        self.H = H
        self.Lt, self.Ls = 0.245 * H, 0.246 * H
        self.Lf = 0.152 * H
        self.ankle_fwd, self.ankle_up = 0.22 * self.Lf, 0.039 * H
        self.Ltr = 0.288 * H
        self.Lua, self.Lfa = 0.186 * H, 0.146 * H
        self.hip_w, self.sh_w = 0.095 * H, 0.20 * H
        self.crank = p.crank_mm / 1000.0
        self.omega = 2 * math.pi * p.cadence_rpm / 60.0
        a = math.radians(p.seat_angle_deg)
        self.hip0 = np.array([-math.cos(a), math.sin(a)]) * p.saddle_height_cm / 100.0 + [0.0, self.BB_Z]

    def crank_angle(self, t: float, side: Side) -> float:
        """Radians, 0 = this side's pedal at the top, pi/2 = forward."""
        return self.omega * t + (0.0 if side == Side.LEFT else math.pi)

    def _leg(self, t: float, side: Side) -> dict[str, np.ndarray]:
        p = self.p
        th = self.crank_angle(t, side)
        sgn = 1.0 if side == Side.LEFT else -1.0
        ball = np.array([self.crank * math.sin(th), self.BB_Z + self.crank * math.cos(th)])
        # the heel is highest after the bottom of the stroke and lowest over the top
        phi = math.radians(p.foot_pitch_deg - 0.5 * p.foot_pitch_range_deg
                           * math.cos(th - math.radians(p.foot_pitch_phase_deg)))
        u = np.array([math.cos(phi), -math.sin(phi)])        # heel -> toe
        n = np.array([math.sin(phi), math.cos(phi)])         # up from the sole
        heel, toe = ball - 0.72 * self.Lf * u, ball + 0.28 * self.Lf * u
        ankle = heel + self.ankle_fwd * u + self.ankle_up * n
        hip = self.hip0 + [0.0, 0.5 * p.hip_bounce_cm / 100.0 * math.cos(th)]
        d = ankle - hip
        L = float(np.linalg.norm(d))
        if L >= self.Lt + self.Ls - 1e-6:
            knee = hip + d * self.Lt / (self.Lt + self.Ls)
        else:
            cos_a = (self.Lt ** 2 + L ** 2 - self.Ls ** 2) / (2 * self.Lt * L)
            knee = hip + synthetic._rot(d / L, math.degrees(math.acos(max(-1.0, min(1.0, cos_a))))) * self.Lt
        hip_y, foot_y = sgn * self.hip_w / 2, sgn * p.stance_cm / 200.0
        lat = p.knee_lateral_cm / 100.0
        off = 0.5 * lat * math.sin(th) if p.knee_track == "in" else -lat * max(0.0, math.cos(th)) ** 2
        f = (hip[1] - knee[1]) / max(hip[1] - ankle[1], 1e-6)
        knee_y = hip_y + f * (foot_y - hip_y) - sgn * off      # ``off`` towards the midline
        return dict(hip=np.array([hip[0], hip_y, hip[1]]), knee=np.array([knee[0], knee_y, knee[1]]),
                    ankle=np.array([ankle[0], foot_y, ankle[1]]), heel=np.array([heel[0], foot_y, heel[1]]),
                    toe=np.array([toe[0], foot_y, toe[1]]))

    def joints(self, t: float) -> np.ndarray:
        """(33, 3) landmark positions in metres at time ``t``."""
        p = self.p
        out = np.zeros((33, 3))
        for side, pre in ((Side.LEFT, "l_"), (Side.RIGHT, "r_")):
            leg = self._leg(t, side)
            for name, key in (("hip", "hip"), ("knee", "knee"), ("ankle", "ankle"), ("heel", "heel"), ("foot", "toe")):
                out[LM[pre + name]] = leg[key]
        hip_c = np.array([self.hip0[0], self.hip0[1]])
        tr = math.radians(p.trunk_angle_deg)
        tdir = np.array([math.cos(tr), math.sin(tr)])
        sh_c = hip_c + self.Ltr * tdir
        ua = synthetic._rot(-tdir, p.shoulder_angle_deg)        # shoulder -> elbow, forward and down
        fa = synthetic._rot(ua, p.elbow_flex_deg)
        el, wr = sh_c + self.Lua * ua, sh_c + self.Lua * ua + self.Lfa * fa
        for pre, sgn in (("l_", 1.0), ("r_", -1.0)):
            y = sgn * self.sh_w / 2
            out[LM[pre + "shoulder"]] = [sh_c[0], y, sh_c[1]]
            out[LM[pre + "elbow"]] = [el[0], y * 1.05, el[1]]
            out[LM[pre + "wrist"]] = [wr[0], y * 0.95, wr[1]]
            hand = np.array([wr[0] + 0.05 * fa[0], y * 0.95, wr[1] + 0.05 * fa[1]])
            for k in ((17, 19, 21) if pre == "l_" else (18, 20, 22)):
                out[k] = hand
        nose = np.array([sh_c[0] + 0.14 * self.H * math.cos(tr * 0.6), 0.0, sh_c[1] + 0.075 * self.H])
        out[0] = nose
        for k, off in {1: (-0.02, 0.02, 0.03), 2: (-0.02, 0.03, 0.03), 3: (-0.025, 0.04, 0.03),
                       4: (-0.02, -0.02, 0.03), 5: (-0.02, -0.03, 0.03), 6: (-0.025, -0.04, 0.03),
                       7: (-0.09, 0.07, 0.02), 8: (-0.09, -0.07, 0.02),
                       9: (-0.01, 0.025, -0.04), 10: (-0.01, -0.025, -0.04)}.items():
            out[k] = nose + np.array(off)
        return out


def generate(p: CyclistParams) -> tuple[list[Pose | None], list[float]]:
    """Synthetic pose sequence and the time of every frame."""
    return ts.generate(CyclistModel(p), p)


def truth(p: CyclistParams) -> dict[str, float]:
    """The true values of the near side (the left one from the front), from the noise-free model."""
    m = CyclistModel(p)
    side = Side.RIGHT if View(p.view) == View.SIDE_RIGHT else Side.LEFT
    pre = "l_" if side == Side.LEFT else "r_"
    T = 60.0 / p.cadence_rpm
    tt = np.linspace(0.0, T, 721)
    J = np.array([m.joints(float(x))[:, [0, 2]] for x in tt])       # the sagittal plane
    g = lambda name: J[:, LM[pre + name]]                              # noqa: E731
    knee = 180.0 - interior_angle(g("hip"), g("knee"), g("ankle"))
    hip = interior_angle(g("shoulder"), g("hip"), g("knee"))
    ankle = interior_angle(g("knee"), g("ankle"), g("foot"))
    th = np.array([m.crank_angle(float(x), side) for x in tt]) % (2 * math.pi)
    fwd = int(np.argmin(np.abs(th - math.pi / 2)))
    pedal = g("heel")[fwd] + 0.72 * (g("foot")[fwd] - g("heel")[fwd])
    return {
        "cadence_rpm": p.cadence_rpm, "knee_flex_bottom_deg": float(knee.min()), "knee_flex_top_deg": float(knee.max()),
        "hip_angle_top_deg": float(hip.min()), "trunk_angle_deg": p.trunk_angle_deg,
        "shoulder_angle_deg": p.shoulder_angle_deg, "elbow_flex_deg": p.elbow_flex_deg,
        "ankle_range_deg": float(ankle.max() - ankle.min()), "hip_bounce_cm": p.hip_bounce_cm,
        "knee_over_pedal_cm": float(g("knee")[fwd, 0] - pedal[0]) * 100.0, "knee_lateral_cm": p.knee_lateral_cm,
    }


def saddle_for_knee(p: CyclistParams, knee_flex_bottom_deg: float) -> float:
    """The saddle height (cm) that gives this knee flexion at the bottom of the stroke."""
    lo, hi = 50.0, 95.0
    for _ in range(40):
        mid = (lo + hi) / 2
        q = CyclistParams(**{**p.__dict__, "saddle_height_cm": mid})
        if truth(q)["knee_flex_bottom_deg"] > knee_flex_bottom_deg:
            lo = mid          # too flexed: the saddle is too low
        else:
            hi = mid
    return (lo + hi) / 2


def with_knee(p: CyclistParams, knee_flex_bottom_deg: float) -> CyclistParams:
    return CyclistParams(**{**p.__dict__, "saddle_height_cm": saddle_for_knee(p, knee_flex_bottom_deg)})


# --- drawing (films for the tests, the review tool and the screenshots) -----------------------------------

def background(p: ts.SynthBase) -> np.ndarray:
    """A plain studio background with a bike on a trainer, BGR."""
    h, w = p.height, p.width
    yy = np.linspace(0, 1, h)[:, None]
    base = np.array([62, 58, 55]) * (1 - 0.45 * yy) + np.array([18, 18, 20]) * 0.45 * yy
    img = np.ascontiguousarray(np.clip(np.repeat(base[:, None, :], w, axis=1), 0, 255).astype(np.uint8))
    proj = synthetic.Projector(p)        # type: ignore[arg-type]
    if View(p.view) not in (View.SIDE_LEFT, View.SIDE_RIGHT):
        g = int(proj.ground_px)
        cv2.line(img, (w // 2, g), (w // 2, g - int(0.68 * proj.px_per_m)), (70, 72, 78), 6, cv2.LINE_AA)
        return img
    pt = lambda x, z: tuple(np.int32(proj(np.array([x, 0.0, z]))))      # noqa: E731
    grey, dark = (88, 90, 96), (44, 44, 48)
    bb, rear, front = pt(0.0, 0.27), pt(-0.41, 0.34), pt(0.59, 0.34)
    saddle, head = pt(-0.2, 0.92), pt(0.42, 0.9)
    r = int(0.34 * proj.px_per_m)
    for c in (rear, front):
        cv2.circle(img, c, r, dark, max(2, r // 9), cv2.LINE_AA)
    for a, b in ((bb, saddle), (bb, rear), (saddle, rear), (bb, head), (saddle, head), (head, front)):
        cv2.line(img, a, b, grey, max(2, r // 12), cv2.LINE_AA)
    cv2.line(img, head, pt(0.5, 0.98), grey, max(2, r // 12), cv2.LINE_AA)
    return img


def write_video(path: str, p: CyclistParams):   # noqa: ANN201
    return ts.write_video(path, CyclistModel(p), p, background)
