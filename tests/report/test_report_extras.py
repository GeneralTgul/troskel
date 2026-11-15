"""Report: the chosen zone system, the comparison table, the Garmin box, estimated VO2max, and the sheet "Om testerna"."""
from __future__ import annotations

import html as html_mod
import re

import pytest

from lopanalys.assessments import lactate as alac
from lopanalys.assessments import vo2max, zone_systems as zs
from lopanalys.core.i18n import language, set_language, t
from lopanalys.core.models import TestKind
from lopanalys.report.builder import ReportData, render_html, render_tests_info

from . import sample_data as sd

UNRESOLVED = re.compile(r"\b(report|pose|assessments|ui)\.[a-z0-9_]+\.[a-z0-9_.+]+")


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", re.sub(r"<style.*?</style>", " ", html, flags=re.S))


def lactate_data(system: str = "garmin", language_code: str | None = None) -> ReportData:
    """A lactate test to exhaustion, with everything computed by the real modules."""
    recs = sd.lactate_records()
    lac = alac.analyze(recs, max_hr=192)
    anchors = zs.Anchors.from_lactate(lac, 192, 46)
    return ReportData(
        athlete=sd.athlete(), session=sd.session(TestKind.LACTATE_STEP), records=recs, lactate=lac.to_dict(),
        zones=[z.to_dict() for z in zs.convert(anchors, system)], branding=sd.branding(), language=language_code,
        zone_system={"key": system, "name": zs.SYSTEMS[system].name, "note": zs.SYSTEMS[system].note},
        zone_compare=[{"name": zs.SYSTEMS[k].name, "zones": [z.to_dict() for z in v]}
                      for k, v in zs.compare(anchors).items()],
        garmin=zs.garmin_setup(anchors).to_dict(),
        vo2max=vo2max.estimate(recs, 192, 50, to_exhaustion=True).to_dict())   # within 15 % of Uth


def max_only_data(language_code: str | None = None) -> ReportData:
    anchors = zs.Anchors(max_hr=192, resting_hr=46)
    return ReportData(
        athlete=sd.athlete(), session=sd.session(TestKind.MAX_HR), samples=sd.max_hr_samples(),
        max_hr=sd.max_hr_result(), zones=[z.to_dict() for z in zs.convert(anchors, "seiler")],
        branding=sd.branding(), language=language_code,
        zone_system={"key": "seiler", "name": zs.SYSTEMS["seiler"].name, "note": zs.SYSTEMS["seiler"].note},
        zone_compare=[{"name": zs.SYSTEMS[k].name, "zones": [z.to_dict() for z in v]}
                      for k, v in zs.compare(anchors).items()],
        garmin=zs.garmin_setup(anchors).to_dict(), vo2max=vo2max.estimate(None, 192, 46).to_dict())


def test_lactate_report_shows_system_comparison_watch_and_vo2max():
    data = lactate_data("garmin")
    text = _text(render_html(data))
    g = data.garmin
    assert "5 zoner · Garmin" in text and "Jämförelse mellan zonsystem" in text
    for head in ("3 zoner · Seiler", "5 zoner · Olympiatoppen I1–I5"):
        assert head in text
    assert "Fyll i din klocka" in text and "Laktattröskelpuls (LTHR)" in text
    assert f"{g['lthr']} slag/min" in text and f"{g['threshold_pace']} min/km" in text
    z2 = next(z for z in g["zones"] if z["zone"] == 2)
    assert f"{z2['hr_low']}–{z2['hr_high']} slag/min" in text
    assert "Varför visar klockan andra zoner?" in text
    assert "Uppskattat VO2max" in text and t("assessments.vo2max.disclaimer") in text
    assert "ml/kg/min" in text and "±10 %" in text
    assert "Uppskattade ur maxpuls" not in text


def test_max_hr_only_report_marks_the_zones_as_estimated():
    text = _text(render_html(max_only_data()))
    assert "Uppskattade ur maxpuls" in text and "3 zoner · Seiler" in text
    assert "Fyll i din klocka" in text and "50–60 % av maxpuls" in text
    assert "inte mätt" in text                                     # no LTHR without a lactate test
    assert "Uppskattat VO2max" in text and "Uth–Sørensen" in text and t("assessments.vo2max.disclaimer") in text
    assert "Lägg de flesta passen i Z1" in text                    # the easy zone of a 3-zone system


@pytest.mark.parametrize("lang", ["sv", "en"])
@pytest.mark.parametrize("make", [lactate_data, max_only_data], ids=["lactate", "max-only"])
def test_no_unresolved_i18n_keys(make, lang):
    data = make(language_code=lang) if make is max_only_data else make("friel", lang)
    text = _text(render_html(data))
    assert not UNRESOLVED.search(text), UNRESOLVED.search(text)
    assert ("Estimated VO2max" if lang == "en" else "Uppskattat VO2max") in text
    assert language() == "sv"


def test_every_zone_system_renders():
    for system in zs.SYSTEMS:
        text = _text(render_html(lactate_data(system)))
        assert zs.SYSTEMS[system].name in text and not UNRESOLVED.search(text), system


def test_report_without_the_new_data_is_unchanged(tmp_path):
    text = _text(render_html(sd.lactate_report(tmp_path)))
    for s in ("Fyll i din klocka", "Uppskattat VO2max", "Jämförelse mellan zonsystem"):
        assert s not in text


def test_vo2max_alone_still_gets_its_block():
    d = ReportData(sd.athlete(), sd.session(TestKind.MAX_HR), samples=sd.max_hr_samples(), max_hr=sd.max_hr_result(),
                   vo2max=vo2max.estimate(None, 192, 46).to_dict())
    text = _text(render_html(d))
    assert "Uppskattat VO2max" in text and t("assessments.vo2max.disclaimer") in text


@pytest.mark.parametrize("lang", ["sv", "en"])
@pytest.mark.parametrize("sport", ["run", "bike", "skierg"])
def test_tests_info_sheet_has_every_test_and_no_unresolved_keys(lang, sport):
    html = render_tests_info(sd.branding(), lang, sport)
    text = _text(html)
    assert not UNRESOLVED.search(text), UNRESOLVED.search(text)
    assert language() == "sv"
    set_language(lang)
    assert t("report.tests_info.title") in text and "Nordisk Löpcoaching" in text
    from lopanalys.assessments import catalog
    assert t(f"report.tests_info.sport.{sport}") in text
    for test in catalog.tests_info(sport):
        assert test["title"] in text
        for f in test["fields"]:
            assert f["label"] in text
            assert " ".join(f["text"].split()) in " ".join(html_mod.unescape(text).split()), (test["key"], f["key"])


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from lopanalys.report import pdf

    pdf._ensure_app()
    return pdf


@pytest.mark.parametrize("lang", ["sv", "en"])
@pytest.mark.parametrize("sport", ["run", "bike", "skierg"])
def test_tests_info_sheet_is_one_a4(qt, tmp_path, lang, sport):
    out = qt.html_to_pdf(render_tests_info(sd.branding(), lang, sport), tmp_path / f"om-{lang}-{sport}.pdf")
    pages = qt.pdf_page_images(out, dpi=40)
    assert len(pages) == 1
    assert abs(pages[0].height() / pages[0].width() - 297 / 210) < 0.01


def test_pdf_with_the_new_blocks(qt, tmp_path):
    out = qt.html_to_pdf(render_html(lactate_data("garmin")), tmp_path / "extras.pdf")
    assert len(qt.pdf_page_images(out, dpi=40)) >= 3


# --- print (real hardware 2026-11-14): dark key frames and pale status colours on paper ----------------------

def test_key_frames_are_lightened_for_print_but_not_on_disk(tmp_path):
    import base64
    import io

    import numpy as np
    from PIL import Image

    from lopanalys.report.builder import _image, for_print

    rng = np.random.default_rng(0)
    dark = (rng.random((90, 160, 3)) * 70 + 10).astype(np.uint8)          # a dim gym, darkened still
    dark[40:44, 20:140] = (0, 200, 170)                                      # a teal limb
    path = tmp_path / "kf.png"
    Image.fromarray(dark).save(path)
    before = path.read_bytes()
    src, _ = _image(str(path), printable=True)
    shown = np.asarray(Image.open(io.BytesIO(base64.b64decode(src.split(",", 1)[1]))).convert("RGB"), float)
    assert shown.mean() > 1.8 * dark.mean()
    assert path.read_bytes() == before                                       # the original stays as drawn
    limb = shown[41, 80]
    assert limb[1] > limb[0] + 60 and limb[2] > limb[0] + 40                 # still teal
    plain, _ = _image(str(path))
    assert plain != src                                                      # logos and others: unchanged
    flat = for_print(Image.new("RGB", (8, 8), (120, 120, 120)))
    assert np.asarray(flat).max() <= 255 and np.asarray(flat).mean() > 120  # a flat image is not blown out


def test_status_colours_print_at_full_strength():
    from pathlib import Path

    from lopanalys.report import charts

    svg = charts.range_bar_svg(165, (140, 200), (160, 200), (150, 200))
    assert "opacity" not in svg and charts.GOOD in svg and charts.FIX in svg
    css = (Path(charts.__file__).parent / "templates" / "report.css").read_text(encoding="utf-8")
    assert "opacity: 0.55" not in css
    # ".watch" is a severity (dots, stripes): a box styled ".watch" made the dots big orange boxes
    import re
    assert not re.search(r"^\.watch[\s{,]", css, re.M)


def test_zone_lactate_column_only_when_lactate_was_measured():
    """The zone systems' textbook lactate ranges are not the athlete's."""
    head = f'<th class="n">{t("report.col.lactate")}</th>'
    data = max_only_data()
    for z in data.zones:
        z.setdefault("lactate_low", 0.8)
        z.setdefault("lactate_high", 1.5)
    assert head not in render_html(data)
    assert head in render_html(lactate_data("olympiatoppen"))


def test_zone_lactate_per_zone_with_missing_values_and_old_saved_values():
    """Olympiatoppen's lactate is a range per zone with None at open ends; a test saved
    with the older shared borders still renders."""
    from lopanalys.report.builder import _zones_ctx
    new = [z.to_dict() for z in zs.nominal_zones(190, system="olympiatoppen")]
    rows = _zones_ctx(new)["rows"]
    assert [r["lactate"] for r in rows] == [t("report.range.below", v="1,5"), "1,0–2,0", "1,5–3,5", "–", "–"]
    old = [dict(z, lactate_low=lo, lactate_high=hi)
           for z, (lo, hi) in zip(new, [(0.8, 1.5), (1.5, 2.5), (2.5, 4.0), (4.0, 6.0), (6.0, 10.0)])]
    assert _zones_ctx(old)["rows"][2]["lactate"] == "2,5–4,0"
