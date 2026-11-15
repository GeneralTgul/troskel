"""Demo mode without a camera: a synthetic runner on a treadmill, and an analyzer that 'sees' it.

Bike fit and double poling have demo athletes of their own: the synthetic cyclist (from the
side and from the front) and the synthetic skier of ``pose.bike.synthetic`` and ``pose.ski.synthetic``.
They are drawn live in the camera picker and on the TV, where their values run through
``pose.technique_live`` as a real camera's do, and after the test the analysis runs on a film of the
same athlete (``technique_demo_film``), since the dev box has no pose model.


Used by ``--demo`` and by the camera picker's "Demo" source, so a coach (or a sales demo) can
run a full test with simulated devices on any laptop. The analyzer mirrors the public surface of
``pose.live.LiveAnalyzer`` (``start``, ``stop``, ``latest``) and publishes ``GaitSummary`` on
the bus about once a second, like the real one.
"""
from __future__ import annotations

import functools
import logging
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterator

import cv2
import numpy as np

from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.core.bus import TOPIC_GAIT_SUMMARY, TOPIC_TECH_SUMMARY, TOPIC_TREADMILL, Bus
from lopanalys.pose.types import LM, GaitSummary, Pose, View

log = logging.getLogger(__name__)

DEMO_ID = "demo:synthetic-runner"
DEMO_REAR_ID = "demo:synthetic-runner-rear"   # the same runner seen from behind (the second camera)
DEMO_BIKE_ID = "demo:synthetic-cyclist"
DEMO_BIKE_FRONT_ID = "demo:synthetic-cyclist-front"
DEMO_SKI_ID = "demo:synthetic-skier"
TECH_DEMOS = {DEMO_BIKE_ID: ("bikefit", "side_left"), DEMO_BIKE_FRONT_ID: ("bikefit", "front"),
              DEMO_SKI_ID: ("ski", "side_left")}
DEMO_IDS = (DEMO_ID, DEMO_REAR_ID) + tuple(TECH_DEMOS)
W, H = 1280, 720


def demo_source_info(label: str) -> SourceInfo:
    """The SourceInfo for the synthetic runner."""
    return SourceInfo(SourceKind.FILE, DEMO_ID, label, W, H, 30.0, live=True)


def demo_rear_source_info(label: str) -> SourceInfo:
    """The SourceInfo for the synthetic runner seen from behind."""
    return SourceInfo(SourceKind.FILE, DEMO_REAR_ID, label, W, H, 30.0, live=True)


def demo_info_for(kind: str, second: bool = False) -> SourceInfo | None:
    """The demo athlete for a test kind: the runner, the cyclist or the skier; ``second``: the one for
    the second camera (None when the kind has no second view)."""
    from lopanalys.core.i18n import t
    if kind == "bikefit":
        sid, key = (DEMO_BIKE_FRONT_ID, "ui.wiz.demo_cyclist_front") if second else (DEMO_BIKE_ID, "ui.wiz.demo_cyclist")
        w, h = (720, 1280) if second else (W, H)        # the front view stands up
        return SourceInfo(SourceKind.FILE, sid, t(key), w, h, 30.0, live=True)
    if kind == "ski":
        return None if second else SourceInfo(SourceKind.FILE, DEMO_SKI_ID, t("ui.wiz.demo_skier"), 720, 1280, 30.0,
                                               live=True)
    return demo_rear_source_info(t("ui.wiz.demo_camera2")) if second else demo_source_info(t("ui.wiz.demo_camera"))


def is_demo_source(info: Any) -> bool:
    return getattr(info, "id", None) in DEMO_IDS


def technique_demo(info: Any) -> tuple[str, str] | None:
    """(kind, view) of a bike fit or skier demo source, else None."""
    return TECH_DEMOS.get(getattr(info, "id", None))


def is_rear_demo(info: Any) -> bool:
    return getattr(info, "id", None) == DEMO_REAR_ID


class SyntheticRunner:
    """Draws a side-view runner at time t and speed v, and returns the 33 landmarks."""

    def __init__(self) -> None:
        self._bg = self._background()
        self.phase = 0.0
        self._last_t: float | None = None

    @staticmethod
    def _background() -> np.ndarray:
        img = np.zeros((H, W, 3), np.uint8)
        grad = np.linspace(46, 22, H).astype(np.uint8)
        img[:] = grad[:, None, None]
        img[..., 0] = np.clip(img[..., 0].astype(int) + 8, 0, 255)  # slightly blue-grey wall
        cv2.rectangle(img, (0, 560), (W, H), (28, 30, 34), -1)        # floor
        cv2.rectangle(img, (180, 600), (1100, 640), (18, 18, 20), -1)  # treadmill deck
        cv2.rectangle(img, (1060, 300), (1110, 620), (40, 42, 46), -1)  # console post
        cv2.rectangle(img, (1000, 280), (1160, 330), (55, 58, 64), -1)  # console
        cv2.line(img, (0, 560), (W, 560), (60, 62, 66), 2)
        return img

    def landmarks(self, t: float, speed: float) -> np.ndarray:
        cadence = 150 + 2.6 * max(speed, 6.0)
        if self._last_t is not None:
            self.phase += 2 * math.pi * (cadence / 120.0) * (t - self._last_t)
        self._last_t = t
        ph = self.phase
        amp = max(0.0, min(1.0, speed / 8.0))   # 0 = standing still
        xy = np.zeros((33, 2), np.float32)
        bounce = 0.0
        hip = np.array([620.0, 335.0 - bounce])
        lean = math.radians(1 + 5 * amp)
        up = np.array([math.sin(lean), -math.cos(lean)])
        sh = hip + 185 * up
        head = sh + 58 * up + np.array([10, 0])
        thigh, shin = 140.0, 138.0
        pts: dict[str, np.ndarray] = {}
        for side, off in (("l", 0.0), ("r", math.pi)):
            p = ph + off
            th = math.radians(amp * (26 * math.sin(p) + 4))          # + = thigh forward (right)
            d = math.cos(p)  # > 0: thigh swinging forward
            knee_flex = math.radians(5 + amp * (17 + 12 * max(0.0, -d) + 88 * max(0.0, d) ** 0.8))
            knee = hip + thigh * np.array([math.sin(th), math.cos(th)])
            sa = th - knee_flex
            ankle = knee + shin * np.array([math.sin(sa), math.cos(sa)])
            heel = ankle + np.array([-16, 12])
            toe = ankle + np.array([34, 16])
            arm = math.radians(-32 * amp * math.sin(p))
            elbow = sh + 95 * np.array([math.sin(arm), math.cos(arm)])
            fa = arm + math.radians(20 + 80 * amp)
            wrist = elbow + 80 * np.array([math.sin(fa), math.cos(fa)])
            pts.update({f"{side}_hip": hip.copy(), f"{side}_knee": knee, f"{side}_ankle": ankle,
                        f"{side}_heel": heel, f"{side}_foot": toe, f"{side}_shoulder": sh.copy(),
                        f"{side}_elbow": elbow, f"{side}_wrist": wrist})
        pts["nose"] = head
        low = max(pts["l_foot"][1], pts["r_foot"][1], pts["l_heel"][1], pts["r_heel"][1])
        dy = min(616.0 - low, 30.0)  # flight phase: lift at most 30 px
        for k in pts:
            pts[k] = pts[k] + np.array([0.0, dy])
        xy[:] = head
        for k, i in LM.items():
            if k in pts:
                xy[i] = pts[k]
        return xy

    def render(self, xy: np.ndarray, t: float) -> np.ndarray:
        img = self._bg.copy()
        # moving belt stripes
        off = int((t * 380) % 80)
        for x in range(180 - off, 1100, 80):
            cv2.line(img, (max(180, x), 604), (max(180, x), 636), (30, 30, 34), 3)
        P = {k: tuple(int(v) for v in xy[i]) for k, i in LM.items()}
        far, near = ("r", "l")
        body = (92, 108, 132)
        dark = (66, 76, 92)
        for side, col, w in ((far, dark, 30), (near, body, 34)):
            cv2.line(img, P[f"{side}_shoulder"], P[f"{side}_elbow"], col, w - 12, cv2.LINE_AA)
            cv2.line(img, P[f"{side}_elbow"], P[f"{side}_wrist"], col, w - 16, cv2.LINE_AA)
            if side == far:
                self._leg(img, P, side, col, w)
        cv2.line(img, P["l_hip"], P["l_shoulder"], (110, 120, 70), 62, cv2.LINE_AA)  # torso (shirt)
        cv2.circle(img, P["nose"], 34, (120, 140, 175), -1, cv2.LINE_AA)
        self._leg(img, P, near, body, 34)
        cv2.line(img, P[f"{near}_shoulder"], P[f"{near}_elbow"], body, 22, cv2.LINE_AA)
        cv2.line(img, P[f"{near}_elbow"], P[f"{near}_wrist"], body, 18, cv2.LINE_AA)
        return img

    @staticmethod
    def _leg(img: np.ndarray, P: dict[str, tuple[int, int]], s: str, col: tuple[int, int, int], w: int) -> None:
        cv2.line(img, P[f"{s}_hip"], P[f"{s}_knee"], (50, 50, 58), w + 4, cv2.LINE_AA)   # shorts/tights
        cv2.line(img, P[f"{s}_knee"], P[f"{s}_ankle"], col, w - 6, cv2.LINE_AA)
        cv2.line(img, P[f"{s}_heel"], P[f"{s}_foot"], (200, 200, 210), 16, cv2.LINE_AA)  # shoe


class RearRunner:
    """The synthetic runner seen from behind: same cadence as the side runner, a little hip drop
    and the knees slightly inwards. Same surface as SyntheticRunner (``landmarks``, ``render``)."""

    def __init__(self) -> None:
        self._bg = self._background()
        self.phase = 0.0
        self._last_t: float | None = None

    @staticmethod
    def _background() -> np.ndarray:
        img = np.zeros((H, W, 3), np.uint8)
        grad = np.linspace(46, 22, H).astype(np.uint8)
        img[:] = grad[:, None, None]
        img[..., 0] = np.clip(img[..., 0].astype(int) + 8, 0, 255)
        cv2.rectangle(img, (0, 560), (W, H), (28, 30, 34), -1)                 # floor
        pts = np.array([[520, 640], [760, 640], [720, 560], [560, 560]], np.int32)
        cv2.fillPoly(img, [pts], (18, 18, 20))                                 # the belt, seen along it
        for x in (500, 780):
            cv2.rectangle(img, (x - 10, 250), (x + 10, 600), (40, 42, 46), -1)  # handrail posts
        cv2.rectangle(img, (560, 215), (720, 260), (55, 58, 64), -1)           # console, beyond the runner
        cv2.line(img, (0, 560), (W, 560), (60, 62, 66), 2)
        return img

    def landmarks(self, t: float, speed: float) -> np.ndarray:
        cadence = 150 + 2.6 * max(speed, 6.0)
        if self._last_t is not None:
            self.phase += 2 * math.pi * (cadence / 120.0) * (t - self._last_t)
        self._last_t = t
        ph = self.phase
        amp = max(0.0, min(1.0, speed / 8.0))
        cx = 640.0
        # From behind the runner's left is the picture's left. Stance on the left for sin(ph) < 0.
        drop = math.radians(4.0 * amp * math.sin(ph))          # the pelvis tilts towards the swing side
        hip_c = np.array([cx + 6 * amp * math.sin(ph), 335.0])
        half = 46.0
        pts: dict[str, np.ndarray] = {}
        for side, sgn, off in (("l", -1.0, 0.0), ("r", 1.0, math.pi)):
            p = ph + off
            swing = max(0.0, math.sin(p))                      # 0 in stance, up to 1 mid swing
            hip = hip_c + np.array([sgn * half * math.cos(drop), sgn * half * math.sin(drop) * -1.0])
            knee = hip + np.array([-sgn * 7 * amp, 140.0 - 26 * amp * swing])
            ankle = knee + np.array([-sgn * (4 + 10 * amp * swing), 138.0 - 70 * amp * swing])
            heel = ankle + np.array([0.0, 12.0])
            toe = ankle + np.array([sgn * 5.0, 18.0])
            sh = hip_c + np.array([sgn * 78.0, -185.0])
            arm = amp * math.sin(p + math.pi)
            elbow = sh + np.array([sgn * 16.0, 92.0])
            wrist = elbow + np.array([-sgn * (26 + 10 * arm), 34.0 - 30 * arm])
            pts.update({f"{side}_hip": hip, f"{side}_knee": knee, f"{side}_ankle": ankle, f"{side}_heel": heel,
                        f"{side}_foot": toe, f"{side}_shoulder": sh, f"{side}_elbow": elbow, f"{side}_wrist": wrist})
        pts["nose"] = hip_c + np.array([0.0, -250.0])
        low = max(pts["l_foot"][1], pts["r_foot"][1])
        dy = min(616.0 - low, 30.0)
        xy = np.zeros((33, 2), np.float32)
        xy[:] = pts["nose"] + np.array([0.0, dy])
        for k, i in LM.items():
            xy[i] = pts[k] + np.array([0.0, dy])
        return xy

    def render(self, xy: np.ndarray, t: float) -> np.ndarray:
        img = self._bg.copy()
        P = {k: tuple(int(v) for v in xy[i]) for k, i in LM.items()}
        body = (92, 108, 132)
        for s in ("l", "r"):
            cv2.line(img, P[f"{s}_hip"], P[f"{s}_knee"], (50, 50, 58), 38, cv2.LINE_AA)
            cv2.line(img, P[f"{s}_knee"], P[f"{s}_ankle"], body, 26, cv2.LINE_AA)
            cv2.line(img, P[f"{s}_heel"], P[f"{s}_foot"], (200, 200, 210), 18, cv2.LINE_AA)
        torso = np.array([P["l_shoulder"], P["r_shoulder"], P["r_hip"], P["l_hip"]], np.int32)
        cv2.fillConvexPoly(img, torso, (110, 120, 70), cv2.LINE_AA)
        cv2.polylines(img, [torso], True, (110, 120, 70), 26, cv2.LINE_AA)
        for s in ("l", "r"):
            cv2.line(img, P[f"{s}_shoulder"], P[f"{s}_elbow"], body, 22, cv2.LINE_AA)
            cv2.line(img, P[f"{s}_elbow"], P[f"{s}_wrist"], body, 18, cv2.LINE_AA)
        cv2.circle(img, P["nose"], 34, (70, 60, 52), -1, cv2.LINE_AA)   # the back of the head
        return img


def draw_skeleton(img: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """Skeleton overlay in the app's accent colours (used when pose.annotate is not available)."""
    teal, blue, white = (153, 166, 34), (246, 130, 60), (243, 238, 233)
    segs = [("l_shoulder", "l_hip"), ("l_hip", "l_knee"), ("l_knee", "l_ankle"), ("l_ankle", "l_heel"),
            ("l_heel", "l_foot"), ("l_ankle", "l_foot"), ("l_shoulder", "l_elbow"), ("l_elbow", "l_wrist"),
            ("r_hip", "r_knee"), ("r_knee", "r_ankle"), ("r_ankle", "r_heel"), ("r_heel", "r_foot"),
            ("r_ankle", "r_foot"), ("r_shoulder", "r_elbow"), ("r_elbow", "r_wrist")]
    P = {k: tuple(int(v) for v in xy[i]) for k, i in LM.items()}
    for a, b in segs:
        col = teal if a.startswith("l") else blue
        cv2.line(img, P[a], P[b], col, 5, cv2.LINE_AA)
    for k, p in P.items():
        if k != "nose":
            cv2.circle(img, p, 7, white, -1, cv2.LINE_AA)
            cv2.circle(img, p, 7, (40, 40, 40), 1, cv2.LINE_AA)
    # vertical through the hip and the shin angle at the near leg
    hip, knee, ankle = np.array(P["l_hip"]), np.array(P["l_knee"]), np.array(P["l_ankle"])
    cv2.line(img, (int(hip[0]), int(hip[1]) - 40), (int(hip[0]), 615), (200, 200, 200), 1, cv2.LINE_AA)
    ang = math.degrees(math.atan2(ankle[0] - knee[0], ankle[1] - knee[1]))
    cv2.putText(img, f"{ang:+.0f}", (int(knee[0]) + 16, int(knee[1]) + 6), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, white, 2, cv2.LINE_AA)
    return img


@functools.lru_cache(maxsize=16)
def technique_params(kind: str, view: str, height_cm: float | None, width: int, height: int,
                     fps: float = 30.0, duration_s: float = 12.0) -> Any:
    """The synthetic cyclist or skier of the demo, with a fit that gives the analysis something to say.
    Kept once made: finding the saddle height for the knee angle takes a few seconds."""
    from lopanalys.pose.types import View
    h = float(height_cm or 180.0)
    v = View(view)
    if kind == "bikefit":
        from lopanalys.pose.bike import synthetic as bs
        if v == View.FRONT:
            return bs.CyclistParams(height_cm=h, view=v, fps=fps, duration_s=duration_s, width=width, height=height,
                                    knee_lateral_cm=5.5)
        return bs.with_knee(bs.CyclistParams(height_cm=h, view=v, fps=fps, duration_s=duration_s, width=width,
                                             height=height, elbow_flex_deg=4.0, shoulder_angle_deg=101.0), 50.0)
    from lopanalys.pose.ski import synthetic as ss
    return ss.SkierParams(height_cm=h, view=v, fps=fps, duration_s=duration_s, width=width, height=height,
                          trunk_lean_end_deg=38.0, trunk_lead_ms=30.0, knee_flex_end_deg=56.0)


class TechniqueAthlete:
    """Draws the synthetic cyclist or skier at time t, with the 33 landmarks (the live demo)."""

    def __init__(self, kind: str, view: str, width: int, height: int) -> None:
        from lopanalys.pose import synthetic
        if kind == "bikefit":
            from lopanalys.pose.bike import synthetic as mod
            self.p = technique_params(kind, view, 180.0, width, height)
            self.model: Any = mod.CyclistModel(self.p)
        else:
            from lopanalys.pose.ski import synthetic as mod  # type: ignore[no-redef]
            self.p = technique_params(kind, view, 180.0, width, height)
            self.model = mod.SkierModel(self.p)
        self._bg = mod.background(self.p)
        self._proj = synthetic.Projector(self.p)
        self._render = synthetic.render
        self.width, self.height = width, height

        # The skier's strokes are laid out for the length of ``p``; after that the time goes round again,
        # whole strokes at a time, so the skier keeps poling for the whole test.
        starts = getattr(self.model, "starts", None)
        self._loop: tuple[float, float] | None = None
        if starts is not None and len(starts) > 3:
            T = float(self.model.T)
            n = int((starts[-2] - starts[1]) / T)
            self._loop = (float(starts[1]), n * T) if n > 0 else None

    def _t(self, t: float) -> float:
        if self._loop is None or t < self._loop[0]:
            return t
        return self._loop[0] + (t - self._loop[0]) % self._loop[1]

    def landmarks(self, t: float, _speed: float = 0.0) -> np.ndarray:
        return np.asarray(self._proj(self.model.joints(self._t(t))), np.float32)

    def render(self, _xy: Any, t: float) -> np.ndarray:
        return self._render(self.model.joints(self._t(t)), self.p, self._bg)


def technique_demo_film(kind: str, view: str, height_cm: float | None, path: Any) -> tuple[Any, Any]:
    """A film of the demo athlete and an estimator that 'sees' it: (path, estimator) for the analysis."""
    from lopanalys.pose import technique_synth as ts
    if kind == "bikefit":
        from lopanalys.pose.bike import synthetic as mod
    else:
        from lopanalys.pose.ski import synthetic as mod  # type: ignore[no-redef]
    front = view in ("front",)
    w, h = (540, 960) if front or kind == "ski" else (960, 540)
    p = technique_params(kind, view, height_cm, w, h, fps=60.0, duration_s=12.0 if kind == "bikefit" else 20.0)
    video = mod.write_video(str(path), p)
    return video, ts.replay(mod.generate(p)[0])


class DemoSource:
    """FrameSource for the synthetic runner, cyclist or skier (used by the camera picker preview)."""

    def __init__(self, info: SourceInfo) -> None:
        self.info = info
        tech = technique_demo(info)
        self._runner: Any = (TechniqueAthlete(tech[0], tech[1], info.width, info.height) if tech else
                             RearRunner() if is_rear_demo(info) else SyntheticRunner())
        self._open = False

    def open(self) -> None:
        self._open = True

    def frames(self) -> Iterator[Frame]:
        i = 0
        t0 = time.perf_counter()
        while self._open:
            t = time.perf_counter() - t0
            img = self._runner.render(self._runner.landmarks(t, 12.0), t)
            yield Frame(img, time.perf_counter(), i)
            i += 1
            time.sleep(1 / 30)

    def close(self) -> None:
        self._open = False


@dataclass
class DemoResult:
    """Same fields as pose.live.LiveResult."""
    frame: Any
    pose: Pose | None
    overlay: np.ndarray
    summary: GaitSummary | None


class DemoAnalyzer:
    """Stands in for pose.live.LiveAnalyzer on the synthetic runner."""

    def __init__(self, source: Any, bus: Bus, height_cm: float, view: View | str,
                 topic: str = TOPIC_GAIT_SUMMARY, tech_topic: str = TOPIC_TECH_SUMMARY) -> None:
        self.source = source
        self.bus = bus
        self.view = view
        self.height_cm = height_cm
        self.topic = topic
        self.tech_topic = tech_topic
        self.name = ""
        self.max_fps: float | None = None
        self.error: str | None = None
        self._speed = 0.0
        self._latest: DemoResult | None = None
        self._summary: GaitSummary | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsub = bus.subscribe(TOPIC_TREADMILL, self._on_treadmill)
        info = getattr(source, "info", None)
        self._rear = is_rear_demo(info)
        tech = technique_demo(info)
        self._tech = tech is not None          # a cyclist or a skier: no running values
        self.technique = tech[0] if tech else None
        self.tech_summary: Any = None          # pose.technique_live.TechniqueLive
        self._poses: deque[tuple[float, Pose]] = deque()
        self._size = (info.width, info.height) if tech else (W, H)
        self._runner: Any = (TechniqueAthlete(tech[0], tech[1], info.width, info.height) if tech else
                             RearRunner() if self._rear else SyntheticRunner())

    def _on_treadmill(self, st: Any) -> None:
        self._speed = float(st.speed_kmh or 0.0)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="demo-analyzer")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._unsub()
        if self._thread:
            self._thread.join(timeout=2)

    def latest(self) -> DemoResult | None:
        return self._latest

    def latest_frame(self) -> Any:
        r = self._latest
        return r.frame if r is not None else None

    def rates(self) -> tuple[float, float]:
        return 30.0, 30.0

    @property
    def source_lost(self) -> bool:
        return False

    def _run(self) -> None:
        t0 = time.perf_counter()
        last_sum = 0.0
        i = 0
        try:
            from lopanalys.pose.annotate import draw_live_overlay
        except Exception:
            draw_live_overlay = None
        while not self._stop.is_set():
            t = time.perf_counter() - t0
            sp = self._speed if self._speed > 0.5 else 0.0
            xy = self._runner.landmarks(t, sp)
            frame = self._runner.render(xy, t if self._tech else t * sp / 12.0)
            pose = Pose(t, i, xy, np.ones(33, np.float32), *self._size)
            overlay = frame.copy()
            try:
                overlay = (draw_live_overlay(overlay, pose, self.view, self._summary, labels=not self._tech)
                           if draw_live_overlay else draw_skeleton(overlay, xy))
            except Exception:
                overlay = draw_skeleton(overlay, xy)
            if self._tech:
                self._poses.append((t, pose))
                while t - self._poses[0][0] > self._window_s():
                    self._poses.popleft()
                if t - last_sum >= 1.0:
                    last_sum = t
                    self._publish_technique()
            elif t - last_sum >= 1.0 and sp > 0.5:
                last_sum = t
                self._summary = self._make_rear_summary(t, sp) if self._rear else self._make_summary(t, sp)
                self.bus.publish(self.topic, self._summary)
            self._latest = DemoResult(Frame(frame, t, i), pose, overlay, self._summary)
            i += 1
            self._stop.wait(1 / 30)

    def _window_s(self) -> float:
        from lopanalys.pose.technique_live import window_s
        return window_s(self.technique or "")

    def _publish_technique(self) -> None:
        """The cyclist's or the skier's values through the same chain as a real camera's (pose.technique_live)."""
        from lopanalys.pose.technique_live import summarize
        data = list(self._poses)
        try:
            live = summarize(self.technique or "", [p for _, p in data], [t for t, _ in data], self.height_cm,
                             self.view)
        except Exception:
            log.exception("demo technique values failed")
            return
        self.tech_summary = live
        self.bus.publish(self.tech_topic, live)

    @staticmethod
    def _make_summary(t: float, sp: float) -> GaitSummary:
        r = random.uniform
        return GaitSummary(
            t - 5, t, int(14 + sp), cadence_spm=round(150 + 2.6 * sp + r(-1.5, 1.5), 1),
            ground_contact_ms=round(300 - 8 * sp + r(-5, 5)), vertical_oscillation_cm=round(9.8 - 0.12 * sp + r(-0.3, 0.3), 1),
            overstride_cm=round(6 + 0.5 * sp + r(-1, 1), 1), shin_angle_deg=round(3 + 0.3 * sp + r(-1, 1), 1),
            knee_flex_contact_deg=round(18 + r(-2, 2), 1), knee_flex_midstance_deg=round(42 + r(-2, 2), 1),
            trunk_lean_deg=round(6 + r(-1, 1), 1), hip_extension_toe_off_deg=round(12 + r(-2, 2), 1),
            foot_strike="midfoot")

    @staticmethod
    def _make_rear_summary(t: float, sp: float) -> GaitSummary:
        r = random.uniform
        return GaitSummary(
            t - 5, t, int(14 + sp), cadence_spm=round(150 + 2.6 * sp + r(-1.5, 1.5), 1),
            ground_contact_ms=None, vertical_oscillation_cm=round(9.8 - 0.12 * sp + r(-0.3, 0.3), 1),
            overstride_cm=None, shin_angle_deg=None, knee_flex_contact_deg=None, knee_flex_midstance_deg=None,
            trunk_lean_deg=None, hip_extension_toe_off_deg=None, foot_strike=None,
            pelvic_drop_deg=round(4.5 + 0.15 * sp + r(-0.4, 0.4), 1), knee_valgus_deg=round(5 + r(-1, 1), 1),
            step_width_cm=round(7 + r(-0.8, 0.8), 1), arm_cross_cm=round(-3 + r(-1, 1), 1), view="rear")


def seed_demo_data(store: Any) -> None:
    """Fill an empty database with a few demo athletes and two earlier lactate tests."""
    from datetime import date, datetime, timedelta, timezone

    from lopanalys.core.i18n import t
    from lopanalys.core.models import (
        Athlete,
        Session,
        Sex,
        StepRecord,
        TestKind,
        TimeSample,
    )
    if store.athletes():
        return
    people = [Athlete(None, "Anna", "Lindqvist", date(1991, 4, 12), Sex.FEMALE, 168, 58, 192, 46),
              Athlete(None, "Erik", "Sandberg", date(1986, 9, 3), Sex.MALE, 183, 76, 186, 50),
              Athlete(None, "Sara", "Holm", date(1999, 1, 27), Sex.FEMALE, 172, 61, 198, 44)]
    for a in people:    # the demo people have signed the form: every part of the program can be shown
        a.consent = {"date": date.today().isoformat(), "test": True, "film": True, "store": True, "contact": True}
        a.email = f"{a.first_name.lower()}.{a.last_name.lower()}@example.com"   # a reserved domain: reaches nobody
    saved = [store.save_athlete(a) for a in people]
    erik = saved[1]
    now = datetime.now(timezone.utc)
    # Erik has got better between the tests, as the example report should show: in the later test the
    # lactate rises later and the heart rate is lower at each speed, so both thresholds move forward
    # (LT1 11,1 -> 11,9 km/h, LT2 12,6 -> 13,1 km/h with the default methods).
    tests = ((5, (1.1, 1.2, 1.5, 2.0, 2.8, 3.9, 5.4, 7.3), (123, 132, 142, 151, 160, 169, 177, 184)),
             (2, (1.0, 1.0, 1.1, 1.4, 2.1, 3.0, 4.4, 6.2), (118, 127, 136, 145, 154, 163, 171, 179)))
    for months, lactate, hr_end in tests:
        started = now - timedelta(days=30 * months)
        s = Session(None, erik.id, TestKind.LACTATE_STEP, started, started + timedelta(minutes=39),
                    protocol_name=t("ui.demo.lactate_protocol"), treadmill_mode="ftms")
        s = store.save_session(s)
        recs = [StepRecord(i, 8.0 + i, 1.0, 240, hr_end=hr_i, lactate_mmol=la, rpe=min(20, 7 + 2 * i))
                for i, (la, hr_i) in enumerate(zip(lactate, hr_end))]
        store.save_records(s.id, recs)
        # the time series behind the stages (heart rate, belt and cadence per second, a stop for each
        # blood sample), so the demo tests can be saved as FIT files too
        samples, hr = [], 95.0
        for r in recs:
            for _ in range(int(r.duration_s)):
                hr += (r.hr_end - hr) * 0.03
                samples.append(TimeSample(float(len(samples)), min(r.hr_end, round(hr + 0.4)), r.speed_kmh, 1.0,
                                          164.0 + 2 * r.index, "strap"))
            for _ in range(30):
                hr -= 0.5
                samples.append(TimeSample(float(len(samples)), round(hr), 0.0, 1.0, None))
        store.save_samples(s.id, samples)
