"""PDF export and printing of the report HTML with Qt WebEngine (Chromium) and QtPdf.

The PDF is the master copy: printing renders the generated PDF page by page at 300 dpi onto the
printer, so paper and PDF match exactly. Works headless (QT_QPA_PLATFORM=offscreen) and inside the
running app: an existing QApplication is reused, otherwise one is created.

Public API: html_to_pdf(html, path), print_html(html, printer_name=None), available_printers().
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEventLoop, QMarginsF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrinter

from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

# Page margins in mm (left, top, right, bottom). Chromium takes the page box from here, not from the
# CSS @page rule, which only styles the margin boxes (footer with page numbers).
MARGINS_MM = QMarginsF(16, 16, 16, 20)  # left, top, right, bottom
LOAD_TIMEOUT_MS = 30_000
PRINT_DPI = 300


class ReportError(RuntimeError):
    """A report could not be written or printed. The message is user-facing (localised)."""


def _ensure_app() -> QCoreApplication:
    """Return the running Qt application, or create a QApplication suitable for WebEngine."""
    app = QCoreApplication.instance()
    if app is not None:
        return app
    # WebEngine wants shared GL contexts set before the application exists.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    from PySide6.QtWidgets import QApplication  # widgets: needed for the print dialog

    return QApplication(sys.argv[:1] or ["lopanalys"])


def _wait(loop: QEventLoop, timeout_ms: int) -> bool:
    """Run loop until it is quit or the timeout fires. Returns False on timeout."""
    timed_out = {"v": False}

    def on_timeout() -> None:
        timed_out["v"] = True
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(on_timeout)
    timer.start(timeout_ms)
    loop.exec()
    timer.stop()
    return not timed_out["v"]


def _wait_for(loop: QEventLoop, done, timeout_ms: int) -> bool:
    """Run loop until done() is true. Returns False on timeout.

    The loop is shared by every step of html_to_pdf, so it can be quit by something that belongs to
    an earlier step (the answer to a font question that came late, a timer still running). Such a
    wake-up is not the end of this step: the loop is entered again until done() or the timeout."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while not done():
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            return False
        _wait(loop, left_ms)
    return True


def html_to_pdf(html: str, path: str | os.PathLike[str], *, timeout_ms: int = LOAD_TIMEOUT_MS) -> Path:
    """Render report HTML (from builder.render_html) to an A4 PDF at path, backgrounds included.

    Blocks until the file is written (a local event loop keeps the UI responsive meanwhile).
    Raises ReportError with a Swedish/English message a coach understands on failure.
    """
    _ensure_app()
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings

    out = Path(path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ReportError(t("report.error.folder", path=str(out.parent), err=str(e))) from e

    # Load from a temp file rather than setHtml: setHtml has a 2 MB limit and the report embeds
    # images. The file:// origin also lets the page load the bundled fonts.
    tmpdir = tempfile.TemporaryDirectory(prefix="lopanalys-report-")
    try:
        src = Path(tmpdir.name) / "report.html"
        src.write_text(html, encoding="utf-8")

        page = QWebEnginePage()
        s = page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.PrintElementBackgrounds, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        try:
            loop = QEventLoop()
            state: dict[str, object] = {"loaded": None, "printed": None, "fonts": False}

            def on_loaded(ok: bool) -> None:
                state["loaded"] = ok
                loop.quit()

            page.loadFinished.connect(on_loaded)
            page.load(QUrl.fromLocalFile(str(src)))
            if not _wait_for(loop, lambda: state["loaded"] is not None, timeout_ms) or not state["loaded"]:
                raise ReportError(t("report.error.load"))

            # Web fonts may still be decoding after loadFinished: poll document.fonts.status.
            def fonts_cb(status: object) -> None:
                state["fonts"] = status == "loaded"
                loop.quit()

            for _ in range(50):  # at most ~5 s
                page.runJavaScript("document.fonts.status", 0, fonts_cb)
                _wait(loop, 200)
                if state["fonts"]:
                    break
                QTimer.singleShot(100, loop.quit)
                loop.exec()

            def on_printed(_file: str, ok: bool) -> None:
                state["printed"] = ok
                loop.quit()

            page.pdfPrintingFinished.connect(on_printed)
            layout = QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                 MARGINS_MM, QPageLayout.Unit.Millimeter)
            page.printToPdf(str(out), layout)
            if not _wait_for(loop, lambda: state["printed"] is not None, timeout_ms) or not state["printed"]:
                raise ReportError(t("report.error.pdf", path=str(out)))
        finally:
            _delete(page)
    finally:
        tmpdir.cleanup()
    return out


def _delete(obj: object) -> None:
    """Destroy a Qt object now, so the WebEngine profile is not released before its page."""
    try:
        import shiboken6

        shiboken6.delete(obj)
    except Exception:  # already gone
        pass


def available_printers() -> list[str]:
    """Names of the installed printers, the system default first. Empty if there are none."""
    _ensure_app()
    from PySide6.QtPrintSupport import QPrinterInfo

    names = list(QPrinterInfo.availablePrinterNames())
    default = QPrinterInfo.defaultPrinterName()
    if default in names:
        names.remove(default)
        names.insert(0, default)
    return names


def default_printer() -> str | None:
    """The system default printer name, or None."""
    _ensure_app()
    from PySide6.QtPrintSupport import QPrinterInfo

    return QPrinterInfo.defaultPrinterName() or None


def _make_printer(printer_name: str | None) -> QPrinter:
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if printer_name:
        printer.setPrinterName(printer_name)
    printer.setResolution(PRINT_DPI)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageOrientation(QPageLayout.Orientation.Portrait)
    printer.setColorMode(QPrinter.ColorMode.Color)
    printer.setFullPage(True)  # the PDF carries its own margins
    printer.setDocName(t("report.print.docname"))
    return printer


def _on_white(img: QImage) -> QImage:
    """PDF pages render with a transparent background; flatten onto white paper."""
    out = QImage(img.size(), QImage.Format.Format_RGB32)
    out.fill(Qt.GlobalColor.white)
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.end()
    return out


def print_pdf(pdf_path: str | os.PathLike[str], printer: QPrinter) -> None:
    """Print an existing PDF onto a configured QPrinter, one page image per sheet at its dpi."""
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument()
    err = doc.load(str(pdf_path))
    if err != QPdfDocument.Error.None_ or doc.pageCount() == 0:
        raise ReportError(t("report.error.read_pdf"))
    painter = QPainter()
    if not painter.begin(printer):
        raise ReportError(t("report.error.printer", name=printer.printerName() or "?"))
    try:
        dpi = printer.resolution()
        for i in range(doc.pageCount()):
            if i > 0 and not printer.newPage():
                raise ReportError(t("report.error.printer", name=printer.printerName() or "?"))
            pt = doc.pagePointSize(i)  # 1/72 inch
            size = QSize(round(pt.width() / 72 * dpi), round(pt.height() / 72 * dpi))
            img = _on_white(doc.render(i, size))
            painter.drawImage(printer.paperRect(QPrinter.Unit.DevicePixel), img)
    finally:
        painter.end()
        doc.close()


def print_html(html: str, printer_name: str | None = None, *, parent=None, copies: int = 1) -> bool:
    """Print the report. The HTML is rendered to PDF first and that PDF is printed at 300 dpi.

    With printer_name: prints straight to that printer, no dialog. Without: shows the system print
    dialog (needs a widget application). Returns False if the coach cancelled the dialog.
    """
    _ensure_app()
    if printer_name and printer_name not in available_printers():
        raise ReportError(t("report.error.no_printer", name=printer_name))
    with tempfile.TemporaryDirectory(prefix="lopanalys-print-") as d:
        pdf = html_to_pdf(html, Path(d) / "report.pdf")
        printer = _make_printer(printer_name)
        printer.setCopyCount(max(1, copies))
        if not printer_name:
            from PySide6.QtPrintSupport import QPrintDialog

            dlg = QPrintDialog(printer, parent)
            dlg.setWindowTitle(t("report.print.dialog_title"))
            if dlg.exec() != QPrintDialog.DialogCode.Accepted:
                return False
            # The dialog may hand back the driver's default paper (Letter on some installs): the report is A4.
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            printer.setFullPage(True)
            # Windows often has the printer on greyscale by default and the dialog hands that back
            # (2026-11-13: the report came out black and white). The report is made for colour.
            printer.setColorMode(QPrinter.ColorMode.Color)
        print_pdf(pdf, printer)
    return True


def pdf_page_images(pdf_path: str | os.PathLike[str], dpi: int = 110) -> list:
    """Render each PDF page to a QImage (for previews and tests)."""
    _ensure_app()
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument()
    if doc.load(str(pdf_path)) != QPdfDocument.Error.None_:
        raise ReportError(t("report.error.read_pdf"))
    out = []
    for i in range(doc.pageCount()):
        pt = doc.pagePointSize(i)
        out.append(_on_white(doc.render(i, QSize(round(pt.width() / 72 * dpi), round(pt.height() / 72 * dpi)))))
    doc.close()
    return out


__all__ = ["ReportError", "available_printers", "default_printer", "html_to_pdf", "pdf_page_images",
           "print_html", "print_pdf"]
