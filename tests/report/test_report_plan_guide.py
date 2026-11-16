"""The printed training plan's reading page (#666): *Passen i schemat* with the templates of every sport the plan
has, and *Så använder du schemat* on the reading page instead of alone on a page after the cover."""
from __future__ import annotations

import re
from html import unescape

import pytest

from lopanalys.core.i18n import has, set_language, t
from lopanalys.plan import build_plan, library
from lopanalys.report.plan_builder import _guide, render_plan_html

from plan import cases as c


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.fixture(scope="module")
def plans():
    set_language("sv")
    return {k: build_plan(c.CASES[k]) for k in ("half_12", "run_bike_12", "four_sports_marathon", "ski_main",
                                                 "run_strength_2", "triathlon_swim", "bike_alone_26")}


def _text(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", "", html, flags=re.S))))


def _guide_html(html: str) -> str:
    a = html.index(f'<h3 class="keep">{t("plan.pdf.guide.title")}</h3>')
    return html[a:html.index(t("plan.pdf.rules.title"), a)]


def _templates(plan) -> set[str]:
    return {w.template_id for w in plan.workouts
            if library.get(w.template_id).family != "race" and has(f"plan.workout.{w.template_id}.purpose")}


def test_running_alone_has_one_group_without_a_heading_and_the_running_titles(plans):
    plan = plans["half_12"]
    groups = _guide(plan)
    assert len(groups) == 1 and groups[0]["sport"] == ""
    titles = [g["title"] for g in groups[0]["items"]]
    assert titles[:2] == ["Lugn löpning", "Återhämtningsjogg"] and "Test hos din coach" in titles
    html = _guide_html(render_plan_html(plan))
    assert 'class="guide-sport' not in html and html.count('<div class="g">') == len(_templates(plan))


@pytest.mark.parametrize("case", ["run_bike_12", "four_sports_marathon", "ski_main", "run_strength_2",
                                  "triathlon_swim", "bike_alone_26"])
def test_every_template_the_plan_uses_is_explained_once_under_its_sport(plans, case):
    plan = plans[case]
    used = _templates(plan)
    assert used
    groups = _guide(plan)
    text = _text(_guide_html(render_plan_html(plan)))
    shown = [g for grp in groups for g in grp["items"]]
    assert len(shown) == len(used)
    for tid in used:
        assert text.count(t(f"plan.workout.{tid}.purpose")) >= 1 and t(f"plan.workout.{tid}.how") in text, tid
    sports = {library.get(tid).sport for tid in used}
    assert len(groups) == len(sports)
    if len(sports) > 1:
        assert all(grp["sport"] and grp["sport"] in text for grp in groups)
    assert all(g["title"][0].isupper() for g in shown)


def test_the_groups_follow_the_plans_sports_and_skis_are_named_with_the_skierg(plans):
    heads = [g["sport"] for g in _guide(plans["four_sports_marathon"])]
    assert heads == ["Löpning", "Cykel", "Skidor", "Simning"]        # the skis use the SkiErg's templates
    heads = [g["sport"] for g in _guide(plans["ski_main"])]
    assert heads[0] == "SkiErg · Skidor" and "Löpning" in heads      # the main sport's templates first
    assert [g["sport"] for g in _guide(plans["run_strength_2"])] == ["Löpning", "Styrka"]
    swim = next(g for g in _guide(plans["triathlon_swim"]) if g["sport"] == "Simning")
    titles = [x["title"] for x in swim["items"]]
    assert titles[0] == "Lugnt pass" and "Teknikpass" in titles
    bike = next(g for g in _guide(plans["run_bike_12"]) if g["sport"] == "Cykel")
    assert bike["items"][0]["title"].startswith("Lugnt pass")


def test_the_sports_own_tests_and_sweet_spot_are_in_the_guide(plans):
    for case in ("four_sports_marathon", "triathlon_swim", "bike_alone_26", "ski_main"):
        plan = plans[case]
        tests = {tid for tid in _templates(plan) if library.get(tid).family == "test"}
        ids = {x["title"] for grp in _guide(plan) for x in grp["items"]}
        for tid in tests:
            title = t(f"plan.workout.{tid}.title")
            assert title[:1].upper() + title[1:] in ids, (case, tid)
    used = {tid for case in plans for tid in _templates(plans[case])}
    assert "bike_sweetspot" in used and any(tid.endswith("_test") for tid in used)   # the cases do cover them


def test_the_use_box_and_the_notes_open_the_reading_page(plans):
    html = render_plan_html(plans["four_sports_marathon"])
    cover, rest = html[:html.index('<section class="section">')], html[html.index('<section class="section">'):]
    assert 'class="plan-promise"' not in cover and 'class="plan-notes"' not in cover
    assert rest.index('class="plan-promise"') < rest.index('class="plan-notes"') < rest.index('class="plan-blocks"')
    assert t("plan.pdf.promise.title") in _text(rest)


# ---------------------------------------------------------------- Qt: the PDF


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from lopanalys.report import pdf

    pdf._ensure_app()
    return pdf


def _page_texts(qt, html: str, path) -> list[str]:
    from PySide6.QtPdf import QPdfDocument

    out = qt.html_to_pdf(html, path)
    doc = QPdfDocument()
    doc.load(str(out))
    try:
        return [" ".join(doc.getAllText(i).text().split()) for i in range(doc.pageCount())]
    finally:
        doc.close()


@pytest.mark.parametrize("case", ["half_12", "run_bike_12", "triathlon_swim"])
def test_pdf_use_box_is_on_the_reading_page_and_no_page_is_nearly_empty(qt, tmp_path, plans, case):
    plan = plans[case]
    pages = _page_texts(qt, render_plan_html(plan), tmp_path / f"{case}.pdf")
    box, read, guide = t("plan.pdf.promise.title"), t("plan.pdf.read.title"), t("plan.pdf.guide.title")
    assert box not in pages[0] and read not in pages[0]              # the cover is one page, and only the cover
    assert box in pages[1] and read in pages[1] and t("plan.pdf.blocks.title") in pages[1]
    weeks = sum(1 for wk in plan.weeks if wk.detailed)
    rules = t("plan.pdf.rules.title")
    last_read = next(i for i, p in enumerate(pages) if rules in p)
    assert t("plan.pdf.disclaimer") in pages[last_read]              # the disclaimer stays with the rules
    first_week = last_read + 1
    assert t("plan.pdf.week", n=1) in pages[first_week]
    assert 2 <= first_week <= 4                                      # the reading pages: one to three
    assert len(pages) >= first_week + weeks
    for i, p in enumerate(pages[:first_week]):
        assert len(p) > 700, (i + 1, len(p))                         # the footer and a lone box are under 300 characters
