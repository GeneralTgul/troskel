"""What bike fit and SkiErg double poling share: the result type, reference values with their basis and
measurement uncertainty, cleaning of the pose series, reading a film, and comparison with the last test.

The running chain (gait.py, metrics.py, findings.py) is built around ground contacts. A pedal
revolution and a pole stroke are other cycles with other values, so each has its own package
(``pose.bike``, ``pose.ski``) and they meet here.

Two rules shape this module:
  * The tests should be as good as possible: only what 2D video measures reliably is
    judged. A value that cannot be trusted (the ankle) is shown without a verdict.
  * A deviation that is smaller than the measurement error is never presented as a fault: it is
    marked uncertain, and the coach is told to measure again before changing anything.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from lopanalys.core.i18n import t
from lopanalys.pose.filters import fill_gaps, lowpass, mask_low_visibility, remove_spikes, resample_uniform
from lopanalys.pose.findings import num
from lopanalys.pose.series import PoseSeries
from lopanalys.pose.types import Finding, KeyFrame, Pose, ScaleInfo, Severity, Side, View

log = logging.getLogger(__name__)

BIKEFIT = "bikefit"
SKI = "ski"
KINDS = (BIKEFIT, SKI)

# How a reference range is founded. Shown next to every range, on screen and in the report.
RESEARCH = "research"        # published range, source given
SOURCE = "source"            # stated by a named source (e.g. Concept2) without numbers in degrees
EXPERIENCE = "experience"    # rule of thumb from coaching and fitting practice, not verified
NO_VERDICT = "none"          # measured and shown, never judged

_ORDER = {Severity.FIX: 0, Severity.WATCH: 1, Severity.OK: 2}


@dataclass(frozen=True)
class Ref:
    """One measured value: its range, where the range comes from and how well video measures it."""
    metric: str
    unit: str = "°"
    ok_lo: float | None = None
    ok_hi: float | None = None
    fix_lo: float | None = None
    fix_hi: float | None = None
    decimals: int = 0
    basis: str = EXPERIENCE
    source: str = ""                    # short citation, printed under the table
    uncertainty: float | None = None    # +- in ``unit``: what 2D pose estimation gives for this value
    max_severity: Severity = Severity.FIX   # WATCH = never shown as a fault (rule of thumb only)

    @property
    def judged(self) -> bool:
        return self.basis != NO_VERDICT and (self.ok_lo is not None or self.ok_hi is not None)

    def classify(self, value: float | None) -> Severity | None:
        """The level of ``value``; None when there is no value or the metric is not judged."""
        if value is None or value != value or not self.judged:
            return None
        v = round(value, self.decimals)
        if (self.ok_lo is None or v >= self.ok_lo) and (self.ok_hi is None or v <= self.ok_hi):
            return Severity.OK
        fix = (self.fix_lo is not None and v < self.fix_lo) or (self.fix_hi is not None and v > self.fix_hi)
        sev = Severity.FIX if fix else Severity.WATCH
        return sev if _ORDER[sev] >= _ORDER[self.max_severity] else self.max_severity

    def deviation(self, value: float | None) -> float:
        """How far outside the ok range ``value`` is (0 inside), in ``unit``. + = above, - = below."""
        if value is None or value != value:
            return 0.0
        if self.ok_hi is not None and value > self.ok_hi:
            return float(value - self.ok_hi)
        if self.ok_lo is not None and value < self.ok_lo:
            return float(value - self.ok_lo)
        return 0.0

    def within_error(self, value: float | None) -> bool:
        """True when ``value`` is outside the ok range by less than the measurement uncertainty."""
        d = abs(self.deviation(value))
        return bool(self.uncertainty) and 0 < d <= float(self.uncertainty)

    def text(self) -> str:
        """The ok range with its unit, localised: "33–43°", "minst 1,2"."""
        u = self.unit if self.unit in ("°", "") else " " + self.unit
        d = self.decimals
        if self.ok_lo is not None and self.ok_hi is not None:
            return t("pose.range.between", lo=num(self.ok_lo, d), hi=num(self.ok_hi, d), unit=u)
        if self.ok_lo is not None:
            return t("pose.range.min", v=num(self.ok_lo, d), unit=u)
        if self.ok_hi is not None:
            return t("pose.range.max", v=num(self.ok_hi, d), unit=u)
        return ""

    def judged_dict(self, value: float | None) -> dict[str, Any]:
        """What is stored with an analysis: the range, the judged value, its level and the caveats."""
        sev = self.classify(value)
        d = asdict(self)
        d["max_severity"] = self.max_severity.value
        d.update(value=None if value is None else round(float(value), 2),
                 severity=sev.value if sev else None, text=self.text(),
                 within_error=bool(sev not in (None, Severity.OK) and self.within_error(value)),
                 basis_label=t(f"technique.basis.{self.basis}"))
        return d


@dataclass
class TechniqueAnalysis:
    """Result of one film of a bike fit or a SkiErg test: what the screen and the report show."""
    kind: str                       # BIKEFIT | SKI
    view: str
    summary: dict[str, Any] = field(default_factory=dict)       # metric -> median over the cycles
    cycles: list[dict[str, Any]] = field(default_factory=list)  # one dict per revolution / stroke
    findings: list[Finding] = field(default_factory=list)
    keyframes: list[KeyFrame] = field(default_factory=list)
    ranges: dict[str, dict[str, Any]] = field(default_factory=dict)   # metric -> Ref.judged_dict()
    warnings: list[str] = field(default_factory=list)           # localised, shown to the coach
    advice: list[dict[str, Any]] = field(default_factory=list)  # bike fit: what to change on the bike
    comparison: list[dict[str, Any]] = field(default_factory=list)   # against the previous test
    quality: dict[str, Any] = field(default_factory=dict)       # {"level": good|uncertain|poor, ...}
    height_cm: float | None = None
    fps: float | None = None
    duration_s: float | None = None
    frames: int = 0
    frames_with_pose: int = 0
    facing: int | None = None
    scale: ScaleInfo | None = None
    video_path: str | None = None
    options: dict[str, Any] = field(default_factory=dict)       # e.g. {"crank_mm": 172.5}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["findings"] = [f.to_dict() for f in self.findings]
        d["keyframes"] = [k.to_dict() for k in self.keyframes]
        d["scale"] = asdict(self.scale) if self.scale else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TechniqueAnalysis":
        names = set(cls.__dataclass_fields__)
        kw = {k: v for k, v in d.items() if k in names}
        kf_names = set(KeyFrame.__dataclass_fields__)
        kw["findings"] = [Finding.from_dict(x) for x in d.get("findings") or []]
        kw["keyframes"] = [KeyFrame(**{k: v for k, v in x.items() if k in kf_names}) for x in d.get("keyframes") or []]
        kw["scale"] = ScaleInfo(**d["scale"]) if d.get("scale") else None
        return cls(**kw)


def is_technique(data: Any) -> bool:
    """True for a stored analysis dict of a bike fit or a SkiErg test (not a running analysis)."""
    return isinstance(data, dict) and data.get("kind") in KINDS


# --- the pose series ----------------------------------------------------------------------------------

def series_from(poses: Sequence[Pose | None], times: Sequence[float] | None = None) -> PoseSeries | None:
    """The poses as a series, without leading and trailing frames where nobody was found."""
    if sum(p is not None for p in poses) < 10:
        return None
    raw = PoseSeries.from_poses(list(poses), times=times)
    have = ~np.isnan(raw.xy[:, 23, 0]) | ~np.isnan(raw.xy[:, 24, 0])
    if not have.any():
        return None
    i0, i1 = int(np.argmax(have)), len(have) - int(np.argmax(have[::-1]))
    return raw.slice(i0, i1)


def prepare(raw: PoseSeries, *, min_visibility: float = 0.2, cutoff_hz: float | None = None) -> PoseSeries:
    """Clean and low-pass a series of a cyclist or a skier.

    The running chain also repairs swapped legs with the treadmill belt as reference
    (``filters.fix_leg_swaps``). There is no belt here and only the near side is measured, so that
    step is left out.
    """
    out = mask_low_visibility(raw, min_visibility)
    out = remove_spikes(out)
    out = fill_gaps(out, max_gap_s=max(0.12, 3.5 / out.fps))
    out = resample_uniform(out)
    return lowpass(out, cutoff_hz)


def achieved_fps(s: PoseSeries) -> float:
    dur = float(s.t[-1] - s.t[0])
    return (len(s) - 1) / dur if dur > 0 else s.fps


def body_facing(s: PoseSeries) -> int:
    """+1 when the athlete faces image right: the shoulders and the nose lie ahead of the hips."""
    d = np.nanmedian(s.mid("shoulder")[:, 0] - s.mid("hip")[:, 0])
    n = np.nanmedian(s.lm("nose")[:, 0] - s.mid("shoulder")[:, 0])
    v = (0.0 if np.isnan(d) else d) + (0.0 if np.isnan(n) else n)
    return 1 if v >= 0 else -1


def visible_share(raw: PoseSeries, names: Sequence[str], min_visibility: float = 0.5) -> float:
    """Share of the frames where all of ``names`` were seen with at least ``min_visibility``."""
    from lopanalys.pose.types import LM
    idx = [LM[n] for n in names]
    ok = (raw.vis[:, idx] >= min_visibility).all(axis=1) & ~np.isnan(raw.xy[:, idx, 0]).any(axis=1)
    return float(ok.mean()) if len(ok) else 0.0


def parabola_extreme(y: np.ndarray, i: int) -> tuple[float, float]:
    """(row, value) of the extreme near row ``i``, refined between the frames with a parabola."""
    if i <= 0 or i >= len(y) - 1 or np.isnan(y[i - 1:i + 2]).any():
        return float(i), float(y[i])
    a, b, c = y[i - 1], y[i], y[i + 1]
    den = a - 2 * b + c
    if abs(den) < 1e-12:
        return float(i), float(b)
    dx = 0.5 * (a - c) / den
    if abs(dx) > 1:
        return float(i), float(b)
    return i + dx, float(b - 0.25 * (a - c) * dx)


def median(values: Sequence[float | None]) -> float | None:
    arr = np.array([v for v in values if v is not None and np.isfinite(v)], float)
    return float(np.median(arr)) if arr.size else None


def near(view: View | str) -> Side:
    return Side.RIGHT if View(view) == View.SIDE_RIGHT else Side.LEFT


# --- findings -------------------------------------------------------------------------------------------

def make_finding(kind: str, code: str, ref: Ref, value: float | None, *, variant: str | None = None,
                 frame_index: int | None = None, highlight: list[str] | None = None, side: Side | None = None,
                 severity: Severity | None = None, **fmt: str) -> Finding:
    """A ``Finding`` with texts from ``technique.<kind>.finding.<code>.*``.

    The reference text always says how the range is founded. A deviation inside the measurement error
    gets a sentence that says so, and is never more than *watch*.
    """
    sev = severity or ref.classify(value) or Severity.OK      # ``severity`` overrides (a joint verdict)
    uncertain = sev != Severity.OK and ref.within_error(value)
    if uncertain:
        sev = Severity.WATCH
    base = f"technique.{kind}.finding.{code}"
    key = variant or ("ok" if sev == Severity.OK else "problem")
    fmt.setdefault("value", num(value, ref.decimals))
    expl = t(f"{base}.{key}", **fmt)
    if uncertain:
        expl += " " + t("technique.finding.within_error", unc=num(ref.uncertainty, ref.decimals),
                        unit=ref.unit if ref.unit in ("°", "") else " " + ref.unit)
    reference = ref.text()
    if reference:
        reference = t("technique.ref.with_basis", range=reference, basis=t(f"technique.basis.{ref.basis}"))
    def text(what: str) -> str:
        """``<base>.<what>.<variant>`` when the variant has its own text, else ``<base>.<what>``; "" when neither exists."""
        for k in (f"{base}.{what}.{key}", f"{base}.{what}"):
            got = t(k, **fmt)
            if got != k:
                return got
        return ""

    cue, drill = ("", "") if sev == Severity.OK else (text("cue"), text("drill"))
    return Finding(code=code, severity=sev, title=t(f"{base}.title"), explanation=expl, cue=cue, drill=drill,
                   value=None if value is None else round(float(value), ref.decimals or 1), unit=ref.unit,
                   reference=reference, frame_index=frame_index, highlight=highlight or [],
                   side=side.value if side else None)


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: _ORDER[f.severity])


def quality(level: str, reasons: list[str]) -> dict[str, Any]:
    return {"level": level, "label": t(f"technique.quality.{level}"), "reasons": reasons}


def common_warnings(kind: str, raw: PoseSeries, frames: int, found: int, fps: float, min_fps: float) -> list[str]:
    """Warnings every film can get: the athlete not seen, and too few frames per second."""
    out = []
    pct = 100.0 * found / max(1, frames)
    if pct < 80:
        out.append(t("technique.warn.low_detection", pct=num(pct)))
    if fps < min_fps:
        out.append(t(f"technique.{kind}.warn.low_fps", fps=num(fps)))
    return out


# --- comparison with the previous test ------------------------------------------------------------------

def compare(current: dict[str, Any] | TechniqueAnalysis, previous: dict[str, Any] | TechniqueAnalysis | None,
            metrics: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Each value against the athlete's previous test of the same kind and view.

    ``trend`` is "better" when the value moved towards its ok range (or stayed inside it), "worse"
    when it moved away, "same" when the change is smaller than the measurement uncertainty, and None
    for values without a range. A change inside the uncertainty is never called an improvement.
    """
    if previous is None:
        return []
    cur = current.to_dict() if isinstance(current, TechniqueAnalysis) else current
    prev = previous.to_dict() if isinstance(previous, TechniqueAnalysis) else previous
    if cur.get("kind") != prev.get("kind"):
        return []
    out = []
    names = set(Ref.__dataclass_fields__)
    for key, rd in (cur.get("ranges") or {}).items():
        if metrics is not None and key not in metrics:
            continue
        now, before = (cur.get("summary") or {}).get(key), (prev.get("summary") or {}).get(key)
        if now is None or before is None:
            continue
        kw = {k: v for k, v in rd.items() if k in names}
        kw["max_severity"] = Severity(kw.get("max_severity", "fix"))
        ref = Ref(**kw)
        delta = float(now) - float(before)
        unc = ref.uncertainty or 0.0
        trend: str | None
        if abs(delta) <= unc or abs(delta) < 10 ** -ref.decimals:
            trend = "same"
        elif not ref.judged:
            trend = None
        else:
            d_now, d_before = abs(ref.deviation(now)), abs(ref.deviation(before))
            trend = "better" if d_now < d_before else "worse" if d_now > d_before else "same"
        out.append({"metric": key, "now": round(float(now), 2), "before": round(float(before), 2),
                    "delta": round(delta, 2), "unit": ref.unit, "decimals": ref.decimals, "trend": trend,
                    "uncertainty": ref.uncertainty, "judged": ref.judged})
    return out


# --- reading a film -----------------------------------------------------------------------------------------

ProgressCb = Callable[..., None]


class TechniqueError(RuntimeError):
    """The film could not be analysed; the message is localised for the coach."""


def read_poses(path: str | Path, *, estimator: Any = None, model_variant: str = "full",
               progress_cb: ProgressCb | None = None, max_seconds: float | None = None,
               cancel: threading.Event | None = None) -> tuple[list[Pose | None], list[float]]:
    """Every frame of a film through the pose estimator: (poses, times).

    The times come from the recorder's sidecar when there is one, as in ``offline.analyze_video``:
    a live camera rarely delivers its nominal rate, and the cadence would be wrong by that factor.
    """
    import cv2

    from lopanalys.pose.estimator import make_estimator
    from lopanalys.pose.models import call_progress
    from lopanalys.pose.offline import _open, recorded_times

    path = Path(path)
    if not path.is_file():
        raise TechniqueError(t("pose.error.video_open", name=path.name))
    own = estimator is None
    est = estimator if estimator is not None else make_estimator(model_variant, "video")
    try:
        cap = _open(path)
    except Exception as e:      # offline.AnalysisError carries a localised message
        if own:
            est.close()         # the model is loaded already: a film that cannot be read must not keep it
        raise TechniqueError(str(e)) from e
    fps_hdr = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    stamps = recorded_times(path, total)
    poses: list[Pose | None] = []
    times: list[float] = []
    i = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise TechniqueError(t("pose.error.cancelled"))
            ok, img = cap.read()
            if not ok:
                break
            tt = stamps[i] if stamps is not None and i < len(stamps) else i / fps_hdr
            if times and tt <= times[-1]:
                tt = times[-1] + 1.0 / fps_hdr
            if max_seconds and tt > max_seconds:
                break
            poses.append(est.process(img, tt, i))
            times.append(tt)
            i += 1
            if i % 15 == 0:
                call_progress(progress_cb, min(0.9 * i / max(total, i + 1), 0.9),
                              t("pose.progress.analyze", i=i, n=total or "?"))
    finally:
        cap.release()
        if own:
            est.close()
    return poses, times


def frame_of_row(s: PoseSeries, row: float) -> int:
    return int(round(float(np.interp(row, np.arange(len(s)), s.frame_index))))


def pose_for_frame(s: PoseSeries, frame_index: int) -> Pose:
    """The filtered pose of the row nearest a source frame, so the drawing matches the image."""
    return s.pose_at(int(np.argmin(np.abs(s.frame_index - frame_index))))
