"""A parametric athlete on the SkiErg: synthetic poses (and simple drawn frames) with known truth.

The counterpart of ``pose.synthetic`` for double poling. The feet stand still, the hands pull the
handles from above eye level down to the thighs while the trunk folds forward at the hips, and
everything returns during the recovery. The posture at the start and at the finish, the order of
trunk and arms, and the rhythm are the parameters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from lopanalys.pose import synthetic, technique_synth as ts
from lopanalys.pose.ski.analysis import ONSET
from lopanalys.pose.types import LM, Pose

_THIGH_SHARE = 0.55      # of the knee flexion that tilts the thigh back (the rest tilts the shank forward)


@dataclass
class SkierParams(ts.SynthBase):
    """Everything that shapes the synthetic skier. Lengths in cm, angles in degrees, times in ms."""
    stroke_rate_spm: float = 40.0
    drive_ms: float = 600.0
    hand_height_start_cm: float = 10.0     # wrist above the nose at the start
    elbow_angle_start_deg: float = 95.0    # 180 = straight arm
    elbow_angle_end_deg: float = 155.0
    trunk_lean_start_deg: float = 4.0
    trunk_lean_end_deg: float = 50.0       # from the vertical at the finish
    knee_flex_start_deg: float = 6.0
    knee_flex_end_deg: float = 34.0
    arm_end_deg: float = 45.0              # shoulder angle (trunk to upper arm) at the finish: the arm hangs down
    trunk_lead_ms: float = 60.0            # + = the trunk starts before the elbow extends
    heel_raise_cm: float = 3.0             # heel off the floor at the start
    rate_jitter: float = 0.0               # stroke times vary by up to this share (uneven rhythm)
    width: int = 360
    height: int = 640


def _smooth(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def _smooth_inv(y: float) -> float:
    """u with smoothstep(u) = y."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _smooth(mid) < y else (lo, mid)
    return (lo + hi) / 2


class SkierModel:
    """Kinematics of the synthetic skier. ``joints(t)`` returns 3D landmark positions (33, 3)."""

    def __init__(self, p: SkierParams) -> None:
        self.p = p
        H = p.height_cm / 100.0
        self.H = H
        self.Lt, self.Ls, self.Lf = 0.245 * H, 0.246 * H, 0.152 * H
        self.ankle_fwd, self.ankle_up = 0.22 * self.Lf, 0.039 * H
        self.Ltr = 0.288 * H
        self.Lua, self.Lfa = 0.186 * H, 0.146 * H
        self.hip_w, self.sh_w = 0.095 * H, 0.20 * H
        self.T = 60.0 / p.stroke_rate_spm
        self.d = p.drive_ms / 1000.0
        # the stroke starts, with optional jitter in the rhythm
        rng = np.random.default_rng(p.seed + 3)
        n = int(p.duration_s / self.T) + 4
        periods = self.T * (1.0 + (rng.uniform(-p.rate_jitter, p.rate_jitter, n) if p.rate_jitter else np.zeros(n)))
        self.starts = 0.35 + np.concatenate([[0.0], np.cumsum(periods)]) - self.T
        # ``trunk_lead_ms`` is the lead as the analysis defines it (both joints at ONSET of their travel)
        self.offset = (p.trunk_lead_ms / 1000.0) / (1.0 - _smooth_inv(ONSET))
        self.alpha0 = self._solve_arm_start()

    # --- the posture for given phases -----------------------------------------------------------
    def _body(self, s_trunk: float, s_arm: float, s_elbow: float, alpha_start: float) -> dict[str, np.ndarray]:
        p = self.p
        lean = math.radians(p.trunk_lean_start_deg + (p.trunk_lean_end_deg - p.trunk_lean_start_deg) * s_trunk)
        kf = math.radians(p.knee_flex_start_deg + (p.knee_flex_end_deg - p.knee_flex_start_deg) * s_trunk)
        lift = (p.heel_raise_cm / 100.0) * (1.0 - s_trunk)
        phi = math.asin(min(lift / self.Lf, 0.9))                 # the foot pivots on the toe
        toe = np.array([self.Lf * 0.78, 0.0])
        u = np.array([math.cos(phi), -math.sin(phi)])
        nrm = np.array([math.sin(phi), math.cos(phi)])
        heel = toe - self.Lf * u
        ankle = heel + self.ankle_fwd * u + self.ankle_up * nrm
        sig, th = (1 - _THIGH_SHARE) * kf, _THIGH_SHARE * kf
        knee = ankle + self.Ls * np.array([math.sin(sig), math.cos(sig)])
        hip = knee + self.Lt * np.array([-math.sin(th), math.cos(th)])
        tdir = np.array([math.sin(lean), math.cos(lean)])
        sh = hip + self.Ltr * tdir
        alpha = alpha_start + (p.arm_end_deg - alpha_start) * s_arm
        ua = synthetic._rot(-tdir, alpha)                         # shoulder -> elbow
        beta = p.elbow_angle_start_deg + (p.elbow_angle_end_deg - p.elbow_angle_start_deg) * s_elbow
        fa = synthetic._rot(ua, 180.0 - beta)
        el = sh + self.Lua * ua
        wr = el + self.Lfa * fa
        nose = sh + np.array([0.06 + 0.05 * math.sin(lean), 0.0]) * 1.0 + 0.1 * self.H * np.array(
            [math.sin(lean * 0.8), math.cos(lean * 0.8)])
        return dict(toe=toe, heel=heel, ankle=ankle, knee=knee, hip=hip, sh=sh, el=el, wr=wr, nose=nose, fa=fa)

    def _solve_arm_start(self) -> float:
        """The shoulder angle at the start that puts the wrist ``hand_height_start_cm`` above the nose."""
        want = self.p.hand_height_start_cm / 100.0
        lo, hi = 60.0, 200.0
        for _ in range(50):
            mid = (lo + hi) / 2
            b = self._body(0.0, 0.0, 0.0, mid)
            lo, hi = (mid, hi) if b["wr"][1] - b["nose"][1] < want else (lo, mid)
        return (lo + hi) / 2

    def phases(self, t: float) -> tuple[float, float, float]:
        """(trunk, arm, elbow) progress 0..1 at time ``t``."""
        k = int(np.searchsorted(self.starts, t, side="right")) - 1
        k = min(max(k, 0), len(self.starts) - 2)
        u = t - self.starts[k]
        period = self.starts[k + 1] - self.starts[k]
        d, off = self.d, self.offset
        if u >= d:
            s = 1.0 - _smooth((u - d) / max(period - d, 1e-6))
            return s, s, s
        lag_trunk, lag_elbow = max(0.0, -off), max(0.0, off)
        s_trunk = _smooth((u - lag_trunk) / max(d - lag_trunk, 1e-6))
        s_elbow = _smooth((u - lag_elbow) / max(d - lag_elbow, 1e-6))
        return s_trunk, _smooth(u / d), s_elbow

    def joints(self, t: float) -> np.ndarray:
        """(33, 3) landmark positions in metres at time ``t``."""
        b = self._body(*self.phases(t), self.alpha0)
        out = np.zeros((33, 3))
        for pre, sgn in (("l_", 1.0), ("r_", -1.0)):
            hy, sy = sgn * self.hip_w / 2, sgn * self.sh_w / 2
            for name, key, y in (("hip", "hip", hy), ("knee", "knee", hy), ("ankle", "ankle", hy), ("heel", "heel", hy),
                                 ("foot", "toe", hy), ("shoulder", "sh", sy), ("elbow", "el", sy), ("wrist", "wr", sy)):
                out[LM[pre + name]] = [b[key][0], y, b[key][1]]
            hand = b["wr"] + 0.05 * b["fa"]
            for k in ((17, 19, 21) if pre == "l_" else (18, 20, 22)):
                out[k] = [hand[0], sy, hand[1]]
        nose = np.array([b["nose"][0], 0.0, b["nose"][1]])
        out[0] = nose
        for k, off in {1: (-0.02, 0.02, 0.03), 2: (-0.02, 0.03, 0.03), 3: (-0.025, 0.04, 0.03),
                       4: (-0.02, -0.02, 0.03), 5: (-0.02, -0.03, 0.03), 6: (-0.025, -0.04, 0.03),
                       7: (-0.09, 0.07, 0.02), 8: (-0.09, -0.07, 0.02),
                       9: (-0.01, 0.025, -0.04), 10: (-0.01, -0.025, -0.04)}.items():
            out[k] = nose + np.array(off)
        return out


def generate(p: SkierParams) -> tuple[list[Pose | None], list[float]]:
    """Synthetic pose sequence and the time of every frame."""
    return ts.generate(SkierModel(p), p)


def truth(p: SkierParams) -> dict[str, float]:
    """The true values the analysis should recover."""
    T = 60.0 / p.stroke_rate_spm
    rec = 1000.0 * T - p.drive_ms
    return {
        "stroke_rate_spm": p.stroke_rate_spm, "drive_ms": p.drive_ms, "recovery_ms": rec,
        "recovery_ratio": rec / p.drive_ms, "hand_height_start_cm": p.hand_height_start_cm,
        "elbow_angle_start_deg": p.elbow_angle_start_deg, "trunk_lean_end_deg": p.trunk_lean_end_deg,
        "knee_flex_end_deg": p.knee_flex_end_deg,
        "hip_flex_end_deg": p.trunk_lean_end_deg + _THIGH_SHARE * p.knee_flex_end_deg,
        "trunk_lead_ms": p.trunk_lead_ms, "heel_raise_cm": p.heel_raise_cm,
    }


# --- drawing (films for the tests, the review tool and the screenshots) -----------------------------------

def background(p: ts.SynthBase) -> np.ndarray:
    """A plain studio background with the SkiErg's post in front of the athlete, BGR."""
    h, w = p.height, p.width
    yy = np.linspace(0, 1, h)[:, None]
    base = np.array([62, 58, 55]) * (1 - 0.45 * yy) + np.array([18, 18, 20]) * 0.45 * yy
    img = np.ascontiguousarray(np.clip(np.repeat(base[:, None, :], w, axis=1), 0, 255).astype(np.uint8))
    proj = synthetic.Projector(p)        # type: ignore[arg-type]
    pt = lambda x, z: tuple(np.int32(proj(np.array([x, 0.0, z]))))      # noqa: E731
    wd = max(3, int(0.05 * proj.px_per_m))
    cv2.line(img, pt(0.75, 0.0), pt(0.75, 2.12), (80, 82, 88), wd, cv2.LINE_AA)
    cv2.circle(img, pt(0.75, 2.0), int(0.2 * proj.px_per_m), (48, 48, 52), -1, cv2.LINE_AA)
    cv2.line(img, pt(-0.2, 0.0), pt(0.9, 0.0), (95, 95, 100), 2, cv2.LINE_AA)
    return img


def write_video(path: str, p: SkierParams):   # noqa: ANN201
    return ts.write_video(path, SkierModel(p), p, background)
