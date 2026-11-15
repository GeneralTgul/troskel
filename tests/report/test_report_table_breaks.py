"""Report: a table near the end of a page is not split so that one or two lone rows land on a page of
their own (the demo report, 2026-11-15). A short table moves whole; a long one keeps at least three
rows on each page."""
from __future__ import annotations

import re

import pytest

from lopanalys.core.i18n import set_language, t
from lopanalys.report.builder import render_html

from . import sample_data as sd


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from lopanalys.report import pdf

    pdf._ensure_app()
    return pdf


def _stage_report(tmp_path, copies: int) -> tuple[str, str]:
    """The lactate report with its table of stages repeated copies times and each row marked, and
    the heading above the table (where the test pushes the table down the page)."""
    set_language("sv")
    html = render_html(sd.lactate_report(tmp_path))
    head = f'<h3 class="keep">{t("report.lactate.stages")}</h3>'
    assert head in html
    m = re.search(r'<table class="stages">.*?<tbody>(.*?)</tbody>', html, re.S)
    rows = re.findall(r"<tr>.*?</tr>", m.group(1), re.S) * copies
    marked = "".join(re.sub(r'<td class="first">\d+</td>', f'<td class="first">ROW{i}Q</td>', r)
                     for i, r in enumerate(rows))
    return html[:m.start(1)] + marked + html[m.end(1):], head


def _rows_per_page(qt, html: str, path) -> list[int]:
    from PySide6.QtPdf import QPdfDocument

    out = qt.html_to_pdf(html, path)
    doc = QPdfDocument()
    doc.load(str(out))
    try:
        counts = [len(re.findall(r"ROW\d+Q", doc.getAllText(i).text())) for i in range(doc.pageCount())]
    finally:
        doc.close()
    return [n for n in counts if n]


@pytest.mark.parametrize("push_mm", [10, 30, 50])
def test_short_table_moves_whole_to_the_next_page(qt, tmp_path, push_mm):
    # Without the rule the 8 stages split 7+1, 5+3 and 3+5 at these heights.
    html, head = _stage_report(tmp_path, 1)
    html = html.replace(head, f'<div style="height:{push_mm}mm"></div>' + head)
    assert _rows_per_page(qt, html, tmp_path / f"s{push_mm}.pdf") == [8]


@pytest.mark.parametrize("push_mm", [0, 48, 144, 192])
def test_long_table_keeps_at_least_three_rows_on_each_page(qt, tmp_path, push_mm):
    html, head = _stage_report(tmp_path, 3)
    html = html.replace(head, f'<div style="height:{push_mm}mm"></div>' + head)
    per_page = _rows_per_page(qt, html, tmp_path / f"l{push_mm}.pdf")
    assert sum(per_page) == 24
    assert min(per_page) >= 3, per_page
