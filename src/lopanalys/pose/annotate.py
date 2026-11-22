"""Drawing: professional key-frame stills for the report, the step-phase strip and the live overlay.

Key frames are drawn with Pillow on a transparent layer at twice the output size and scaled down
with a Lanczos filter, so lines, arcs and text are smooth at print size. The photo underneath is
cropped around the runner, slightly darkened and desaturated so the drawing stands out. Colours
follow the app's visual identity: left side teal (accent), right side blue (accent 2), status
colours good / watch / fix for measured values, rings and arrows.

The live overlay (``draw_live_overlay``) is drawn with OpenCV for speed: skeleton plus a few big
angle labels that can be read from 3 m on a TV. Text uses small Pillow-rendered patches that are
cached, because OpenCV's own fonts look poor.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from lopanalys.core.i18n import t
from lopanalys.pose.findings import num
from lopanalys.pose.series import angle_from_vertical, interior_angle
from lopanalys.pose.types import LM, Finding, GaitSummary, Pose, Severity, Side, StepMetrics, View

# --- visual identity ------------------------------------------------------------------------------

def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


INK = _hex("#141A21")
SURFACE = _hex("#1B232C")
TEXT = _hex("#E9EEF3")
MUTED = _hex("#8B98A6")
ACCENT = _hex("#22A699")
ACCENT2 = _hex("#3C82F6")
GOOD = _hex("#3FB950")
WATCH = _hex("#F2A93B")
FIX = _hex("#E5484D")
SIDE_COLOR = {Side.LEFT: ACCENT, Side.RIGHT: ACCENT2}
SEVERITY_COLOR = {Severity.OK: GOOD, Severity.WATCH: WATCH, Severity.FIX: FIX}

# which finding codes colour which measured value
_VALUE_CODES = {
    "overstride": ("overstride",), "shin": ("overstride",),
    "knee_contact": ("knee_stiff_contact",), "knee_mid": ("knee_flex_low", "knee_flex_high"),
    "trunk": ("trunk_upright", "trunk_forward", "trunk"), "hip_ext": ("hip_ext_low",),
    "pelvis": ("pelvic_drop",), "valgus": ("knee_valgus",), "width": ("step_width",),
    "vosc": ("vertical_osc",),
}

_FONT_CANDIDATES = {
    False: ["segoeui.ttf", "SegoeUI.ttf", "Inter-Regular.ttf", "arial.ttf",
            "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
    True: ["seguisb.ttf", "segoeuib.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf", "arialbd.ttf",
           "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
}


@lru_cache(maxsize=None)
def find_font(bold: bool = False) -> str | None:
    """Path to the best available UI font (Segoe UI on Windows), or None for Pillow's default."""
    dirs: list[Path] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs += [Path(windir) / "Fonts",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    dirs += [Path("/usr/share/fonts/truetype/liberation"), Path("/usr/share/fonts/truetype/dejavu"),
             Path("/usr/share/fonts/TTF"), Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    try:
        import matplotlib
        dirs.append(Path(matplotlib.get_data_path()) / "fonts" / "ttf")
    except Exception:  # pragma: no cover
        pass
    for name in _FONT_CANDIDATES[bold]:
        for d in dirs:
            p = d / name
            if p.is_file():
                return str(p)
    return None


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_font(bold)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


# --- geometry helpers -----------------------------------------------------------------------------

def _vis_points(pose: Pose, min_vis: float = 0.2) -> np.ndarray:
    ok = (pose.visibility >= min_vis) & ~np.isnan(pose.xy).any(axis=1)
    nose = bool(ok[0])
    ok[:11] = False            # face points are not needed for the frame
    ok[0] = nose               # but the nose is: the top of the head
    return pose.xy[ok]


def crop_box(poses: Sequence[Pose], aspect: float = 0.8, margin: float = 0.12,
             image_size: tuple[int, int] | None = None, below: float = 0.14) -> tuple[int, int, int, int]:
    """A crop (x0, y0, x1, y1) around the runner in all ``poses``, width/height = ``aspect``.

    The crop is allowed to extend past the image; those parts are filled with a dark tone.
    """
    pts = np.concatenate([_vis_points(p) for p in poses]) if poses else np.zeros((0, 2))
    if pts.size == 0:
        w, h = image_size or (poses[0].width, poses[0].height)
        return 0, 0, w, h
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    body = y1 - y0
    top, bottom = y0 - margin * body, y1 + below * body      # room for brackets and labels under the feet
    h = bottom - top
    w = max((x1 - x0) * (1 + 2 * margin), h * aspect)
    if w / aspect > h:                                          # wider than needed: grow height evenly
        extra = w / aspect - h
        top, bottom = top - extra / 2, bottom + extra / 2
    cx = (x0 + x1) / 2
    return int(cx - w / 2), int(top), int(cx + w / 2), int(bottom)


def _crop_image(image: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    h, w = image.shape[:2]
    out = np.full((y1 - y0, x1 - x0, 3), 24, np.uint8)
    sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = image[sy0:sy1, sx0:sx1]
    return out


def _photo(image: np.ndarray, box: tuple[int, int, int, int], out_w: int, out_h: int) -> Image.Image:
    crop = _crop_image(image, box)
    interp = cv2.INTER_AREA if crop.shape[0] > out_h else cv2.INTER_LANCZOS4
    crop = cv2.resize(crop, (out_w, out_h), interpolation=interp)
    pil = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    pil = ImageEnhance.Color(pil).enhance(0.7)
    pil = ImageEnhance.Brightness(pil).enhance(0.78)
    # soft vignette
    yy, xx = np.mgrid[0:out_h, 0:out_w]
    r = np.sqrt(((xx - out_w / 2) / (out_w / 2)) ** 2 + ((yy - out_h / 2) / (out_h / 2)) ** 2)
    v = np.clip(1.0 - 0.28 * np.clip(r - 0.55, 0, None) ** 1.5, 0.6, 1.0)
    arr = (np.asarray(pil, np.float32) * v[..., None]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(arr).convert("RGBA")


def _lm_angle(pose: Pose, a: str, b: str, c: str) -> float:
    return float(interior_angle(pose.xy[LM[a]], pose.xy[LM[b]], pose.xy[LM[c]]))


def _severity_for(findings: Iterable[Finding], key: str) -> Severity | None:
    codes = _VALUE_CODES.get(key, ())
    sev = [f.severity for f in findings if f.code in codes]
    if not sev:
        return None
    order = {Severity.FIX: 0, Severity.WATCH: 1, Severity.OK: 2}
    return min(sev, key=lambda s: order[s])


def _value_color(findings: Iterable[Finding], key: str) -> tuple[int, int, int]:
    s = _severity_for(findings, key)
    return ACCENT if s is None else SEVERITY_COLOR[s]


# --- the canvas -------------------------------------------------------------------------------------

class Canvas:
    """A 2x supersampled RGBA drawing layer plus a glow layer, mapped from image pixels."""

    SS = 2

    def __init__(self, out_w: int, out_h: int, box: tuple[int, int, int, int]) -> None:
        self.w, self.h = out_w, out_h
        self.box = box
        self.k = out_w / (box[2] - box[0]) * self.SS
        self.layer = Image.new("RGBA", (out_w * self.SS, out_h * self.SS), (0, 0, 0, 0))
        self.glow = Image.new("RGBA", (out_w * self.SS, out_h * self.SS), (0, 0, 0, 0))
        self.under = Image.new("RGBA", (out_w * self.SS, out_h * self.SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.layer)
        self.g = ImageDraw.Draw(self.glow)
        self.ud = ImageDraw.Draw(self.under)
        self.placed: list[tuple[float, float, float, float]] = []
        self.u = out_h / 1000.0 * self.SS     # one "unit": 1/1000 of the output height, at 2x

    def p(self, xy: np.ndarray) -> tuple[float, float]:
        return ((float(xy[0]) - self.box[0]) * self.k, (float(xy[1]) - self.box[1]) * self.k)

    def lm(self, pose: Pose, name: str) -> tuple[float, float]:
        return self.p(pose.xy[LM[name]])

    # primitives (coordinates already on the 2x canvas)
    def line(self, a: tuple[float, float], b: tuple[float, float], color: tuple[int, ...], width: float,
             shadow: bool = True) -> None:
        w = max(1, int(round(width)))
        if shadow:
            self.d.line([a, b], fill=INK + (150,), width=int(w + 3 * self.u))
        self.d.line([a, b], fill=_rgba(color), width=w)
        r = w / 2
        for q in (a, b):
            self.d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=_rgba(color))

    def dashed(self, a: tuple[float, float], b: tuple[float, float], color: tuple[int, ...], width: float,
               dash: float | None = None, gap: float | None = None) -> None:
        dash = dash or 14 * self.u
        gap = gap or 9 * self.u
        ax, ay = a
        L = math.hypot(b[0] - ax, b[1] - ay)
        if L < 1:
            return
        ux, uy = (b[0] - ax) / L, (b[1] - ay) / L
        s = 0.0
        while s < L:
            e = min(L, s + dash)
            self.d.line([(ax + ux * s, ay + uy * s), (ax + ux * e, ay + uy * e)], fill=_rgba(color),
                         width=max(1, int(width)))
            s = e + gap

    def joint(self, q: tuple[float, float], color: tuple[int, ...], r: float) -> None:
        self.d.ellipse([q[0] - r - 2 * self.u, q[1] - r - 2 * self.u, q[0] + r + 2 * self.u,
                        q[1] + r + 2 * self.u], fill=INK + (170,))
        self.d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=_rgba(color))
        ri = r * 0.45
        self.d.ellipse([q[0] - ri, q[1] - ri, q[0] + ri, q[1] + ri], fill=(255, 255, 255, 235))

    def ring(self, q: tuple[float, float], r: float, color: tuple[int, int, int]) -> None:
        """Status ring with a soft glow around a problem area."""
        w = 7 * self.u
        self.g.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], outline=color + (230,), width=int(w * 3.2))
        self.ud.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=color + (40,))
        self.ud.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], outline=color + (255,), width=int(w))
        r2 = r + 10 * self.u
        self.ud.ellipse([q[0] - r2, q[1] - r2, q[0] + r2, q[1] + r2], outline=color + (110,),
                        width=max(1, int(2.5 * self.u)))

    def arrow(self, a: tuple[float, float], b: tuple[float, float], color: tuple[int, int, int],
              width: float | None = None) -> None:
        width = width or 9 * self.u
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1:
            return
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        head = min(L * 0.45, 34 * self.u)
        base = (b[0] - ux * head, b[1] - uy * head)
        nx, ny = -uy, ux
        hw = head * 0.62
        tri = [b, (base[0] + nx * hw, base[1] + ny * hw), (base[0] - nx * hw, base[1] - ny * hw)]
        sh = 3 * self.u
        self.d.line([a, base], fill=INK + (170,), width=int(width + 2 * sh))
        self.d.polygon([(x + 0 * sh, y + 0 * sh) for x, y in _grow(tri, sh)], fill=INK + (170,))
        self.d.line([a, base], fill=color + (255,), width=int(width))
        self.d.polygon(tri, fill=color + (255,))

    def arc(self, c: tuple[float, float], r: float, a0: float, a1: float, color: tuple[int, int, int],
            width: float | None = None) -> float:
        """Arc from direction a0 to a1 (degrees, image convention, shortest way). Returns mid angle."""
        width = width or 6 * self.u
        d = (a1 - a0 + 540) % 360 - 180
        start, end = (a0, a0 + d) if d >= 0 else (a0 + d, a0)
        box = [c[0] - r, c[1] - r, c[0] + r, c[1] + r]
        self.d.pieslice(box, start, end, fill=color + (58,))
        self.d.arc([b - 1.5 * self.u if i < 2 else b + 1.5 * self.u for i, b in enumerate(box)],
                   start, end, fill=INK + (140,), width=int(width + 3 * self.u))
        self.d.arc(box, start, end, fill=color + (255,), width=int(width))
        return (start + end) / 2

    def label(self, anchor: tuple[float, float], text: str, color: tuple[int, int, int],
              size: float = 30, align: str = "left") -> tuple[float, float, float, float]:
        """A pill-shaped label with a coloured edge; ``anchor`` is its left-middle (or right/center)."""
        f = font(int(size * self.u), bold=True)
        tb = self.d.textbbox((0, 0), text, font=f)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        px, py = 14 * self.u, 9 * self.u
        bw, bh = tw + 2 * px + 6 * self.u, th + 2 * py
        x, y = anchor
        if align == "right":
            x -= bw
        elif align == "center":
            x -= bw / 2
        y -= bh / 2
        x = min(max(x, 8 * self.u), self.layer.width - bw - 8 * self.u)
        y = min(max(y, 8 * self.u), self.layer.height - bh - 8 * self.u)
        # move away from labels already placed (alternately below and above)
        for k in range(1, 12):
            if not any(x < q[2] and x + bw > q[0] and y < q[3] and y + bh > q[1] for q in self.placed):
                break
            y0 = y + (1 if k % 2 else -1) * ((k + 1) // 2) * (bh + 6 * self.u)
            if 8 * self.u <= y0 <= self.layer.height - bh - 8 * self.u:
                y = y0
        self.placed.append((x, y, x + bw, y + bh))
        rr = bh / 2
        self.d.rounded_rectangle([x, y, x + bw, y + bh], radius=rr, fill=INK + (225,),
                                 outline=color + (255,), width=max(1, int(3 * self.u)))
        self.d.ellipse([x + px * 0.55, y + bh / 2 - 5 * self.u, x + px * 0.55 + 10 * self.u, y + bh / 2 + 5 * self.u],
                       fill=color + (255,))
        self.d.text((x + px + 8 * self.u - tb[0], y + py - tb[1]), text, font=f, fill=TEXT + (255,))
        return x, y, x + bw, y + bh

    def compose(self, photo: Image.Image) -> Image.Image:
        glow = self.glow.filter(ImageFilter.GaussianBlur(14 * self.u))
        glow = glow.resize((self.w, self.h), Image.LANCZOS)
        layer = self.layer.resize((self.w, self.h), Image.LANCZOS)
        under = self.under.resize((self.w, self.h), Image.LANCZOS)
        out = Image.alpha_composite(photo, glow)
        out = Image.alpha_composite(out, under)
        return Image.alpha_composite(out, layer)


def _rgba(c: tuple[int, ...]) -> tuple[int, int, int, int]:
    return c if len(c) == 4 else (c[0], c[1], c[2], 255)  # type: ignore[return-value]


def _grow(tri: list[tuple[float, float]], d: float) -> list[tuple[float, float]]:
    cx = sum(p[0] for p in tri) / 3
    cy = sum(p[1] for p in tri) / 3
    out = []
    for x, y in tri:
        L = math.hypot(x - cx, y - cy) or 1
        out.append((x + (x - cx) / L * d * 1.6, y + (y - cy) / L * d * 1.6))
    return out


def _dir(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _from_dir(c: tuple[float, float], ang: float, r: float) -> tuple[float, float]:
    return c[0] + r * math.cos(math.radians(ang)), c[1] + r * math.sin(math.radians(ang))


# --- skeleton ---------------------------------------------------------------------------------------

_SEGMENTS_SIDE = [("shoulder", "elbow"), ("elbow", "wrist"), ("hip", "knee"), ("knee", "ankle"),
                  ("ankle", "heel"), ("heel", "foot"), ("ankle", "foot")]


def _near_side(view: View, pose: Pose) -> Side:
    if view == View.SIDE_LEFT:
        return Side.LEFT
    if view == View.SIDE_RIGHT:
        return Side.RIGHT
    return Side.LEFT


def draw_skeleton(cv: Canvas, pose: Pose, view: View, dim_far: bool = True,
                  emphasis: Side | None = None) -> None:
    """Both sides, far side first and dimmer (side views), trunk and head line on top."""
    side_view = view in (View.SIDE_LEFT, View.SIDE_RIGHT)
    near = _near_side(view, pose)
    order = [Side.RIGHT if near == Side.LEFT else Side.LEFT, near] if side_view else [Side.LEFT, Side.RIGHT]
    lw = 9 * cv.u
    for side in order:
        far = side_view and side != near
        col = SIDE_COLOR[side]
        alpha = 150 if (far and dim_far) else 255
        if emphasis is not None and side != emphasis and not side_view:
            alpha = 170
        pre = "l_" if side == Side.LEFT else "r_"
        width = lw * (0.72 if far else 1.0)
        for a, b in _SEGMENTS_SIDE:
            pa, pb = pose.xy[LM[pre + a]], pose.xy[LM[pre + b]]
            if np.isnan(pa).any() or np.isnan(pb).any():
                continue
            cv.line(cv.p(pa), cv.p(pb), col + (alpha,), width, shadow=not far)
    # trunk: shoulders and hips as a quad outline + centre line
    ls, rs, lh, rh = (cv.lm(pose, k) for k in ("l_shoulder", "r_shoulder", "l_hip", "r_hip"))
    sc = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    hc = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    if not side_view:
        cv.line(ls, rs, TEXT + (230,), lw * 0.8)
        cv.line(lh, rh, TEXT + (230,), lw * 0.8)
        for side, (s_, h_) in ((Side.LEFT, (ls, lh)), (Side.RIGHT, (rs, rh))):
            cv.line(s_, h_, SIDE_COLOR[side] + (200,), lw * 0.7)
    cv.line(hc, sc, TEXT + (240,), lw * 1.05)
    nose = cv.lm(pose, "nose")
    head = nose
    cv.line(sc, ((sc[0] * 0.35 + head[0] * 0.65), (sc[1] * 0.35 + head[1] * 0.65)), TEXT + (200,), lw * 0.8)
    # joints
    jr = 8.5 * cv.u
    for side in order:
        far = side_view and side != near
        pre = "l_" if side == Side.LEFT else "r_"
        for j in ("shoulder", "elbow", "wrist", "hip", "knee", "ankle"):
            q = pose.xy[LM[pre + j]]
            if np.isnan(q).any():
                continue
            cv.joint(cv.p(q), SIDE_COLOR[side] + ((150,) if far else (255,)), jr * (0.75 if far else 1.0))
    cv.joint(hc, TEXT, jr * 0.9)


# --- key frames ---------------------------------------------------------------------------------------

@dataclass
class KeyFrameSpec:
    """What one key frame shows."""
    kind: str                       # contact | midstance | toe_off | flight
    side: Side | None
    title: str
    subtitle: str = ""


def _caption_band(img: Image.Image, title: str, subtitle: str,
                  chips: list[tuple[str, tuple[int, int, int]]]) -> Image.Image:
    """Return ``img`` with a caption band below it: title, subtitle and value chips with status dots."""
    W, H = img.size
    u = H / 1000.0
    ft, fs, fc = font(int(38 * u), True), font(int(24 * u)), font(int(26 * u), True)
    pad = 26 * u
    tmp = ImageDraw.Draw(img)
    chip_ws = [tmp.textlength(c[0], font=fc) + 44 * u for c in chips]
    rows: list[list[int]] = [[]]
    row_w = 0.0
    for i, cw in enumerate(chip_ws):
        if rows[-1] and row_w + cw + 12 * u > W - 2 * pad - 14 * u:
            rows.append([])
            row_w = 0
        rows[-1].append(i)
        row_w += cw + 12 * u
    n_rows = len([r for r in rows if r])
    band_h = int(pad * 2 + 46 * u + (32 * u if subtitle else 0) + n_rows * 50 * u)
    out = Image.new("RGBA", (W, H + band_h), INK + (255,))
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    d.rectangle([0, H, W, H + 4 * u], fill=ACCENT + (255,))
    x0, y = pad, H + pad
    d.text((x0, y), title, font=ft, fill=TEXT + (255,))
    y += 50 * u
    if subtitle:
        d.text((x0, y), subtitle, font=fs, fill=MUTED + (255,))
        y += 34 * u
    for r in rows:
        x = x0
        for i in r:
            text, col = chips[i]
            cw = chip_ws[i]
            d.rounded_rectangle([x, y, x + cw, y + 40 * u], radius=20 * u, fill=SURFACE + (255,),
                                outline=col + (255,), width=max(1, int(2 * u)))
            d.ellipse([x + 14 * u, y + 15 * u, x + 24 * u, y + 25 * u], fill=col + (255,))
            d.text((x + 32 * u, y + 20 * u), text, font=fc, fill=TEXT + (255,), anchor="lm")
            x += cw + 12 * u
        y += 50 * u
    return out


def _legend(img: Image.Image, view: View) -> None:
    """Small left/right colour key at the top-right."""
    d = ImageDraw.Draw(img)
    W, H = img.size
    u = H / 1000.0
    f = font(int(21 * u), True)
    items = [(t("pose.side_title.left"), ACCENT), (t("pose.side_title.right"), ACCENT2)]
    widths = [d.textlength(s, font=f) for s, _ in items]
    total = sum(widths) + len(items) * 34 * u + 22 * u
    x = W - 26 * u - total
    y = 26 * u
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle([x, y, x + total, y + 40 * u], radius=20 * u, fill=INK + (200,))
    img.alpha_composite(overlay)
    d = ImageDraw.Draw(img)
    x += 14 * u
    for (s, col), w in zip(items, widths):
        d.rounded_rectangle([x, y + 17 * u, x + 20 * u, y + 23 * u], radius=3 * u, fill=col + (255,))
        d.text((x + 26 * u, y + 20 * u), s, font=f, fill=TEXT + (255,), anchor="lm")
        x += w + 34 * u


def _sagittal_annotations(cv: Canvas, pose: Pose, kind: str, side: Side, facing: int,
                          step: StepMetrics | None, findings: list[Finding],
                          chips: list[tuple[str, tuple[int, int, int]]], colorf: list[Finding]) -> None:
    pre = "l_" if side == Side.LEFT else "r_"
    hip, knee, ankle = (cv.lm(pose, pre + k) for k in ("hip", "knee", "ankle"))
    heel, toe = cv.lm(pose, pre + "heel"), cv.lm(pose, pre + "foot")
    lh, rh = cv.lm(pose, "l_hip"), cv.lm(pose, "r_hip")
    hc = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    ls, rs = cv.lm(pose, "l_shoulder"), cv.lm(pose, "r_shoulder")
    sc = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    u = cv.u
    ground = max(heel[1], toe[1])
    if kind == "contact":
        # plumb line from the hip to the belt
        cv.dashed((hc[0], hc[1] - 40 * u), (hc[0], ground + 18 * u), TEXT + (210,), 4 * u)
        # overstride bracket at belt level
        os_cm = step.overstride_cm if step else None
        col = _value_color(colorf, "overstride")
        y = ground + 34 * u
        cv.line((hc[0], y), (heel[0], y), col, 5 * u)
        for x in (hc[0], heel[0]):
            cv.line((x, y - 14 * u), (x, y + 14 * u), col, 5 * u)
        if os_cm is not None:
            mid = ((hc[0] + heel[0]) / 2, y + 44 * u)
            cv.label(mid, t("pose.label.overstride", value=num(os_cm)), col, size=30, align="center")
            chips.append((f"{t('pose.metric.overstride_cm')} {num(os_cm)} cm", col))
        # shin angle against the vertical
        shin = step.shin_angle_deg if step and step.shin_angle_deg is not None else float(
            angle_from_vertical(pose.xy[LM[pre + "knee"]], pose.xy[LM[pre + "ankle"]], facing))
        scol = _value_color(colorf, "shin")
        L = math.hypot(ankle[0] - knee[0], ankle[1] - knee[1])
        cv.dashed(knee, (knee[0], knee[1] + L * 0.95), TEXT + (170,), 3 * u)
        cv.arc(knee, L * 0.5, 90, _dir(knee, ankle), scol)
        fx = knee[0] * 0.6 + ankle[0] * 0.4
        cv.label((fx + facing * 46 * u, knee[1] + L * 0.38), t("pose.label.shin", value=num(shin)), scol, size=27,
                 align="left" if facing > 0 else "right")
        chips.append((t("pose.label.shin", value=num(shin)), scol))
        # knee flexion at contact
        kf = step.knee_flex_contact_deg if step and step.knee_flex_contact_deg is not None else \
            180 - _lm_angle(pose, pre + "hip", pre + "knee", pre + "ankle")
        kcol = _value_color(colorf, "knee_contact")
        chips.append((t("pose.label.knee", value=num(kf)), kcol))
        sev = _severity_for(findings, "overstride")
        if sev in (Severity.WATCH, Severity.FIX):
            fl = ((heel[0] + toe[0]) / 2, (heel[1] + toe[1]) / 2)
            r = max(50 * u, 0.62 * math.hypot(toe[0] - heel[0], toe[1] - heel[1]))
            cv.ring(fl, r, SEVERITY_COLOR[sev])
            # where the foot should land: under the knee/closer to the plumb line
            target_x = knee[0] * 0.5 + hc[0] * 0.5
            ty = ground - 6 * u
            cv.dashed((target_x - 40 * u, ground + 4 * u), (target_x + 40 * u, ground + 4 * u), ACCENT + (255,), 5 * u)
            start = (heel[0] - facing * 18 * u, ty - r * 0.15)
            end = (target_x + facing * 26 * u, ty - r * 0.15)
            if abs(end[0] - start[0]) > 30 * u:
                cv.arrow(start, end, ACCENT)
    elif kind == "midstance":
        kf = step.knee_flex_midstance_deg if step and step.knee_flex_midstance_deg is not None else \
            180 - _lm_angle(pose, pre + "hip", pre + "knee", pre + "ankle")
        kcol = _value_color(colorf, "knee_mid")
        L = min(math.hypot(hip[0] - knee[0], hip[1] - knee[1]), math.hypot(ankle[0] - knee[0], ankle[1] - knee[1]))
        m = cv.arc(knee, L * 0.33, _dir(knee, hip) + 180, _dir(knee, ankle), kcol)
        cv.dashed(knee, _from_dir(knee, _dir(hip, knee), L * 0.5), TEXT + (170,), 3 * u)
        lab = _from_dir(knee, m, L * 0.33 + 30 * u)
        cv.label(lab, t("pose.label.knee", value=num(kf)), kcol, size=27,
                 align="left" if math.cos(math.radians(m)) >= 0 else "right")
        chips.append((t("pose.label.knee", value=num(kf)), kcol))
        sev = _severity_for(findings, "knee_mid")
        if sev in (Severity.WATCH, Severity.FIX):
            cv.ring(knee, L * 0.42, SEVERITY_COLOR[sev])
        # trunk lean
        lean = step.trunk_lean_deg if step and step.trunk_lean_deg is not None else None
        tcol = _value_color(colorf, "trunk")
        T = math.hypot(sc[0] - hc[0], sc[1] - hc[1])
        cv.dashed(hc, (hc[0], hc[1] - T * 1.05), TEXT + (170,), 3 * u)
        m = cv.arc(hc, T * 0.45, -90, _dir(hc, sc), tcol)
        if lean is not None:
            lab = _from_dir(hc, m, T * 0.45 + 30 * u)
            cv.label((lab[0] + facing * 10 * u, lab[1]), t("pose.label.trunk", value=num(lean)), tcol, size=27,
                     align="left" if facing > 0 else "right")
            chips.append((t("pose.label.trunk", value=num(lean)), tcol))
        sev = _severity_for(findings, "trunk")
        if sev in (Severity.WATCH, Severity.FIX):
            cv.ring(((hc[0] + sc[0]) / 2, (hc[1] + sc[1]) / 2), T * 0.42, SEVERITY_COLOR[sev])
        if step and step.vertical_oscillation_cm is not None:
            chips.append((t("pose.label.vosc", value=num(step.vertical_oscillation_cm, 1)),
                          _value_color(colorf, "vosc")))
    elif kind == "toe_off":
        he = step.hip_extension_toe_off_deg if step and step.hip_extension_toe_off_deg is not None else None
        hcol = _value_color(colorf, "hip_ext")
        L = math.hypot(knee[0] - hip[0], knee[1] - hip[1])
        cv.dashed(hip, (hip[0], hip[1] + L * 1.05), TEXT + (170,), 3 * u)
        m = cv.arc(hip, L * 0.45, 90, _dir(hip, knee), hcol)
        if he is not None:
            lab = _from_dir(hip, m, L * 0.45 + 30 * u)
            cv.label((lab[0] - facing * 10 * u, lab[1]), t("pose.label.hip_ext", value=num(he)), hcol, size=27,
                     align="right" if facing > 0 else "left")
            chips.append((t("pose.label.hip_ext", value=num(he)), hcol))
        sev = _severity_for(findings, "hip_ext")
        if sev in (Severity.WATCH, Severity.FIX):
            cv.ring(hip, 50 * u, SEVERITY_COLOR[sev])
            cv.arrow((knee[0], knee[1] + 10 * u), (knee[0] - facing * 70 * u, knee[1] - 6 * u), ACCENT)
        if step and step.ground_contact_ms is not None:
            chips.append((f"{t('pose.live.gct').capitalize()} {num(step.ground_contact_ms)} ms", ACCENT))
    elif kind == "flight":
        if step and step.vertical_oscillation_cm is not None:
            col = _value_color(colorf, "vosc")
            y = hc[1]
            cv.dashed((hc[0] - 120 * u, y), (hc[0] + 120 * u, y), col + (230,), 4 * u)
            chips.append((t("pose.label.vosc", value=num(step.vertical_oscillation_cm, 1)), col))


def _frontal_annotations(cv: Canvas, pose: Pose, view: View, kind: str, side: Side,
                         step: StepMetrics | None, findings: list[Finding],
                         chips: list[tuple[str, tuple[int, int, int]]], colorf: list[Finding]) -> None:
    u = cv.u
    lh, rh = cv.lm(pose, "l_hip"), cv.lm(pose, "r_hip")
    hc = ((lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2)
    ls, rs = cv.lm(pose, "l_shoulder"), cv.lm(pose, "r_shoulder")
    sc = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    la, ra = cv.lm(pose, "l_ankle"), cv.lm(pose, "r_ankle")
    ground = max(la[1], ra[1], cv.lm(pose, "l_heel")[1], cv.lm(pose, "r_heel")[1])
    # body midline
    top = (sc[0] + (sc[0] - hc[0]) * 0.3, sc[1] - 60 * u)
    cv.dashed(top, (hc[0] + (hc[0] - sc[0]) * 0.0, ground + 30 * u), TEXT + (150,), 3 * u)
    pre = "l_" if side == Side.LEFT else "r_"
    if kind in ("midstance", "contact"):
        # pelvis line and horizontal reference
        pcol = _value_color(colorf, "pelvis")
        hw = math.hypot(rh[0] - lh[0], rh[1] - lh[1])
        ext = hw * 0.55
        ux, uy = (rh[0] - lh[0]) / (hw or 1), (rh[1] - lh[1]) / (hw or 1)
        cv.line((lh[0] - ux * ext, lh[1] - uy * ext), (rh[0] + ux * ext, rh[1] + uy * ext), pcol, 6 * u)
        stance_hip = lh if side == Side.LEFT else rh
        other_hip = rh if side == Side.LEFT else lh
        cv.dashed(stance_hip, (stance_hip[0] + (other_hip[0] - stance_hip[0]) * 1.6, stance_hip[1]),
                  TEXT + (190,), 3 * u)
        a_line = _dir(stance_hip, other_hip)
        a_ref = 0.0 if other_hip[0] >= stance_hip[0] else 180.0
        cv.arc(stance_hip, hw * 0.85, a_ref, a_line, pcol)
        if step and step.pelvic_drop_deg is not None:
            out_dir = -1 if other_hip[0] >= stance_hip[0] else 1      # away from the other hip
            cv.label((stance_hip[0] + out_dir * (ext + 20 * u), stance_hip[1] - 40 * u),
                     t("pose.label.pelvis", value=num(step.pelvic_drop_deg, 1)), pcol, size=27,
                     align="left" if out_dir > 0 else "right")
            chips.append((t("pose.label.pelvis", value=num(step.pelvic_drop_deg, 1)), pcol))
        sev = _severity_for(findings, "pelvis")
        if sev in (Severity.WATCH, Severity.FIX):
            cv.ring(other_hip, 46 * u, SEVERITY_COLOR[sev])
            cv.arrow((other_hip[0], other_hip[1] + 60 * u), (other_hip[0], other_hip[1] - 4 * u - 50 * u), ACCENT)
        # knee: frontal plane projection angle on the stance leg
        hip, knee, ankle = (cv.lm(pose, pre + k) for k in ("hip", "knee", "ankle"))
        vcol = _value_color(colorf, "valgus")
        cv.dashed(hip, ankle, TEXT + (170,), 3 * u)
        if step and step.knee_valgus_deg is not None:
            L = math.hypot(ankle[0] - knee[0], ankle[1] - knee[1])
            cv.arc(knee, L * 0.35, _dir(hip, knee), _dir(knee, ankle), vcol)
            outward = -1 if (side == Side.LEFT) == (view == View.REAR) else 1
            cv.label((knee[0] + outward * 40 * u, knee[1]), t("pose.label.valgus", value=num(step.knee_valgus_deg)),
                     vcol, size=27, align="left" if outward > 0 else "right")
            chips.append((t("pose.label.valgus", value=num(step.knee_valgus_deg)), vcol))
            sev = _severity_for(findings, "valgus")
            if sev in (Severity.WATCH, Severity.FIX):
                cv.ring(knee, 48 * u, SEVERITY_COLOR[sev])
                cv.arrow((knee[0] - outward * 26 * u, knee[1] + 60 * u),
                         (knee[0] + outward * 64 * u, knee[1] + 60 * u), ACCENT)
        if step and step.step_width_cm is not None:
            wcol = _value_color(colorf, "width")
            y = ground + 34 * u
            cv.line((la[0], y), (ra[0], y), wcol, 5 * u)
            for x in (la[0], ra[0]):
                cv.line((x, y - 12 * u), (x, y + 12 * u), wcol, 5 * u)
            cv.label(((la[0] + ra[0]) / 2, y + 40 * u), t("pose.label.width", value=num(step.step_width_cm)), wcol,
                     size=26, align="center")
    for f in findings:
        if f.code == "arm_cross" and f.highlight:
            w = cv.lm(pose, f.highlight[0])
            cv.ring(w, 40 * u, SEVERITY_COLOR[f.severity])


def render_keyframe(image: np.ndarray, pose: Pose, view: View | str, kind: str, *, side: Side | None = None,
                    facing: int | None = None, step: StepMetrics | None = None,
                    findings: Sequence[Finding] = (), crop: tuple[int, int, int, int] | None = None,
                    out_height: int = 1350, title: str | None = None, subtitle: str = "",
                    legend: bool = True, all_findings: Sequence[Finding] | None = None) -> np.ndarray:
    """An annotated still (BGR) for the report.

    ``kind``: "contact", "midstance", "toe_off" or "flight". ``step`` supplies the measured values
    (so the numbers match the report tables); ``findings`` add rings and arrows, and
    ``all_findings`` (default: ``findings``) set the status colour of each value.
    """
    v = View(view)
    side = side or _near_side(v, pose)
    if facing is None:
        d = pose.xy[LM["l_foot"], 0] - pose.xy[LM["l_heel"], 0]
        facing = 1 if d >= 0 else -1
    box = crop or crop_box([pose])
    aspect = (box[2] - box[0]) / (box[3] - box[1])
    out_w = int(round(out_height * aspect))
    photo = _photo(image, box, out_w, out_height)
    cv = Canvas(out_w, out_height, box)
    draw_skeleton(cv, pose, v, emphasis=side)
    chips: list[tuple[str, tuple[int, int, int]]] = []
    fl = list(findings)
    colorf = list(all_findings) if all_findings is not None else fl
    if v in (View.SIDE_LEFT, View.SIDE_RIGHT):
        _sagittal_annotations(cv, pose, kind, side, facing, step, fl, chips, colorf)
    else:
        _frontal_annotations(cv, pose, v, kind, side, step, fl, chips, colorf)
    img = cv.compose(photo)
    if legend:
        _legend(img, v)
    if title is None:
        title = t("pose.kf.side_label", phase=t(f"pose.kf.{kind}"), side=t(f"pose.side.{side.value}"))
    img = _caption_band(img, title, subtitle, chips)
    return cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def render_sequence(items: Sequence[tuple[np.ndarray, Pose, str]], view: View | str, *,
                    side: Side | None = None, panel_height: int = 900, title: str | None = None,
                    crop: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """Phases side by side (e.g. contact, midstance, toe-off, flight) with one shared crop. BGR."""
    v = View(view)
    if not items:
        raise ValueError("no frames")
    box = crop or crop_box([p for _, p, _ in items], aspect=0.62)
    aspect = (box[2] - box[0]) / (box[3] - box[1])
    pw = int(round(panel_height * aspect))
    u = panel_height / 1000.0
    gap = int(14 * u)
    header = int(124 * u)
    W = len(items) * pw + (len(items) + 1) * gap
    H = header + panel_height + gap
    sheet = Image.new("RGBA", (W, H), INK + (255,))
    d = ImageDraw.Draw(sheet)
    side = side or _near_side(v, items[0][1])
    head = title or t("pose.kf.sequence")
    # Text sizes are chosen for print: the strip spans the text width of an A4 page (about 175 mm),
    # where 46 units is roughly 9 pt.
    fh = font(int(54 * u), True)
    d.text((gap + 8 * u, header / 2), head, font=fh, fill=TEXT + (255,), anchor="lm")
    sub = t(f"pose.side_title.{side.value}")
    hw = d.textlength(head, font=fh)
    d.text((gap + 8 * u + hw + 26 * u, header / 2 + 4 * u), sub, font=font(int(38 * u)), fill=MUTED + (255,),
           anchor="lm")
    f = font(int(46 * u), True)
    for i, (img, pose, label) in enumerate(items):
        photo = _photo(img, box, pw, panel_height)
        cv = Canvas(pw, panel_height, box)
        draw_skeleton(cv, pose, v, emphasis=side)
        panel = cv.compose(photo)
        pd = ImageDraw.Draw(panel)
        text = f"{i + 1}  {label}"
        tw = pd.textlength(text, font=f)
        while tw > pw - 80 * u and f.size > 20:   # long labels in a narrow panel
            f = font(f.size - 2, True)
            tw = pd.textlength(text, font=f)
        ov = Image.new("RGBA", panel.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        od.rounded_rectangle([18 * u, 18 * u, 18 * u + tw + 56 * u, 98 * u], radius=40 * u, fill=INK + (225,))
        panel.alpha_composite(ov)
        pd = ImageDraw.Draw(panel)
        pd.text((46 * u, 58 * u), f"{i + 1}", font=f, fill=SIDE_COLOR[side] + (255,), anchor="lm")
        pd.text((46 * u + tw - pd.textlength(label, font=f), 58 * u), label, font=f, fill=TEXT + (255,), anchor="lm")
        mask = Image.new("L", panel.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw - 1, panel_height - 1], radius=int(18 * u), fill=255)
        x = gap + i * (pw + gap)
        sheet.paste(panel, (x, header), mask)
    return cv2.cvtColor(np.asarray(sheet.convert("RGB")), cv2.COLOR_RGB2BGR)


# --- live overlay (TV) ----------------------------------------------------------------------------------

_BGR = {k: (c[2], c[1], c[0]) for k, c in
        dict(accent=ACCENT, accent2=ACCENT2, text=TEXT, ink=INK, good=GOOD, watch=WATCH, fix=FIX, muted=MUTED).items()}


@lru_cache(maxsize=512)
def _text_patch(text: str, px: int, rgb: tuple[int, int, int], bg_alpha: int, accent: tuple[int, int, int] | None
                ) -> tuple[np.ndarray, np.ndarray]:
    """A rendered label as (BGR, alpha 0..1) arrays, cached."""
    f = font(px, True)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tb = tmp.textbbox((0, 0), text, font=f)
    padx, pady = int(px * 0.45), int(px * 0.28)
    bar = int(px * 0.16) if accent else 0
    w, h = tb[2] - tb[0] + 2 * padx + bar, tb[3] - tb[1] + 2 * pady
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if bg_alpha:
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(h * 0.3), fill=INK + (bg_alpha,))
    if accent:
        d.rounded_rectangle([0, 0, bar + int(h * 0.3), h - 1], radius=int(h * 0.3), fill=accent + (255,))
        d.rectangle([bar, 0, bar + int(h * 0.3), h - 1], fill=INK + (bg_alpha,))
    d.text((padx + bar - tb[0], pady - tb[1]), text, font=f, fill=rgb + (255,))
    arr = np.asarray(im)
    return np.ascontiguousarray(arr[..., 2::-1]), arr[..., 3:4].astype(np.float32) / 255.0


def _blit(dst: np.ndarray, patch: tuple[np.ndarray, np.ndarray], x: int, y: int) -> None:
    bgr, a = patch
    h, w = bgr.shape[:2]
    H, W = dst.shape[:2]
    x, y = int(min(max(x, 0), W - w)), int(min(max(y, 0), H - h))
    if w > W or h > H:
        return
    roi = dst[y:y + h, x:x + w]
    roi[:] = (roi * (1 - a) + bgr * a).astype(np.uint8)


def live_angles(pose: Pose, view: View | str) -> dict[str, float]:
    """Angles shown on the TV for one frame: knee flexion (near leg), trunk lean, shin, pelvis."""
    v = View(view)
    out: dict[str, float] = {}
    xy = pose.xy
    if v in (View.SIDE_LEFT, View.SIDE_RIGHT):
        pre = "l_" if v == View.SIDE_LEFT else "r_"
        d = xy[LM[pre + "foot"], 0] - xy[LM[pre + "heel"], 0]
        facing = 1 if d >= 0 else -1
        out["knee"] = 180 - _lm_angle(pose, pre + "hip", pre + "knee", pre + "ankle")
        hc = (xy[LM["l_hip"]] + xy[LM["r_hip"]]) / 2
        sc = (xy[LM["l_shoulder"]] + xy[LM["r_shoulder"]]) / 2
        out["trunk"] = float(math.degrees(math.atan2(facing * (sc[0] - hc[0]), hc[1] - sc[1])))
        out["shin"] = float(angle_from_vertical(xy[LM[pre + "knee"]], xy[LM[pre + "ankle"]], facing))
    else:
        m = -1 if v == View.FRONT else 1
        lh, rh = xy[LM["l_hip"]], xy[LM["r_hip"]]
        out["pelvis"] = float(math.degrees(math.atan2(rh[1] - lh[1], m * (rh[0] - lh[0]))))
        out["knee_l"] = 180 - _lm_angle(pose, "l_hip", "l_knee", "l_ankle")
        out["knee_r"] = 180 - _lm_angle(pose, "r_hip", "r_knee", "r_ankle")
    return {k: v for k, v in out.items() if np.isfinite(v)}


_LIVE_SEGMENTS = [("shoulder", "elbow"), ("elbow", "wrist"), ("hip", "knee"), ("knee", "ankle"),
                  ("ankle", "heel"), ("heel", "foot"), ("ankle", "foot")]


def draw_live_overlay(image: np.ndarray, pose: Pose | None, view: View | str,
                      summary: GaitSummary | None = None, copy: bool = True, labels: bool = True) -> np.ndarray:
    """Skeleton and big live angles on a camera frame (BGR). Fast enough for 60 fps at 1080p.

    ``labels`` False: the skeleton only. The angles are a runner's (knee, trunk, pelvic drop) and say
    nothing true about a cyclist or a skier (bike fit and double poling)."""
    out = image.copy() if copy else image
    if pose is None:
        return out
    v = View(view)
    H = out.shape[0]
    s = H / 1080.0
    lw = max(2, int(round(7 * s)))
    side_view = v in (View.SIDE_LEFT, View.SIDE_RIGHT)
    near = Side.LEFT if v != View.SIDE_RIGHT else Side.RIGHT
    sides = ([Side.RIGHT, Side.LEFT] if near == Side.LEFT else [Side.LEFT, Side.RIGHT]) if side_view \
        else [Side.LEFT, Side.RIGHT]
    xy = pose.xy

    def pt(i: int) -> tuple[int, int] | None:
        q = xy[i]
        if np.isnan(q).any() or pose.visibility[i] < 0.15:
            return None
        return int(round(q[0] * 16)), int(round(q[1] * 16))   # 4-bit sub-pixel precision

    for side in sides:
        far = side_view and side != near
        col = _BGR["accent"] if side == Side.LEFT else _BGR["accent2"]
        if far:
            col = tuple(int(c * 0.6) for c in col)
        pre = "l_" if side == Side.LEFT else "r_"
        for a, b in _LIVE_SEGMENTS:
            pa, pb = pt(LM[pre + a]), pt(LM[pre + b])
            if pa and pb:
                if not far:
                    cv2.line(out, pa, pb, _BGR["ink"], lw + max(2, int(4 * s)), cv2.LINE_AA, shift=4)
                cv2.line(out, pa, pb, col, lw if not far else max(2, int(lw * 0.7)), cv2.LINE_AA, shift=4)
    pts = [pt(LM[k]) for k in ("l_shoulder", "r_shoulder", "l_hip", "r_hip")]
    if all(pts):
        sc = ((pts[0][0] + pts[1][0]) // 2, (pts[0][1] + pts[1][1]) // 2)
        hc = ((pts[2][0] + pts[3][0]) // 2, (pts[2][1] + pts[3][1]) // 2)
        if not side_view:
            cv2.line(out, pts[0], pts[1], _BGR["text"], lw, cv2.LINE_AA, shift=4)
            cv2.line(out, pts[2], pts[3], _BGR["text"], lw, cv2.LINE_AA, shift=4)
        cv2.line(out, hc, sc, _BGR["ink"], lw + max(2, int(4 * s)), cv2.LINE_AA, shift=4)
        cv2.line(out, hc, sc, _BGR["text"], lw, cv2.LINE_AA, shift=4)
    r = max(3, int(round(6 * s)))
    for side in sides:
        pre = "l_" if side == Side.LEFT else "r_"
        for j in ("shoulder", "elbow", "wrist", "hip", "knee", "ankle"):
            q = pt(LM[pre + j])
            if q:
                cv2.circle(out, q, (r + 2) * 16, _BGR["ink"], -1, cv2.LINE_AA, shift=4)
                cv2.circle(out, q, r * 16, (255, 255, 255), -1, cv2.LINE_AA, shift=4)
    if not labels:
        return out
    # big angle labels next to the joints
    ang = live_angles(pose, v)
    px = max(16, int(round(46 * s)))
    if side_view:
        pre = "l_" if near == Side.LEFT else "r_"
        k = xy[LM[pre + "knee"]]
        hc_ = (xy[LM["l_hip"]] + xy[LM["r_hip"]]) / 2
        sc_ = (xy[LM["l_shoulder"]] + xy[LM["r_shoulder"]]) / 2
        facing = 1 if xy[LM[pre + "foot"], 0] >= xy[LM[pre + "heel"], 0] else -1
        if "knee" in ang and not np.isnan(k).any():
            p = _text_patch(f"{t('pose.live.knee')} {ang['knee']:.0f}°", px, TEXT, 215, ACCENT)
            _blit(out, p, int(k[0] - facing * 30 * s - (p[0].shape[1] if facing > 0 else 0)), int(k[1] - p[0].shape[0] / 2))
        if "trunk" in ang and not np.isnan(sc_).any():
            p = _text_patch(f"{t('pose.live.trunk')} {ang['trunk']:.0f}°", px, TEXT, 215, ACCENT)
            mid = (hc_ + sc_) / 2
            _blit(out, p, int(mid[0] - facing * 60 * s - (p[0].shape[1] if facing > 0 else 0)), int(mid[1]))
    else:
        lh, rh = xy[LM["l_hip"]], xy[LM["r_hip"]]
        if "pelvis" in ang and not (np.isnan(lh).any() or np.isnan(rh).any()):
            p = _text_patch(f"{t('pose.live.pelvis')} {ang['pelvis']:+.0f}°".replace("+", "±" if round(ang['pelvis']) == 0 else "+"),
                            px, TEXT, 215, ACCENT)
            x = int(max(lh[0], rh[0]) + 40 * s)
            _blit(out, p, x, int((lh[1] + rh[1]) / 2 - p[0].shape[0] / 2))
    # summary strip (cadence and contact time) in the top-left corner
    if summary is not None:
        x, y = int(24 * s), int(24 * s)
        for label, value in ((t("pose.live.cadence"), summary.cadence_spm),
                             (t("pose.live.gct"), summary.ground_contact_ms)):
            if value is None:
                continue
            unit = t("pose.unit.spm") if label == t("pose.live.cadence") else "ms"
            p = _text_patch(f"{label}  {value:.0f} {unit}", max(16, int(round(40 * s))), TEXT, 200, ACCENT)
            _blit(out, p, x, y)
            y += p[0].shape[0] + int(10 * s)
    return out


def save_png(image: np.ndarray, path: str | Path) -> str:
    """Write a BGR image as PNG (works with non-ASCII paths on Windows)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise OSError(f"could not encode {path.name}")
    path.write_bytes(buf.tobytes())
    return str(path)
