"""Types for pose estimation and gait analysis. No Qt imports in pose/.

Coordinates are image pixels (x right, y down) unless named *_cm. Angles in degrees.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from lopanalys.camera.base import Frame


class View(str, Enum):
    SIDE_LEFT = "side_left"    # camera on the athlete's left side
    SIDE_RIGHT = "side_right"
    REAR = "rear"
    FRONT = "front"


# MediaPipe Pose landmark indices that the analysis uses (33-point model).
LM = dict(
    nose=0, l_shoulder=11, r_shoulder=12, l_elbow=13, r_elbow=14, l_wrist=15, r_wrist=16,
    l_hip=23, r_hip=24, l_knee=25, r_knee=26, l_ankle=27, r_ankle=28,
    l_heel=29, r_heel=30, l_foot=31, r_foot=32,
)


@dataclass
class Pose:
    t: float
    frame_index: int
    xy: np.ndarray          # (33, 2) pixels
    visibility: np.ndarray  # (33,) 0..1
    width: int
    height: int


class Side(str, Enum):
    LEFT = "left"
    RIGHT = "right"


@dataclass
class StepMetrics:
    """Measured at one ground contact (initial contact to toe-off)."""
    side: Side
    t_contact: float
    t_toe_off: float | None
    contact_frame: int
    ground_contact_ms: float | None
    overstride_cm: float | None        # horizontal heel-to-hip(centre of mass proxy) at contact
    shin_angle_deg: float | None       # tibia vs vertical at contact, + = foot ahead of knee
    knee_flex_contact_deg: float | None
    knee_flex_midstance_deg: float | None
    trunk_lean_deg: float | None       # + = forward
    hip_extension_toe_off_deg: float | None
    foot_strike: str | None            # "heel" | "midfoot" | "forefoot"
    # rear view
    pelvic_drop_deg: float | None = None
    knee_valgus_deg: float | None = None
    step_width_cm: float | None = None
    # added by the pose module (all optional, defaults keep old callers working)
    toe_off_frame: int | None = None
    midstance_frame: int | None = None
    foot_angle_deg: float | None = None        # foot vs its flat-foot angle at contact, + = toe up
    vertical_oscillation_cm: float | None = None  # pelvis rise and fall over this step
    heel_whip_deg: float | None = None         # rear view, early swing, + = heel moves inwards
    arm_cross_cm: float | None = None          # rear/front view, + = wrist past the body midline

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GaitSummary:
    """Rolling or per-stage summary. Values are medians over the window; *_asym in percent."""
    t_from: float
    t_to: float
    steps: int
    cadence_spm: float | None
    ground_contact_ms: float | None
    vertical_oscillation_cm: float | None
    overstride_cm: float | None
    shin_angle_deg: float | None
    knee_flex_contact_deg: float | None
    knee_flex_midstance_deg: float | None
    trunk_lean_deg: float | None
    hip_extension_toe_off_deg: float | None
    foot_strike: str | None
    pelvic_drop_deg: float | None = None
    knee_valgus_deg: float | None = None
    step_width_cm: float | None = None
    asymmetry: dict[str, float] = field(default_factory=dict)  # metric -> left/right diff %
    # added by the pose module (all optional)
    foot_angle_deg: float | None = None
    flight_ms: float | None = None
    duty_factor_pct: float | None = None       # contact time / stride time per foot
    elbow_angle_deg: float | None = None
    arm_swing_deg: float | None = None        # near upper arm, range forward and back (side view)
    arm_cross_cm: float | None = None
    heel_whip_deg: float | None = None
    speed_kmh: float | None = None             # treadmill speed during the window, when known
    view: str | None = None
    cadence_source: str | None = None          # "strap" when cadence_spm is the strap's (RSC); None = camera

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GaitSummary":
        names = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in names})


class Severity(str, Enum):
    OK = "ok"
    WATCH = "watch"
    FIX = "fix"


@dataclass
class Finding:
    """A technique observation with what to draw on the key frame."""
    code: str                 # e.g. "overstride", "trunk_lean_back", "pelvic_drop"
    severity: Severity
    title: str                # short, localised (sv), e.g. "Översteg"
    explanation: str          # what it is and why it matters, 1-3 sentences
    cue: str                  # coaching cue / drill, 1-2 sentences
    value: float | None
    unit: str
    reference: str            # the target range, e.g. "under 10 cm"
    frame_index: int | None = None     # key frame that shows it best
    highlight: list[str] = field(default_factory=list)  # landmark names to ring/mark
    drill: str = ""                    # added: an exercise that trains the change
    side: str | None = None            # added: "left" | "right" when it concerns one side
    uncertain: bool = False            # added: built on a value the frame rate cannot carry

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        names = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in d.items() if k in names}
        kw["severity"] = Severity(kw["severity"])
        return cls(**kw)


@dataclass
class KeyFrame:
    """An annotated still for the report: contact, midstance, toe-off, flight, or a finding."""
    label: str
    frame_index: int
    t: float
    image_path: str           # PNG with skeleton, angles and rings drawn
    findings: list[str] = field(default_factory=list)  # Finding.code
    kind: str = ""            # added: "contact" | "midstance" | "toe_off" | "flight" | "sequence" | finding code
    caption: str = ""         # added: one line under the image in the report

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScaleInfo:
    """How pixels were turned into centimetres (from the athlete's height)."""
    px_per_cm: float
    uncertainty_pct: float     # rough 1-sigma uncertainty of every *_cm value
    stature_px: float
    method: str = "segments"


@dataclass
class GaitAnalysis:
    """Result of a whole clip (offline) or a test stage: what the report shows."""
    summary: GaitSummary | None
    steps: list[StepMetrics]
    findings: list[Finding]
    keyframes: list[KeyFrame]
    view: str
    height_cm: float | None = None
    fps: float | None = None
    duration_s: float | None = None
    frames: int = 0
    frames_with_pose: int = 0
    facing: int | None = None          # +1 athlete faces image right, -1 left (side views)
    scale: ScaleInfo | None = None
    speed_kmh: float | None = None
    video_path: str | None = None
    warnings: list[str] = field(default_factory=list)  # localised, shown to the coach
    # added: metric -> pose.findings.MetricRange.to_dict(): the ok/watch/fix ranges used for this
    # analysis (speed, leg length, view) with the judged value and its severity. Empty in analyses
    # stored before it existed.
    ranges: dict[str, dict[str, Any]] = field(default_factory=dict)
    # added: metric -> "uncertain" | "hidden" at this clip's frame rate (pose.reliability);
    # hidden metrics are None in the summary. Missing in analyses stored before it existed.
    reliability: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict() if self.summary else None,
            "steps": [s.to_dict() for s in self.steps],
            "findings": [f.to_dict() for f in self.findings],
            "keyframes": [k.to_dict() for k in self.keyframes],
            "view": self.view, "height_cm": self.height_cm, "fps": self.fps,
            "duration_s": self.duration_s, "frames": self.frames,
            "frames_with_pose": self.frames_with_pose, "facing": self.facing,
            "scale": asdict(self.scale) if self.scale else None,
            "speed_kmh": self.speed_kmh, "video_path": self.video_path,
            "warnings": list(self.warnings),
            "ranges": {k: dict(v) for k, v in self.ranges.items()},
            "reliability": dict(self.reliability),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GaitAnalysis":
        def _step(x: dict[str, Any]) -> StepMetrics:
            names = set(StepMetrics.__dataclass_fields__)
            kw = {k: v for k, v in x.items() if k in names}
            kw["side"] = Side(kw["side"])
            return StepMetrics(**kw)
        kf_names = set(KeyFrame.__dataclass_fields__)
        return cls(
            summary=GaitSummary.from_dict(d["summary"]) if d.get("summary") else None,
            steps=[_step(x) for x in d.get("steps", [])],
            findings=[Finding.from_dict(x) for x in d.get("findings", [])],
            keyframes=[KeyFrame(**{k: v for k, v in x.items() if k in kf_names})
                       for x in d.get("keyframes", [])],
            view=d.get("view", View.SIDE_LEFT.value), height_cm=d.get("height_cm"), fps=d.get("fps"),
            duration_s=d.get("duration_s"), frames=d.get("frames", 0),
            frames_with_pose=d.get("frames_with_pose", 0), facing=d.get("facing"),
            scale=ScaleInfo(**d["scale"]) if d.get("scale") else None,
            speed_kmh=d.get("speed_kmh"), video_path=d.get("video_path"),
            warnings=list(d.get("warnings", [])),
            ranges={k: dict(v) for k, v in (d.get("ranges") or {}).items()},
            reliability=dict(d.get("reliability") or {}),
        )


@dataclass
class LiveResult:
    """The newest live analysis result (pose.live.LiveAnalyzer.latest())."""
    frame: "Frame"                     # camera.base.Frame the result belongs to
    pose: Pose | None                  # None when nobody was found in the frame
    overlay: np.ndarray                # BGR copy of frame.image with skeleton and live angles drawn
    summary: GaitSummary | None        # rolling summary over the last seconds, None until enough steps
    fps_camera: float = 0.0            # frames per second delivered by the source
    fps_analysis: float = 0.0          # frames per second actually analysed (skips when slow)
    dropped: int = 0                   # frames skipped so far to keep up with the camera
    latency_ms: float = 0.0            # capture to result
    status: str = ""                   # localised status line, e.g. "Ingen löpare i bild"
