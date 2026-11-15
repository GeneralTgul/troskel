"""The printed training plan: HTML content in both languages and the PDF chain."""
from __future__ import annotations

import re

import pytest

from lopanalys.core.i18n import language, set_language
from lopanalys.core.zone_colors import GARMIN
from lopanalys.plan import RUNNING, Capacity, build_plan
from lopanalys.plan.demo import demo_plan, main
from lopanalys.report.builder import Branding
from lopanalys.report.plan_builder import overview_svg, render_plan_html

from . import cases as c

BRAND = Branding(business_name="Nordisk Löpcoaching", coach_name="Anna Berg", contact="070-174 06 50")


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", "", html, flags=re.S)))


def test_cover_weeks_and_targets():
    plan = build_plan(c.CASES["half_12"])
    html = render_plan_html(plan, BRAND)
    text = _text(html)
    assert "Halvmaran" in text and "under 1:45" in text and "Seedningslopp" in text and "Nordisk Löpcoaching" in text
    assert "Laktattrappa · 13 november 2026" in text
    assert html.count('<section class="week">') == 12 and all(f"Vecka {i} " in text for i in range(1, 13))
    assert "170–174 slag/min · 4:21–4:10 min/km" in text          # targets from the test
    for name in ("Grund", "Uppbyggnad", "Topp", "Nedtrappning", "Tröskelintervaller", "Långpass", "Vila"):
        assert name in text, name
    assert all(col in html for col in GARMIN)                       # the watch's zone colours
    assert not re.search(r"\bplan\.[a-z_]+\.[a-z_.0-9]+", text), "an untranslated key in the printout"
    assert "{{" not in html and "None" not in text


def test_english_and_language_is_restored():
    html = render_plan_html(build_plan(c.CASES["no_goal_12"]), BRAND, "en")
    text = _text(html)
    assert language() == "sv"
    assert "Training plan" in text and "Week 12" in text and "Test week" in text and "Threshold intervals" in text
    assert "bpm" in text and "slag/min" not in text and "Vecka" not in text
    assert not re.search(r"\bplan\.[a-z_]+\.[a-z_.0-9]+", text)


def test_without_a_test_and_with_estimated_zones():
    text = _text(render_plan_html(build_plan(c.request(weeks=12, capacity=()))))
    assert "Inget test" in text and "slag/min" not in text.split("Reglerna bakom schemat")[0].split("Vecka 1 ")[0]
    assert "ansträngning 7 av 10" in text
    from lopanalys.assessments.zone_systems import Anchors
    text = _text(render_plan_html(build_plan(c.request(weeks=12, capacity=(Capacity(RUNNING, Anchors(max_hr=190)),)))))
    assert "uppskattade ur maxpuls" in text and "152–171 slag/min" in text


def test_overview_chart_has_a_bar_per_week_and_a_band_per_block():
    plan = build_plan(c.CASES["half_12"])
    svg = overview_svg(plan)
    assert svg.count("<clipPath") == 12 and svg.count('rx="9"') == len(plan.blocks)
    assert ">mål<" in svg and ">test<" in svg and ">lopp<" in svg
    assert overview_svg(build_plan(c.CASES["no_goal_12"])).count(">lätt<") == 2
    year = overview_svg(build_plan(c.CASES["three_days_52"]))      # a long plan stays readable: fewer labels
    assert year.count("<clipPath") == 52 and ">lätt<" not in year


# ---------------------------------------------------------------- Qt: the PDF


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from lopanalys.report import pdf

    pdf._ensure_app()
    return pdf


def test_pdf_has_cover_guide_and_a_page_per_week(qt, tmp_path):
    plan = demo_plan(12)
    out = qt.html_to_pdf(render_plan_html(plan, BRAND), tmp_path / "plan.pdf")
    assert out.read_bytes()[:5] == b"%PDF-"
    pages = qt.pdf_page_images(out, dpi=40)
    assert 12 + 2 <= len(pages) <= 12 + 3          # cover, one or two guide pages, twelve week sheets
    assert abs(pages[0].height() / pages[0].width() - 297 / 210) < 0.01
    for img in (pages[0], pages[-1]):
        dark = sum(1 for y in range(0, img.height(), 4) for x in range(0, img.width(), 4)
                   if img.pixelColor(x, y).lightness() < 100)
        assert dark > 20


def test_demo_command_writes_pdf_calendar_and_picture(qt, tmp_path, capsys):
    assert main(["--weeks", "8", "--no-goal", "--lang", "en", "--out", str(tmp_path / "x.pdf"),
                 "--png", str(tmp_path / "x.png")]) == 0
    assert (tmp_path / "x.pdf").read_bytes()[:5] == b"%PDF-"
    assert (tmp_path / "x.ics").read_bytes().startswith(b"BEGIN:VCALENDAR\r\n")
    assert (tmp_path / "x.png").stat().st_size > 10_000
    set_language("sv")
