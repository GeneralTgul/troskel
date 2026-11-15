"""The report of Puls- och fartzoner: the cover with heart rate against speed and the zone
bar with speeds, the test's own page, the marking "speed from heart rate, not from lactate", and the
collected report."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from lopanalys.assessments import hr_zones, library, zone_systems
from lopanalys.core.i18n import set_language
from lopanalys.core.models import StepRecord, TestKind, TimeSample
from lopanalys.report.builder import ReportData, collect, render_html

from . import sample_data as sd
from .test_report_cover import _cover
from .test_report_extras import lactate_data

SPEEDS = (8.0, 9.0, 10.0, 11.0, 12.0)
STEP_HR = (128, 137, 146, 155, 165)


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from lopanalys.report import pdf

    pdf._ensure_app()
    return pdf


def _test(last_rise: float = 0.0):
    """Records and samples of one test: five settled steps (the last may still rise) and a ramp."""
    recs, smp, t0 = [], [], 610.0
    for i, (v, h) in enumerate(zip(SPEEDS, STEP_HR)):
        rise = last_rise if i == len(SPEEDS) - 1 else 0.0
        for k in range(240):
            hr = h - 9 * max(0, 150 - k) / 150 + (rise * (k - 210) / 60 if k >= 180 else -rise / 2)
            smp.append(TimeSample(t0 + k + 1, int(round(hr)), v, 1.0))
        t0 += 240
        recs.append(StepRecord(1 + i, v, 1.0, 240.0, hr_end=h))
    for j in range(8):
        v, h = 12.5 + 0.5 * j, min(188, 168 + 3 * j)
        smp += [TimeSample(t0 + k + 1, h, v, 1.0) for k in range(60)]
        t0 += 60
        recs.append(StepRecord(6 + j, v, 1.0, 60.0, hr_end=h))
    smp += [TimeSample(t0 + k + 1, 188 - k // 2, 5.0, 0.0) for k in range(65)]
    return recs, smp, t0


def hr_zones_data(system: str = "garmin", last_rise: float = 0.0) -> ReportData:
    recs, smp, end = _test(last_rise)
    p = library.hr_zones(8.0)
    rel = hr_zones.analyze(recs, smp, end_t_s=end, steps=hr_zones.step_indexes(p.to_dict()))
    zones = hr_zones.zone_speeds(zone_systems.nominal_zones(188, system=system), rel)
    s = replace(sd.session(TestKind.HR_ZONES, name=p.name), protocol=p.to_dict())
    return ReportData(athlete=replace(sd.athlete(), max_hr=None), session=s, records=recs, samples=smp,
                      zones=[z.to_dict() for z in zones], zone_system={"key": system, "name": "5 zoner · Garmin", "note": ""},
                      max_hr={"peak": 188, "time": end - 3, "plateau": True, "recovery_1min": 30,
                              "likely_true_max": True, "stop_time": end},
                      hr_speed=dict(rel.to_dict(), quality_text=hr_zones.quality_text(rel)), branding=sd.branding())


def test_cover_has_the_chart_the_zone_bar_with_speed_and_the_marking():
    data = hr_zones_data()
    assert data.kinds == ["hr_zones", "max_hr"] and data.kind == "hr_zones" and data.test_kinds == ["hr_zones"]
    html = render_html(data)
    cover = _cover(html)
    assert "Puls- och fartzoner" in cover and "<h1>Puls- och fartzoner</h1>" in html
    assert "Puls mot fart" in cover and "Maxpuls" in cover and "188" in cover
    assert "Lugn fart (Z2)" in cover and "Hård fart (Z4)" in cover
    assert "fart ur puls, inte ur laktat" in cover and "uppskattade ur maxpuls" in cover
    assert cover.count("km/h") >= 6                       # the tiles and every zone of the bar that has a speed
    assert "Spring lugnt på puls" in cover
    assert "LT1" not in cover and "mmol" not in cover
    assert 'class="cz-speed' in html and "report." not in cover and "assessments." not in cover


def test_own_page_zones_page_and_max_page():
    html = render_html(hr_zones_data())
    for text in ("Stabil puls", "Stiger sista minuten", "Samband (R²)", "Uppmätta farter", "8,0–12,0",
                 "Bra samband: 5 steg", "Uppskattat utan laktat", "Farterna gäller löpband med 1 % lutning",
                 "Zonernas gränser är procent av din maxpuls", "Fart ur puls, inte ur laktat",
                 "Pulsen under testet", "Tempo min/km"):
        assert text in html, text
    assert html.index("Puls mot fart") < html.index("Dina träningszoner") < html.index("Pulsen under testet")
    assert "Zonerna bygger på dina trösklar" not in html and "Laktat mmol/l" not in html
    assert "≈" in html                                    # a border outside 8-12 km/h is marked
    assert html.count("<svg") >= 3                        # cover chart, the page's chart, the max HR curve


def test_unsettled_step_is_said_in_the_report():
    html = render_html(hr_zones_data(last_rise=8.0))
    assert "Pulsen stabiliserades inte på 12,0 km/h" in html and "inte stabil, väger mindre" in html


def test_english_report_has_no_swedish_or_missing_keys():
    set_language("en")
    html = render_html(hr_zones_data())
    assert "Speed from heart rate, not from lactate" in html and "Heart rate against speed" in html
    assert "report." not in _cover(html) and "assessments.hrzones" not in html and "Stabil puls" not in html


def test_pdf_cover_is_one_page(qt, tmp_path):
    data = hr_zones_data()
    pages = qt.pdf_page_images(qt.html_to_pdf(render_html(data), tmp_path / "hz.pdf"), dpi=30)
    assert len(pages) == 4                                # cover, heart rate and speed, zones, max HR
    data.coach_comment = "Bra genomfört. Håll de lugna passen i zon 2 och kör testet igen om tre månader."
    assert len(qt.pdf_page_images(qt.html_to_pdf(render_html(data), tmp_path / "hz2.pdf"), dpi=30)) == 4


def test_collected_report_takes_the_test_and_lactate_zones_win():
    hz = hr_zones_data()
    alone = collect([hz])
    assert alone.hr_speed and alone.kinds == ["hr_zones", "max_hr"]
    assert "Puls mot fart" in render_html(alone)
    lac = replace(lactate_data(), session=replace(lactate_data().session, started=datetime(2026, 11, 12, 9, 0)))
    both = collect([lac, hz])
    assert both.kinds == ["lactate", "hr_zones", "max_hr"] and both.kind == "combined"
    assert both.zones == lac.zones and both.max_hr == hz.max_hr and both.hr_speed == hz.hr_speed
    html = render_html(both)
    assert "Laktat och puls" in html and "Puls mot fart" in html and "Zonerna bygger på dina trösklar" in html
