"""Key frames for bike fit and double poling, drawn with the same canvas and look as the running stills.

``pose.annotate`` owns the canvas, the skeleton, the caption band and the phase strip. This module adds
what the two new analyses share: an angle drawn as an arc with its value, the colour of a value from
its verdict (grey when the value is shown without one), and the loop that turns a list of planned
frames into PNG files and ``KeyFrame`` rows.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from lopanalys.core.i18n import t
from lopanalys.pose import annotate as an
from lopanalys.pose import technique as tq
from lopanalys.pose.series import PoseSeries
from lopanalys.pose.types import LM, Finding, KeyFrame, Pose, Severity, Side, View

log = logging.getLogger(__name__)

Chips = list[tuple[str, tuple[int, int, int]]]
Point = tuple[float, float]


@dataclass
class Plan:
    """One planned key frame."""
    kind: str
    side: Side
    row: float                                   # row in the kinematic series
    frames: list[int]                            # source frames needed (several for a strip)
    values: dict[str, Any] = field(default_factory=dict)        # the measured values of the shown cycle
    findings: list[Finding] = field(default_factory=list)
    rows: list[tuple[str, float]] | None = None  # a strip: (label, row)
    path: np.ndarray | None = None               # (N, 2) image pixels: a joint's track to draw


def color(ranges: dict[str, dict[str, Any]], metric: str) -> tuple[int, int, int]:
    """The status colour of a value; grey for a value that is shown without a verdict."""
    sev = (ranges.get(metric) or {}).get("severity")
    return an.SEVERITY_COLOR[Severity(sev)] if sev else an.MUTED


def flagged(ranges: dict[str, dict[str, Any]], metric: str) -> tuple[int, int, int] | None:
    """The ring colour when the value is outside its range, else None."""
    sev = (ranges.get(metric) or {}).get("severity")
    return an.SEVERITY_COLOR[Severity(sev)] if sev in ("watch", "fix") else None


def _label_at(cv: an.Canvas, centre: Point, mid_deg: float, r: float, text: str, col: tuple[int, int, int]) -> None:
    if not text:
        return
    lab = an._from_dir(centre, mid_deg, r + 34 * cv.u)
    cv.label(lab, text, col, size=27, align="left" if math.cos(math.radians(mid_deg)) >= 0 else "right")


def interior(cv: an.Canvas, pose: Pose, a: str, b: str, c: str, col: tuple[int, int, int], text: str,
             share: float = 0.3) -> None:
    """The angle a-b-c as an arc at b."""
    pa, pb, pc = (cv.lm(pose, k) for k in (a, b, c))
    if any(math.isnan(v) for q in (pa, pb, pc) for v in q):
        return
    r = share * min(math.dist(pa, pb), math.dist(pc, pb))
    mid = cv.arc(pb, r, an._dir(pb, pa), an._dir(pb, pc), col)
    _label_at(cv, pb, mid, r, text, col)


def flexion(cv: an.Canvas, pose: Pose, a: str, b: str, c: str, col: tuple[int, int, int], text: str,
            share: float = 0.33) -> None:
    """Flexion at b (180 deg minus the angle a-b-c): the arc between the extension of a-b and b-c."""
    pa, pb, pc = (cv.lm(pose, k) for k in (a, b, c))
    if any(math.isnan(v) for q in (pa, pb, pc) for v in q):
        return
    L = min(math.dist(pa, pb), math.dist(pc, pb))
    ext = an._dir(pa, pb)
    cv.dashed(pb, an._from_dir(pb, ext, L * 0.5), an.TEXT + (170,), 3 * cv.u)
    mid = cv.arc(pb, share * L, ext, an._dir(pb, pc), col)
    _label_at(cv, pb, mid, share * L, text, col)


def against(cv: an.Canvas, origin: Point, tip: Point, ref_deg: float, col: tuple[int, int, int], text: str,
            share: float = 0.45) -> None:
    """The segment origin -> tip against a dashed reference direction (-90 = straight up, 0 = image right)."""
    L = math.dist(origin, tip)
    if not L or math.isnan(L):
        return
    cv.dashed(origin, an._from_dir(origin, ref_deg, L * 1.05), an.TEXT + (170,), 3 * cv.u)
    mid = cv.arc(origin, share * L, ref_deg, an._dir(origin, tip), col)
    _label_at(cv, origin, mid, share * L, text, col)


def track(cv: an.Canvas, pts: np.ndarray, col: tuple[int, int, int], width: float = 4.0) -> None:
    """A joint's path as a thin curve."""
    q = [cv.p(p) for p in pts if not np.isnan(p).any()]
    if len(q) >= 2:
        cv.d.line(q, fill=col + (220,), width=max(1, int(width * cv.u)), joint="curve")


DrawFn = Callable[[an.Canvas, Pose, Plan, Chips], None]


def render_all(kin: PoseSeries, view: View | str, plans: list[Plan], frames: dict[int, np.ndarray],
               out_dir: str | Path, stem: str, prefix: str, draw: DrawFn, *, aspect: float = 0.8,
               out_height: int = 1350) -> list[KeyFrame]:
    """Draw and save the planned key frames. ``prefix`` is the i18n root, e.g. "technique.bikefit"."""
    v = View(view)
    out_dir = Path(out_dir)
    singles = [p for p in plans if p.rows is None]
    ref = next(iter(frames.values()), None)
    size = (ref.shape[1], ref.shape[0]) if ref is not None else None
    crop = an.crop_box([tq.pose_for_frame(kin, p.frames[0]) for p in singles] or [kin.pose_at(0)],
                       aspect=aspect, image_size=size)
    out: list[KeyFrame] = []
    tt = lambda row: float(np.interp(row, np.arange(len(kin)), kin.t))     # noqa: E731
    for k, p in enumerate(plans):
        try:
            title = t(f"{prefix}.kf.{p.kind}")
            if p.rows is not None:
                items = []
                for (label, _row), fi in zip(p.rows, p.frames):
                    if fi not in frames:
                        continue
                    img = frames[fi]
                    if p.path is not None and len(p.path) >= 2:
                        img = img.copy()
                        pts = p.path[~np.isnan(p.path).any(axis=1)].astype(np.int32)
                        cv2.polylines(img, [pts], False, (153, 166, 34), max(1, img.shape[0] // 300), cv2.LINE_AA)
                    items.append((img, tq.pose_for_frame(kin, fi), label))
                if len(items) < 2:
                    continue
                image = an.render_sequence(items, v, side=p.side, title=title)
            else:
                fi = p.frames[0]
                if fi not in frames:
                    continue
                pose = tq.pose_for_frame(kin, fi)
                w = int(round(out_height * (crop[2] - crop[0]) / (crop[3] - crop[1])))
                cv = an.Canvas(w, out_height, crop)
                an.draw_skeleton(cv, pose, v, emphasis=p.side)
                chips: Chips = []
                draw(cv, pose, p, chips)
                pic = cv.compose(an._photo(frames[fi], crop, w, out_height))
                if v not in (View.SIDE_LEFT, View.SIDE_RIGHT):
                    an._legend(pic, v)
                image = cv2.cvtColor(np.asarray(an._caption_band(pic, title, "", chips).convert("RGB")),
                                     cv2.COLOR_RGB2BGR)
            path = an.save_png(image, out_dir / f"{stem}_{k + 1:02d}_{p.kind}.png")
            out.append(KeyFrame(label=title, frame_index=p.frames[0], t=tt(p.row), image_path=path,
                                findings=[f.code for f in p.findings], kind="sequence" if p.rows is not None else p.kind,
                                caption=t(f"{prefix}.kf.caption.{p.kind}")))
        except Exception:      # one bad drawing must not lose the analysis
            log.exception("drawing key frame %s failed", p.kind)
    return out


def attach(findings: list[Finding], plans: list[Plan], kind_of: dict[str, str]) -> None:
    """Point each finding at the key frame that shows it (and list it under that frame)."""
    for f in findings:
        match = [p for p in plans if p.kind == kind_of.get(f.code)]
        if match:
            f.frame_index = match[0].frames[0]
            if f not in match[0].findings:
                match[0].findings.append(f)


def lm(pose: Pose, name: str) -> np.ndarray:
    return pose.xy[LM[name]]
