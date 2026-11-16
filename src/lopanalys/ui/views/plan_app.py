"""*Till appen* on the plan page: the plan for the athlete's app as a QR code, a link and a ``.troskel`` file.

The three hold the same plan (plan/link.py): the link and the QR code the short payload, with the request the app
builds the workouts from, and the file the whole plan. The QR code is drawn black on white whatever the theme, large
enough to scan from the screen. A link too long for one QR code (share/qr.py) gets a line that says so instead,
pointing to the mail and the file, which have no limit.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap, QTextOption
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QVBoxLayout,
                               QWidget)

from lopanalys.core.i18n import t
from lopanalys.share import qr
from lopanalys.ui import theme
from lopanalys.ui.widgets.common import button, field, label

log = logging.getLogger(__name__)

QR_PIXELS = 560        # about the code's width on the screen; each module is a whole number of pixels


def qr_image(text: str, pixels: int = QR_PIXELS) -> QImage | None:
    """The QR code of ``text`` as an image, black on white with the quiet zone; None when it does not fit."""
    m = qr.matrix(text)
    if m is None:
        return None
    scale = max(3, pixels // len(m))
    size = len(m) * scale
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    black = QColor("#000000")
    for y, row in enumerate(m):
        for x, dark in enumerate(row):
            if dark:
                p.fillRect(QRect(x * scale, y * scale, scale, scale), black)
    p.end()
    return img


class AppDialog(QDialog):
    """The QR code, the link with *Kopiera*, and *Spara som fil*. ``on_saved(path)`` is told about a saved file."""

    def __init__(self, link: str, file_bytes: bytes, target: Path, parent: QWidget | None = None,
                 on_saved: Callable[[Path], None] | None = None) -> None:
        super().__init__(parent)
        self.link, self.file_bytes, self.target, self.on_saved = link, file_bytes, target, on_saved
        self.setWindowTitle(t("ui.plan.app_title"))
        self.setMinimumWidth(640)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(14)
        v.addWidget(label(t("ui.plan.app_title"), "SectionTitle"))
        v.addWidget(label(t("ui.plan.app_hint"), wrap=True))
        self.image = qr_image(link)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.too_long = label("", wrap=True)
        self.too_long.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        if self.image is not None:
            self.qr.setPixmap(QPixmap.fromImage(self.image))
            v.addWidget(self.qr)
            self.too_long.hide()
        else:
            self.qr.hide()
            self.too_long.setText(t("ui.plan.app_too_long", n=len(link), max=qr.QR_MAX))
        v.addWidget(self.too_long)
        self.link_text = QPlainTextEdit(link)
        self.link_text.setReadOnly(True)
        self.link_text.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)   # one long word: break it anywhere
        self.link_text.setFixedHeight(64)
        v.addWidget(field(t("ui.plan.app_link"), self.link_text))
        row = QHBoxLayout()
        self.copy_btn = button(t("ui.plan.app_copy"))
        self.copy_btn.clicked.connect(self.copy)
        self.file_btn = button(t("ui.plan.app_file"), "save")
        self.file_btn.clicked.connect(lambda: self.save_file())
        row.addWidget(self.copy_btn)
        row.addWidget(self.file_btn)
        row.addStretch(1)
        close = button(t("ui.common.close"), kind="primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.link)
        self.copy_btn.setText(t("ui.plan.app_copied"))

    def save_file(self) -> bool:
        fn, _ = QFileDialog.getSaveFileName(self, t("ui.plan.app_file"), str(self.target), t("ui.plan.app_filter"))
        if not fn:
            return False
        try:
            Path(fn).write_bytes(self.file_bytes)
        except Exception as e:
            log.exception("the plan's app file failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.plan.err_app", detail=str(e)))
            return False
        if self.on_saved is not None:
            self.on_saved(Path(fn))
        QMessageBox.information(self, t("ui.plan.app_file"), t("ui.plan.file_saved", path=fn))
        return True
