"""The printed training plan's week sheets (#666): one week is one A4 page, also with several sports and strength.
The triathlon plan printed on 86 pages for 28 weeks: the head alone on a page, then the table, then the foot. The
tests read the PDF page by page. A week too full for one page breaks inside the table with the column heads
repeated, and neither the head nor the foot stands alone."""
from __future__ import annotations

import re

import pytest

from lopanalys.core.i18n import set_language, t
from lopanalys.plan import build_plan
from lopanalys.report.plan_builder import render_plan_html

from plan import cases as c

CASES = ("triathlon_swim", "run_bike_12", "run_strength_2", "half_12")
WEEK_OPEN = '<section class="week">'


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from lopanalys.report import pdf

    pdf._ensure_app()
    return pdf


def _pages(qt, html: str, path) -> list[str]:
    from PySide6.QtPdf import QPdfDocument

    out = qt.html_to_pdf(html, path)
    doc = QPdfDocument()
    doc.load(str(out))
    try:
        return [doc.getAllText(i).text() for i in range(doc.pageCount())]
    finally:
        doc.close()


def _plain(text: str) -> str:
    """Letters and digits in lower case: the print has capitals, and the PDF's text has its own apostrophes."""
    return re.sub(r"[\W_]+", "", text.lower())


def _marks(lang: str) -> tuple[re.Pattern, str, str]:
    """What tells the parts of a week sheet apart in a page's text: the head's line of sessions and easy share,
    the table's column head and the foot's title (the two last compared as _plain text)."""
    set_language(lang)
    head = re.compile(re.escape(t("plan.pdf.week_sum", n="N", pct="P")).replace("N", r"\d+").replace("P", r"\d+"))
    return head, _plain(t("plan.pdf.col.parts")), _plain(t("plan.pdf.week_zones"))


def _parts(pages: list[str], lang: str) -> list[tuple[int, int, int]]:
    head, col, foot = _marks(lang)
    return [(len(head.findall(p)), _plain(p).count(col), _plain(p).count(foot)) for p in pages]


@pytest.mark.parametrize("lang", ["sv", "en"])
@pytest.mark.parametrize("case", CASES)
def test_every_week_is_one_page_with_head_table_and_foot(qt, tmp_path, case, lang):
    set_language(lang)
    try:
        html = render_plan_html(build_plan(c.CASES[case]))
        weeks = html.count(WEEK_OPEN)
        assert weeks >= 12
        parts = _parts(_pages(qt, html, tmp_path / f"{case}.pdf"), lang)
    finally:
        set_language("sv")
    sheets = [i for i, p in enumerate(parts) if p[0]]
    # every week starts on a page of its own, and the sheets follow each other without a page between them
    assert [parts[i][0] for i in sheets] == [1] * weeks
    assert sheets == list(range(sheets[0], sheets[0] + weeks))
    # the whole sheet is on that page: the head, the table and the foot
    assert [parts[i] for i in sheets] == [(1, 1, 1)] * weeks
    # no table or foot without its head, and no more pages than the cover, the reading pages and the sketch add
    # (86 pages for the 28 weeks of triathlon_swim before)
    assert all(p == (0, 0, 0) for i, p in enumerate(parts) if i not in sheets)
    assert weeks + 2 <= len(parts) <= weeks + 5


def test_a_week_too_full_for_one_page_breaks_inside_the_table(qt, tmp_path):
    # Week 1 of the plan with strength, its nine rows three times over: more than a page holds.
    set_language("sv")
    html = render_plan_html(build_plan(c.CASES["run_strength_2"]))
    a = html.index(WEEK_OPEN)
    b = html.index(WEEK_OPEN, a + 1)
    week = html[a:b]
    m = re.search(r"<tbody>(.*)</tbody>", week, re.S)
    n = iter(range(1000))
    body = re.sub(r'<div class="w-purpose">', lambda _: f'<div class="w-purpose">ROW{next(n)}Q ', m.group(1) * 3)
    total = next(n)
    assert total >= 15
    full = html[:a] + week[:m.start(1)] + body + week[m.end(1):] + html[b:]
    pages = _pages(qt, full, tmp_path / "full.pdf")
    rows = [len(re.findall(r"ROW\d+Q", p)) for p in pages]
    sheet = [i for i, r in enumerate(rows) if r]
    parts = _parts(pages, "sv")
    assert len(sheet) >= 2 and sheet == list(range(sheet[0], sheet[-1] + 1)) and sum(rows) == total
    first, last = sheet[0], sheet[-1]
    # the head has rows under it, the column heads are repeated on every page and the foot has rows above it
    assert parts[first][0] == 1 and rows[first] >= 3
    assert [parts[i][1] for i in sheet] == [1] * len(sheet)
    assert parts[last][2] == 1 and rows[last] >= 2
    assert all(parts[i][2] == 0 for i in sheet[:-1])
    # and the next week still starts on a new page
    assert parts[last + 1][0] == 1 and rows[last + 1] == 0
