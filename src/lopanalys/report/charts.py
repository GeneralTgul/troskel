"""Report charts: matplotlib figures rendered to inline SVG strings, styled to the print identity.

No pyplot and no global backend: each chart builds its own Figure with the SVG canvas, so this is
safe to call from the Qt app. Text stays real text (svg.fonttype="none") and uses the report's
bundled font through CSS, so charts and body text share one typeface. Labels sit on the marks
instead of in legend boxes; numbers use the locale's decimal sign via core.i18n.
"""
from __future__ import annotations

import io
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from matplotlib import font_manager, rc_context
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from lopanalys.core.i18n import language, t

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_FAMILY = "Source Sans Pro"

# Print identity tokens (brief: print/light variant).
INK = "#17202A"
MUTED = "#5F6B78"       # muted text on paper (darker than the screen muted for print contrast)
RULE = "#D7DEE5"
GRID = "#E8EDF1"
PAPER = "#FFFFFF"
ACCENT = "#22A699"
ACCENT_2 = "#3C82F6"
GOOD = "#3FB950"
WATCH = "#F2A93B"
FIX = "#E5484D"
PREVIOUS = "#9AA5B1"

MM = 1 / 25.4  # inches per mm


@lru_cache(maxsize=1)
def _register_fonts() -> str:
    """Make the bundled font known to matplotlib (for text metrics). Returns the family name."""
    found = False
    for f in sorted(FONT_DIR.glob("*.ttf")):
        try:
            font_manager.fontManager.addfont(str(f))
            found = True
        except Exception:  # a broken font file must not break the report
            continue
    return FONT_FAMILY if found else "DejaVu Sans"


def _rc(base_size: float = 8.0) -> dict:
    family = _register_fonts()
    return {
        "svg.fonttype": "none",
        "svg.hashsalt": "lopanalys",  # deterministic ids
        "font.family": family,
        "font.size": base_size,
        "axes.edgecolor": RULE,
        "axes.linewidth": 0.6,
        "axes.labelcolor": MUTED,
        "axes.labelsize": base_size,
        "axes.titlesize": base_size + 1,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelcolor": MUTED,
        "ytick.labelcolor": MUTED,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "xtick.major.pad": 4,
        "ytick.major.pad": 4,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "lines.solid_capstyle": "round",
        "lines.solid_joinstyle": "round",
        "figure.facecolor": "none",
        "axes.facecolor": "none",
        "savefig.facecolor": "none",
    }


def fmt_num(value: float | None, decimals: int = 1) -> str:
    """Number with the current language's decimal sign and a real minus. None -> en dash."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    s = f"{value:.{decimals}f}"
    if language() == "sv":
        s = s.replace(".", ",")
    return s.replace("-", "−")


def _axis_fmt(decimals: int) -> FuncFormatter:
    return FuncFormatter(lambda v, _pos: fmt_num(v, decimals))


def _to_svg(fig: Figure, title: str) -> str:
    buf = io.StringIO()
    FigureCanvasSVG(fig)
    fig.savefig(buf, format="svg", transparent=True)
    svg = buf.getvalue()
    svg = svg[svg.index("<svg"):]  # drop the XML prolog and DOCTYPE: the SVG is inlined in HTML
    # Matplotlib writes font: ... 'Source Sans Pro'; let CSS supply the whole stack instead.
    svg = re.sub(r"font: (\d+) ([\d.]+)px '[^']*'",
                 lambda m: f"font-weight: {m.group(1)}; font-size: {m.group(2)}px", svg)
    svg = re.sub(r"font-family: '[^']*';? ?", "", svg)
    svg = svg.replace("<svg ", f'<svg class="chart" role="img" aria-label="{escape(title)}" ', 1)
    return svg


def _style_axes(ax, *, left_spine: bool = True) -> None:
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(left_spine)
    ax.spines["bottom"].set_color(RULE)


@dataclass
class ZoneBand:
    """A shaded training zone along the speed axis."""
    label: str
    speed_low: float | None
    speed_high: float | None
    color: str | None = None   # the zone colour (Garmin); None = a tint of the accent


@dataclass
class Threshold:
    label: str            # "LT1"
    speed_kmh: float
    lactate: float | None
    hr: float | None


def lactate_chart(
    speeds: Sequence[float],
    lactate: Sequence[float | None],
    hr: Sequence[float | None],
    *,
    curve: Sequence[tuple[float, float]] | None = None,
    thresholds: Sequence[Threshold] = (),
    zones: Sequence[ZoneBand] = (),
    previous: Sequence[tuple[float, float]] | None = None,
    previous_label: str = "",
    accent: str = ACCENT,
    width_mm: float = 178,
    height_mm: float = 84,
    load_unit: str = "km/h",
    axis_label: str | None = None,
    title: str | None = None,
    load_decimals: int = 1,
) -> str:
    """Lactate (left axis) and heart rate (right axis) against the load (speed for running), with
    LT markers and zones. ``load_unit``: the unit of the test's sport, for the threshold labels.

    Lactate tests are read this way by convention, so the two scales share one chart; each line
    carries its name and unit at its right end so the reader never has to match colours.
    """
    with rc_context(_rc()):
        fig = Figure(figsize=(width_mm * MM, height_mm * MM))
        # Margins in mm, so a smaller chart (the cover's) keeps room for the axis labels.
        ax = fig.add_axes((0.075, 11 / height_mm, 0.80, 1 - 21 / height_mm))
        ax2 = ax.twinx()
        _style_axes(ax)
        ax2.spines["right"].set_visible(False)
        ax2.spines["left"].set_visible(False)
        ax2.spines["bottom"].set_visible(False)

        xs = [s for s in speeds]
        lac_pts = [(x, y) for x, y in zip(xs, lactate) if y is not None]
        hr_pts = [(x, y) for x, y in zip(xs, hr) if y is not None]
        allx = xs + [p[0] for p in (curve or [])] + [p[0] for p in (previous or [])]
        x_lo = (min(allx) - 0.6) if allx else 6
        x_hi = (max(allx) + 0.6) if allx else 20
        ax.set_xlim(x_lo, x_hi)

        lac_max = max([y for _, y in lac_pts] + [y for _, y in (curve or [])] +
                      [y for _, y in (previous or [])] + [4.0])
        ax.set_ylim(0, math.ceil(lac_max * 1.18 + 0.2))
        if hr_pts:
            h = [y for _, y in hr_pts]
            lo = max(60, 10 * math.floor((min(h) - 25) / 10))
            hi = 10 * math.ceil((max(h) + 8) / 10)
            ax2.set_ylim(lo, hi)

        # Zones: very light, ordered tints of the accent, labelled at the top of the band.
        n = max(1, len(zones))
        for i, z in enumerate(zones):
            zlo = x_lo if z.speed_low is None else max(x_lo, z.speed_low)
            zhi = x_hi if z.speed_high is None else min(x_hi, z.speed_high)
            if zhi <= zlo:
                continue
            ax.axvspan(zlo, zhi, color=z.color or accent,
                       alpha=0.16 if z.color else 0.035 + 0.075 * i / max(1, n - 1),
                       linewidth=0, zorder=0)
            ax.text((zlo + zhi) / 2, 1.015, z.label, transform=ax.get_xaxis_transform(),
                    ha="center", va="bottom", fontsize=7, color=MUTED)

        # Previous test: muted dashed line, named at its end.
        if previous:
            px, py = zip(*sorted(previous))
            ax.plot(px, py, color=PREVIOUS, linewidth=1.2, linestyle=(0, (3, 2)), zorder=2)
            if previous_label:
                ax.annotate(previous_label, (px[-1], py[-1]), xytext=(4, -2), textcoords="offset points",
                            fontsize=7, color=MUTED, va="center")

        # Heart rate: thinner, blue.
        if hr_pts:
            hx, hy = zip(*hr_pts)
            ax2.plot(hx, hy, color=ACCENT_2, linewidth=1.4, zorder=3)
            ax2.scatter(hx, hy, s=16, color=ACCENT_2, edgecolors=PAPER, linewidths=1.0, zorder=4)
            ax2.annotate(t("report.chart.hr_line"), (hx[-1], hy[-1]), xytext=(7, 0),
                         textcoords="offset points", fontsize=7.5, color=INK, va="center")

        # Lactate: the main series.
        if lac_pts:
            lx, ly = zip(*lac_pts)
            if curve and len(curve) > 2:
                cx, cy = zip(*sorted(curve))
                ax.plot(cx, cy, color=accent, linewidth=2.0, zorder=5)
            else:
                ax.plot(lx, ly, color=accent, linewidth=2.0, zorder=5)
            ax.scatter(lx, ly, s=26, color=accent, edgecolors=PAPER, linewidths=1.2, zorder=6)
            ax.annotate(t("report.chart.lactate_line"), (lx[-1], ly[-1]), xytext=(7, 0),
                        textcoords="offset points", fontsize=7.5, color=INK, va="center",
                        fontweight="semibold")

        # Thresholds: thin ink rule with a ringed dot on the curve and a label at the top.
        y_top = ax.get_ylim()[1]
        for th in thresholds:
            ax.plot([th.speed_kmh, th.speed_kmh], [0, y_top * 0.86], color=INK, linewidth=0.8,
                    linestyle=(0, (2, 2)), zorder=4)
            if th.lactate is not None:
                ax.scatter([th.speed_kmh], [th.lactate], s=46, color=INK, edgecolors=PAPER,
                           linewidths=1.5, zorder=7)
            txt = f"{th.label}  {fmt_num(th.speed_kmh, load_decimals)} {load_unit}"
            ax.text(th.speed_kmh, y_top * 0.88, txt, ha="center", va="bottom", fontsize=7.5,
                    color=INK, fontweight="semibold", zorder=8,
                    bbox=dict(boxstyle="round,pad=0.25", fc=PAPER, ec="none"))

        ax.set_xlabel(axis_label or t("report.chart.speed_axis"))
        ax.set_ylabel(t("report.chart.lactate_axis"))
        ax2.set_ylabel(t("report.chart.hr_axis"), color=MUTED, rotation=270, labelpad=12)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=12))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 5, 10], integer=ax.get_ylim()[1] > 5))
        ax.yaxis.set_major_formatter(_axis_fmt(0 if ax.get_ylim()[1] > 5 else 1))
        ax.xaxis.set_major_formatter(_axis_fmt(0))
        ax2.yaxis.set_major_locator(MaxNLocator(nbins=6, integer=True))
        ax2.tick_params(axis="y", length=0, colors=MUTED)
        return _to_svg(fig, title or t("report.chart.lactate_title"))


def hr_chart(
    times_s: Sequence[float],
    hr: Sequence[float | None],
    *,
    peak_bpm: float | None = None,
    peak_t_s: float | None = None,
    stop_t_s: float | None = None,
    speeds: Sequence[float | None] | None = None,
    accent: str = ACCENT_2,
    width_mm: float = 178,
    height_mm: float = 80,
    strip_key: str = "report.chart.speed_strip",
) -> str:
    """Heart rate over time for the max HR test: the curve, the peak, and the recovery after stop.

    Speed, when given, is drawn as a quiet step band along the bottom with its own small labels
    (not as a second y-axis).
    """
    with rc_context(_rc()):
        fig = Figure(figsize=(width_mm * MM, height_mm * MM))
        ax = fig.add_axes((0.065, 11.2 / height_mm, 0.90, 1 - 17.6 / height_mm))
        _style_axes(ax)
        pts = [(x / 60.0, y) for x, y in zip(times_s, hr) if y is not None]
        if not pts:
            ax.text(0.5, 0.5, t("report.chart.no_data"), transform=ax.transAxes, ha="center", color=MUTED)
            return _to_svg(fig, t("report.chart.hr_title"))
        x, y = zip(*pts)
        lo = max(40, 10 * math.floor((min(y) - 15) / 10))
        hi = 10 * math.ceil(((peak_bpm or max(y)) + 12) / 10)
        ax.set_ylim(lo, hi)
        ax.set_xlim(0, max(x))

        if stop_t_s is not None:
            ax.axvspan(stop_t_s / 60, max(x), color=GRID, alpha=0.7, linewidth=0, zorder=0)
            ax.text((stop_t_s / 60 + max(x)) / 2, hi - (hi - lo) * 0.04, t("report.chart.recovery"),
                    ha="center", va="top", fontsize=7, color=MUTED)

        # Speed as a step strip at the bottom of the plot.
        if speeds:
            sp = [(xx / 60.0, s) for xx, s in zip(times_s, speeds) if s is not None]
            if sp:
                band = (hi - lo) * 0.10
                smin = min(s for _, s in sp)
                smax = max(s for _, s in sp) or 1
                scale = band / max(0.1, smax - smin + 1)
                sx, sy = zip(*sp)
                ys = [lo + (s - smin + 0.5) * scale for s in sy]
                ax.fill_between(sx, lo, ys, step="post", color=ACCENT, alpha=0.2, linewidth=0, zorder=1)
                ax.text(sx[0] + 0.1, lo + band * 1.15,
                        t(strip_key, lo=fmt_num(smin, 1 if strip_key == "report.chart.speed_strip" else 0),
                          hi=fmt_num(smax, 1 if strip_key == "report.chart.speed_strip" else 0)),
                        fontsize=6.5, color=MUTED, va="bottom")

        ax.plot(x, y, color=accent, linewidth=1.6, zorder=3)
        if peak_bpm is not None:
            pt = (peak_t_s / 60.0) if peak_t_s is not None else x[y.index(max(y))]
            ax.axhline(peak_bpm, color=INK, linewidth=0.6, linestyle=(0, (2, 2)), zorder=2)
            ax.scatter([pt], [peak_bpm], s=52, color=INK, edgecolors=PAPER, linewidths=1.6, zorder=5)
            ax.annotate(t("report.chart.peak", bpm=fmt_num(peak_bpm, 0)), (pt, peak_bpm),
                        xytext=(-8, 6), textcoords="offset points", ha="right", va="bottom",
                        fontsize=8, color=INK, fontweight="semibold")
        ax.set_xlabel(t("report.chart.time_axis"))
        ax.set_ylabel(t("report.chart.hr_axis"))
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=12))
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=7))
        ax.xaxis.set_major_formatter(_axis_fmt(0))
        return _to_svg(fig, t("report.chart.hr_title"))


def range_bar_svg(
    value: float | None,
    scale: tuple[float, float],
    good: tuple[float, float],
    watch: tuple[float, float],
    *,
    width: float = 150,
    height: float = 16,
) -> str:
    """A small horizontal range bar: red / amber / green bands and a marker at the value.

    good is the green range, watch the wider amber range around it; the rest of scale is red.
    Values outside the scale clamp to the end with an arrow. Pure SVG, no matplotlib.
    """
    lo, hi = scale

    def px(v: float) -> float:
        return 4 + (min(max(v, lo), hi) - lo) / (hi - lo) * (width - 8)

    y0, bh = height / 2 - 2.5, 5.0
    parts = [f'<svg class="rangebar" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
             f'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">']
    # Track in the full status colours, separated by 1.5px paper gaps. Pale tints (an earlier 38 %
    # opacity) all but vanished on a colour laser printer (real hardware 2026-11-14); the dots and stripes
    # on the findings page print well at full strength, so the bars use the same.
    segs = [(lo, watch[0], FIX), (watch[0], good[0], WATCH), (good[0], good[1], GOOD),
            (good[1], watch[1], WATCH), (watch[1], hi, FIX)]
    for a, b, col in segs:
        a, b = max(a, lo), min(b, hi)
        if b <= a:
            continue
        x1, x2 = px(a), px(b)
        parts.append(f'<rect x="{x1 + 0.75:.2f}" y="{y0}" width="{max(0.5, x2 - x1 - 1.5):.2f}" '
                     f'height="{bh}" rx="1.5" fill="{col}"/>')
    if value is not None and not math.isnan(value):
        x = px(value)
        out = value < lo or value > hi
        if out:
            d = -1 if value < lo else 1
            parts.append(f'<path d="M{x - 3 * d:.2f},{height / 2 - 4.5} L{x + 2 * d:.2f},{height / 2} '
                         f'L{x - 3 * d:.2f},{height / 2 + 4.5} Z" fill="{INK}"/>')
        else:
            parts.append(f'<circle cx="{x:.2f}" cy="{height / 2}" r="4.6" fill="{INK}" '
                         f'stroke="{PAPER}" stroke-width="1.6"/>')
    parts.append("</svg>")
    return "".join(parts)


def hr_speed_chart(
    points: Sequence[tuple[float, float, bool]],
    line: Sequence[tuple[float, float, bool]] = (),
    *,
    zones: Sequence[tuple[float, float, str, str]] = (),
    max_hr: float | None = None,
    accent: str = ACCENT,
    width_mm: float = 178,
    height_mm: float = 84,
) -> str:
    """Settled heart rate per step against speed, with the line that gives the zones their speed.

    points: (speed, heart rate, settled). line: (speed, heart rate, extrapolated) in speed order; a
    segment that touches an extrapolated end is dashed. zones: (hr low, hr high, colour, label) drawn
    as horizontal bands, since the zones are defined on the heart-rate axis. Labels sit on the marks.
    """
    with rc_context(_rc()):
        fig = Figure(figsize=(width_mm * MM, height_mm * MM))
        ax = fig.add_axes((0.075, 11 / height_mm, 0.86, 1 - 17 / height_mm))
        _style_axes(ax)
        if not points:
            ax.text(0.5, 0.5, t("report.chart.no_data"), transform=ax.transAxes, ha="center", color=MUTED)
            return _to_svg(fig, t("report.chart.hrspeed_title"))
        xs = [p[0] for p in points] + [p[0] for p in line]
        ys = [p[1] for p in points] + [p[1] for p in line] + ([max_hr] if max_hr else [])
        x_lo, x_hi = min(xs) - 0.4, max(xs) + 0.4
        y_lo = 10 * math.floor((min(ys) - 6) / 10)
        y_hi = 10 * math.ceil((max(ys) + 6) / 10)
        ax.set_xlim(x_lo, x_hi)
        ax.set_ylim(y_lo, y_hi)
        ax.grid(False)
        for lo, hi, color, label in zones:
            a, b = max(lo, y_lo), min(hi, y_hi)
            if b <= a:
                continue
            ax.axhspan(a, b, color=color, alpha=0.16, linewidth=0, zorder=0)
            if b - a >= (y_hi - y_lo) * 0.07:
                ax.text(1.008, (a + b) / 2, label, transform=ax.get_yaxis_transform(), ha="left", va="center",
                        fontsize=7, color=MUTED)
        for (xa, ya, ea), (xb, yb, eb) in zip(line, line[1:]):
            dashed = ea or eb
            ax.plot([xa, xb], [ya, yb], color=accent, linewidth=1.3 if dashed else 2.0,
                    linestyle=(0, (3, 2)) if dashed else "solid", zorder=4)
        if max_hr:
            ax.axhline(max_hr, color=INK, linewidth=0.6, linestyle=(0, (2, 2)), zorder=2)
            ax.text(x_lo + 0.1, max_hr - (y_hi - y_lo) * 0.015, t("report.chart.hrspeed_max", bpm=fmt_num(max_hr, 0)),
                    ha="left", va="top", fontsize=7.5, color=INK, fontweight="semibold")
        for x, y, settled in points:
            ax.scatter([x], [y], s=34, color=accent if settled else PAPER, edgecolors=PAPER if settled else WATCH,
                       linewidths=1.2 if settled else 1.6, zorder=6)
            ax.annotate(fmt_num(y, 0), (x, y), xytext=(0, 6), textcoords="offset points", ha="center", va="bottom",
                        fontsize=7.5, color=INK, fontweight="semibold", zorder=7)
        ax.set_xlabel(t("report.chart.speed_axis"))
        ax.set_ylabel(t("report.chart.hr_axis"))
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=12))
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=7))
        ax.xaxis.set_major_formatter(_axis_fmt(0))
        return _to_svg(fig, t("report.chart.hrspeed_title"))
