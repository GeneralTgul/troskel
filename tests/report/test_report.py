"""Report: HTML content per kind and language, PDF export, page rendering and printing to a PDF printer."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from lopanalys.core.i18n import language, set_language, t
from lopanalys.core.models import Session, TestKind
from lopanalys.report import charts
from lopanalys.report.builder import Branding, ReportData, pace_str, render_html

from . import sample_data as sd

I18N = Path(__file__).resolve().parents[2] / "src/lopanalys/i18n"


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


# ---------------------------------------------------------------- pure (no Qt)


def test_i18n_files_have_same_keys_and_no_placeholders_lost():
    sv = json.loads((I18N / "report.sv.json").read_text(encoding="utf-8"))
    en = json.loads((I18N / "report.en.json").read_text(encoding="utf-8"))
    assert sv.keys() == en.keys()
    for k in sv:
        assert set(re.findall(r"{(\w+)}", sv[k])) == set(re.findall(r"{(\w+)}", en[k])), k


def test_every_key_used_in_templates_and_code_exists():
    sv = json.loads((I18N / "report.sv.json").read_text(encoding="utf-8"))
    src = Path(__file__).resolve().parents[2] / "src/lopanalys/report"
    used = set()
    for f in [*src.glob("*.py"), *src.glob("templates/*")]:
        used |= set(re.findall(r"""t\(\s*["'](report\.[a-z0-9_.]+[a-z0-9_])["']""", f.read_text(encoding="utf-8")))
    missing = sorted(k for k in used if k not in sv)
    assert not missing, missing


def test_kinds():
    tmp = Path("/nonexistent")
    a = sd.athlete()
    assert ReportData(a, sd.session(TestKind.LACTATE_STEP), lactate=sd.lactate_result()).kind == "lactate"
    assert ReportData(a, sd.session(TestKind.MAX_HR), max_hr=sd.max_hr_result()).kind == "max_hr"
    assert ReportData(a, sd.session(TestKind.GAIT), gait={"summary": {}, "findings": [], "keyframes": []}).kind == "gait"
    assert ReportData(a, sd.session(TestKind.LACTATE_STEP, ramp=True), lactate=sd.lactate_result(),
                      max_hr=sd.max_hr_result()).kind == "combined"
    # A lactate test stays a lactate report, even when it was stopped before the first stage and the
    # app passes the peak heart rate it always computes (it must not say "maxpulstest").
    assert ReportData(a, sd.session(TestKind.LACTATE_STEP), max_hr=sd.max_hr_result(),
                      samples=sd.max_hr_samples()).kind == "lactate"
    assert ReportData(a, sd.session(TestKind.LACTATE_STEP), lactate=sd.lactate_result(),
                      max_hr=sd.max_hr_result()).kind == "lactate"
    del tmp


def test_lactate_html_content(tmp_path):
    html = render_html(sd.lactate_report(tmp_path))
    text = _text(html)
    for s in ("Laktattrappa", "Exempel Löpare", "Det viktigaste", "14,6", "12,4", "Dina träningszoner",
              "Jämfört med förra testet", "Nordisk Löpcoaching", "inte medicinsk", "4:07"):
        assert s in text, s
    assert 'counter(pages)' in html
    assert "<svg" in html and "data:image/png;base64" in html  # chart + logo embedded
    assert "Anna Berg" in html


def test_english_renders(tmp_path):
    data = sd.combined_report(tmp_path)
    data.language = "en"
    text = _text(render_html(data))
    for s in ("Test report", "Key results", "What to work on", "Your training zones", "14.6"):
        assert s in text, s
    assert language() == "sv"  # restored


def test_gait_html_has_frames_metrics_findings(tmp_path):
    html = render_html(sd.gait_report(tmp_path))
    text = _text(html)
    assert html.count("data:image/jpeg;base64") == 4
    for s in ("Bilder från löpningen", "Mätvärden", "Översteg", "Tänk på", "Övning", "Per fart", "Kadens"):
        assert s in text, s
    assert html.count('class="rangebar"') >= 10


def test_gait_from_dict_and_missing_image(tmp_path):
    g = sd.gait_report(tmp_path).gait
    d = {"summary": g.summary.to_dict(),
         "findings": [{**vars(f), "severity": f.severity.value} for f in g.findings],
         "keyframes": [{**vars(k), "image_path": str(tmp_path / "gone.png")} for k in g.keyframes]}
    html = render_html(ReportData(sd.athlete(), sd.session(TestKind.GAIT), gait=d))
    assert "Bilden saknas" in html


def test_minimal_and_bad_input_does_not_crash():
    a = sd.athlete()
    a.birth_date = a.height_cm = a.weight_kg = None
    s = Session(id=None, athlete_id=1, kind=TestKind.LACTATE_STEP, started=datetime(2026, 1, 2))
    html = render_html(ReportData(a, s, lactate={"lt1": {"speed_kmh": "x"}, "methods": [{"name": "n"}]},
                                  zones=[{}], branding=Branding(accent_color="red; }", logo_path="/no/logo.png")))
    assert "Laktattrappa" in html
    assert "red; }" not in html


def test_previous_comparison_rows(tmp_path):
    text = _text(render_html(sd.combined_report(tmp_path)))
    assert "Fart vid LT2" in text and "+0,4" in text and "Maxpuls" in text


def test_overrides(tmp_path):
    data = sd.lactate_report(tmp_path, previous=False)
    data.highlights = [{"label": "Egen", "value": "42", "unit": "x", "sub": ""}]
    data.focus = [{"title": "Sov mer", "text": "Åtta timmar."}]
    text = _text(render_html(data))
    assert "Egen" in text and "Sov mer" in text and "Tröskelpass med rätt fart" not in text


def test_formatting():
    assert pace_str(12.0) == "5:00" and pace_str(None) == "–"
    assert charts.fmt_num(-1.25, 1) in ("−1,2", "−1,3")
    set_language("en")
    assert charts.fmt_num(3.14159, 2) == "3.14"


def test_range_bar():
    svg = charts.range_bar_svg(30, (0, 26), (0, 10), (0, 15))
    assert svg.startswith("<svg") and "<path" in svg  # out of scale -> arrow


# ---------------------------------------------------------------- the real chain: video -> pose -> report


@pytest.fixture(scope="module")
def real_gait(tmp_path_factory):
    """A synthetic runner clip analysed by the real pose code (replayed poses instead of the model)."""
    import cv2

    from lopanalys.pose import synthetic
    from lopanalys.pose.estimator import ReplayEstimator
    from lopanalys.pose.offline import analyze_video
    from lopanalys.pose.types import View

    set_language("sv")
    out = tmp_path_factory.mktemp("real")
    p = synthetic.RunnerParams(overstride_cm=30, trunk_lean_deg=1.0, cadence_spm=164, vertical_oscillation_cm=11,
                               fps=60, duration_s=4, width=640, height=360, noise_px=0.8)
    frames, poses, _ = synthetic.frames_and_poses(p)
    w = cv2.VideoWriter(str(out / "lopning.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), p.fps, (p.width, p.height))
    for f in frames:
        w.write(f)
    w.release()
    return analyze_video(out / "lopning.mp4", 178, View.SIDE_LEFT, keyframes_dir=out / "kf",
                         estimator=ReplayEstimator(dict(enumerate(poses))), speed_kmh=12)


def _real_report(ga, tmp_path, **kw) -> ReportData:
    data = sd.real_combined_report(tmp_path)
    data.gait = ga
    for k, v in kw.items():
        setattr(data, k, v)
    return data


def test_real_chain_has_no_unresolved_keys(real_gait, tmp_path):
    html = render_html(_real_report(real_gait, tmp_path))
    text = _text(html)
    assert "report." not in text and "pose." not in text
    assert "steg/min" in text and "Ålder saknas" not in text
    assert not re.search(r"\d\s°(?!C)", html)             # "2,4°", never "2,4 °"
    en = _text(render_html(_real_report(real_gait, tmp_path, language="en")))
    assert not re.search(r"\b(report|pose|assessments)\.[a-z_]+\.", en)


def test_real_chain_table_colours_equal_finding_severities(real_gait, tmp_path):
    from lopanalys.report import builder

    order = {"ok": 0, "watch": 1, "fix": 2}
    stored = real_gait.to_dict()
    old = {k: v for k, v in stored.items() if k != "ranges"}     # stored before pose saved the ranges
    for gait in (real_gait, stored, old):
        gv = builder._gait_view(gait, 178)
        ctx = builder._gait_ctx(_real_report(gait, tmp_path), gv)
        status = {m["key"]: m["status"] for m in ctx["metrics"]}
        assert status["cadence_spm"] == "ok" and status["ground_contact_ms"] == "none"
        seen = 0
        for f in ctx["findings"]:
            own = [status[k] for k, r in gv.ranges.items() if f["code"] in r.codes and k in status]
            if own:
                assert max(own, key=order.get) == f["severity"], (f["code"], own)
                seen += 1
        assert seen >= 5
        assert {m["key"] for m in ctx["metrics"] if m["status"] != "ok"} >= {"shin_angle_deg", "trunk_lean_deg"}
    assert all(m["bar"] for m in ctx["metrics"] if m["status"] != "none")


def test_real_chain_key_frames_layout_and_chips(real_gait, tmp_path):
    html = render_html(_real_report(real_gait, tmp_path))
    assert 'class="frames two-portrait"' in html and html.count('class="frame card"') == 4
    assert html.count('class="frame strip"') == 1
    assert html.index('class="frame strip"') > html.rindex('class="frame card"')   # the strip comes last
    assert 'gait-metrics follows' in html                # ... and the table shares its page
    chips = re.findall(r'class="chip"><span class="dot (\w+)"></span>([^<]+)<', html)
    assert chips and {sev for sev, _ in chips} <= {"watch", "fix"}
    sev = {f.title: f.severity.value for f in real_gait.findings}
    assert all(sev[title] == s for s, title in chips)
    assert "Kadens" not in {title for _, title in chips}


def test_three_portrait_frames_share_one_row(real_gait, tmp_path):
    d = real_gait.to_dict()
    d["keyframes"] = [k for k in d["keyframes"] if k["kind"] != "flight"]
    html = render_html(_real_report(d, tmp_path))
    assert 'class="frames three"' in html and "gait-metrics follows" not in html


def test_charts_are_inline_svg_with_css_fonts():
    svg = charts.lactate_chart([9, 10, 11], [1.0, 1.5, 3.0], [130, 140, 150],
                               thresholds=[charts.Threshold("LT2", 10.5, 2.0, 145)])
    assert svg.startswith("<svg") and "Source Sans Pro" not in svg and "font-size" in svg
    assert "LT2" in svg and "Laktat" in svg
    assert charts.hr_chart([], []).startswith("<svg")


# ---------------------------------------------------------------- Qt: PDF and printing


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6.QtWebEngineCore")
    from lopanalys.report import pdf

    pdf._ensure_app()
    return pdf


@pytest.mark.parametrize("kind,min_pages", [("lactate", 3), ("max_hr", 2), ("gait", 4), ("combined", 7)])
def test_pdf_all_kinds(qt, tmp_path, kind, min_pages):
    data = getattr(sd, f"{kind}_report")(tmp_path)
    out = qt.html_to_pdf(render_html(data), tmp_path / "out" / f"{kind}.pdf")
    assert out.read_bytes()[:5] == b"%PDF-"
    pages = qt.pdf_page_images(out, dpi=40)
    assert len(pages) >= min_pages
    # A4 portrait: 210 x 297 mm.
    assert abs(pages[0].height() / pages[0].width() - 297 / 210) < 0.01
    # Page 1 is not blank: the cover title is dark ink on white.
    img = pages[0]
    dark = sum(1 for y in range(0, img.height(), 4) for x in range(0, img.width(), 4)
               if img.pixelColor(x, y).lightness() < 100)
    assert dark > 20


def test_pdf_english(qt, tmp_path):
    data = sd.max_hr_report(tmp_path)
    data.language = "en"
    out = qt.html_to_pdf(render_html(data), tmp_path / "en.pdf")
    assert out.stat().st_size > 10_000


def test_pdf_bad_path_gives_clear_error(qt, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(qt.ReportError):
        qt.html_to_pdf("<p>x</p>", blocker / "sub" / "r.pdf")


def test_print_pdf_onto_pdf_printer(qt, tmp_path):
    """The print path (PDF -> 300 dpi page images -> QPrinter) produces one sheet per page."""
    from PySide6.QtPrintSupport import QPrinter

    src = qt.html_to_pdf(render_html(sd.max_hr_report(tmp_path)), tmp_path / "src.pdf")
    printer = qt._make_printer(None)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(tmp_path / "printed.pdf"))
    qt.print_pdf(src, printer)
    assert len(qt.pdf_page_images(tmp_path / "printed.pdf", 20)) == len(qt.pdf_page_images(src, 20))


def test_printers_and_unknown_printer(qt):
    names = qt.available_printers()
    assert isinstance(names, list)
    with pytest.raises(qt.ReportError):
        qt.print_html("<p>x</p>", "RICOH finns inte 123")


def test_real_assessment_formats(tmp_path):
    pytest.importorskip("lopanalys.assessments.lactate")
    data = sd.real_combined_report(tmp_path)
    text = _text(render_html(data))
    assert "Aerob tröskel" in text and "Dina träningszoner" in text and "Fart vid LT2" in text
    assert "– km/h" not in text


def test_stopped_test_gets_a_notice_instead_of_empty_pages():
    """A lactate test stopped after a few seconds: one page that says so, titled as the test that was run."""
    d = ReportData(sd.athlete(), sd.session(TestKind.LACTATE_STEP), records=[], max_hr={"peak": 96},
                   samples=sd.max_hr_samples()[:8], branding=sd.branding())
    html = render_html(d)
    assert t("report.empty.title") in html
    assert t("report.kind.lactate") in html and t("report.maxhr.title") not in html


# ---------------------------------------------------------------- two camera views
def _rear_gait(tmp_path: Path) -> dict:
    from lopanalys.pose.types import Finding, Severity
    rear = sd.gait_summary(cadence_spm=165, overstride_cm=None, shin_angle_deg=None, trunk_lean_deg=None,
                           knee_flex_contact_deg=None, knee_flex_midstance_deg=None, hip_extension_toe_off_deg=None,
                           foot_strike=None, pelvic_drop_deg=8.2, knee_valgus_deg=13.0, step_width_cm=2.5,
                           arm_cross_cm=4.0, asymmetry={}, view="rear")
    fs = [Finding("knee_valgus", Severity.FIX, "Knä inåt", "Knät faller 13° inåt i stödfasen.",
                  "Tänk ”knät över lilltån”.", 13.0, "deg", "under 8°"),
          Finding("arm_cross", Severity.WATCH, "Armarna korsar mittlinjen", "Handen går 4 cm över mitten.",
                  "Pendla armarna rakt fram och bak.", 4.0, "cm", "under 0 cm")]
    kf = sd.keyframes(tmp_path / "kf2")[:1]
    return {"summary": rear.to_dict(), "view": "rear", "angle": "second",
            "findings": [{**vars(f), "severity": f.severity.value} for f in fs],
            "keyframes": [vars(k) for k in kf], "sync": {"matched": 152, "first": 160, "second": 158, "lag_s": 0.18}}


def test_two_views_each_get_their_sections(tmp_path):
    base = sd.gait_report(tmp_path, previous=False)
    g = base.gait
    main = {"summary": g.summary.to_dict(), "view": "side_left",
            "findings": [{**vars(f), "severity": f.severity.value} for f in g.findings],
            "keyframes": [vars(k) for k in g.keyframes], "second": _rear_gait(tmp_path)}
    recs = sd.gait_records()
    for i, r in enumerate(recs):
        r.gait = dict(r.gait, second={"pelvic_drop_deg": 6.0 + i, "knee_valgus_deg": 10.0 + i, "view": "rear"})
    html = render_html(ReportData(sd.athlete(), sd.session(TestKind.GAIT), records=recs, gait=main,
                                  branding=base.branding))
    text = _text(html)
    assert text.count("Löpteknik · Från vänster") == 3 and text.count("Löpteknik · Bakifrån") == 3
    for s in ("Översteg", "Knä inåt", "Armarna korsar mittlinjen", "knät över lilltån", "Stegbredd",
              "152 av 158 steg är samma steg i båda filmerna"):
        assert s in text, s
    assert html.count("data:image/jpeg;base64") == 5              # four from the side, one from behind
    side, rear = text.split("Löpteknik · Bakifrån", 1)
    assert "Per fart" in side and "Per fart" in rear              # each view's own table over the speeds
    # the summary page counts what the second camera found too: the knee is the worst finding now
    assert "Knä inåt" in text.split("Löpteknik · Från vänster")[0]


def test_one_view_has_no_view_names(tmp_path):
    text = _text(render_html(sd.gait_report(tmp_path)))
    assert "Löpteknik · Från" not in text and "· Bakifrån" not in text and "båda filmerna" not in text


def test_only_the_second_view_analysed_takes_the_first_ones_place(tmp_path):
    html = render_html(ReportData(sd.athlete(), sd.session(TestKind.GAIT), gait={"second": _rear_gait(tmp_path)}))
    text = _text(html)
    assert text.count("Löpteknik · Bakifrån") >= 2 and "Knä inåt" in text and "Från vänster" not in text


def test_a_stray_wake_up_of_the_event_loop_does_not_fail_the_pdf(qt, tmp_path, monkeypatch):
    # html_to_pdf waits for each step in one shared event loop. An answer to the font question that
    # came late (or its timer) quit the loop while the PDF was still being written, and the coach got
    # "could not write the PDF" for a report that was fine. Here every wait gets such a wake-up.
    from PySide6.QtCore import QEventLoop, QTimer

    class Woken(QEventLoop):
        def exec(self, *a):
            QTimer.singleShot(0, self.quit)
            return super().exec(*a)

    monkeypatch.setattr(qt, "QEventLoop", Woken)
    out = qt.html_to_pdf("<p>Rapport</p>", tmp_path / "r.pdf")
    assert out.exists() and out.read_bytes()[:5] == b"%PDF-"
