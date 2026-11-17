"""Builds the printed test report: data in, self-contained HTML out (Jinja2 templates).

The same HTML is shown in the app's preview and turned into the PDF by report.pdf. Images are
embedded as data URIs (down-scaled, sharp at 300 dpi), so a saved HTML file stays complete; the
bundled font is referenced by file URL and falls back to the system font stack.

Inputs from other modules arrive as plain dicts (their to_dict()), so the report keeps working if a
module adds fields. Expected keys are documented on ReportData.
"""
from __future__ import annotations

import base64
import io
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from lopanalys.assessments import catalog, vo2max, zone_systems
from lopanalys.assessments.cadence import cadence_label, with_strap_cadence
from lopanalys.core import simulated
from lopanalys.report import technique as technique_report
from lopanalys.core.i18n import language, set_language, t
from lopanalys.core.zone_colors import zone_color
from lopanalys.core.models import Athlete, Session, StepRecord, TestKind, TimeSample
from contextvars import ContextVar

from lopanalys.core.sport import Sport, sport_of
from lopanalys.core.sport import profile as sport_profile
from lopanalys.plan.expected import TOLERANCE_PCT
from lopanalys.pose import findings as pose_findings
from lopanalys.pose import reliability
from lopanalys.pose.findings import MetricRange
from lopanalys.pose.types import Finding, GaitSummary, KeyFrame, Severity

from . import charts
from .charts import fmt_num

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
ASSETS = HERE / "assets"
DEFAULT_ACCENT = charts.ACCENT
IMAGE_MAX_PX = 1600  # long side of embedded key frames: ~300 dpi at half the page width
PRINT_GAMMA = 0.65   # midtone lift of key frames for paper (for_print)


# --------------------------------------------------------------------------------------------
# Public data types
# --------------------------------------------------------------------------------------------

@dataclass
class Branding:
    """The coach's business on the report: name, contact line, logo and accent colour."""
    business_name: str = ""
    coach_name: str = ""
    contact: str = ""                 # one line: phone, e-mail, web
    logo_path: str | None = None      # PNG, JPG or SVG
    accent_color: str = DEFAULT_ACCENT


class GaitAnalysisLike(Protocol):
    """What the report needs from pose.offline.GaitAnalysis."""
    summary: GaitSummary | None
    findings: list[Finding]
    keyframes: list[KeyFrame]


@dataclass
class ReportData:
    """Everything one report shows. Only athlete and session are required.

    Sections appear for the data that is present; with more than one kind it becomes the combined
    test report.

    lactate (assessments.lactate.LactateResult.to_dict()):
        {"points": [{"speed_kmh", "lactate", "hr"}],          # measured; else taken from records
         "curve": [{"speed_kmh", "lactate"}],                 # fitted curve, optional
         "methods": {"<method name>": {"lactate", "speed_kmh", "pace", "hr", "pct_max_hr"}},
         "lt1": {"method", "lactate", "speed_kmh", "pace", "hr", "pct_max_hr"},
         "lt2": {...same...},
         "baseline": float | None}
        pace may be "4:12" or seconds per km; missing pace is computed from speed_kmh.
        The method dicts may also carry "name"; a list of such dicts is accepted for "methods".
    zones (assessments.zones Zone.to_dict() each):
        [{"name", "purpose", "hr_low", "hr_high", "speed_low", "speed_high",
          "lactate_low", "lactate_high"}]   # None = open-ended
    max_hr (assessments.max_hr.MaxHrResult.to_dict()):
        {"peak", "time" (s from start), "plateau" (bool), "recovery_1min" (bpm drop),
         "likely_true_max" (bool), "stop_time" (s, optional)}
    gait: pose.offline.GaitAnalysis, or its to_dict() (keys summary, findings, keyframes).
    samples: 1 Hz series for the max HR curve.
    zone_system: {"key", "name", "note"} of the system ``zones`` is in (assessments.zone_systems).
    zone_compare: the same thresholds in other systems, [{"name", "zones": [Zone.to_dict()]}].
    garmin: zone_systems.GarminSetup.to_dict(), the values for the athlete's watch.
    vo2max: vo2max.VO2maxResult.to_dict(), always shown as an estimate with its interval.
    hr_speed: hr_zones.HrSpeedResult.to_dict() of a zones test without lactate: the settled heart rate
        per step and the line that gives the zones their speed.
    previous: the athlete's previous test of the same kind, for the trend page.
    plan_followup: plan.expected.Followup.to_dict() of the athlete's plan between the previous test and this one,
        for the part *Mot schemat* beside the trend (#778): what the plan counted on and how much was done.
    sessions: the separate tests behind a collected report (several tests of one athlete, e.g. a
        lactate test, a max HR test and a gait film the same day); None for one test. See collect().
    highlights / focus: override the automatic "Det viktigaste" texts.
    """
    athlete: Athlete
    session: Session
    records: list[StepRecord] = field(default_factory=list)
    lactate: dict[str, Any] | None = None
    zones: list[dict[str, Any]] | None = None
    max_hr: dict[str, Any] | None = None
    gait: GaitAnalysisLike | dict[str, Any] | None = None
    previous: ReportData | None = None
    branding: Branding = field(default_factory=Branding)
    samples: list[TimeSample] = field(default_factory=list)
    coach_comment: str = ""
    highlights: list[dict[str, str]] | None = None   # [{"label", "value", "unit", "sub"}]
    focus: list[dict[str, str]] | None = None        # [{"title", "text"}]
    language: str | None = None                      # "sv" | "en"; None = app language
    zone_system: dict[str, Any] | None = None
    zone_compare: list[dict[str, Any]] | None = None
    garmin: dict[str, Any] | None = None
    vo2max: dict[str, Any] | None = None
    sessions: list[Session] | None = None
    hr_speed: dict[str, Any] | None = None
    power: dict[str, Any] | None = None              # assessments.power.PowerResult.to_dict(): FTP, best minute, W/kg
    # bike fit or SkiErg technique: {"kind", "views": [TechniqueAnalysis.to_dict()], "checklist", "previous_date"}
    technique: dict[str, Any] | None = None
    plan_followup: dict[str, Any] | None = None

    @property
    def weight_kg(self) -> float | None:
        """The body weight for W/kg: the one saved with the test, else the athlete's."""
        res = getattr(self.session, "result", None) or {}
        return _f(res.get("weight_kg")) or _f((self.power or {}).get("weight_kg")) or _f(self.athlete.weight_kg)

    @property
    def sport(self) -> Sport:
        """The sport of the test: it gives the unit of the load in the report."""
        return sport_of(getattr(self.session, "sport", None))

    @property
    def kinds(self) -> list[str]:
        """Sections in report order: 'lactate', 'hr_zones', 'max_hr', 'gait'."""
        # The sections follow the test that was run, not whichever results happen to exist: the app
        # always computes a peak heart rate, and that must not turn a lactate test into a max HR report.
        out = []
        kinds = {TestKind(s.kind) for s in self.sessions or [self.session]}
        # lactate steps followed by a max ramp
        ramp = any((s.protocol or {}).get("open_ended") for s in self.sessions or [self.session])
        # A max test without lactate values shows no lactate at all: the app
        # always computes a (empty) lactate result, and that must not put an empty lactate curve first.
        if TestKind.LACTATE_STEP in kinds or (self.lactate and self.lactate_measured):
            out.append("lactate")
        if TestKind.HR_ZONES in kinds:
            out.append("hr_zones")
        if TestKind.MAX_HR in kinds or (self.max_hr and ramp):
            out.append("max_hr")
        if TestKind.FTP in kinds:
            out.append("ftp")
        if self.gait is not None:
            out.append("gait")
        if self.technique:
            out.append(str(self.technique.get("kind")))      # "bikefit" | "ski" (pose.technique)
        if not out:
            out.append(TestKind(self.session.kind).value)
        return out

    @property
    def lactate_measured(self) -> bool:
        """True when a lactate value was entered for some stage."""
        if isinstance(self.lactate, Mapping) and self.lactate.get("points"):
            return True
        return any(getattr(r, "lactate_mmol", None) is not None for r in self.records or [])

    @property
    def kind(self) -> str:
        """'gait', 'lactate', 'max_hr', 'hr_zones' or 'combined'."""
        k = self.test_kinds
        return k[0] if len(k) == 1 else "combined"

    @property
    def test_kinds(self) -> list[str]:
        """The tests behind the report, for its title. The zones test without lactate ends in a ramp
        to max and so has a max HR section, but it is one test and is named as one."""
        k = self.kinds
        if "hr_zones" in k and not any(TestKind(s.kind) == TestKind.MAX_HR for s in self.sessions or [self.session]):
            k = [x for x in k if x != "max_hr"]
        return k


def collect(parts: list[ReportData], **override: Any) -> ReportData:
    """One report for several separate tests of one athlete (Historik → Samlad rapport).

    Each part is the report of one test as the app would print it alone. The lactate test gives the
    steps, thresholds and zones, the max HR test its curve and peak, the gait test its film; the
    cover then sums up all of them. ``override`` sets fields computed over all the tests together
    (zones from the lactate thresholds and the measured max HR, the watch values, VO2max)."""
    if not parts:
        raise ValueError("collect() needs at least one report")
    parts = sorted(parts, key=lambda d: d.session.started)
    lac = next((d for d in parts if d.lactate or TestKind(d.session.kind) == TestKind.LACTATE_STEP), None)
    hz = next((d for d in reversed(parts) if TestKind(d.session.kind) == TestKind.HR_ZONES), None)
    # the max HR test first; else the zones test without lactate, which also ends in a ramp to max
    mx = next((d for d in reversed(parts) if TestKind(d.session.kind) == TestKind.MAX_HR), None) or hz
    gt = next((d for d in reversed(parts) if d.gait is not None), None)
    base = lac or mx or gt or parts[0]
    zsrc = lac if lac is not None and lac.zones else (hz or mx or base)
    names = list(dict.fromkeys(d.session.protocol_name for d in parts if d.session.protocol_name))
    comments = [c for c in dict.fromkeys((d.coach_comment or d.session.notes or "").strip() for d in parts) if c]
    session = replace(base.session, protocol_name=" · ".join(names),
                      camera_source=gt.session.camera_source if gt else "", notes="")
    data = replace(
        base, session=session, records=(lac or mx or base).records,
        lactate=lac.lactate if lac else None, max_hr=mx.max_hr if mx else None,
        samples=mx.samples if mx else [], gait=gt.gait if gt else None, previous=None,
        hr_speed=hz.hr_speed if hz else None,
        power=next((d.power for d in reversed(parts) if d.power and d.power.get("ftp_w")), None),
        zones=zsrc.zones, zone_system=zsrc.zone_system, zone_compare=zsrc.zone_compare, garmin=zsrc.garmin,
        vo2max=(mx.vo2max if mx and mx.vo2max else zsrc.vo2max), coach_comment="\n\n".join(comments),
        highlights=None, focus=None, sessions=[d.session for d in parts])
    return replace(data, **override)


# --------------------------------------------------------------------------------------------
# The gait metrics table: which metrics, in which order, and how they are drawn. The reference
# ranges themselves come from pose/findings.py (MetricRange), the single source for the findings,
# this table and the live tiles.
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricSpec:
    key: str
    unit: str
    decimals: int
    scale: tuple[float, float]   # default extent of the range bar; widened to hold the limits
    better: str = "range"        # "higher" | "lower" | "range": what counts as improvement in trends


GAIT_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("cadence_spm", "spm", 0, (140, 200), "range"),
    MetricSpec("ground_contact_ms", "ms", 0, (150, 340), "lower"),
    MetricSpec("vertical_oscillation_cm", "cm", 1, (4, 14), "lower"),
    MetricSpec("overstride_cm", "cm", 0, (0, 45), "lower"),
    MetricSpec("shin_angle_deg", "deg", 0, (-5, 20), "lower"),
    MetricSpec("knee_flex_contact_deg", "deg", 0, (0, 30), "range"),
    MetricSpec("knee_flex_midstance_deg", "deg", 0, (15, 70), "range"),
    MetricSpec("trunk_lean_deg", "deg", 0, (-5, 25), "range"),
    MetricSpec("hip_extension_toe_off_deg", "deg", 0, (-5, 25), "range"),
    MetricSpec("pelvic_drop_deg", "deg", 1, (0, 12), "lower"),
    MetricSpec("knee_valgus_deg", "deg", 0, (0, 22), "lower"),
    MetricSpec("step_width_cm", "cm", 0, (-6, 16), "range"),
)


# --------------------------------------------------------------------------------------------
# Formatting helpers (also Jinja filters)
# --------------------------------------------------------------------------------------------

def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


# The sport and the body weight of the report being rendered. They decide the unit and the decimals
# of every load, what the column beside the load holds (pace per km, pace per 500 m, or W/kg) and
# which wording a text gets. Set by _render(); running is the default, and running's output is
# exactly what it was before sport existed.
_SPORT: ContextVar[Sport] = ContextVar("report_sport", default=Sport.RUN)
_WEIGHT: ContextVar[float | None] = ContextVar("report_weight", default=None)


def ts(key: str, **kw: Any) -> str:
    """A text in the words of the report's sport: ``<key>.<sport>`` when the sport has its own
    (cycling, SkiErg), else ``key`` (running)."""
    sp = _SPORT.get()
    if sp is not Sport.RUN:
        own = f"{key}.{sp.value}"
        text = t(own, **kw)
        if text != own:
            return text
    return t(key, **kw)


def _dec() -> int:
    """Decimals of a load in the report's sport (12,5 km/h, 250 W)."""
    return sport_profile(_SPORT.get()).load_decimals


def fmt_load(v: float | None) -> str:
    return fmt_num(v, _dec())


def _w_per_kg(watt: float | None) -> float | None:
    kg = _WEIGHT.get()
    return watt / kg if watt and kg else None


def pace_str(speed_kmh: float | None) -> str:
    """What stands beside a load: km/h -> 'm:ss' per km for running, watt -> 'm:ss' per 500 m on a
    SkiErg, and watt -> W/kg on a bike (an en dash without the body weight)."""
    v = _f(speed_kmh)
    if not v or v <= 0:
        return "–"
    sp = _SPORT.get()
    if sp is Sport.BIKE:
        return fmt_num(_w_per_kg(v), 2)
    if sp is not Sport.RUN:
        return sport_profile(sp).pace_str(v) or "–"
    sec = round(3600 / v)
    return f"{sec // 60}:{sec % 60:02d}"


def _pace_of(d: Mapping[str, Any]) -> str:
    if _SPORT.get() is not Sport.RUN:
        return pace_str(d.get("speed_kmh"))
    p = d.get("pace")
    if isinstance(p, str) and p:
        return p
    if _f(p):
        s = round(_f(p))  # seconds per km
        return f"{s // 60}:{s % 60:02d}"
    return pace_str(d.get("speed_kmh"))


_UNIT_KEYS = frozenset({"spm", "ms", "cm", "deg", "bpm", "mmol", "kmh", "%"})


def unit_label(unit: str) -> str:
    """Display text for a unit: the report's own unit codes are translated; anything else (pose
    findings carry localised display units such as "°" or "steg/min") is shown as it is."""
    return t(f"report.unit.{unit}") if unit in _UNIT_KEYS else (unit or "")


def date_long(d: date | datetime | None) -> str:
    if d is None:
        return "–"
    return t("report.date_long", day=d.day, month=t(f"report.month.{d.month}"), year=d.year)


def date_short(d: date | datetime | None) -> str:
    return "–" if d is None else (d.strftime("%Y-%m-%d"))


def _range_str(lo: Any, hi: Any, decimals: int, fmt=None, lo_mark: str = "", hi_mark: str = "") -> str:
    """"8,1–9,6", "under 9,6", "över 8,1" or an en dash; the marks go before the numbers."""
    fmt = fmt or (lambda v: fmt_num(v, decimals))
    lo, hi = _f(lo), _f(hi)
    if lo is None and hi is None:
        return "–"
    if lo is None:
        return t("report.range.below", v=hi_mark + fmt(hi))
    if hi is None:
        return t("report.range.above", v=lo_mark + fmt(lo))
    sep = " – " if lo < 0 or hi < 0 else "–"
    return f"{lo_mark}{fmt(lo)}{sep}{hi_mark}{fmt(hi)}"


def with_unit(value: str, unit: str) -> str:
    """'6,1' + 'deg' -> '6,1°'; '13' + 'cm' -> '13 cm' (no-break space)."""
    label = unit_label(unit)
    if not label:
        return value
    return value + label if label.startswith("°") else value + "\u00a0" + label


def _pace_range(lo_speed: Any, hi_speed: Any, lo_mark: str = "", hi_mark: str = "") -> str:
    # Faster speed = lower pace, so the pace range runs from the high speed to the low one.
    lo, hi = _f(lo_speed), _f(hi_speed)
    if lo is None and hi is None:
        return "–"
    if _SPORT.get() is Sport.BIKE:      # W/kg rises with the watt: low to high, like the load itself
        return _range_str(_w_per_kg(lo), _w_per_kg(hi), 2, lo_mark=lo_mark, hi_mark=hi_mark)
    if hi is None:
        return t("report.range.faster", v=lo_mark + pace_str(lo))
    if lo is None:
        return t("report.range.slower", v=hi_mark + pace_str(hi))
    return f"{hi_mark}{pace_str(hi)}–{lo_mark}{pace_str(lo)}"


def _age(birth: date | None, on: date) -> int | None:
    if not birth:
        return None
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def _data_uri_image(path: str | None, max_px: int = IMAGE_MAX_PX) -> str | None:
    """Down-scale an image to max_px on the long side and return a data URI (None if unreadable)."""
    return _image(path, max_px)[0]


def for_print(im: Any) -> Any:
    """A key frame lightened for paper: contrast stretch on the brightness, then a lighter midtone.

    The stills are drawn darkened (so the skeleton stands out on screen) from a gym video that is
    often dim already, and a colour laser printer darkens midtones further: on paper they came out
    nearly black (real hardware 2026-11-14). The stretch maps the 0.5-99.5 % brightness percentiles to the
    full range (at most 2.5 x), the same factor for all three channels so the colours of the
    drawing stay; gamma 0.65 lifts the midtones. Only the copy in the report changes; the PNG on disk
    stays as drawn.
    """
    import numpy as np
    from PIL import Image

    a = np.asarray(im.convert("RGB"), np.float32) / 255.0
    lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
    lo, hi = (float(x) for x in np.percentile(lum, [0.5, 99.5]))
    if hi - lo < 0.4:
        lo = max(0.0, hi - 0.4)             # an almost flat image: no more than 2.5 x
    a = np.clip((a - lo) / max(hi - lo, 1e-3), 0.0, 1.0) ** PRINT_GAMMA
    return Image.fromarray((a * 255.0 + 0.5).astype(np.uint8), "RGB")


def _image(path: str | None, max_px: int = IMAGE_MAX_PX, *, printable: bool = False) -> tuple[str | None, float | None]:
    """(data URI, width / height) of an image down-scaled to max_px; (None, None) if unreadable.

    ``printable``: lighten a photo for paper (``for_print``); for key frames, not logos."""
    if not path:
        return None, None
    p = Path(path)
    if not p.is_file():
        return None, None
    if p.suffix.lower() == ".svg":
        return "data:image/svg+xml;base64," + base64.b64encode(p.read_bytes()).decode(), None
    try:
        from PIL import Image

        with Image.open(p) as im:
            im.load()
            aspect = im.size[0] / max(1, im.size[1])
            has_alpha = im.mode in ("RGBA", "LA", "P")
            if max(im.size) > max_px:
                im.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            if has_alpha:  # logos: keep transparency
                im.convert("RGBA").save(buf, "PNG", optimize=True)
                mime = "png"
            else:
                (for_print(im) if printable else im.convert("RGB")).save(buf, "JPEG", quality=90, subsampling=0)
                mime = "jpeg"
    except Exception:
        return None, None
    return f"data:image/{mime};base64," + base64.b64encode(buf.getvalue()).decode(), aspect


# --------------------------------------------------------------------------------------------
# Normalising inputs
# --------------------------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_val(v: Any) -> Any:
    return getattr(v, "value", v)


@dataclass
class _GaitView:
    summary: dict[str, Any]
    findings: list[dict[str, Any]]
    keyframes: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    ranges: dict[str, MetricRange] = field(default_factory=dict)
    view: str | None = None
    height_cm: float | None = None
    speed_kmh: float | None = None
    # metric -> "uncertain" | "hidden" at the film's frame rate (pose.reliability)
    reliability: dict[str, str] = field(default_factory=dict)
    fps: float | None = None


def _gait_view(gait: Any, height_cm: float | None = None) -> _GaitView | None:
    if gait is None:
        return None
    summary = _get(gait, "summary") or {}
    if not isinstance(summary, Mapping):
        summary = summary.to_dict() if hasattr(summary, "to_dict") else vars(summary)
    findings = []
    for f in _get(gait, "findings") or []:
        findings.append({
            "code": _get(f, "code", ""),
            "severity": str(_enum_val(_get(f, "severity", Severity.WATCH))),
            "title": _get(f, "title", ""),
            "explanation": _get(f, "explanation", ""),
            "cue": _get(f, "cue", ""),
            "drill": _get(f, "drill", "") or "",
            "value": _f(_get(f, "value")),
            "unit": _get(f, "unit", "") or "",
            "reference": _get(f, "reference", "") or "",
            "frame_index": _get(f, "frame_index"),
            "uncertain": bool(_get(f, "uncertain", False)),
        })
    keyframes = []
    for k in _get(gait, "keyframes") or []:
        keyframes.append({
            "label": _get(k, "label", ""),
            "frame_index": _get(k, "frame_index"),
            "t": _f(_get(k, "t")),
            "image_path": _get(k, "image_path"),
            "findings": list(_get(k, "findings") or []),
            "caption": _get(k, "caption", "") or "",
            "kind": _get(k, "kind", "") or "",
        })
    summary = dict(summary)
    view = _get(gait, "view") or summary.get("view")
    height = _f(_get(gait, "height_cm")) or _f(height_cm)
    speed = _f(_get(gait, "speed_kmh")) or _f(summary.get("speed_kmh"))
    fps = _f(_get(gait, "fps"))
    # analyses stored before the frame-rate levels have none: they follow from the frame rate they were made at
    rel = dict(_get(gait, "reliability") or {}) or reliability.levels(fps)
    return _GaitView(summary, findings, keyframes, [str(w) for w in _get(gait, "warnings") or []],
                     _ranges(_get(gait, "ranges"), summary, view, height, speed), view, height, speed,
                     rel, fps)


def _reference_ranges(view: Any, height_cm: float | None, speed_kmh: float | None) -> dict[str, MetricRange]:
    try:
        return pose_findings.reference_ranges(view or None, height_cm, speed_kmh)
    except ValueError:   # a view this version does not know
        return pose_findings.reference_ranges(None, height_cm, speed_kmh)


def _ranges(stored: Any, summary: Mapping[str, Any], view: Any, height_cm: float | None,
            speed_kmh: float | None) -> dict[str, MetricRange]:
    """The ranges the analysis was judged with (GaitAnalysis.ranges). Analyses stored before pose
    saved them get the current ranges for their view, height and speed, judged on the summary."""
    out: dict[str, MetricRange] = {}
    if isinstance(stored, Mapping) and stored:
        for key, r in stored.items():
            try:
                out[str(key)] = r if isinstance(r, MetricRange) else MetricRange.from_dict(dict(r))
            except (TypeError, ValueError):
                continue
        if out:
            return out
    for key, r in _reference_ranges(view, height_cm, speed_kmh).items():
        v = _f(summary.get(key))
        if v is None:
            continue
        sev = r.classify(v)
        out[key] = replace(r, value=v, severity=sev.value if sev else None)
    return out


def _status(r: MetricRange | None, value: float | None) -> str:
    """'ok' | 'watch' | 'fix' as pose judged it, 'none' without a reference range."""
    if r is None or value is None:
        return "none"
    if r.severity and r.value is not None and abs(r.value - value) < 1e-6:
        return str(r.severity)
    sev = r.classify(value)
    return sev.value if sev else "none"


def _target_text(r: MetricRange | None) -> str:
    """The green range without unit, for the narrow target column: 'minst 160', '30–55'."""
    if r is None:
        return "–"
    lo, hi = r.ok_lo, r.ok_hi
    if lo is not None and hi is not None:
        return _range_str(lo, hi, 0)
    if lo is not None:
        return t("report.range.min", v=fmt_num(lo, 0))
    if hi is not None:
        return t("report.range.max", v=fmt_num(hi, 0))
    return "–"


def _bar(spec: MetricSpec, r: MetricRange | None, value: float | None) -> str:
    """Range bar for one metric, drawn from the pose range (empty without one)."""
    if r is None:
        return ""
    lo, hi = spec.scale
    bounds = [b for b in (r.ok_lo, r.ok_hi, r.fix_lo, r.fix_hi) if b is not None]
    if bounds:
        pad = 0.12 * (hi - lo)
        lo, hi = min(lo, min(bounds) - pad), max(hi, max(bounds) + pad)
    good = (lo if r.ok_lo is None else r.ok_lo, hi if r.ok_hi is None else r.ok_hi)
    watch = (lo if r.fix_lo is None else r.fix_lo, hi if r.fix_hi is None else r.fix_hi)
    return charts.range_bar_svg(value, (lo, hi), good, watch)


def _lactate_points(data: ReportData) -> list[dict[str, Any]]:
    lac = data.lactate or {}
    pts = lac.get("points")
    if pts:
        return [{"speed_kmh": _f(p.get("speed_kmh")), "lactate": _f(p.get("lactate", p.get("lactate_mmol"))),
                 "hr": _f(p.get("hr", p.get("hr_end")))} for p in pts if _f(p.get("speed_kmh"))]
    return [{"speed_kmh": r.speed_kmh, "lactate": _f(r.lactate_mmol), "hr": _f(r.hr_end or r.hr_mean)}
            for r in data.records if r.lactate_mmol is not None or r.hr_end or r.hr_mean]


def _threshold(d: Any, label: str, max_hr: float | None) -> dict[str, Any] | None:
    if not isinstance(d, Mapping) or _f(d.get("speed_kmh")) is None:
        return None
    hr = _f(d.get("hr"))
    pct = _f(d.get("pct_max_hr"))
    if pct is None and hr and max_hr:
        pct = 100 * hr / max_hr
    return {"label": label, "method": d.get("method") or d.get("name") or "",
            "speed_kmh": _f(d["speed_kmh"]), "speed": fmt_load(_f(d["speed_kmh"])),
            "pace": _pace_of(d), "hr": fmt_num(hr, 0), "hr_raw": hr,
            "lactate": fmt_num(_f(d.get("lactate")), 1), "lactate_raw": _f(d.get("lactate")),
            "pct_max_hr": fmt_num(pct, 0)}


def _methods(lac: Mapping[str, Any], max_hr: float | None) -> list[dict[str, Any]]:
    m = lac.get("methods") or {}
    items = m.items() if isinstance(m, Mapping) else [(x.get("name", ""), x) for x in m]
    out = []
    for name, d in items:
        th = _threshold(d, str(name), max_hr)
        if th:
            th["method"] = str(name)
            out.append(th)
    return out


def _mark_chosen(methods: list[dict[str, Any]], *chosen: dict[str, Any] | None) -> list[dict[str, Any]]:
    for m in methods:
        m["chosen"] = next((c["label"] for c in chosen if c and c["method"] == m["method"]), "")
    return methods


COVER_CHART_H_MM = 46.0   # the small chart on the cover
COVER_ZONE_HEAD_CHARS = 95   # what fits on the line of the cover's zone heading (8 pt, 178 mm)


def _prefix_ids(svg: str, prefix: str) -> str:
    """Give an inline SVG its own ids: the cover repeats a chart of a later page in the same HTML."""
    if not svg:
        return svg
    svg = re.sub(r'\bid="([^"]+)"', lambda m: f'id="{prefix}-{m.group(1)}"', svg)
    return re.sub(r'(url\(#|href="#)([^)"]+)', lambda m: f"{m.group(1)}{prefix}-{m.group(2)}", svg)


def _max_hr_value(data: ReportData) -> float | None:
    if data.max_hr and _f(data.max_hr.get("peak")):
        return _f(data.max_hr["peak"])
    return _f(data.athlete.max_hr_for(data.sport))


# --------------------------------------------------------------------------------------------
# Section contexts
# --------------------------------------------------------------------------------------------

def _lactate_ctx(data: ReportData, accent: str) -> dict[str, Any]:
    lac = data.lactate or {}
    mx = _max_hr_value(data)
    pts = _lactate_points(data)
    lt1 = _threshold(lac.get("lt1"), "LT1", mx)
    lt2 = _threshold(lac.get("lt2"), "LT2", mx)
    zones = _zones_ctx(data.zones or [])["rows"] if data.zones else []
    prev_pts = None
    prev_label = ""
    if data.previous is not None and (data.previous.lactate or data.previous.records):
        pp = [(p["speed_kmh"], p["lactate"]) for p in _lactate_points(data.previous) if p["lactate"] is not None]
        if len(pp) >= 2:
            prev_pts = pp
            prev_label = t("report.lactate.caption_previous", date=date_short(data.previous.session.started))
    curve = [(_f(c.get("speed_kmh")), _f(c.get("lactate"))) for c in (lac.get("curve") or [])]
    curve = [c for c in curve if c[0] is not None and c[1] is not None]
    chart = cover_chart = ""
    unit = sport_profile(data.sport).load_unit
    axis = dict(load_unit=unit, axis_label=ts("report.chart.speed_axis"), title=ts("report.chart.lactate_title"),
                load_decimals=_dec())
    if pts:
        chart = charts.lactate_chart(
            [p["speed_kmh"] for p in pts], [p["lactate"] for p in pts], [p["hr"] for p in pts],
            curve=curve or None,
            thresholds=[charts.Threshold(x["label"], x["speed_kmh"], x["lactate_raw"], x["hr_raw"])
                        for x in (lt1, lt2) if x],
            zones=_zone_bands(zones),
            previous=prev_pts, accent=accent, **axis)
        cover_chart = charts.lactate_chart(
            [p["speed_kmh"] for p in pts], [p["lactate"] for p in pts], [p["hr"] for p in pts],
            curve=curve or None,
            thresholds=[charts.Threshold(x["label"], x["speed_kmh"], x["lactate_raw"], x["hr_raw"])
                        for x in (lt1, lt2) if x],
            zones=_zone_bands(zones), accent=accent, height_mm=COVER_CHART_H_MM, **axis)
    stages = []
    recs = data.records or []
    manual = {x.get("index") for x in ((getattr(data.session, "result", None) or {}).get("stage_stats") or [])
              if x.get("manual") is not None}
    for i, r in enumerate(recs):
        stages.append({"n": i + 1, "speed": fmt_load(r.speed_kmh) + ("*" if r.index in manual else ""),
                       "pace": pace_str(r.speed_kmh),
                       "incline": fmt_num(r.incline_pct), "hr": fmt_num(_f(r.hr_end or r.hr_mean), 0),
                       "lactate": fmt_num(_f(r.lactate_mmol), 1), "rpe": str(r.rpe) if r.rpe else "–",
                       "duration": f"{int(r.duration_s // 60)}:{int(r.duration_s % 60):02d}"})
    if not stages:
        for i, p in enumerate(pts):
            stages.append({"n": i + 1, "speed": fmt_load(p["speed_kmh"]), "pace": pace_str(p["speed_kmh"]),
                           "incline": "–", "hr": fmt_num(p["hr"], 0), "lactate": fmt_num(p["lactate"], 1),
                           "rpe": "–", "duration": "–"})
    peak_lac = max([p["lactate"] for p in pts if p["lactate"] is not None], default=None)
    # A SkiErg cannot be set: the load of each stage is the watt that was measured, and the report says so.
    stats = (getattr(data.session, "result", None) or {}).get("stage_stats") or []
    note = ts("report.lactate.self_paced_note") if any(x.get("self_paced") for x in stats) else ""
    if manual & {r.index for r in recs}:
        note = " ".join(x for x in (note, t("report.lactate.manual_note")) if x)
    drag = _drag_text(data)
    note = " ".join(x for x in (note, drag) if x)
    return {"note": note, "chart": Markup(chart), "cover_chart": Markup(_prefix_ids(cover_chart, "cv")), "previous_note": prev_label, "warnings": lac.get("warnings") or [], "lt1": lt1, "lt2": lt2, "stages": stages,
            "methods": _mark_chosen(_methods(lac, mx), lt1, lt2), "peak_lactate": fmt_num(peak_lac, 1),
            "baseline": fmt_num(_f(lac.get("baseline")), 1) if lac.get("baseline") is not None else None}


def _zone_bands(rows: list[dict[str, Any]]) -> list[charts.ZoneBand]:
    """Bands for the lactate chart: only zones whose speed range the test measured. A zone that is
    open at one end is drawn only when it is the first or the last one (a system in % of max HR, or
    with borders outside the thresholds, has no place on the speed axis)."""
    last = len(rows) - 1
    return [charts.ZoneBand(z["short"], z["speed_low_raw"], z["speed_high_raw"], z["color"]) for i, z in enumerate(rows)
            if (z["speed_low_raw"] is not None or z["speed_high_raw"] is not None)
            and (z["speed_low_raw"] is not None or i == 0) and (z["speed_high_raw"] is not None or i == last)]


def _strip_code(name: str, code: str) -> str:
    """'I1 Lugn distans' with code 'I1' -> 'Lugn distans' (the table shows the code already)."""
    return name[len(code):].lstrip(" ·:-–") if name.startswith(code + " ") else name


def _zones_ctx(zones: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Zone rows plus which columns have any data (HR-only zones hide pace, speed and lactate)."""
    zs = list(zones)
    rows = []
    for i, z in enumerate(zs):
        rows.append({
            "short": z.get("code") or f"Z{i + 1}",
            "name": _strip_code(z.get("name") or "", z.get("code") or f"Z{i + 1}"),
            "purpose": z.get("purpose", ""),
            "hr": _range_str(z.get("hr_low"), z.get("hr_high"), 0),
            "pct": _range_str(z.get("hr_pct_low"), z.get("hr_pct_high"), 0),
            "speed": _range_str(z.get("speed_low"), z.get("speed_high"), _dec(), lo_mark=_mark(z, "low"),
                                hi_mark=_mark(z, "high")),
            "pace": _pace_range(z.get("speed_low"), z.get("speed_high"), lo_mark=_mark(z, "low"),
                                hi_mark=_mark(z, "high")),
            "lactate": _range_str(z.get("lactate_low"), z.get("lactate_high"), 1),
            "speed_low_raw": _f(z.get("speed_low")), "speed_high_raw": _f(z.get("speed_high")),
            "color": zone_color(i, len(zs)),
        })
    cols = {c: any(r[c] != "–" for r in rows) for c in ("hr", "pct", "pace", "speed", "lactate")}
    return {"rows": rows, "cols": cols, "speed_from_hr": any(z.get("speed_basis") == "hr" for z in zs),
            "extrapolated": any(z.get("speed_low_extrapolated") or z.get("speed_high_extrapolated") for z in zs)}


EXTRAPOLATED_MARK = "≈"   # before a speed outside the measured steps of a zones test without lactate


def _mark(z: Mapping[str, Any], end: str) -> str:
    return EXTRAPOLATED_MARK if z.get(f"speed_{end}_extrapolated") else ""


def _compare_ctx(compare: Iterable[Mapping[str, Any]] | None) -> dict[str, Any] | None:
    """The same thresholds in several systems side by side: one column per system, one row per zone."""
    cols = []
    for c in compare or []:
        cells = [{"short": z.get("code") or f"Z{i + 1}", "hr": _range_str(z.get("hr_low"), z.get("hr_high"), 0)}
                 for i, z in enumerate(c.get("zones") or [])]
        if cells:
            cols.append({"name": c.get("name", ""), "cells": cells})
    if len(cols) < 2:
        return None
    n = max(len(c["cells"]) for c in cols)
    return {"heads": [c["name"] for c in cols],
            "rows": [[c["cells"][i] if i < len(c["cells"]) else None for c in cols] for i in range(n)]}


def _hr_zones_ctx(data: ReportData, zone_rows: list[dict[str, Any]], zones: list[Mapping[str, Any]],
                  accent: str) -> dict[str, Any] | None:
    """The zones test without lactate: heart rate against speed, the steps and how good the line is."""
    h = data.hr_speed or {}
    pts = [p for p in h.get("points") or [] if _f(p.get("hr")) is not None]
    if not pts:
        return None
    mx = _max_hr_value(data)
    marks = [(float(p["speed_kmh"]), float(p["hr"]), p.get("stable") is not False) for p in pts]
    line = [(float(x), float(y), bool(e)) for x, y, e in h.get("line") or []]
    bands = [(float(z["hr_low"]), float(z["hr_high"]), r["color"], r["short"]) for z, r in zip(zones, zone_rows)
             if z.get("hr_low") and z.get("hr_high")]
    kw = dict(zones=bands, max_hr=mx, accent=accent)
    steps = []
    for i, p in enumerate(h.get("points") or []):
        hr, rise, v = _f(p.get("hr")), _f(p.get("rise_bpm")), _f(p.get("speed_kmh"))
        note = p.get("note") or ("ok" if p.get("stable") else "unknown")
        steps.append({"n": i + 1, "speed": fmt_num(v), "pace": pace_str(v),
                      "duration": f"{int((p.get('duration_s') or 0) // 60)}:{int((p.get('duration_s') or 0) % 60):02d}",
                      "hr": fmt_num(hr, 0), "pct": fmt_num(100 * hr / mx, 0) if hr and mx else "–",
                      "rise": "–" if rise is None else ("+" if rise > 0 else "") + fmt_num(rise, 1),
                      "note": t(f"report.hrzones.note.{note}"), "warn": note in ("unstable", "short", "no_hr")})
    ok = bool(h.get("ok"))
    used = [p for p in pts if (p.get("weight") or 0) > 0]
    durs = sorted({round((p.get("duration_s") or 0) / 60) for p in used})
    incline = _f(h.get("incline_pct"))
    return {
        "chart": Markup(charts.hr_speed_chart(marks, line, **kw)),
        "cover_chart": Markup(_prefix_ids(charts.hr_speed_chart(marks, line, **kw, height_mm=COVER_CHART_H_MM), "cv")),
        "lead": t("report.hrzones.lead", n=len(used) or len(pts), min=durs[-1] if durs else 4),
        "steps": steps, "ok": ok,
        "slope": fmt_num(_f(h.get("slope")), 1) if ok else "–",
        "r2": fmt_num(_f(h.get("r2")), 2) if ok else "–",
        "see": "±" + fmt_num(_f(h.get("see_bpm")), 1) if ok and h.get("see_bpm") is not None else "–",
        "range": f"{fmt_num(_f(h.get('speed_min')))}–{fmt_num(_f(h.get('speed_max')))}" if ok else "–",
        "quality": h.get("quality_text") or "",
        "warnings": [w.get("text", "") if isinstance(w, Mapping) else str(w) for w in h.get("warnings") or []],
        "limits": " ".join(x for x in (
            t("assessments.hrzones.speed_from_hr_long"),
            t("assessments.hrzones.extrapolated", lo=fmt_num(_f(h.get("speed_min"))), hi=fmt_num(_f(h.get("speed_max"))),
              km=fmt_num(_f(h.get("extrapolate_kmh")), 0)) if ok else "",
            t("report.hrzones.incline", v=fmt_num(incline, 0)) if incline is not None else "") if x),
    }


def _max_hr_ctx(data: ReportData) -> dict[str, Any]:
    m = data.max_hr or {}
    peak = _f(m.get("peak"))
    if peak is None and data.samples:
        peak = max((s.hr for s in data.samples if s.hr), default=None)
    chart = cover_chart = ""
    if data.samples:
        args = ([s.t_s for s in data.samples], [s.hr for s in data.samples])
        kw = dict(peak_bpm=peak, peak_t_s=_f(m.get("time")), stop_t_s=_f(m.get("stop_time")),
                  speeds=[s.speed_kmh for s in data.samples])
        if data.sport is not Sport.RUN:
            kw["strip_key"] = "report.chart.power_strip"
        chart = charts.hr_chart(*args, **kw)
        cover_chart = _prefix_ids(charts.hr_chart(*args, **kw, height_mm=COVER_CHART_H_MM), "cv")
    tt = _f(m.get("time"))
    plateau = m.get("plateau")
    likely = m.get("likely_true_max")
    return {
        "chart": Markup(chart), "cover_chart": Markup(cover_chart), "peak": fmt_num(peak, 0),
        # the max HR is the highest 5-s mean; the highest single reading stands under it when it differs
        "single": fmt_num(_f(m.get("single")), 0) if _f(m.get("single")) and peak and round(_f(m.get("single"))) != round(peak) else "",
        "time": f"{int(tt // 60)}:{int(tt % 60):02d}" if tt is not None else "–",
        "plateau": t("report.yes") if plateau else (t("report.no") if plateau is not None else "–"),
        "recovery": fmt_num(_f(m.get("recovery_1min")), 0),
        "likely": likely,
        # a peak that did not meet the max criteria is the highest heart rate, not a max HR
        "peak_label": t("report.maxhr.peak") if likely is not False else t("report.maxhr.peak_not_max"),
        "lead": ts("report.maxhr.lead") if likely is not False else ts("report.maxhr.lead_not_max"),
        "not_max": likely is False,
        "criteria": m.get("criteria") or [], "warnings": m.get("warnings") or [],
        "verdict": (t("report.maxhr.verdict_true") if likely else
                    t("report.maxhr.not_max") + " " + (t("report.maxhr.verdict_rising") if plateau is False
                                                         else t("report.maxhr.verdict_maybe")))
        if likely is not None else "",
    }


STRIP_KIND = "sequence"
PAGE_H_MM, TEXT_W_MM = 261.0, 178.0   # content box of a page (see report.css)


def _frames_ctx(gv: _GaitView) -> dict[str, Any]:
    """Key frames as cards plus the full-width phase strip, with the grid that shows them largest."""
    by_code = {f["code"]: f for f in gv.findings}
    order = {"fix": 0, "watch": 1}
    cards, strips = [], []
    for k in gv.keyframes:
        src, aspect = _image(k["image_path"], printable=True)
        marked = [by_code[c] for c in k["findings"] if c in by_code and by_code[c]["severity"] in order]
        marked.sort(key=lambda f: order[f["severity"]])
        frame = {"src": src, "label": k["label"], "caption": k["caption"], "aspect": aspect,
                 "time": fmt_num(k["t"], 2) + " s" if k["t"] is not None else "",
                 "findings": [{"title": f["title"], "severity": f["severity"]} for f in marked]}
        (strips if k["kind"] == STRIP_KIND else cards).append(frame)
    # Portrait stills: three across when that makes one row, else two large ones per row.
    portrait = bool(cards) and all((c["aspect"] or 1.33) < 0.95 for c in cards)
    if portrait and len(cards) == 3:
        layout, col_w = "three", (TEXT_W_MM - 2 * 5) / 3
    elif portrait:
        layout, col_w = "two-portrait", 63.0
    else:
        layout, col_w = "two", (TEXT_W_MM - 5) / 2
    per_row = 3 if layout == "three" else 2
    row_h = max((col_w / (c["aspect"] or 1.33) for c in cards), default=0.0) + 17
    cards_h = math.ceil(len(cards) / per_row) * row_h
    strip_h = sum(TEXT_W_MM / (s["aspect"] or 2.2) + 12 for s in strips)
    # The strip comes last. When it does not fit under the cards it opens the next page, and the
    # metrics table follows it there instead of starting a page of its own.
    overflow = bool(strips) and bool(cards) and 30 + cards_h + strip_h > PAGE_H_MM
    return {"cards": cards, "strips": strips, "layout": layout, "strip_overflows": overflow}


def _gait_ctx(data: ReportData, gv: _GaitView) -> dict[str, Any]:
    frames = _frames_ctx(gv)
    metrics = []
    for spec in GAIT_METRICS:
        v = _f(gv.summary.get(spec.key))
        lv = gv.reliability.get(spec.key)
        if v is None or lv == reliability.HIDDEN:
            continue
        r = gv.ranges.get(spec.key)
        if r is not None and r.value is not None:
            v = r.value   # the value pose judged (the worse side for pelvic drop and knee valgus)
        name = (cadence_label(gv.summary.get("cadence_source"), "report.metric.cadence_spm")
                if spec.key == "cadence_spm" else t(f"report.metric.{spec.key}"))
        metrics.append({"name": name, "hint": t(f"report.metric_hint.{spec.key}"),
                        "value": fmt_num(v, spec.decimals), "unit": unit_label(spec.unit),
                        "tight": spec.unit == "deg", "key": spec.key, "raw": v,
                        "target": _target_text(r), "bar": Markup(_bar(spec, r, v)),
                        "status": _status(r, v), "uncertain": lv == reliability.UNCERTAIN})
    fs = gv.summary.get("foot_strike")
    sev_order = {"fix": 0, "watch": 1, "ok": 2}
    findings = sorted(gv.findings, key=lambda f: sev_order.get(f["severity"], 3))
    for f in findings:
        f["value_str"] = (with_unit(fmt_num(f["value"], _finding_decimals(f)), f["unit"])
                          if f["value"] is not None else "")
        if not f["drill"]:
            key = f"report.drill.{f['code']}"
            drill = t(key)
            f["drill"] = "" if drill == key else drill
        f["severity_label"] = t(f"report.severity.{f['severity']}")
    # Per-speed table from the gait summaries stored on each stage. A max HR test with technique
    # counts only the stages its film was judged on, not the last minutes of the ramp.
    win = _get(data.gait, "window") if data.gait is not None else None
    judged = set(win.get("stages") or []) if isinstance(win, Mapping) else None
    per_speed = []
    stage_gait = [(r.speed_kmh, r.gait) for r in data.records if r.gait and (judged is None or r.index in judged)]
    if len(stage_gait) >= 2:
        stage_ranges = [_reference_ranges(gv.view, gv.height_cm, _f(speed)) for speed, _ in stage_gait]
        for ref in GAIT_METRICS:
            if gv.reliability.get(ref.key) == reliability.HIDDEN:
                continue
            vals = [_f(g.get(ref.key)) for _, g in stage_gait]
            known = [v for v in vals if v is not None]
            # Only what changes with speed; the rest is already in the table above.
            if len(known) < 2 or max(known) - min(known) < 10 ** -ref.decimals:
                continue
            name = t(f"report.metric.{ref.key}")
            if ref.key == "cadence_spm" and any(g.get("cadence_source") == "strap" for _, g in stage_gait):
                name = cadence_label("strap", "report.metric.cadence_spm")
            per_speed.append({"name": name, "unit": unit_label(ref.unit),
                              "values": [{"v": fmt_num(v, ref.decimals), "status": _status(rr.get(ref.key), v)}
                                         for v, rr in zip(vals, stage_ranges)]})
    asym = gv.summary.get("asymmetry") or {}
    return {"window_note": _window_note(win, data.records),
            "uncertain_note": (t("report.gait.uncertain_note", fps=fmt_num(gv.fps, 0),
                                 need=fmt_num(reliability.min_fps_for_all(), 0))
                               if any(m["uncertain"] for m in metrics) else ""),
            "frames": frames, "metrics": metrics, "ranges": gv.ranges, "findings": findings, "warnings": gv.warnings,
            "foot_strike": t(f"report.foot_strike.{fs}") if fs else None,
            "steps": gv.summary.get("steps"), "per_speed": per_speed,
            "speeds": [fmt_num(s) for s, _ in stage_gait],
            "asymmetry": [{"name": t(f"report.metric.{k}"), "value": fmt_num(_f(v), 0)} for k, v in asym.items()
                          if _f(v) is not None and abs(_f(v)) >= 5]}


def _view_label(view: Any) -> str:
    key = f"report.view.{_enum_val(view)}" if view else ""
    name = t(key) if key else ""
    return "" if name == key else name


def _add_second_view(sections: dict[str, Any], data: ReportData, gv: _GaitView) -> None:
    """Two camera views: the second camera's analysis (``gait["second"]``) becomes a context of
    its own under ``sections["gait"]["second"]``, built like the first, and both get the name of their
    view. Its per-speed table comes from the second view's summaries on each stage. When only the
    second film could be analysed it takes the first one's place."""
    second = _get(data.gait, "second") if data.gait is not None else None
    if not second:
        return
    a = data.athlete
    gv2 = _gait_view(second, a.height_cm)
    if gv2 is None:
        return
    recs = [replace(r, gait=r.gait.get("second")) for r in data.records
            if isinstance(r.gait, Mapping) and r.gait.get("second")]
    ctx2 = _gait_ctx(replace(data, gait=second, records=recs), gv2)
    ctx2["view_label"] = _view_label(gv2.view)
    sync = _get(second, "sync")
    if isinstance(sync, Mapping) and sync.get("matched"):
        ctx2["sync_note"] = t("report.gait.sync", n=sync["matched"],
                              of=min(sync.get("first") or 0, sync.get("second") or 0))
    main = sections["gait"]
    if not gv.summary and not main["findings"]:
        sections["gait"] = ctx2
        return
    main["view_label"] = _view_label(gv.view)
    main["second"] = ctx2


def _window_note(win: Any, records: list[StepRecord]) -> str:
    """Maxpuls med löpteknik: which stages of the ramp the technique was judged on, in words."""
    if not isinstance(win, Mapping):
        return ""
    idx = set(win.get("stages") or [])
    chosen = [r for r in records if r.index in idx]
    if not chosen:
        return t("report.gait.window_warmup")
    last = max(chosen, key=lambda r: r.index)
    what = f"{fmt_num(last.speed_kmh)} km/h, {fmt_num(last.incline_pct, 0)} %"
    if win.get("basis") == "hr" and win.get("peak_hr"):
        key = "report.gait.window_steps_hr" if win.get("plan_kind") == "hr_zones" else "report.gait.window_hr"
        return t(key, n=len(chosen), last=what, hr=int(win["peak_hr"] * 0.9))
    return t("report.gait.window_count", n=len(chosen), last=what)


def _finding_decimals(f: Mapping[str, Any]) -> int:
    """One decimal for small values, none for large ones."""
    return 1 if abs(f["value"]) < 10 else 0


def _better(ref_better: str, old: float, new: float, ref: MetricRange | None = None) -> str:
    """'better' | 'worse' | 'same' | 'neutral'."""
    if abs(new - old) < 1e-9:
        return "same"
    if ref_better == "higher":
        return "better" if new > old else "worse"
    if ref_better == "lower":
        return "better" if new < old else "worse"
    if ref_better == "range" and ref:
        def dist(v: float) -> float:
            if ref.ok_lo is not None and v < ref.ok_lo:
                return ref.ok_lo - v
            return v - ref.ok_hi if ref.ok_hi is not None and v > ref.ok_hi else 0.0
        do, dn = dist(old), dist(new)
        return "same" if abs(do - dn) < 1e-9 else ("better" if dn < do else "worse")
    return "neutral"


def _trend_ctx(data: ReportData) -> dict[str, Any] | None:
    prev = data.previous
    if prev is None:
        return None
    rows: list[dict[str, Any]] = []

    def add(name: str, unit: str, old: float | None, new: float | None, decimals: int, better: str,
            ref: MetricRange | None = None) -> None:
        if old is None or new is None:
            return
        d = new - old
        sign = "+" if d > 0 else ""
        rows.append({"name": name, "unit": unit, "old": fmt_num(old, decimals), "new": fmt_num(new, decimals),
                     "arrow": "↑" if d > 0 else ("↓" if d < 0 else "·"),
                     "delta": sign + fmt_num(d, decimals) if abs(d) >= 10 ** -decimals / 2 else "±0",
                     "dir": _better(better, old, new, ref)})

    for key, label in (("lt1", "LT1"), ("lt2", "LT2")):
        a, b = (prev.lactate or {}).get(key) or {}, (data.lactate or {}).get(key) or {}
        add(ts("report.trend.speed_at", lt=label), sport_profile(data.sport).load_unit, _f(a.get("speed_kmh")),
            _f(b.get("speed_kmh")), _dec(), "higher")
        add(t("report.trend.hr_at", lt=label), unit_label("bpm"), _f(a.get("hr")), _f(b.get("hr")), 0, "neutral")
    if prev.max_hr or data.max_hr:
        add(t("report.trend.max_hr"), unit_label("bpm"), _f((prev.max_hr or {}).get("peak")),
            _f((data.max_hr or {}).get("peak")), 0, "neutral")
        add(t("report.trend.recovery"), unit_label("bpm"), _f((prev.max_hr or {}).get("recovery_1min")),
            _f((data.max_hr or {}).get("recovery_1min")), 0, "higher")
    pg = _gait_view(with_strap_cadence(prev.gait, prev.samples, data.athlete.height_cm), data.athlete.height_cm)
    ng = _gait_view(with_strap_cadence(data.gait, data.samples, data.athlete.height_cm), data.athlete.height_cm)
    if pg and ng:
        for ref in GAIT_METRICS:
            add(t(f"report.metric.{ref.key}"), unit_label(ref.unit), _f(pg.summary.get(ref.key)),
                _f(ng.summary.get(ref.key)), ref.decimals, ref.better, ng.ranges.get(ref.key))
    if not rows:
        return None
    return {"date": date_long(prev.session.started), "rows": rows}


def _followup_ctx(data: ReportData) -> dict[str, Any] | None:
    """*Mot schemat* (#778): the thresholds the plan counted on against the measured ones, and how much of the plan
    was done between the tests (plan.expected). None without a plan between the tests or without a previous test."""
    f, prev = data.plan_followup, data.previous
    if not f or prev is None:
        return None
    exp, dn = f.get("expected"), f.get("done") or {}
    rows: list[dict[str, Any]] = []
    if exp:
        gain = float(exp["gain_pct"])
        for key, label in (("lt1", "LT1"), ("lt2", "LT2")):
            a, b = (prev.lactate or {}).get(key) or {}, (data.lactate or {}).get(key) or {}
            for name, unit, before, now, dec, scale in (
                    (ts("report.trend.speed_at", lt=label), sport_profile(data.sport).load_unit,
                     _f(a.get("speed_kmh")), _f(b.get("speed_kmh")), _dec(), True),
                    (t("report.trend.hr_at", lt=label), unit_label("bpm"), _f(a.get("hr")), _f(b.get("hr")), 0, False)):
                if before is None or now is None:
                    continue
                want = before * (1 + gain / 100) if scale else before
                d = now - want
                diff = (d / before * 100) if before else 0.0
                verdict = ("on" if abs(diff) <= TOLERANCE_PCT else "ahead" if diff > 0 else "behind") if scale else "hr"
                rows.append({"name": name, "unit": unit, "old": fmt_num(before, dec), "want": fmt_num(want, dec),
                             "new": fmt_num(now, dec),
                             "delta": ("+" if d > 0 else "") + fmt_num(d, dec) if abs(d) >= 10 ** -dec / 2 else "±0",
                             "dir": {"ahead": "better", "behind": "worse", "on": "same"}.get(verdict, "neutral"),
                             "verdict": t(f"report.followup.verdict.{verdict}")})
    share = dn.get("share")
    sports = [{"name": t(f"plan.sport.{s['sport']}"), "planned": s["planned_min"], "done": s["done_min"],
               "share": fmt_num(s["done_min"] / s["known_min"] * 100, 0) if s["known_min"] else "–",
               "quality": f"{s['quality_done']}/{s['quality_planned']}" if s["quality_planned"] else "–"}
              for s in dn.get("sports") or []]
    if not rows and not sports:
        return None
    level = t(f"plan.level.{exp['level']}") if exp else ""
    lead = (ts("report.followup.lead", weeks=fmt_num(exp["weeks"], 1), gain=fmt_num(exp["gain_pct"], 1), level=level)
            if exp and rows else t("report.followup.lead_no_thresholds"))
    if share is None:
        done_text = t("report.followup.done_unknown")
    else:
        done_text = t("report.followup.done", share=fmt_num(share * 100, 0), done=dn["done_min"], known=dn["known_min"],
                      qd=dn["quality_done"], qp=dn["quality_planned"])
    notes = []
    if share is not None and dn["known_min"] < dn["planned_min"]:
        notes.append(t("report.followup.not_logged", minutes=dn["planned_min"] - dn["known_min"],
                       date=date_long(date.fromisoformat(dn["logged_until"])))
                     if dn.get("logged_until") else t("report.followup.no_log"))
    if dn.get("extras"):
        notes.append(t("report.followup.extras", n=dn["extras"], minutes=dn["extras_min"]))
    return {"lead": lead, "rows": rows, "done": done_text, "sports": sports if len(sports) > 1 else [],
            "notes": notes, "estimate": t("report.followup.estimate", tol=fmt_num(TOLERANCE_PCT, 1))}


def _summary_ctx(data: ReportData, sections: dict[str, Any]) -> dict[str, Any]:
    """Up to four key results and three things to work on, picked from what the test measured.

    The tiles come in a fixed order of importance (thresholds, max HR, cadence, estimated VO2max, the
    rest) and the first four that exist are shown, so every kind of test fills the cover the same way.
    Lactate only appears when the test measured it."""
    focus: list[dict[str, str]] = []
    lac, mx, gait = sections.get("lactate"), sections.get("max_hr"), sections.get("gait")
    zones = sections.get("zones")
    picked: dict[str, dict[str, str]] = {}
    hz = sections.get("hr_zones")
    # Zones with speed from heart rate (no lactate): the easy zone and the hard one as tiles.
    zr = zones["rows"] if zones and zones.get("speed_from_hr") and not lac else []
    easy_row = (zr[1] if len(zr) >= 5 else zr[0]) if zr else None
    # the pace first (min/km), km/h and the heart rate under it
    if easy_row and easy_row["speed"] != "–":
        picked["easy"] = {"label": t("report.summary.easy", zone=easy_row["short"]), "value": easy_row["pace"],
                          "unit": "min/km", "cls": "range", "sub": t("report.summary.easy_sub", kmh=easy_row["speed"], hr=easy_row["hr"])}
    hard_row = zr[-2] if len(zr) >= 4 else None
    if hard_row and hard_row["speed"] != "–" and hard_row is not easy_row:
        picked["hard"] = {"label": t("report.summary.hard", zone=hard_row["short"]), "value": hard_row["pace"],
                          "unit": "min/km", "cls": "range", "sub": t("report.summary.easy_sub", kmh=hard_row["speed"], hr=hard_row["hr"])}
    if lac:
        for key in ("lt1", "lt2"):
            x = lac[key]
            if x:
                run_pace = sport_profile(data.sport).load_quantity == "speed" and x["pace"] != "–"
                picked[key] = {"label": ts(f"report.summary.{key}"), "value": x["pace"] if run_pace else x["speed"],
                               "unit": "min/km" if run_pace else sport_profile(data.sport).load_unit,
                               # running: the pace is the value, km/h stands under it
                               "sub": (t("report.summary.lt_sub_kmh", kmh=x["speed"], hr=x["hr"]) if run_pace
                                       else ts("report.summary.lt_sub", pace=x["pace"], hr=x["hr"]) if x["pace"] != "–"
                                       else t("report.summary.lt_sub_hr", hr=x["hr"]))}
        if lac["peak_lactate"] != "–":
            picked["peak_lactate"] = {"label": t("report.summary.peak_lactate"), "value": lac["peak_lactate"],
                                      "unit": unit_label("mmol"), "sub": t("report.summary.peak_lactate_sub")}
    if mx and mx["peak"] != "–":
        picked["max_hr"] = {"label": mx["peak_label"] if mx.get("not_max") else t("report.summary.max_hr"),
                            "value": mx["peak"], "unit": unit_label("bpm"),
                            "sub": t("report.maxhr.not_max") if mx.get("not_max") else
                            (t("report.summary.max_hr_sub", rec=mx["recovery"]) if mx["recovery"] != "–" else "")}
        if not lac and not hz:
            if mx["recovery"] != "–":
                picked["recovery"] = {"label": t("report.maxhr.recovery"), "value": mx["recovery"],
                                      "unit": unit_label("bpm"), "sub": t("report.summary.recovery_sub")}
            if mx["time"] != "–":
                picked["time"] = {"label": t("report.maxhr.time"), "value": mx["time"], "unit": "min",
                                  "sub": t("report.summary.plateau_sub", v=mx["plateau"].lower())}
    pw = sections.get("power")
    if pw:       # cycling in watt: FTP with how it was computed, and the best minute
        if pw["ftp"] != "–":
            picked["ftp"] = {"label": "FTP", "value": pw["ftp"], "unit": "W",
                             "sub": " · ".join(x for x in (pw["ftp_wkg"] and f"{pw['ftp_wkg']} W/kg", pw["method"]) if x)}
        if pw["map"] != "–":
            picked["map"] = {"label": t("report.power.map"), "value": pw["map"], "unit": "W",
                             "sub": f"{pw['map_wkg']} W/kg" if pw["map_wkg"] else t("report.power.map_sub")}
    vo2 = (zones or {}).get("vo2max")
    if vo2 and not vo2.get("none"):
        picked["vo2max"] = {"label": vo2["title"], "value": vo2["value"], "unit": vo2["unit"], "sub": vo2["range"]}
    if gait:
        metrics = {m["key"]: m for m in gait["metrics"]}
        cad = metrics.get("cadence_spm")
        if cad:
            picked["cadence"] = {"label": cad["name"], "value": cad["value"], "unit": cad["unit"],
                                 "sub": t("report.summary.target", target=cad["target"])}
        second = gait.get("second") or {}      # two camera views: what the second one found counts too
        sev_rank = {"fix": 0, "watch": 1}
        all_findings = sorted(gait["findings"] + list(second.get("findings") or []),
                              key=lambda f: sev_rank.get(f["severity"], 2))
        worst = next((f for f in all_findings if f["severity"] in ("fix", "watch") and f["value"] is not None
                      and f["code"] not in ("cadence", "low_cadence", "high_cadence")), None)
        if worst:
            picked["worst"] = {"label": worst["title"], "value": fmt_num(worst["value"], _finding_decimals(worst)),
                               "unit": unit_label(worst["unit"]),
                               "sub": t("report.summary.target", target=worst["reference"]) if worst["reference"] else ""}
        seen = {m["key"] for m in gait["metrics"]}
        judged = [m for m in gait["metrics"] + [m for m in second.get("metrics") or [] if m["key"] not in seen]
                  if m["status"] != "none"]
        if judged:
            ok = sum(1 for m in judged if m["status"] == "ok")
            picked["in_range"] = {"label": t("report.summary.in_range"), "value": str(ok),
                                  "unit": t("report.summary.of_n", n=len(judged)),
                                  "sub": t("report.summary.in_range_sub")}
        for f in all_findings:
            if f["severity"] in ("fix", "watch") and len(focus) < (2 if lac or mx else 3):
                focus.append({"title": f["title"], "text": f["cue"] or f["explanation"], "severity": f["severity"]})
    order = ("lt1", "lt2", "ftp", "map", "max_hr", "easy", "hard", "cadence", "vo2max", "worst", "in_range", "recovery",
             "time", "peak_lactate")
    tiles = [picked[k] for k in order if k in picked]
    if lac and lac["lt1"] and len(focus) < 3:
        focus.append({"title": t("report.focus.easy_title"),
                      "text": ts("report.focus.easy_text", hr=lac["lt1"]["hr"], pace=lac["lt1"]["pace"],
                                 load=lac["lt1"]["speed"])})
    if lac and lac["lt2"] and len(focus) < 3:
        focus.append({"title": ts("report.focus.threshold_title"),
                      "text": ts("report.focus.threshold_text", hr=lac["lt2"]["hr"], pace=lac["lt2"]["pace"],
                                 load=lac["lt2"]["speed"])})
    if mx and mx["likely"] is False and len(focus) < 3:
        focus.append({"title": t("report.focus.maxhr_title"), "text": t("report.focus.maxhr_text")})
    if pw and pw["ftp"] != "–" and len(focus) < 3:
        focus.append({"title": t("report.focus.ftp_title"), "text": t("report.focus.ftp_text", ftp=pw["ftp"],
                                                                      lo=pw["z4_low"], hi=pw["z4_high"])})
    if mx and not lac and mx["peak"] != "–" and len(focus) < 3:
        focus.append({"title": t("report.focus.watch_title"), "text": ts("report.focus.watch_text", peak=mx["peak"])})
    if easy_row and easy_row["speed"] != "–" and len(focus) < 3:
        focus.insert(0, {"title": t("report.focus.hrzones_title"),
                         "text": t("report.focus.hrzones_text", zone=easy_row["short"], hr=easy_row["hr"],
                                   speed=easy_row["speed"], pace=easy_row["pace"])})
    elif zones and zones["rows"] and not lac and len(focus) < 3:
        # The easy zone: the second of five or more (I2, zone 2), the first in a 3- or 4-zone system.
        easy = zones["rows"][1] if len(zones["rows"]) >= 5 else zones["rows"][0]
        focus.append({"title": t("report.focus.zones_title"),
                      "text": t("report.focus.zones_text", zone=easy["short"], hr=easy["hr"])})
    return {"tiles": data.highlights or tiles[:4], "focus": data.focus or focus[:3], "custom_focus": bool(data.focus)}


def _cover_zones(zones: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The athlete's heart rate zones as one coloured bar for the cover; None without heart rates."""
    if not zones or not zones.get("rows") or not zones["cols"].get("hr"):
        return None
    # "Zon 1 Lätt" under the code Z1 reads "Lätt": the code is printed already.
    with_speed = bool(zones.get("speed_from_hr"))
    rows = [{"short": z["short"], "name": re.sub(r"^(Zon|Zone)\s*\d+\s*", "", z["name"]), "hr": z["hr"],
             "color": z["color"], "speed": z["speed"] if with_speed and z["speed"] != "–" else "",
             "pace": z.get("pace", "") if with_speed and z.get("pace") != "–" else ""}
            for z in zones["rows"]]
    system = (zones.get("system") or {}).get("name") or ""
    basis = zones.get("basis", "")
    # The sentence on which max HR the zones use goes on the heading's own line when it fits there
    # (about 95 characters beside "Dina pulszoner"); a longer one takes a line of its own, and a
    # cover with the lactate chart then shows one point less under "Att jobba med" to stay one page.
    head = len(system) + (24 if zones.get("estimated") else 0) + 10 + (31 if with_speed else 0)
    return {"rows": rows, "system": system, "estimated": bool(zones.get("estimated")), "basis": basis,
            "basis_inline": bool(basis) and head + len(basis) <= COVER_ZONE_HEAD_CHARS,
            "speed_from_hr": with_speed}


def _zone_basis(data: ReportData) -> str:
    """Which max HR the zones use and where it comes from (max_hr.zone_basis). The zones use the
    athlete's saved max HR, else the test's peak (as the result page)."""
    from lopanalys.assessments.max_hr import zone_basis
    a, m = data.athlete, data.max_hr or {}
    peak = _f(m.get("peak"))
    saved = _f(a.max_hr_for(data.sport))        # the sport's own max HR; its source is only kept for running
    run = data.sport is Sport.RUN
    used = saved or peak
    started = data.session.started
    return zone_basis(used, saved, getattr(a, "max_hr_source", "") if run else "",
                      getattr(a, "max_hr_date", None) if run else None,
                      peak, m.get("likely_true_max"), started.date() if hasattr(started, "date") else started)


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------

def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals["t"] = t
    env.globals["ts"] = ts
    env.globals["zone_color"] = zone_color
    env.filters["num"] = fmt_num
    return env


def _footer_line(*parts: str) -> str:
    """Join with ' · ' and keep each part (a phone number, an address) on one line."""
    return " · ".join(p.strip().replace(" ", "\u00a0") for p in parts if p and p.strip())


def _date_span(data: ReportData, started: date) -> str:
    """The test date, or the first and last date of a collected report over several days."""
    days = sorted({(x.started.date() if isinstance(x.started, datetime) else x.started) for x in data.sessions or []})
    if len(days) > 1:
        return f"{date_long(days[0])} – {date_long(days[-1])}"
    return date_long(started)


def _contents(sections: Mapping[str, Any], trend: Any, followup: Any = None) -> list[str]:
    """Section names in the order the template prints them."""
    order = (["lactate", "zones", "power", "max_hr"] if "lactate" in sections else
             ["hr_zones", "zones", "max_hr"] if "hr_zones" in sections else ["power", "max_hr", "zones"])
    names = [t(f"report.section.{k}") for k in order + ["gait"] if k in sections]
    if "technique" in sections:
        names.append(sections["technique"]["name"])
    if trend:
        names.append(t("report.section.trend"))
    if followup:
        names.append(t("report.section.followup"))
    return names


def _kind_name(kind: str) -> str:
    """The name of a test kind; bike fit and SkiErg technique have their texts with pose.technique."""
    return t(f"technique.kind.{kind}") if kind in ("bikefit", "ski") else t(f"report.kind.{kind}")


def _css_str(text: str) -> str:
    """A CSS string literal for generated content (margin boxes)."""
    text = re.sub(r"\s+", " ", text)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\3C ") + '"'


def _safe_color(c: str | None) -> str:
    return c if c and re.fullmatch(r"#[0-9A-Fa-f]{6}", c) else DEFAULT_ACCENT


def _first(d: Mapping[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _norm_threshold(d: Any) -> dict[str, Any] | None:
    if hasattr(d, "to_dict"):
        d = d.to_dict()
    if not isinstance(d, Mapping):
        return None
    return {"method": d.get("name") or d.get("method") or "", "speed_kmh": d.get("speed_kmh"),
            "lactate": _first(d, "lactate", "lactate_mmol"), "hr": d.get("hr"),
            "pct_max_hr": _first(d, "pct_max_hr", "hr_pct_max"), "pace": d.get("pace")}


def normalize_lactate(lac: Any) -> dict[str, Any] | None:
    """Accept LactateResult, its to_dict(), or the documented short form; return the short form."""
    if lac is None:
        return None
    if hasattr(lac, "to_dict"):
        lac = lac.to_dict()
    lac = dict(lac)
    curve = []
    for c in lac.get("curve") or []:
        if isinstance(c, Mapping):
            curve.append({"speed_kmh": c.get("speed_kmh"), "lactate": _first(c, "lactate", "lactate_mmol")})
        elif isinstance(c, (list, tuple)) and len(c) >= 2:
            curve.append({"speed_kmh": c[0], "lactate": c[1]})
    methods = lac.get("methods") or lac.get("thresholds") or {}
    if isinstance(methods, Mapping):
        methods = {(_norm_threshold(v) or {}).get("method") or k: _norm_threshold(v) for k, v in methods.items()}
    else:
        methods = {(_norm_threshold(v) or {}).get("method", ""): _norm_threshold(v) for v in methods}
    return {
        "points": [{"speed_kmh": p.get("speed_kmh"), "lactate": _first(p, "lactate", "lactate_mmol"),
                    "hr": _first(p, "hr", "hr_end")} for p in lac.get("points") or [] if isinstance(p, Mapping)],
        "curve": curve, "methods": {k: v for k, v in methods.items() if v},
        "lt1": _norm_threshold(lac.get("lt1")), "lt2": _norm_threshold(lac.get("lt2")),
        "baseline": _first(lac, "baseline", "baseline_mmol"),
        "warnings": [w.get("text", "") if isinstance(w, Mapping) else str(w) for w in lac.get("warnings") or []],
    }


def normalize_zones(zones: Any) -> list[dict[str, Any]] | None:
    """Accept assessments.zones.Zone objects or dicts; return the documented dict form."""
    if not zones:
        return None
    out = []
    for z in zones:
        if hasattr(z, "to_dict"):
            z = z.to_dict()
        z = dict(z)
        out.append({"code": z.get("code"), "name": z.get("name", ""), "purpose": z.get("purpose", ""),
                    "hr_low": z.get("hr_low"), "hr_high": z.get("hr_high"),
                    "hr_pct_low": z.get("hr_pct_low"), "hr_pct_high": z.get("hr_pct_high"),
                    "speed_low": _first(z, "speed_low", "speed_low_kmh"),
                    "speed_high": _first(z, "speed_high", "speed_high_kmh"),
                    "lactate_low": z.get("lactate_low"), "lactate_high": z.get("lactate_high"),
                    "estimated": bool(z.get("estimated")), "speed_basis": z.get("speed_basis") or "",
                    "speed_low_extrapolated": bool(z.get("speed_low_extrapolated")),
                    "speed_high_extrapolated": bool(z.get("speed_high_extrapolated"))})
    return out


def normalize_max_hr(m: Any) -> dict[str, Any] | None:
    """Accept MaxHrResult, its to_dict(), or the documented short form; return the short form."""
    if m is None:
        return None
    if hasattr(m, "to_dict"):
        m = m.to_dict()
    m = dict(m)
    crit = []
    for c in m.get("criteria") or []:
        c = c if isinstance(c, Mapping) else vars(c)
        if c.get("text"):
            crit.append({"met": c.get("met"), "text": c["text"]})
    return {"peak": _first(m, "peak", "peak_hr"), "time": _first(m, "time", "peak_t_s"),
            "plateau": m.get("plateau"), "recovery_1min": _first(m, "recovery_1min", "recovery_drop_60"),
            "likely_true_max": _first(m, "likely_true_max", "likely_max"),
            "stop_time": _first(m, "stop_time", "end_t_s"), "criteria": crit,
            "single": _first(m, "single", "peak_single_hr"),
            "warnings": [w.get("text", "") if isinstance(w, Mapping) else str(w) for w in m.get("warnings") or []]}


def _normalized(data: ReportData) -> ReportData:
    return replace(data, lactate=normalize_lactate(data.lactate), zones=normalize_zones(data.zones),
                   max_hr=normalize_max_hr(data.max_hr),
                   zone_compare=[{"name": c.get("name", ""), "zones": normalize_zones(c.get("zones")) or []}
                                 for c in data.zone_compare] if data.zone_compare else None,
                   previous=_normalized(data.previous) if data.previous is not None else None)


def render_html(data: ReportData) -> str:
    """Render the report for data as one self-contained HTML document (A4 print CSS)."""
    old_lang = language()
    if data.language and data.language != old_lang:
        set_language(data.language)
    try:
        return _render(_normalized(data))
    finally:
        if language() != old_lang:
            set_language(old_lang)


def _drag_text(data: ReportData) -> str:
    """'Dragfaktor 110, läst från monitorn.' for a SkiErg test that has one, else ''."""
    if data.sport is not Sport.SKIERG:
        return ""
    d = (getattr(data.session, "result", None) or {}).get("drag_factor") or {}
    if not d.get("value"):
        return ""
    return t("report.drag." + ("pm5" if d.get("source") == "pm5" else "manual"), value=int(d["value"]))


def _power_ctx(data: ReportData) -> dict[str, Any] | None:
    """FTP, the best minute, W/kg and Coggan's power zones of a test in watt, with the chart of
    heart rate over the power. None when the test gave neither an FTP nor a best minute."""
    pw = data.power or {}
    if not pw.get("ftp_w") and not pw.get("map_w"):
        return None
    chart = cover = ""
    if data.samples:
        args = ([s.t_s for s in data.samples], [s.hr for s in data.samples])
        kw = dict(speeds=[s.speed_kmh for s in data.samples], strip_key="report.chart.power_strip")
        chart = charts.hr_chart(*args, **kw)
        cover = _prefix_ids(charts.hr_chart(*args, **kw, height_mm=COVER_CHART_H_MM), "cv")
    zones = [{"short": str(z.get("code", "")).upper(), "name": z.get("name", ""),
              "power": _range_str(z.get("low_w"), z.get("high_w"), 0),
              "wkg": _range_str(_w_per_kg(_f(z.get("low_w"))), _w_per_kg(_f(z.get("high_w"))), 2),
              "color": zone_color(i, len(pw.get("zones") or []))} for i, z in enumerate(pw.get("zones") or [])]
    z4 = next((z for z in pw.get("zones") or [] if z.get("code") == "z4"), {})
    ftp20 = pw.get("method") == "ftp20"
    return {
        "chart": Markup(chart), "cover_chart": Markup(cover),
        "ftp": fmt_num(_f(pw.get("ftp_w")), 0), "ftp_wkg": fmt_num(_f(pw.get("ftp_w_per_kg")), 2) if pw.get("ftp_w_per_kg") else "",
        "method": pw.get("method_text") or "",
        "map": fmt_num(_f(pw.get("map_w")), 0), "map_wkg": fmt_num(_f(pw.get("map_w_per_kg")), 2) if pw.get("map_w_per_kg") else "",
        "mean20": fmt_num(_f(pw.get("mean_20min_w")), 0) if ftp20 else "–",
        "hr20": fmt_num(_f(pw.get("hr_mean_20min")), 0) if ftp20 else "–",
        "cadence": fmt_num(_f(pw.get("cadence_mean")), 0),
        "weight": fmt_num(_WEIGHT.get(), 1) if _WEIGHT.get() else "",
        "zones": zones, "has_wkg": bool(_WEIGHT.get()) and bool(zones),
        "z4_low": fmt_num(_f(z4.get("low_w")), 0), "z4_high": fmt_num(_f(z4.get("high_w")), 0),
        "warnings": [w.get("text", "") for w in pw.get("warnings") or []],
        "drag": _drag_text(data),
        "lead": t("report.power.lead_ftp20" if ftp20 else ("report.power.lead_ramp" if pw.get("ftp_w")
                                                             else "report.power.lead_best")),
        "title": t("report.power.title" if pw.get("ftp_w") else "report.power.title_best"),
    }


def _render(data: ReportData) -> str:
    tokens = (_SPORT.set(data.sport), _WEIGHT.set(data.weight_kg))
    try:
        return _render_sport(data)
    finally:
        _SPORT.reset(tokens[0])
        _WEIGHT.reset(tokens[1])


def _render_sport(data: ReportData) -> str:
    b = data.branding
    accent = _safe_color(b.accent_color)
    s = data.session
    a = data.athlete
    started = s.started.date() if isinstance(s.started, datetime) else s.started
    sections: dict[str, Any] = {}
    kinds = data.kinds
    if "lactate" in kinds:
        sections["lactate"] = _lactate_ctx(data, accent)
    # what to type into the athlete's watch, for the watch brand chosen in the result
    garmin = zone_systems.watch_card(data.garmin, getattr(a, "watch_brand", "") or None)
    vo2 = vo2max.card(data.vo2max, getattr(a, "watch_vo2max", None), getattr(a, "watch_vo2max_date", None))
    compare = _compare_ctx(data.zone_compare)
    if data.zones or garmin or vo2 or compare:
        zones = data.zones or []
        sections["zones"] = _zones_ctx(zones)
        # The zone systems carry textbook lactate ranges (Olympiatoppen I1 0.8-1.5 ...). They are not the
        # athlete's, so the column shows only when this test measured lactate.
        measured = any(r.lactate_mmol is not None for r in data.records) or any(
            p.get("lactate", p.get("lactate_mmol")) is not None for p in (data.lactate or {}).get("points") or [])
        if not measured:
            sections["zones"]["cols"]["lactate"] = False
        sections["zones"]["methods"] = sections.get("lactate", {}).get("methods", [])
        # Zones fall back to heart rate when the thresholds could not be separated: say so in the lead.
        sections["zones"]["from_lactate"] = not sections["zones"]["speed_from_hr"] and any(
            (z.get("speed_low") is not None or z.get("speed_high") is not None) for z in zones)
        sections["zones"].update(system=data.zone_system, estimated=any(z.get("estimated") for z in zones),
                                 compare=compare, garmin=garmin, vo2max=vo2, basis=_zone_basis(data) if zones else "")
    if "hr_zones" in kinds:
        hz = _hr_zones_ctx(data, sections.get("zones", {}).get("rows", []), data.zones or [], accent)
        if hz:
            sections["hr_zones"] = hz
    if "max_hr" in kinds:
        sections["max_hr"] = _max_hr_ctx(data)
    if data.sport is not Sport.RUN and ("ftp" in kinds or "max_hr" in kinds):
        pw = _power_ctx(data)
        if pw:
            sections["power"] = pw
    # the strap's cadence (RSC) before the camera's, when the test has it
    gv = _gait_view(with_strap_cadence(data.gait, data.samples, a.height_cm), a.height_cm)
    if "gait" in kinds and gv:
        sections["gait"] = _gait_ctx(data, gv)
        _add_second_view(sections, data, gv)
    tech = technique_report.technique_ctx(data.technique)
    if tech:
        sections["technique"] = tech
    # A test that was stopped before anything was measured gets a plain notice instead of empty pages.
    if "lactate" in sections and not _lactate_points(data):
        del sections["lactate"]
    empty_notice = t("report.empty.notice") if not sections else ""
    trend = _trend_ctx(data)
    followup = _followup_ctx(data)
    kind = data.kind
    test_names = [_kind_name(k) for k in data.test_kinds]
    summary = _summary_ctx(data, sections)
    if tech and not data.highlights and not summary["tiles"]:      # a bike fit or SkiErg test alone
        tiles, focus = technique_report.summary(tech)
        summary = {"tiles": tiles, "focus": data.focus or focus, "custom_focus": bool(data.focus)}
    business = b.business_name or b.coach_name or t("report.app_name")
    initials = "".join(w[0] for w in business.split()[:2]).upper() or "L"
    athlete_facts = []
    age = _age(a.birth_date, started)
    if age is not None:
        athlete_facts.append(t("report.cover.age", age=age))
    if a.height_cm:
        athlete_facts.append(f"{fmt_num(a.height_cm, 0)}\u00a0cm")
    if a.weight_kg:
        athlete_facts.append(f"{fmt_num(a.weight_kg, 1 if a.weight_kg % 1 else 0)}\u00a0kg")
    conditions = []
    incl = sorted({r.incline_pct for r in data.records}) if sport_profile(data.sport).has_incline else []
    if incl:
        conditions.append(t("report.cover.incline", v=fmt_num(incl[0])))
    if s.temperature_c is not None:
        conditions.append(f"{fmt_num(s.temperature_c, 0)}\u00a0°C")
    if "gait" in kinds and s.camera_source:
        conditions.append(s.camera_source)
    # A practice run with a simulated treadmill or heart rate is never taken for a real test.
    sim = sorted({d for x in [s, *(data.sessions or [])] for d in simulated.of_session(x)}, reverse=True)
    if sim:
        conditions.append(simulated.label(sim, data.sport))
    ctx = {
        "lang": language(),
        "sport": data.sport.value, "load_unit": sport_profile(data.sport).load_unit,
        "has_incline": sport_profile(data.sport).has_incline,
        # running: the pace (min/km) stands first everywhere and km/h after it
        "pace_first": sport_profile(data.sport).load_quantity == "speed",
        "no_own_test": (t("report.zones.no_own_test", sport=t(f"report.sport.{data.sport.value}"))
                        if data.sport is not Sport.RUN and not a.max_hr_for(data.sport)
                        and not (sections.get("lactate") or {}).get("lt2") and "max_hr" not in sections else ""),
        "empty_notice": empty_notice,
        "kind": kind,
        "title": _kind_name(kind),
        "test_names": test_names,
        "athlete": a, "athlete_facts": athlete_facts,
        "session": s, "protocol": s.protocol_name or ", ".join(test_names),
        "date": _date_span(data, started), "conditions": conditions,
        "branding": b, "business": business, "initials": initials,
        "logo": _data_uri_image(b.logo_path, 800) if b.logo_path else None,
        "accent": accent,
        "font_dir": (ASSETS / "fonts").as_uri(),
        "sections": sections,
        "summary": summary,
        "cover_zones": _cover_zones(sections.get("zones")),
        "collected": len(data.sessions or []) > 1,
        "kicker": " · ".join(test_names) if len(test_names) > 1 else t("report.cover.kicker"),
        "contents": _contents(sections, trend, followup),
        "trend": trend,
        "followup": followup,
        "coach_comment": data.coach_comment or s.notes,
        "footer_left_css": Markup(_css_str(
            _footer_line(b.business_name, b.coach_name, *b.contact.split(" · ")) or business)
            + ' "\\A" ' + _css_str(t("report.disclaimer.short"))),
        "footer_page_css": Markup(
            _css_str(t("report.footer.page") + " ") + " counter(page) " + _css_str(" " + t("report.footer.of") + " ")
            + " counter(pages) " + '"\\A" ' + _css_str(f"{a.name} · {date_short(started)}")),
    }
    return _env().get_template("report.html.j2").render(**ctx)


def render_tests_info(branding: Branding | None = None, language_code: str | None = None, sport: Any = None) -> str:
    """The sheet "Om testerna": every test of one sport (running when none is given) on one A4, to
    hand to a client. The texts are the ones the app shows on the page "Testerna" (assessments.catalog)."""
    old_lang = language()
    if language_code and language_code != old_lang:
        set_language(language_code)
    try:
        b = branding or Branding()
        business = b.business_name or b.coach_name or t("report.app_name")
        title = t("report.tests_info.title")
        ctx = {
            "lang": language(), "title": title, "tests": catalog.tests_info(sport),
            "sport": t(f"report.tests_info.sport.{sport_of(sport).value}"),
            "branding": b, "business": business,
            "initials": "".join(w[0] for w in business.split()[:2]).upper() or "L",
            "logo": _data_uri_image(b.logo_path, 800) if b.logo_path else None,
            "accent": _safe_color(b.accent_color), "font_dir": (ASSETS / "fonts").as_uri(),
            "footer_left_css": Markup(_css_str(
                _footer_line(b.business_name, b.coach_name, *b.contact.split(" · ")) or business)
                + ' "\\A" ' + _css_str(t("report.disclaimer.short"))),
            "footer_page_css": Markup(_css_str(title)),
        }
        return _env().get_template("tests_info.html.j2").render(**ctx)
    finally:
        if language() != old_lang:
            set_language(old_lang)


__all__ = ["Branding", "GAIT_METRICS", "MetricSpec", "ReportData", "collect", "pace_str", "render_html",
           "render_tests_info"]
