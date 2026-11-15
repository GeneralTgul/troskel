"""Report: the cover page, which must give a professional impression."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.core.models import TestKind
from lopanalys.report.builder import Branding, ReportData, collect, render_html

from . import sample_data as sd
from .test_report_extras import lactate_data, max_only_data


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _cover(html: str) -> str:
    """The text of the first page: everything before the first section."""
    body = html[html.index('<div class="cover">'):]
    body = body[:body.find('<section class="section') if '<section class="section' in body else len(body)]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def test_lactate_cover_has_identity_test_and_key_results():
    data = lactate_data()
    html = render_html(data)
    text = _cover(html)
    lt1, lt2 = data.lactate["lt1"], data.lactate["lt2"]
    for s in ("Nordisk Löpcoaching", "Anna Berg", "070-174 06 50", "Laktattrappa", "Exempel Löpare",
              "13 november 2026", "Testtyp", "Protokoll", "Laktattrappa 9–16 km/h, 4 min",
              "Det viktigaste", "Aerob tröskel (LT1)", "Tröskelfart (LT2)", "Uppskattat VO2max", "ml/kg/min",
              "Laktat och puls mot fart", "Dina pulszoner", "Att jobba med"):
        assert s in text, s
    for x in (lt1, lt2):   # speed and heart rate of both thresholds
        assert f"{x['speed_kmh']:.1f}".replace(".", ",") in text and f"puls {round(x['hr'])}" in text
    assert "troligen" in text                       # VO2max always with its interval: it is an estimate
    assert html.count('class="tile"') == 4
    assert 'class="cz"' in html and "#2F80ED" in html   # zone bar in Garmin's colours


def test_max_hr_cover_has_no_lactate_and_marks_estimated_zones():
    text = _cover(render_html(max_only_data()))
    assert "Maxpulstest" in text and "Maxpuls" in text and "192" in text
    assert "uppskattade ur maxpuls" in text and "Uppskattat VO2max" in text
    assert "laktat" not in text.lower().replace("laktattrappa", "")   # lactate is not measured


def test_gait_cover_without_branding_or_logo(tmp_path):
    data = replace(sd.gait_report(tmp_path, previous=False), branding=Branding())
    html = render_html(data)
    text = _cover(html)
    assert "Löpteknik" in text and "Kadens" in text and "Översteg" in text and "Att jobba med" in text
    assert 'class="mono"' in html and "<img" not in html[:html.index('<section class="hero">')]
    assert "Coach" not in text                       # no coach name: no empty field
    assert "Dina pulszoner" not in text              # no heart rate zones in a gait test


def test_combined_cover_lists_all_tests_and_keeps_four_tiles(tmp_path):
    data = sd.real_combined_report(tmp_path)
    data.coach_comment = "Fin utveckling sedan i våras."
    html = render_html(data)
    text = _cover(html)
    assert "Laktattrappa · Maxpulstest · Löpteknik" in text and "Testrapport" in text
    assert html.count('class="tile"') == 4 and "Kadens" in text and "Maxpuls" in text
    assert "Coachens kommentar" in text
    # chart and comment: the comment takes the place of the points (they are on the later pages)
    assert 'class="cover-chart"' in html and '<div class="no">' not in html[:html.index('<section class="section')]


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from lopanalys.report import pdf

    pdf._ensure_app()
    return pdf


def test_pdf_cover_stays_one_page(qt, tmp_path):
    """The combined report with a coach comment has the most on the cover: it still fits on page 1."""
    data = sd.combined_report(tmp_path)
    data.coach_comment = sd.lactate_report(tmp_path).coach_comment
    html = render_html(data)
    with_cover = qt.pdf_page_images(qt.html_to_pdf(html, tmp_path / "a.pdf"), dpi=30)
    data.coach_comment = ""
    without = qt.pdf_page_images(qt.html_to_pdf(render_html(data), tmp_path / "b.pdf"), dpi=30)
    assert len(with_cover) == len(without)
    # the sentence on which max HR the zones use stands beside the heading when it fits; a long one
    # (the test did not reach max) takes its own line, and the cover is still one page
    assert 'class="cz-basis-inline"' in html
    long = sd.combined_report(tmp_path)
    long.max_hr = dict(long.max_hr, peak=184, likely_true_max=False)
    html_long = render_html(long)
    assert 'class="cz-basis"' in html_long and "nådde troligen inte max" in html_long
    assert len(qt.pdf_page_images(qt.html_to_pdf(html_long, tmp_path / "c.pdf"), dpi=30)) == len(without)


# ---------------------------------------------------------------- collected report and cover chart


def _day(d: ReportData, hour: int, minute: int = 0) -> ReportData:
    return replace(d, session=replace(d.session, started=datetime(2026, 11, 13, hour, minute)))


def _collected(tmp_path) -> ReportData:
    b = sd.branding()
    return collect([replace(_day(sd.gait_report(tmp_path, previous=False), 10, 50), branding=b),
                    replace(_day(lactate_data(), 9), branding=b), replace(_day(max_only_data(), 10, 15), branding=b)])


def test_collect_merges_separate_tests_into_one_report(tmp_path):
    data = _collected(tmp_path)
    assert data.kinds == ["lactate", "max_hr", "gait"] and data.kind == "combined"
    assert data.session.kind == TestKind.LACTATE_STEP and len(data.sessions) == 3
    html = render_html(data)
    text = _cover(html)
    assert "Laktattrappa · Maxpulstest · Löpteknik" in text and "Testerna" in text
    assert "Laktattrappa 9–16 km/h, 4 min · Maxpulstrappa · Löpteknik 10–12–14 km/h" in text
    assert "GoPro HERO8 Black (USB)" in text            # the camera of the gait test
    for s in ("Aerob tröskel (LT1)", "Tröskelfart (LT2)", "Maxpuls", "Kadens"):
        assert s in text, s
    body = re.sub(r"<[^>]+>", " ", html)
    for s in ("Stegen i testet", "Pulsen under testet", "Mätvärden"):   # each test keeps its own pages
        assert s in body, s


def test_collect_over_two_days_shows_both_dates(tmp_path):
    data = _collected(tmp_path)
    data.sessions[-1] = replace(data.sessions[-1], started=datetime(2026, 11, 14, 9, 0))
    assert "13 november 2026 – 14 november 2026" in _cover(render_html(data))


def test_cover_chart_follows_the_test():
    lac, mx = render_html(lactate_data()), render_html(max_only_data())
    cover = lambda h: h[h.index('<div class="cover">'):h.index('<section class="section')]
    assert 'class="cover-chart"' in cover(lac) and "Laktat och puls mot fart" in cover(lac)
    assert 'class="cover-chart"' in cover(mx) and "Puls under testet" in cover(mx)
    # the cover's copy of a chart has its own ids
    assert not set(re.findall(r'id="([^"]+)"', cover(lac))) & set(re.findall(r'id="([^"]+)"', lac[lac.index('<section class="section'):]))
    # the chart takes the place of the contents line
    assert "I rapporten" not in _cover(lac)


def test_gait_cover_has_no_chart_but_the_contents(tmp_path):
    text_html = render_html(sd.gait_report(tmp_path))
    assert 'class="cover-chart"' not in text_html and "I rapporten" in _cover(text_html)


class _Store:
    """The few Store calls render_collected_report makes, in memory."""

    def __init__(self, records, samples, gait):
        self._r, self._s, self._g = records, samples, gait

    def records(self, sid):
        return self._r.get(sid, [])

    def samples(self, sid):
        return self._s.get(sid, [])

    def gait(self, sid):
        return self._g.get(sid)


def test_app_builds_the_collected_report_from_saved_tests(tmp_path):
    from lopanalys.ui import services

    g = sd.gait_report(tmp_path, previous=False).gait
    gait = {"summary": g.summary.to_dict(), "findings": [{**vars(f), "severity": f.severity.value} for f in g.findings],
            "keyframes": []}
    lac = replace(sd.session(TestKind.LACTATE_STEP, datetime(2026, 11, 13, 9, 0)), id=1)
    mx = replace(sd.session(TestKind.MAX_HR, datetime(2026, 11, 13, 10, 15)), id=2,
                 result={"exhaustion_t_s": 760})
    gt = replace(sd.session(TestKind.GAIT, datetime(2026, 11, 13, 10, 50)), id=3, result={"coach_comment": "Bra jobbat."})
    store = _Store({1: sd.lactate_records(), 3: sd.gait_records()}, {2: sd.max_hr_samples()}, {3: gait})
    html = services.render_collected_report(store, sd.athlete(), [gt, mx, lac],
                                            {"business": "Nordisk Löpcoaching", "coach": "Anna Berg"})
    text = _cover(html)
    assert "Testrapport" in text and "Laktattrappa · Maxpulstest · Löpteknik" in text
    assert "Aerob tröskel (LT1)" in text and "Maxpuls" in text and "Kadens" in text
    assert "Bra jobbat." in text and 'class="cover-chart"' in html
