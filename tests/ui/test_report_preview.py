"""Förhandsgranska rapport (Resultat) and the collected report's preview (Historik).

On the gym PC nothing happened: the button's clicked(False) landed in show_report(switch=False), so the
page never changed, and QWebEngineView.setHtml shows nothing for content over 2 MB, which a report
with its images is. The preview now loads the HTML from a file, and falls back to the PDF.
"""
from __future__ import annotations

import base64
import builtins

from lopanalys.ui.widgets import report_view as rv

BIG = ("<html><body><h1 id='t'>Testrapport</h1><img src='data:image/png;base64,"
       + base64.b64encode(b"x" * 2_400_000).decode() + "'></body></html>")


def _text(qtbot, web) -> str:
    got: dict[str, str] = {}
    web.page().runJavaScript("document.getElementById('t') ? document.getElementById('t').innerText : ''",
                             0, lambda v: got.setdefault("v", v or ""))
    qtbot.waitUntil(lambda: "v" in got, timeout=5000)
    return got["v"]


def test_report_over_2_mb_is_shown(qapp, qtbot):
    assert len(BIG) > 2 * 1024 * 1024
    view = rv.ReportView()
    view.resize(900, 700)
    view.show()
    with qtbot.waitSignal(view._web_view().loadFinished, timeout=20000) as sig:
        view.show_html(BIG)
    assert sig.args == [True] and view.mode == "web"
    assert _text(qtbot, view._web) == "Testrapport"
    view.cleanup()


def test_without_webengine_the_pdf_is_shown(qapp, qtbot, monkeypatch):
    real_import = builtins.__import__

    def no_webengine(name, *a, **k):
        if name.startswith("PySide6.QtWebEngineWidgets"):
            raise ImportError("no QtWebEngine in this build")
        return real_import(name, *a, **k)
    made: list[str] = []

    def fake_pdf(html, path):
        from lopanalys.report import pdf
        made.append(html)
        monkeypatch.setattr(builtins, "__import__", real_import)
        pdf.html_to_pdf("<html><body><h1>Rapport</h1></body></html>", path)
    from lopanalys.ui import services
    monkeypatch.setattr(services, "html_to_pdf", fake_pdf)
    monkeypatch.setattr(builtins, "__import__", no_webengine)
    view = rv.ReportView()
    view.show_html("<html><body>x</body></html>")
    monkeypatch.setattr(builtins, "__import__", real_import)
    assert made and view.mode == "pdf"
    assert view._pdf_doc.pageCount() == 1
    view.cleanup()


def test_failed_load_falls_back_to_the_pdf(qapp, qtbot, monkeypatch):
    from lopanalys.ui import services
    monkeypatch.setattr(services, "html_to_pdf", lambda html, path: (_ for _ in ()).throw(RuntimeError("Chromium")))
    view = rv.ReportView()
    view._html = "<html></html>"
    view._web_view()
    view._loaded(False)                    # what a page that does not load reports
    assert view.mode == "failed" and "Chromium" in view.message.text()


def test_preview_button_switches_to_the_report(demo_window, qtbot, monkeypatch):
    res = demo_window.results
    shown: list[str] = []
    monkeypatch.setattr(res, "html", lambda: BIG)          # the report itself is tested in tests/report
    monkeypatch.setattr(rv, "ReportView", lambda *a, **k: _FakeView(shown))
    res._web = None
    res.tabs.setCurrentIndex(0)
    res.preview_btn.click()
    assert res.tabs.currentWidget() is res.report_tab
    assert shown == [BIG], "the report was rendered more than once or not at all"


class _FakeView(__import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget):
    def __init__(self, shown):
        super().__init__()
        self.shown = shown

    def show_html(self, html):
        self.shown.append(html)


def test_collected_report_dialog_shows_the_report(qapp, qtbot):
    from lopanalys.ui.context import AppContext
    from lopanalys.ui.views.history import CollectedReportDialog
    ctx = AppContext(demo=True)
    dlg = CollectedReportDialog(ctx, BIG, "x.pdf")
    dlg.show()
    qtbot.waitUntil(lambda: dlg.web.mode == "web", timeout=5000)
    with qtbot.waitSignal(dlg.web._web.loadFinished, timeout=20000, raising=False):
        pass
    qtbot.waitUntil(lambda: _text(qtbot, dlg.web._web) == "Testrapport", timeout=20000)
    dlg.close()
