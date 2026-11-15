"""The dialog "Lägg till telefon eller nätverkskamera": an address, or a search on the network.

The coach types what the phone app shows (an IP address is enough) and presses *Prova adressen*:
the program asks the usual ports and paths of the phone apps and lists the streams that answer.
*Sök på nätverket* lists the cameras that announce themselves (Bonjour/mDNS). Nothing is opened
here; the chosen address goes back to the wizard, which shows its preview like any other camera.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from lopanalys.core.i18n import t
from lopanalys.ui import services, theme
from lopanalys.ui.bridge import run_async
from lopanalys.ui.widgets.common import StatusPill, button, label


class AddStreamDialog(QDialog):
    """``exec()`` and read ``url`` (None when cancelled)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.url: str | None = None
        self._busy = False
        self.setWindowTitle(t("ui.stream.title"))
        self.setMinimumWidth(620)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        v.addWidget(label(t("ui.stream.title"), "SectionTitle"))
        v.addWidget(label(t("ui.stream.intro"), "Hint", wrap=True))
        row = QHBoxLayout()
        self.address = QLineEdit()
        self.address.setPlaceholderText(t("ui.stream.placeholder"))
        self.address.returnPressed.connect(self.probe)
        row.addWidget(self.address, 1)
        self.probe_btn = button(t("ui.stream.probe"), "refresh", kind="primary")
        self.probe_btn.clicked.connect(self.probe)
        row.addWidget(self.probe_btn)
        v.addLayout(row)
        row = QHBoxLayout()
        self.status = StatusPill(t("ui.stream.idle"), "idle")
        row.addWidget(self.status)
        row.addStretch(1)
        self.search_btn = button(t("ui.stream.search"), "refresh", kind="ghost")
        self.search_btn.clicked.connect(self.search)
        row.addWidget(self.search_btn)
        v.addLayout(row)
        self.found = QListWidget()
        self.found.setMinimumHeight(130)
        self.found.itemDoubleClicked.connect(lambda _: self.accept())
        self.found.currentItemChanged.connect(lambda *_: self._update())
        v.addWidget(self.found, 1)
        v.addWidget(label(t("ui.stream.hint"), "Hint", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(t("ui.common.cancel"), kind="ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok = button(t("ui.stream.add"), "check", kind="primary")
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        v.addLayout(row)
        self._update()

    # ------------------------------------------------------------------ actions
    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.probe_btn.setEnabled(not busy)
        self.search_btn.setEnabled(not busy)
        if busy:
            self.status.set(text, "busy")

    def _add(self, url: str, text: str, select: bool = False) -> None:
        for i in range(self.found.count()):
            if self.found.item(i).data(Qt.ItemDataRole.UserRole) == url:
                return
        it = QListWidgetItem(theme.icon("camera", "accent"), text)
        it.setData(Qt.ItemDataRole.UserRole, url)
        self.found.addItem(it)
        if select or self.found.count() == 1:
            self.found.setCurrentItem(it)

    def probe(self) -> None:
        text = self.address.text().strip()
        if not text or self._busy:
            return
        self._set_busy(True, t("ui.stream.probing"))
        run_async(services.probe_stream_address, text, on_done=lambda r: self._probed(text, r),
                  on_error=lambda m: self._probed(text, []))

    def _probed(self, text: str, results: list[Any]) -> None:
        self._set_busy(False)
        self.found.clear()
        for r in results:
            info = services.stream_source_info(r.url)
            note = t("ui.stream.needs_login") if r.needs_login else t(f"ui.stream.proto.{r.protocol}")
            self._add(r.url, f"{info.label}   ({note})")
        if results:
            login = all(r.needs_login for r in results)
            self.status.set(t("ui.stream.login") if login else t("ui.stream.found", n=len(results)),
                            "watch" if login else "ok")
        elif services.looks_like_url(text):
            # A full address that did not answer the quick question may still open (some cameras only
            # answer a real player): the coach may add it and see in the preview.
            self._add(text, t("ui.stream.unanswered", label=services.stream_source_info(text).label))
            self.status.set(t("ui.stream.no_answer"), "watch")
        else:
            self.status.set(t("ui.stream.none"), "fix")
        self._update()

    def search(self) -> None:
        if self._busy:
            return
        self._set_busy(True, t("ui.stream.searching"))
        run_async(services.find_network_cameras, on_done=self._searched, on_error=lambda m: self._searched([]))

    def _searched(self, cams: list[Any]) -> None:
        self._set_busy(False)
        for c in cams:
            self._add(c.id, c.label)
        self.status.set(t("ui.stream.found", n=len(cams)) if cams else t("ui.stream.none_announced"),
                        "ok" if cams else "watch")
        self._update()

    def _update(self) -> None:
        self.ok.setEnabled(self.found.currentItem() is not None)

    def accept(self) -> None:
        it = self.found.currentItem()
        if it is None:
            return
        self.url = str(it.data(Qt.ItemDataRole.UserRole))
        super().accept()
