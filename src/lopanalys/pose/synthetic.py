"""A parametric runner on a treadmill: synthetic poses (and simple rendered frames) with known truth.

Used by the tests (the analysis must recover cadence, contact time, overstride, trunk lean, pelvic
drop ... from these poses), and by demo mode when no camera or pose model is available.

The model is 3D (x forward, y to the athlete's left, z up, metres) and projected orthographically
to the chosen camera view. Segment lengths follow Winter's anthropometric proportions of standing
height (Winter, "Biomechanics and Motor Control of Human Movement"). On a treadmill the hip stays in
place and the stance foot moves backwards with the belt; the swing foot follows a smooth path from
toe-off to the next contact with a little foot retraction before landing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from lopanalys.pose.types import LM, Pose, Side, View


@dataclass
class RunnerParams:
    """Everything that shapes the synthetic runner. Lengths in cm, angles in degrees."""
    height_cm: float = 178.0
    speed_kmh: float = 12.0
    cadence_spm: float = 170.0
    ground_contact_ms: float = 225.0
    gct_asymmetry_pct: float = 0.0       # + = left foot longer contact
    overstride_cm: float = 20.0          # heel ahead of the hip at initial contact
    foot_strike_deg: float = 12.0        # foot angle at contact, + toe up (heel strike), - forefoot
    trunk_lean_deg: float = 6.0
    vertical_oscillation_cm: float = 8.0
    pelvic_drop_deg: float = 0.0         # contralateral hip drop at midstance (rear view)
    knee_valgus_deg: float = 0.0         # frontal plane projection angle at midstance
    step_width_cm: float = 8.0
    heel_whip_deg: float = 0.0
    arm_cross_cm: float = -6.0           # + = wrist passes the midline, - = stays short of it
    elbow_deg: float = 85.0
    view: View = View.SIDE_LEFT
    fps: float = 60.0
    duration_s: float = 8.0
    width: int = 1280
    height: int = 720
    noise_px: float = 1.0
    outlier_rate: float = 0.0            # share of frames with one landmark thrown far off
    swap_rate: float = 0.0               # chance per frame that a mislabelling of the legs starts
    swap_frames: tuple[int, int] = (1, 3)  # how many frames a mislabelling lasts (min, max)
    swap_at_crossing: bool = False       # mislabellings start only where the feet pass each other (as
                                         # MediaPipe's do), where position alone cannot tell them apart
    heel_marker_cm: float = 0.0          # the heel landmark sits this far above the sole (MediaPipe's
                                         # heel point is on the back of the heel, not under it)
    far_lift_px: float = 0.0             # side view: the far side drawn this much higher (perspective:
                                         # the far foot stands on a higher belt line in the image)
    time_jitter: float = 0.0             # frame intervals vary by up to this share (a live camera
                                         # over USB: 25 fps achieved with 17-75 ms between frames)
    missing_rate: float = 0.0            # share of frames without a pose
    t0: float = 0.1                      # first left contact
    seed: int = 1


@dataclass
class Truth:
    """Ground truth that the analysis should recover."""
    contacts: list[tuple[Side, float, float]] = field(default_factory=list)  # (side, t_ic, t_to)
    px_per_cm: float = 0.0
    facing: int = 1
    times: list[float] = field(default_factory=list)   # the time of every frame, also those without a pose


def _smooth(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def _rot(v: np.ndarray, deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


class RunnerModel:
    """Kinematics of the synthetic runner. ``joints(t)`` returns 3D landmark positions (33, 3)."""

    def __init__(self, p: RunnerParams) -> None:
        self.p = p
        H = p.height_cm / 100.0
        self.H = H
        self.Lt, self.Ls = 0.245 * H, 0.246 * H       # thigh, shank
        self.Lf = 0.152 * H                           # heel to toe
        self.ankle_fwd, self.ankle_up = 0.22 * self.Lf, 0.039 * H
        self.Ltr = 0.288 * H                          # hip to shoulder
        self.Lua, self.Lfa = 0.186 * H, 0.146 * H     # upper arm, forearm
        self.hip_w = 0.095 * H                        # between hip joint centres
        self.sh_w = 0.20 * H
        self.v = p.speed_kmh / 3.6
        self.T = 60.0 / p.cadence_spm                 # step period
        self.Hm = 0.497 * H                           # mean hip height
        g = p.ground_contact_ms / 1000.0
        a = p.gct_asymmetry_pct / 200.0
        self.gct = {Side.LEFT: g * (1 + a), Side.RIGHT: g * (1 - a)}
        self.ic0 = {Side.LEFT: p.t0, Side.RIGHT: p.t0 + self.T}
        self.hpk = float(np.clip(0.30 + 0.025 * (p.speed_kmh - 10), 0.18, 0.5))
        self.phi_to = 30.0
        # swing height profile, normalised to a peak of 1
        s = np.linspace(0, 1, 401)
        self._gnorm = float(np.max(0.75 * s * (1 - s) ** 2 + 0.25 * s * (1 - s)))

    # --- legs ------------------------------------------------------------------------------------

    def _phase(self, t: float, side: Side) -> tuple[float, float]:
        stride = 2 * self.T
        n = math.floor((t - self.ic0[side]) / stride)
        tic = self.ic0[side] + n * stride
        return tic, t - tic

    def _foot_points(self, heel: np.ndarray, phi: float) -> tuple[np.ndarray, np.ndarray]:
        toe = heel + self.Lf * np.array([math.cos(math.radians(phi)), math.sin(math.radians(phi))])
        ankle = heel + _rot(np.array([self.ankle_fwd, self.ankle_up]), phi)
        return toe, ankle

    def _stance_heel(self, dt: float, gct: float) -> tuple[np.ndarray, float]:
        """Heel (x, z) and foot angle during stance."""
        p = self.p
        os_ = p.overstride_cm / 100.0
        hx = os_ - self.v * dt
        q = dt / gct
        phi_ic = p.foot_strike_deg
        if q < 0.15:
            phi = phi_ic * (1 - _smooth(q / 0.15))
            pivot_heel = phi_ic >= 0
        elif q < 0.55:
            phi, pivot_heel = 0.0, True
        else:
            phi = -self.phi_to * _smooth((q - 0.55) / 0.45)
            pivot_heel = False
        if pivot_heel:
            return np.array([hx, 0.0]), phi
        toe = np.array([hx + self.Lf, 0.0])
        heel = toe - self.Lf * np.array([math.cos(math.radians(phi)), math.sin(math.radians(phi))])
        return heel, phi

    def _leg(self, t: float, side: Side) -> dict[str, np.ndarray]:
        p = self.p
        tic, dt = self._phase(t, side)
        gct = self.gct[side]
        sgn = 1.0 if side == Side.LEFT else -1.0
        foot_y = sgn * p.step_width_cm / 200.0
        heel_y = toe_y = ankle_y = foot_y
        stance = dt < gct
        if stance:
            heel, phi = self._stance_heel(dt, gct)
        else:
            tsw = 2 * self.T - gct
            s = (dt - gct) / tsw
            h0, phi0 = self._stance_heel(gct - 1e-9, gct)
            h1, phi1 = self._stance_heel(0.0, gct)
            m0, m1 = -self.v * tsw, -0.4 * self.v * tsw
            hx = ((2 * s**3 - 3 * s**2 + 1) * h0[0] + (s**3 - 2 * s**2 + s) * m0
                  + (-2 * s**3 + 3 * s**2) * h1[0] + (s**3 - s**2) * m1)
            g = (0.75 * s * (1 - s) ** 2 + 0.25 * s * (1 - s)) / self._gnorm
            hz = h0[1] * (1 - s) ** 2 + h1[1] * s**2 + self.hpk * g
            phi = phi0 + (phi1 - phi0) * _smooth(s) - 25.0 * math.sin(math.pi * s) * (1 - s)
            heel = np.array([hx, hz])
            if p.heel_whip_deg and s < 0.45:
                w = self.Ls * math.sin(math.radians(p.heel_whip_deg)) * math.sin(math.pi * s / 0.45)
                heel_y -= sgn * w
                ankle_y -= sgn * 0.5 * w
                toe_y += sgn * 0.5 * w
        toe, ankle = self._foot_points(heel, phi)
        heel[1], toe[1] = max(heel[1], 0.0), max(toe[1], 0.0)
        return dict(heel=np.array([heel[0], heel_y, heel[1]]), toe=np.array([toe[0], toe_y, toe[1]]),
                    ankle=np.array([ankle[0], ankle_y, ankle[1]]), stance=np.array(stance),
                    q=np.array(dt / gct if stance else -1.0))

    def _knee(self, hip: np.ndarray, ankle: np.ndarray, valgus_deg: float, side: Side) -> np.ndarray:
        d = np.array([ankle[0] - hip[0], ankle[2] - hip[2]])
        L = float(np.linalg.norm(d))
        if L >= self.Lt + self.Ls - 1e-6:
            k2 = np.array([hip[0], hip[2]]) + d * self.Lt / (self.Lt + self.Ls)
        else:
            cos_a = (self.Lt**2 + L**2 - self.Ls**2) / (2 * self.Lt * L)
            alpha = math.degrees(math.acos(max(-1.0, min(1.0, cos_a))))
            k2 = np.array([hip[0], hip[2]]) + _rot(d / L, alpha) * self.Lt
        f = self.Lt / (self.Lt + self.Ls)
        ky = hip[1] + (ankle[1] - hip[1]) * f
        if valgus_deg:
            zt, zs = hip[2] - k2[1], k2[1] - ankle[2]
            delta = (2 * zt * zs / max(zt + zs, 1e-6)) * math.tan(math.radians(valgus_deg) / 2)
            ky -= (1.0 if side == Side.LEFT else -1.0) * delta
        return np.array([k2[0], ky, k2[1]])

    # --- whole body ------------------------------------------------------------------------------

    def joints(self, t: float) -> np.ndarray:
        """(33, 3) landmark positions in metres at time ``t``."""
        p = self.p
        out = np.zeros((33, 3))
        legs = {s: self._leg(t, s) for s in (Side.LEFT, Side.RIGHT)}
        g_mean = (self.gct[Side.LEFT] + self.gct[Side.RIGHT]) / 2
        zc = self.Hm - (p.vertical_oscillation_cm / 200.0) * math.cos(
            2 * math.pi * (t - (p.t0 + g_mean / 2)) / self.T)
        # pelvic drop: the hip opposite the stance leg drops, peaking at midstance
        tilt = 0.0  # + = right hip lower
        for s, leg in legs.items():
            if leg["stance"]:
                tilt = (1 if s == Side.LEFT else -1) * p.pelvic_drop_deg * math.sin(math.pi * float(leg["q"]))
        dz = (self.hip_w / 2) * math.tan(math.radians(tilt))
        hips = {Side.LEFT: np.array([0.0, self.hip_w / 2, zc + dz]),
                Side.RIGHT: np.array([0.0, -self.hip_w / 2, zc - dz])}
        for s in (Side.LEFT, Side.RIGHT):
            leg = legs[s]
            val = p.knee_valgus_deg * math.sin(math.pi * float(leg["q"])) if leg["stance"] else 0.0
            pre = "l_" if s == Side.LEFT else "r_"
            out[LM[pre + "hip"]] = hips[s]
            out[LM[pre + "knee"]] = self._knee(hips[s], leg["ankle"], val, s)
            out[LM[pre + "ankle"]] = leg["ankle"]
            out[LM[pre + "heel"]] = leg["heel"] + np.array([0.0, 0.0, p.heel_marker_cm / 100.0])
            out[LM[pre + "foot"]] = leg["toe"]
        # trunk and head
        lean = math.radians(p.trunk_lean_deg)
        sh_c = np.array([self.Ltr * math.sin(lean), 0.0, zc + self.Ltr * math.cos(lean)])
        out[LM["l_shoulder"]] = sh_c + [0, self.sh_w / 2, 0]
        out[LM["r_shoulder"]] = sh_c + [0, -self.sh_w / 2, 0]
        nose = sh_c + np.array([0.05 + 0.1 * self.H * math.sin(lean) * 0.5, 0.0, 0.1 * self.H])
        out[0] = nose
        for k, off in {1: (-0.02, 0.02, 0.03), 2: (-0.02, 0.03, 0.03), 3: (-0.025, 0.04, 0.03),
                       4: (-0.02, -0.02, 0.03), 5: (-0.02, -0.03, 0.03), 6: (-0.025, -0.04, 0.03),
                       7: (-0.09, 0.07, 0.02), 8: (-0.09, -0.07, 0.02),
                       9: (-0.01, 0.025, -0.04), 10: (-0.01, -0.025, -0.04)}.items():
            out[k] = nose + np.array(off)
        # arms: each arm swings with the opposite leg
        stride = 2 * self.T
        for s, pre, sgn in ((Side.LEFT, "l_", 1.0), (Side.RIGHT, "r_", -1.0)):
            ph = 2 * math.pi * (t - self.ic0[s]) / stride
            fwd = 0.5 * (1 + math.cos(ph + math.pi))     # 0 at own leg's contact (arm back), 1 opposite
            alpha = math.radians(-25 + 55 * fwd)
            sh = out[LM[pre + "shoulder"]]
            ua = np.array([math.sin(alpha + lean * 0.3), -math.cos(alpha + lean * 0.3)])
            fa = _rot(ua, 180 - p.elbow_deg)
            el2 = np.array([sh[0], sh[2]]) + ua * self.Lua
            wr2 = el2 + fa * self.Lfa
            wy_back, wy_front = sgn * 0.17, sgn * (-p.arm_cross_cm / 100.0)
            wy = wy_back + (wy_front - wy_back) * fwd
            ey = sh[1] + 0.02 * sgn
            out[LM[pre + "elbow"]] = [el2[0], ey, el2[1]]
            out[LM[pre + "wrist"]] = [wr2[0], wy, wr2[1]]
            hand = np.array([wr2[0], wy, wr2[1]]) + np.array([fa[0], 0, fa[1]]) * 0.05
            for k in ((17, 19, 21) if s == Side.LEFT else (18, 20, 22)):
                out[k] = hand + np.array([0, 0.01 * sgn * (k % 3), 0])
        return out

    def contacts(self, t_end: float) -> list[tuple[Side, float, float]]:
        """True (side, t_ic, t_to) inside [0, t_end]."""
        out = []
        for s in (Side.LEFT, Side.RIGHT):
            tic = self.ic0[s]
            while tic < t_end:
                out.append((s, tic, tic + self.gct[s]))
                tic += 2 * self.T
        return sorted(out, key=lambda c: c[1])


class Projector:
    """Orthographic projection of the 3D runner to image pixels for a view."""

    def __init__(self, p: RunnerParams) -> None:
        self.p = p
        H = p.height_cm / 100.0
        self.px_per_m = 0.72 * p.height / H
        self.ground_px = 0.9 * p.height
        self.cx = 0.5 * p.width

    def axis(self, xyz: np.ndarray) -> np.ndarray:
        v = View(self.p.view)
        if v == View.SIDE_LEFT:
            return -xyz[..., 0]
        if v == View.SIDE_RIGHT:
            return xyz[..., 0]
        if v == View.REAR:
            return -xyz[..., 1]
        return xyz[..., 1]

    def depth(self, xyz: np.ndarray) -> np.ndarray:
        """Larger = closer to the camera."""
        v = View(self.p.view)
        return {View.SIDE_LEFT: xyz[..., 1], View.SIDE_RIGHT: -xyz[..., 1],
                View.REAR: -xyz[..., 0], View.FRONT: xyz[..., 0]}[v]

    def __call__(self, xyz: np.ndarray) -> np.ndarray:
        u = self.axis(xyz)
        out = np.empty(xyz.shape[:-1] + (2,))
        out[..., 0] = self.cx + u * self.px_per_m
        out[..., 1] = self.ground_px - xyz[..., 2] * self.px_per_m
        return out

    @property
    def facing(self) -> int:
        return {View.SIDE_LEFT: -1, View.SIDE_RIGHT: 1}.get(View(self.p.view), 0)


def generate(p: RunnerParams) -> tuple[list[Pose | None], Truth]:
    """Synthetic pose sequence (with noise, outliers, swaps and gaps as configured) and its truth."""
    rng = np.random.default_rng(p.seed)
    model = RunnerModel(p)
    proj = Projector(p)
    n = int(round(p.duration_s * p.fps))
    poses: list[Pose | None] = []
    near_left = View(p.view) == View.SIDE_LEFT
    side_view = View(p.view) in (View.SIDE_LEFT, View.SIDE_RIGHT)
    left_idx = [i for k, i in LM.items() if k.startswith("l_")] + [1, 2, 3, 7, 9, 17, 19, 21]
    vis = np.full(33, 0.95)
    if side_view:
        far = np.zeros(33, bool)
        far[left_idx] = True
        if near_left:
            far = ~far
        far[0] = False
        vis[far] = 0.6
    legs_l = [LM["l_" + k] for k in ("knee", "ankle", "heel", "foot")]
    legs_r = [LM["r_" + k] for k in ("knee", "ankle", "heel", "foot")]
    swap_left = 0
    if p.time_jitter:
        dts = (1.0 + rng.uniform(-p.time_jitter, p.time_jitter, n)) / p.fps
        times = np.concatenate([[0.0], np.cumsum(dts[:-1])])
    else:
        times = np.arange(n) / p.fps
    for i in range(n):
        t = float(times[i])
        xy = proj(model.joints(t))
        if side_view and p.far_lift_px:
            xy[far, 1] -= p.far_lift_px
        if p.noise_px:
            xy = xy + rng.normal(0, p.noise_px, xy.shape)
        if p.outlier_rate and rng.random() < p.outlier_rate:
            k = int(rng.integers(0, 33))
            xy[k] += rng.normal(0, 0.08 * p.height, 2)
        crossing = abs(xy[LM["l_ankle"], 0] - xy[LM["r_ankle"], 0]) < 0.06 * p.height
        if p.swap_rate and swap_left == 0 and (crossing or not p.swap_at_crossing) and rng.random() < p.swap_rate:
            swap_left = int(rng.integers(p.swap_frames[0], p.swap_frames[1] + 1))
        if swap_left:
            xy[legs_l], xy[legs_r] = xy[legs_r].copy(), xy[legs_l].copy()
            swap_left -= 1
        if p.missing_rate and rng.random() < p.missing_rate:
            poses.append(None)
            continue
        poses.append(Pose(t, i, xy, vis.copy(), p.width, p.height))
    truth = Truth(model.contacts(float(times[-1]) + 1.0 / p.fps), proj.px_per_m / 100.0, proj.facing,
                  [float(x) for x in times])
    return poses, truth


def clean_poses(p: RunnerParams) -> list[Pose]:
    """The same sequence without any noise or artefacts (for truth values of angles)."""
    q = RunnerParams(**{**p.__dict__, "noise_px": 0.0, "outlier_rate": 0.0, "swap_rate": 0.0,
                        "missing_rate": 0.0})
    poses, _ = generate(q)
    return [x for x in poses if x is not None]


# --- rendering (demo mode and visual checks) -------------------------------------------------------

_SKIN = (150, 170, 205)
_SHIRT = (120, 92, 40)
_SHORTS = (52, 45, 40)
_SHOE = (225, 225, 230)


def background(p: RunnerParams) -> np.ndarray:
    """A plain studio background with a treadmill, BGR."""
    h, w = p.height, p.width
    yy = np.linspace(0, 1, h)[:, None]
    base = (np.array([62, 58, 55]) * (1 - 0.45 * yy) + np.array([18, 18, 20]) * 0.45 * yy)
    img = np.repeat(base[:, None, :], w, axis=1).reshape(h, w, 3)
    img = np.ascontiguousarray(np.clip(img, 0, 255).astype(np.uint8))
    proj = Projector(p)
    g = int(proj.ground_px)
    side = View(p.view) in (View.SIDE_LEFT, View.SIDE_RIGHT)
    if side:
        x0, x1 = int(0.18 * w), int(0.82 * w)
        cv2.rectangle(img, (x0, g), (x1, g + int(0.03 * h)), (38, 38, 42), -1, cv2.LINE_AA)
        cv2.rectangle(img, (x0, g + int(0.03 * h)), (x1, g + int(0.06 * h)), (70, 72, 78), -1, cv2.LINE_AA)
        cv2.line(img, (x0, g), (x1, g), (95, 95, 100), 2, cv2.LINE_AA)
        console_x = x0 if proj.facing < 0 else x1
        cv2.line(img, (console_x, g), (console_x - proj.facing * int(0.02 * w), int(0.42 * h)),
                 (80, 82, 88), int(0.012 * h), cv2.LINE_AA)
    else:
        bw = int(0.22 * w)
        pts = np.array([[w // 2 - bw // 2, g - int(0.08 * h)], [w // 2 + bw // 2, g - int(0.08 * h)],
                        [w // 2 + bw, h], [w // 2 - bw, h]], np.int32)
        cv2.fillPoly(img, [pts], (40, 40, 44), cv2.LINE_AA)
    return img


def render(pose_xyz: np.ndarray, p: RunnerParams, bg: np.ndarray | None = None) -> np.ndarray:
    """Draw the runner (3D joints from ``RunnerModel.joints``) over a background."""
    proj = Projector(p)
    img = (bg if bg is not None else background(p)).copy()
    xy = proj(pose_xyz)
    depth = proj.depth(pose_xyz)
    s = proj.px_per_m

    def limb(a: int, b: int, width_m: float, col: tuple[int, int, int], shade: float) -> tuple:
        c = tuple(int(v * shade) for v in col)
        return (float(depth[a] + depth[b]) / 2, a, b, max(2, int(width_m * s)), c)

    parts = []
    for pre, sgn in (("l_", 1), ("r_", -1)):
        near = 1.0 if (proj.depth(pose_xyz[LM[pre + "hip"]]) >= proj.depth(pose_xyz[LM["l_hip" if pre == "r_" else "r_hip"]])) else 0.7
        parts += [limb(LM[pre + "hip"], LM[pre + "knee"], 0.13, _SHORTS, near),
                  limb(LM[pre + "knee"], LM[pre + "ankle"], 0.09, _SKIN, near),
                  limb(LM[pre + "heel"], LM[pre + "foot"], 0.07, _SHOE, near),
                  limb(LM[pre + "ankle"], LM[pre + "heel"], 0.06, _SHOE, near),
                  limb(LM[pre + "shoulder"], LM[pre + "elbow"], 0.08, _SHIRT, near),
                  limb(LM[pre + "elbow"], LM[pre + "wrist"], 0.06, _SKIN, near)]
    parts.sort(key=lambda x: x[0])
    torso_pts = np.array([xy[LM["l_shoulder"]], xy[LM["r_shoulder"]], xy[LM["r_hip"]], xy[LM["l_hip"]]])
    hip_c = (xy[LM["l_hip"]] + xy[LM["r_hip"]]) / 2
    sh_c = (xy[LM["l_shoulder"]] + xy[LM["r_shoulder"]]) / 2
    far_parts = [x for x in parts if x[0] < np.mean(depth[[11, 12, 23, 24]])]
    near_parts = [x for x in parts if x[0] >= np.mean(depth[[11, 12, 23, 24]])]
    for _, a, b, wd, c in far_parts:
        cv2.line(img, tuple(np.int32(xy[a])), tuple(np.int32(xy[b])), c, wd, cv2.LINE_AA)
    cv2.line(img, tuple(np.int32(hip_c)), tuple(np.int32(sh_c)), _SHIRT, int(0.2 * s), cv2.LINE_AA)
    cv2.fillPoly(img, [np.int32(torso_pts)], _SHIRT, cv2.LINE_AA)
    cv2.line(img, tuple(np.int32(hip_c)), tuple(np.int32(hip_c + (sh_c - hip_c) * 0.15)), _SHORTS,
             int(0.2 * s), cv2.LINE_AA)
    for _, a, b, wd, c in near_parts:
        cv2.line(img, tuple(np.int32(xy[a])), tuple(np.int32(xy[b])), c, wd, cv2.LINE_AA)
    head = xy[0] + (xy[7] + xy[8] - 2 * xy[0]) * 0.25
    cv2.circle(img, tuple(np.int32(head)), int(0.11 * s), _SKIN, -1, cv2.LINE_AA)
    cv2.line(img, tuple(np.int32(sh_c)), tuple(np.int32(head)), _SKIN, int(0.07 * s), cv2.LINE_AA)
    return img


def frames_and_poses(p: RunnerParams) -> tuple[list[np.ndarray], list[Pose | None], Truth]:
    """Rendered BGR frames plus the matching (noisy) poses. Keep ``duration_s`` small: memory."""
    poses, truth = generate(p)
    model = RunnerModel(p)
    bg = background(p)
    frames = [render(model.joints(i / p.fps), p, bg) for i in range(len(poses))]
    return frames, poses, truth
