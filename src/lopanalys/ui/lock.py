"""The lock: a PIN to open the program, and a lock after a while without use.

The PIN keeps the next customer from seeing the previous one when the screen stands in the gym. It
protects the screen, not the files: whoever has the Windows account can read the data folder. A
forgotten PIN is removed by starting the program once with ``--reset-pin``.

The PIN is stored as a salted PBKDF2 hash in the settings (``lock.pin``), never as text.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from lopanalys.core.i18n import t
from lopanalys.ui import theme
from lopanalys.ui.widgets.common import button, label

MIN_PIN = 4
ITERATIONS = 200_000
IDLE_CHOICES = (0, 2, 5, 10, 30)     # minutes; 0 = never


def hash_pin(pin: str) -> dict[str, Any]:
    salt = os.urandom(16)
    return {"salt": salt.hex(), "n": ITERATIONS,
            "hash": hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, ITERATIONS).hex()}


def check_pin(pin: str, stored: Any) -> bool:
    try:
        want = bytes.fromhex(stored["hash"])
        got = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(stored["salt"]), int(stored["n"]))
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(want, got)


class LockScreen(QWidget):
    """Covers the whole main window until the PIN is typed. Shows no person and no test."""

    unlocked = Signal()

    def __init__(self, check: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._check = check
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        col = QVBoxLayout()
        col.setSpacing(14)
        logo = label("")
        logo.setPixmap(theme.pixmap("logo", "accent", 64))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(logo)
        title = label(t("ui.lock.title"), "PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(title)
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setPlaceholderText(t("ui.lock.pin"))
        self.pin.setFixedWidth(260)
        self.pin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pin.returnPressed.connect(self.try_unlock)
        self.open_btn = button(t("ui.lock.unlock"), kind="primary")
        self.open_btn.setFixedWidth(260)
        self.open_btn.clicked.connect(self.try_unlock)
        self.error = label("")
        self.error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error.setStyleSheet(f"color: {theme.TOKENS['fix']}; font-weight: 600;")
        for w in (self.pin, self.open_btn):
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(w)
            row.addStretch(1)
            col.addLayout(row)
        col.addWidget(self.error)
        outer.addLayout(col)
        outer.addStretch(3)

    def showEvent(self, e: Any) -> None:
        super().showEvent(e)
        self.pin.clear()
        self.error.clear()
        self.pin.setFocus()

    def try_unlock(self) -> None:
        if self._check(self.pin.text()):
            self.pin.clear()
            self.unlocked.emit()
        else:
            self.pin.clear()
            self.error.setText(t("ui.lock.wrong"))


def system_idle_seconds() -> float | None:
    """Seconds since the last key or mouse input on the whole computer (Windows), else None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = _LastInput()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):   # type: ignore[attr-defined]
            return None
        now = ctypes.windll.kernel32.GetTickCount()                         # type: ignore[attr-defined]
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:  # noqa: BLE001 - no idle time: the fallback below is used
        return None


class IdleWatcher(QObject):
    """Emits ``idle`` when nobody used the computer for ``minutes`` (0: never), then again only after
    someone has used it in between.

    It asks once a second instead of filtering every event of the program: an event filter on the
    application runs Python code for each paint and timer event, which the live view cannot afford.
    Windows tells the time since the last input; elsewhere a mouse that has not moved counts as idle.
    """

    idle = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._limit_s = 0.0
        self._fired = False
        self._pos = QCursor.pos()
        self._moved = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_minutes(self, minutes: float) -> None:
        """Start counting from now (called when the lock setting changes and after an unlock)."""
        self._timer.stop()
        self._limit_s = max(0.0, float(minutes or 0) * 60.0)
        self._fired = False
        self._moved = time.monotonic()
        if self._limit_s > 0:
            self._timer.start(int(min(1000.0, max(20.0, self._limit_s * 250))))

    def stop(self) -> None:
        self._timer.stop()

    def idle_seconds(self) -> float:
        pos = QCursor.pos()
        if pos != self._pos:
            self._pos, self._moved = pos, time.monotonic()
        since_start = time.monotonic() - self._moved
        system = system_idle_seconds()
        return since_start if system is None else min(system, since_start)

    def _tick(self) -> None:
        idle = self.idle_seconds()
        if idle < self._limit_s:
            self._fired = False
        elif not self._fired:
            self._fired = True
            self.idle.emit()
