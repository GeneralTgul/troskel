"""The dialog "Lägg till telefon eller nätverkskamera": an address, or a search on the network.

The coach types what the phone app shows (an IP address is enough) and presses *Prova adressen*:
the program asks the usual ports and paths of the phone apps and lists the streams that answer.
*Sök på nätverket* lists the cameras that announce themselves (Bonjour/mDNS). Nothing is opened
here; the chosen address goes back to the wizard, which shows its preview like any other camera.

A stream that answers *login needed* (HTTP 401/403, RTSP 401) shows the fields *Användarnamn* and
*Lösenord*, and is not added without them. They go into the address as ``user:password@`` (percent-
encoded), which is how a camera's login has always been kept (the setting ``camera.recent``); labels
and log lines show the address without them (``camera.base.safe_url``). With ``url`` the dialog edits
a saved camera: the address and its login are filled in.
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
    """``exec()`` and read ``url`` (None when cancelled). ``url`` given: edit that saved camera."""

    def __init__(self, parent: QWidget | None = None, url: str | None = None) -> None:
        super().__init__(parent)
        from lopanalys.camera import network
        self.url: str | None = None
        self._busy = False
        self.editing = url
        self.setWindowTitle(t("ui.stream.title_edit") if url else t("ui.stream.title"))
        self.setMinimumWidth(620)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        v.addWidget(label(t("ui.stream.title_edit") if url else t("ui.stream.title"), "SectionTitle"))
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
        # The login: shown when a stream answers that it needs one (or the saved camera has one).
        self.login_row = QWidget()
        lr = QHBoxLayout(self.login_row)
        lr.setContentsMargins(0, 0, 0, 0)
        self.user = QLineEdit()
        self.user.setPlaceholderText(t("ui.stream.user"))
        self.user.returnPressed.connect(self.probe)
        lr.addWidget(self.user, 1)
        self.password = QLineEdit()
        self.password.setPlaceholderText(t("ui.stream.password"))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self.probe)
        lr.addWidget(self.password, 1)
        self.login_row.hide()
        v.addWidget(self.login_row)
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
        self.ok = button(t("ui.common.save") if url else t("ui.stream.add"), "check", kind="primary")
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        v.addLayout(row)
        if url:
            bare, user, password = network.split_credentials(url)
            self.address.setText(bare)
            self.user.setText(user)
            self.password.setText(password)
            self.login_row.setVisible(bool(user))
            self._add(url, services.stream_source_info(url).label, select=True)
            self.status.set(t("ui.stream.saved"), "idle")
        self._update()

    # ------------------------------------------------------------------ actions
    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.probe_btn.setEnabled(not busy)
        self.search_btn.setEnabled(not busy)
        if busy:
            self.status.set(text, "busy")

    def show_login(self) -> None:
        self.login_row.show()
        (self.password if self.user.text() else self.user).setFocus()

    def _typed(self) -> str:
        """The address as typed, with the login from the fields when a user name is filled in."""
        from lopanalys.camera import network
        text = self.address.text().strip()
        user = self.user.text().strip()
        return network.with_credentials(text, user, self.password.text()) if user and text else text

    def _add(self, url: str, text: str, select: bool = False, login: bool = False) -> None:
        for i in range(self.found.count()):
            if self.found.item(i).data(Qt.ItemDataRole.UserRole) == url:
                return
        it = QListWidgetItem(theme.icon("camera", "accent"), text)
        it.setData(Qt.ItemDataRole.UserRole, url)
        it.setData(Qt.ItemDataRole.UserRole + 1, login)
        self.found.addItem(it)
        if select or self.found.count() == 1:
            self.found.setCurrentItem(it)

    def probe(self) -> None:
        text = self._typed()
        if not text or self._busy:
            return
        self._set_busy(True, t("ui.stream.probing"))
        run_async(services.probe_stream_address, text, on_done=lambda r: self._probed(text, r),
                  on_error=lambda m: self._probed(text, []))

    def _probed(self, text: str, results: list[Any]) -> None:
        self._set_busy(False)
        from lopanalys.camera import network
        self.found.clear()
        given = bool(network.split_credentials(text)[1])
        for r in results:
            info = services.stream_source_info(r.url)
            # An HTTP stream is asked with the login, so 401 with one filled in is a wrong login. RTSP
            # is asked without it (OPTIONS): its 401 is checked in the preview instead.
            wrong = given and r.needs_login and network.is_http(r.url)
            if r.needs_login:
                note = t("ui.stream.wrong_login") if wrong else (
                    t("ui.stream.login_given") if given else t("ui.stream.needs_login"))
            else:
                note = t(f"ui.stream.proto.{r.protocol}")
            self._add(r.url, f"{info.label}   ({note})", login=r.needs_login and not given)
        if results:
            if not all(r.needs_login for r in results):
                self.status.set(t("ui.stream.found", n=len(results)), "ok")
            elif not given:
                self.show_login()
                self.status.set(t("ui.stream.login"), "watch")
            elif any(network.is_http(r.url) for r in results):
                self.status.set(t("ui.stream.wrong_login"), "fix")
            else:
                self.status.set(t("ui.stream.login_given"), "watch")
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
        from lopanalys.camera import network
        it = self.found.currentItem()
        if it is None:
            return
        url = str(it.data(Qt.ItemDataRole.UserRole))
        user = self.user.text().strip()
        if not self.login_row.isHidden():   # what the fields say now, also when not probed again
            url = network.with_credentials(url, user, self.password.text())
        if it.data(Qt.ItemDataRole.UserRole + 1) and not user:
            # It answered that it needs a login: not saved without one (it would never open).
            self.show_login()
            self.status.set(t("ui.stream.login_first"), "fix")
            return
        self.url = url
        super().accept()
