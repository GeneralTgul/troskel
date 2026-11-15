"""The report preview: the report HTML in a web view, with the PDF as the fallback.

``QWebEngineView.setHtml`` silently shows nothing for content over 2 MB, and the report embeds its
images as data URIs, so a real report never appeared (real hardware 2026-11-14). The HTML is written
to a temporary file and loaded from there, as report.pdf does. If the web view cannot be created
or the page does not load, the report is rendered to PDF and shown in a PDF view (QtPdf), or opened
in the system's PDF program. Every failure is logged.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from lopanalys.core.i18n import t

log = logging.getLogger(__name__)


class ReportView(QWidget):
    """Shows report HTML. ``show_html(html)`` may be called again with a new report."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._dir = Path(tempfile.mkdtemp(prefix="lopanalys-preview-"))
        self._web: Any = None
        self._pdf_view: Any = None
        self._pdf_doc: Any = None
        self._html = ""
        self.mode = ""          # "web" | "pdf" | "external" | "failed" (tests and diagnostics)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.hide()
        self._lay.addWidget(self.message)
        self.destroyed.connect(lambda *_: shutil.rmtree(self._dir, ignore_errors=True))

    # ---------------------------------------------------------------- public
    def show_html(self, html: str) -> None:
        self._html = html
        web = self._web_view()
        if web is None:
            self._show_pdf()
            return
        src = self._dir / "report.html"
        try:
            src.write_text(html, encoding="utf-8")
        except OSError:
            log.exception("report preview: writing the temporary page failed")
            self._show_pdf()
            return
        log.info("report preview: loading %d kB in the web view", len(html.encode("utf-8")) // 1024)
        self.mode = "web"
        web.load(QUrl.fromLocalFile(str(src)))

    def cleanup(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    # ---------------------------------------------------------------- web
    def _web_view(self) -> Any:
        if self._web is not None:
            return self._web
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
            web = QWebEngineView(self)
            s = web.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        except Exception:
            log.exception("report preview: Qt WebEngine is not available, showing the PDF instead")
            return None
        web.loadFinished.connect(self._loaded)
        self._lay.addWidget(web, 1)
        self._web = web
        return web

    def _loaded(self, ok: bool) -> None:
        if ok:
            log.info("report preview: loaded")
            return
        log.warning("report preview: the web view did not load the report, showing the PDF instead")
        self._web.hide()
        self._show_pdf()

    # ---------------------------------------------------------------- PDF fallback
    def _show_pdf(self) -> None:
        pdf = self._dir / "report.pdf"
        try:
            from lopanalys.ui import services
            services.html_to_pdf(self._html, pdf)
        except Exception as e:
            log.exception("report preview: rendering the PDF failed")
            self._fail(str(e))
            return
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView
            if self._pdf_view is None:
                self._pdf_doc = QPdfDocument(self)
                self._pdf_view = QPdfView(self)
                self._pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
                self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
                self._pdf_view.setDocument(self._pdf_doc)
                self._lay.addWidget(self._pdf_view, 1)
            self._pdf_doc.close()
            if self._pdf_doc.load(str(pdf)) != QPdfDocument.Error.None_:
                raise RuntimeError("QPdfDocument could not read the PDF")
            self._pdf_view.show()
            self.mode = "pdf"
            log.info("report preview: showing the PDF")
            return
        except Exception:
            log.exception("report preview: the PDF view failed, opening the PDF in the system program")
        from PySide6.QtGui import QDesktopServices
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf))):
            self.mode = "external"
            self.message.setText(t("ui.res.preview_external"))
            self.message.show()
        else:
            log.error("report preview: no program opened the PDF")
            self._fail(t("ui.res.preview_no_program"))

    def _fail(self, detail: str) -> None:
        self.mode = "failed"
        self.message.setText(t("ui.err.report", detail=detail))
        self.message.show()
