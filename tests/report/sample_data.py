"""Realistic fictional test data for the report: "Exempel Löpare", lactate, max HR, gait.

Key-frame images are drawn here with Pillow (a stick-figure runner with skeleton, angle arcs and a
ring on the fault), so the report can be rendered without camera or pose model.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from lopanalys.core.models import Athlete, Session, Sex, StepRecord, TestKind, TimeSample
from lopanalys.pose.types import Finding, GaitSummary, KeyFrame, Severity
from lopanalys.report.builder import Branding, ReportData

SPEEDS = [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
LACTATE = [1.1, 1.0, 1.2, 1.4, 1.9, 2.6, 3.9, 6.2]
HR = [131, 139, 147, 155, 163, 171, 179, 187]
RPE = [8, 9, 10, 12, 13, 15, 17, 19]


def athlete() -> Athlete:
    return Athlete(id=1, first_name="Exempel", last_name="Löpare", birth_date=date(1988, 4, 12), sex=Sex.MALE,
                   height_cm=181, weight_kg=74.5, max_hr=192, resting_hr=46)


def branding(logo: str | None = None) -> Branding:
    return Branding(business_name="Nordisk Löpcoaching", coach_name="Anna Berg",
                    contact="070-174 06 50 · anna@exempel.se", logo_path=logo)


def draw_logo(path: Path) -> Path:
    """A simple placeholder logo (transparent PNG)."""
    im = Image.new("RGBA", (640, 160), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((8, 8, 152, 152), fill=(34, 166, 153, 255))
    d.polygon([(50, 118), (80, 40), (98, 84), (112, 62), (130, 118)], fill=(255, 255, 255, 255))
    font = _font(64, bold=True)
    d.text((180, 34), "NORDISK", fill=(23, 32, 42, 255), font=font)
    d.text((182, 96), "LÖPCOACHING", fill=(95, 107, 120, 255), font=_font(40))
    im.save(path)
    return path


def lactate_records(with_gait: bool = False) -> list[StepRecord]:
    out = []
    for i, (s, la, hr, rpe) in enumerate(zip(SPEEDS, LACTATE, HR, RPE)):
        out.append(StepRecord(index=i, speed_kmh=s, incline_pct=1.0, duration_s=240, hr_end=hr, hr_mean=hr - 4,
                              lactate_mmol=la, rpe=rpe))
    return out


def _fit_curve() -> list[dict]:
    # A smooth monotone curve through the measured points, as a fitted model would give.
    from scipy.interpolate import PchipInterpolator

    f = PchipInterpolator(SPEEDS, [1.05, 1.05, 1.15, 1.4, 1.9, 2.6, 3.9, 6.2])
    return [{"speed_kmh": round(9.0 + k * 0.1, 2), "lactate": round(float(f(9.0 + k * 0.1)), 3)} for k in range(71)]


def lactate_result(shift: float = 0.0) -> dict:
    """LactateResult.to_dict() as the assessments agent is expected to produce it."""
    lt1 = {"method": "Baslinje + 0,5 mmol/l", "lactate": 1.6, "speed_kmh": 12.4 + shift, "hr": 159, "pct_max_hr": 82.8}
    lt2 = {"method": "Modifierad Dmax", "lactate": 3.4, "speed_kmh": 14.6 + shift, "hr": 176, "pct_max_hr": 91.7}
    return {
        "points": [{"speed_kmh": s + shift, "lactate": la, "hr": hr} for s, la, hr in zip(SPEEDS, LACTATE, HR)],
        "curve": [{"speed_kmh": p["speed_kmh"] + shift, "lactate": p["lactate"]} for p in _fit_curve()],
        "baseline": 1.1,
        "lt1": lt1,
        "lt2": lt2,
        "methods": {
            "Baslinje + 0,5 mmol/l": {**lt1},
            "Log-log": {"lactate": 1.5, "speed_kmh": 12.1 + shift, "hr": 157, "pct_max_hr": 81.8},
            "Modifierad Dmax": {**lt2},
            "OBLA 4 mmol/l": {"lactate": 4.0, "speed_kmh": 15.1 + shift, "hr": 180, "pct_max_hr": 93.8},
            "Dmax": {"lactate": 3.1, "speed_kmh": 14.4 + shift, "hr": 175, "pct_max_hr": 91.1},
        },
    }


def zones() -> list[dict]:
    return [
        {"name": "Återhämtning", "purpose": "Lugn jogg, nedvarvning", "hr_low": None, "hr_high": 145,
         "speed_low": None, "speed_high": 10.5, "lactate_low": None, "lactate_high": 1.2},
        {"name": "Lugn distans", "purpose": "Grunden: långpass och vardagsmil", "hr_low": 145, "hr_high": 159,
         "speed_low": 10.5, "speed_high": 12.4, "lactate_low": 1.2, "lactate_high": 1.6},
        {"name": "Tempo", "purpose": "Maratonfart, längre tempopass", "hr_low": 159, "hr_high": 170,
         "speed_low": 12.4, "speed_high": 13.8, "lactate_low": 1.6, "lactate_high": 2.6},
        {"name": "Tröskel", "purpose": "Tröskelintervaller, 8–15 min", "hr_low": 170, "hr_high": 178,
         "speed_low": 13.8, "speed_high": 14.9, "lactate_low": 2.6, "lactate_high": 3.8},
        {"name": "VO₂max", "purpose": "Korta intervaller, 2–5 min", "hr_low": 178, "hr_high": None,
         "speed_low": 14.9, "speed_high": None, "lactate_low": 3.8, "lactate_high": None},
    ]


def max_hr_samples() -> list[TimeSample]:
    """Ramp: 3 min at 10 km/h, +0.5 km/h per minute, exhausted at 12:40, 2 min recovery walk."""
    out = []
    stop = 760
    for ts in range(0, stop + 121):
        if ts <= stop:
            speed = 10.0 if ts < 180 else 10.0 + 0.5 * ((ts - 180) // 60 + 1)
            hr = 150 + 42 * (1 - math.exp(-(ts - 30) / 260)) if ts > 30 else 110 + ts * 1.3
            hr = min(hr, 191 + 0.002 * ts)
            if ts > 700:
                hr = min(192, hr + (ts - 700) * 0.02)
        else:
            speed = 5.0
            hr = 192 - 34 * (1 - math.exp(-(ts - stop) / 35)) - 0.1 * (ts - stop)
        jitter = 0.6 * math.sin(ts / 7.0) + 0.4 * math.sin(ts / 2.3)
        out.append(TimeSample(t_s=float(ts), hr=int(round(hr + jitter)), speed_kmh=speed, incline_pct=1.0))
    return out


def max_hr_result(peak: int = 192) -> dict:
    return {"peak": peak, "time": 752, "plateau": True, "recovery_1min": 31, "likely_true_max": True,
            "stop_time": 760}


# ---------------------------------------------------------------- gait


def gait_summary(**over) -> GaitSummary:
    d = dict(t_from=0, t_to=60, steps=164, cadence_spm=164, ground_contact_ms=248, vertical_oscillation_cm=9.6,
             overstride_cm=13.2, shin_angle_deg=8.5, knee_flex_contact_deg=17, knee_flex_midstance_deg=41,
             trunk_lean_deg=5.5, hip_extension_toe_off_deg=14, foot_strike="heel", pelvic_drop_deg=6.1,
             knee_valgus_deg=3.0, step_width_cm=7, asymmetry={"ground_contact_ms": 6.4, "overstride_cm": 3.1})
    d.update(over)
    return GaitSummary(**d)


def findings() -> list[Finding]:
    return [
        Finding("overstride", Severity.FIX, "Översteg",
                "Hälen landar 13 cm framför höften. Det bromsar varje steg och ger mer belastning på knä och skenben.",
                "Tänk ”sätt ner foten under dig”, och korta steget lite i stället för att sträcka ut det.",
                13.2, "cm", "under 10 cm", frame_index=412, highlight=["l_heel", "l_hip"]),
        Finding("low_cadence", Severity.WATCH, "Låg kadens",
                "164 steg i minuten är lite lågt vid den här farten. Med fler och kortare steg blir översteget mindre.",
                "Höj kadensen med 5 %, till ungefär 172 steg/min, med en metronom i klockan.",
                164, "spm", "170–190 steg/min"),
        Finding("pelvic_drop", Severity.WATCH, "Höftfall",
                "Bäckenet sjunker 6° på motsatt sida när du står på höger ben. Det tyder på svag sätesmuskel.",
                "Tänk ”höga höfter” och stå stadigt på stödbenet.",
                6.1, "deg", "under 5°", frame_index=530, highlight=["l_hip", "r_hip"]),
        Finding("trunk_lean", Severity.OK, "Bålens lutning",
                "Lätt framåtlutning från höften, precis som det ska vara.", "", 5.5, "deg", "3–10°"),
    ]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    base = Path(__file__).resolve().parents[2] / "src/lopanalys/report/assets/fonts"
    for name in (("SourceSansPro-Bold.ttf" if bold else "SourceSansPro-Semibold.ttf"), "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(str(base / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_keyframe(path: Path, label: str, phase: float, ring: str | None = None, rear: bool = False) -> Path:
    """A 1600x1200 placeholder still: treadmill scene, runner skeleton, an angle and an optional ring."""
    w, h = 1600, 1200
    im = Image.new("RGB", (w, h), (28, 33, 40))
    d = ImageDraw.Draw(im)
    # Background: wall gradient, treadmill deck and rails.
    for y in range(h):
        c = int(30 + 18 * y / h)
        d.line([(0, y), (w, y)], fill=(c, c + 4, c + 9))
    d.rectangle((140, 980, 1460, 1030), fill=(18, 20, 24))
    d.rectangle((140, 975, 1460, 985), fill=(70, 78, 88))
    d.line([(1300, 980), (1380, 560)], fill=(90, 98, 110), width=14)
    d.line([(1380, 560), (1460, 560)], fill=(90, 98, 110), width=14)
    # Runner (side view) skeleton in pixels.
    cx = 760
    s = math.sin(phase)
    pts = {
        "head": (cx + 40, 250), "neck": (cx + 25, 340), "hip": (cx - 5, 600),
        "l_knee": (cx + 90 + 60 * s, 780), "l_ankle": (cx + 150 + 90 * s, 960), "l_heel": (cx + 130 + 90 * s, 975),
        "l_toe": (cx + 200 + 90 * s, 980), "r_knee": (cx - 60 - 40 * s, 760), "r_ankle": (cx - 190 - 50 * s, 860),
        "r_toe": (cx - 150 - 50 * s, 905), "elbow_f": (cx + 95, 470), "wrist_f": (cx + 160, 420),
        "elbow_b": (cx - 50, 480), "wrist_b": (cx - 30, 560),
    }
    if rear:
        pts.update({"l_knee": (cx - 70, 790), "l_ankle": (cx - 80, 965), "r_knee": (cx + 80, 760),
                    "r_ankle": (cx + 95, 880), "hip": (cx, 600), "neck": (cx, 340), "head": (cx, 250)})
    # Body silhouette for some realism.
    body = (64, 72, 84)
    d.ellipse((pts["head"][0] - 55, pts["head"][1] - 65, pts["head"][0] + 55, pts["head"][1] + 65), fill=body)
    for a, b, wdt in (("neck", "hip", 120), ("hip", "l_knee", 70), ("l_knee", "l_ankle", 52),
                      ("hip", "r_knee", 70), ("r_knee", "r_ankle", 52)):
        d.line([pts[a], pts[b]], fill=body, width=wdt)
    # Skeleton.
    bones = [("head", "neck"), ("neck", "hip"), ("hip", "l_knee"), ("l_knee", "l_ankle"), ("hip", "r_knee"),
             ("r_knee", "r_ankle"), ("neck", "elbow_f"), ("elbow_f", "wrist_f"), ("neck", "elbow_b"),
             ("elbow_b", "wrist_b")]
    if not rear:
        bones += [("l_ankle", "l_heel"), ("l_heel", "l_toe"), ("r_ankle", "r_toe")]
    for a, b in bones:
        d.line([pts[a], pts[b]], fill=(34, 166, 153), width=9)
    for k, p in pts.items():
        if k == "head":
            continue
        d.ellipse((p[0] - 13, p[1] - 13, p[0] + 13, p[1] + 13), fill=(255, 255, 255), outline=(34, 166, 153), width=4)
    # Vertical reference through the hip and an angle label at the knee.
    hx = pts["hip"][0]
    for y in range(560, 990, 28):
        d.line([(hx, y), (hx, y + 14)], fill=(242, 169, 59), width=4)
    f = _font(46, bold=True)
    kx, ky = pts["l_knee"]
    d.rounded_rectangle((kx + 30, ky - 40, kx + 190, ky + 30), radius=12, fill=(20, 26, 33))
    d.text((kx + 46, ky - 36), f"{int(160 + 10 * s)}°", fill=(233, 238, 243), font=f)
    if ring:
        rx, ry = pts[ring]
        d.ellipse((rx - 85, ry - 85, rx + 85, ry + 85), outline=(229, 72, 77), width=10)
        if not rear:
            d.line([(hx, pts["l_heel"][1]), pts["l_heel"]], fill=(229, 72, 77), width=6)
            d.text(((hx + pts["l_heel"][0]) / 2 - 50, pts["l_heel"][1] - 80), "13 cm", fill=(229, 72, 77), font=f)
    d.text((40, 30), label, fill=(233, 238, 243), font=_font(52, bold=True))
    im.save(path)
    return path


def keyframes(dir_: Path) -> list[KeyFrame]:
    dir_.mkdir(parents=True, exist_ok=True)
    specs = [("Isättning", 0.9, "l_heel", False, ["overstride"]), ("Mittstöd", 0.1, None, False, []),
             ("Frånskjut", -0.9, None, False, []), ("Bakifrån: höger stödben", 0.0, "hip", True, ["pelvic_drop"])]
    out = []
    for i, (label, ph, ring, rear, fcodes) in enumerate(specs):
        p = draw_keyframe(dir_ / f"kf{i}.png", label, ph, ring, rear)
        out.append(KeyFrame(label=label, frame_index=400 + 40 * i, t=13.4 + 0.11 * i, image_path=str(p),
                            findings=fcodes))
    return out


class FakeGait:
    """Stands in for pose.offline.GaitAnalysis."""

    def __init__(self, summary: GaitSummary, findings: list[Finding], keyframes: list[KeyFrame]):
        self.summary, self.findings, self.keyframes, self.steps = summary, findings, keyframes, []


def gait_records() -> list[StepRecord]:
    out = []
    for i, (v, cad, osd, vo) in enumerate([(10.0, 160, 14.5, 9.9), (12.0, 164, 13.2, 9.6), (14.0, 171, 10.4, 9.0)]):
        out.append(StepRecord(index=i, speed_kmh=v, incline_pct=1.0, duration_s=60, hr_end=140 + 12 * i,
                              gait=gait_summary(cadence_spm=cad, overstride_cm=osd, vertical_oscillation_cm=vo,
                                                ground_contact_ms=262 - 14 * i).to_dict()))
    return out


# ---------------------------------------------------------------- whole reports


def session(kind: TestKind, when: datetime = datetime(2026, 11, 13, 9, 30), name: str = "",
            ramp: bool = False) -> Session:
    """ramp: lactate steps followed by a max HR ramp (the combined protocol)."""
    names = {TestKind.LACTATE_STEP: "Laktattrappa 9–16 km/h, 4 min", TestKind.MAX_HR: "Maxpulstrappa",
             TestKind.GAIT: "Löpteknik 10–12–14 km/h"}
    return Session(id=10, athlete_id=1, kind=kind, started=when, protocol_name=name or names[kind],
                   protocol={"open_ended": True} if ramp else {},
                   treadmill_mode="sim", camera_source="GoPro HERO8 Black (USB)", temperature_c=18)


def previous_lactate() -> ReportData:
    prev = lactate_result(shift=-0.4)
    prev["lt1"]["hr"], prev["lt2"]["hr"] = 161, 177
    return ReportData(athlete=athlete(), session=session(TestKind.LACTATE_STEP, datetime(2026, 5, 20, 9, 0)),
                      lactate=prev, max_hr=max_hr_result(peak=190) | {"recovery_1min": 27})


def lactate_report(tmp: Path, previous: bool = True) -> ReportData:
    return ReportData(athlete=athlete(), session=session(TestKind.LACTATE_STEP), records=lactate_records(),
                      lactate=lactate_result(), zones=zones(), branding=branding(str(draw_logo(tmp / "logo.png"))),
                      previous=previous_lactate() if previous else None,
                      coach_comment="Fin utveckling sedan i våras: tröskeln har flyttats upp nästan en halv km/h. "
                                    "Nu bygger vi vidare med mer lugn distans och ett tröskelpass i veckan.")


def hr_zones() -> list[dict]:
    """Zones from max and resting HR only (assessments.zones.from_heart_rate)."""
    edges = [None, 145, 159, 170, 178, None]
    names = [("Återhämtning", "Lugn jogg, nedvarvning"), ("Lugn distans", "Långpass och vardagsmil"),
             ("Tempo", "Maratonfart"), ("Tröskel", "Tröskelintervaller"), ("VO₂max", "Korta intervaller")]
    return [{"name": n, "purpose": p, "hr_low": edges[i], "hr_high": edges[i + 1], "speed_low": None,
             "speed_high": None, "lactate_low": None, "lactate_high": None} for i, (n, p) in enumerate(names)]


def max_hr_report(tmp: Path) -> ReportData:
    return ReportData(athlete=athlete(), session=session(TestKind.MAX_HR), samples=max_hr_samples(),
                      max_hr=max_hr_result(), zones=hr_zones(), branding=branding())


def gait_report(tmp: Path, previous: bool = True) -> ReportData:
    prev = None
    if previous:
        prev = ReportData(athlete=athlete(), session=session(TestKind.GAIT, datetime(2026, 5, 20, 9, 0)),
                          gait=FakeGait(gait_summary(cadence_spm=158, overstride_cm=15.8, pelvic_drop_deg=7.0),
                                        [], []))
    return ReportData(athlete=athlete(), session=session(TestKind.GAIT), records=gait_records(),
                      gait=FakeGait(gait_summary(), findings(), keyframes(tmp / "kf")),
                      branding=branding(str(draw_logo(tmp / "logo.png"))), previous=prev)


def combined_report(tmp: Path) -> ReportData:
    g = gait_report(tmp, previous=False)
    return ReportData(athlete=athlete(), session=session(TestKind.LACTATE_STEP, name="Testdag: laktat, maxpuls och löpteknik", ramp=True),
                      records=lactate_records(), lactate=lactate_result(), zones=zones(), max_hr=max_hr_result(),
                      samples=max_hr_samples(), gait=g.gait, branding=g.branding, previous=previous_lactate())


def real_combined_report(tmp: Path) -> ReportData:
    """The same test day, but with the results computed by lopanalys.assessments (real dict formats)."""
    from lopanalys.assessments import lactate as alac, max_hr as amax, zones as azones

    recs = lactate_records()
    lac = alac.analyze(recs, max_hr=192)
    mx = amax.analyze(max_hr_samples(), end_t_s=760, age=38, final_rpe=19)  # the app passes the athlete's age
    z = azones.from_lactate(lac, max_hr=192, resting_hr=46)
    g = gait_report(tmp, previous=False)
    prev_recs = [StepRecord(index=r.index, speed_kmh=r.speed_kmh - 0.5, incline_pct=1.0, duration_s=240,
                            hr_end=(r.hr_end or 0) + 2, lactate_mmol=r.lactate_mmol) for r in recs]
    prev = ReportData(athlete=athlete(), session=session(TestKind.LACTATE_STEP, datetime(2026, 5, 20, 9, 0)),
                      records=prev_recs, lactate=alac.analyze(prev_recs, max_hr=190).to_dict())
    return ReportData(athlete=athlete(), session=session(TestKind.LACTATE_STEP, name="Testdag", ramp=True), records=recs,
                      lactate=lac.to_dict(), zones=[x.to_dict() for x in z], max_hr=mx.to_dict(),
                      samples=max_hr_samples(), gait=g.gait, branding=g.branding, previous=prev)
