"""Nytt test -> Enheter: one connect at a time, the same mode keeps the link, "Ansluten" for all."""
from __future__ import annotations

import threading
import time

from lopanalys.core.i18n import t


def _to_devices(w, qtbot):
    nt = w.new_test
    w.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    nt._next()
    nt._next()
    nt._next()                                     # a lactate test is not filmed: no camera step
    assert nt.stack.currentIndex() == 4
    qtbot.waitUntil(lambda: nt.tm_connected and nt.hr_connected, timeout=8000)   # demo connects itself
    return nt


def test_button_is_locked_while_connecting(demo_window, qtbot, monkeypatch):
    nt = _to_devices(demo_window, qtbot)
    calls: list[str] = []
    gate = threading.Event()
    real = nt.ctx.devices.connect_treadmill

    def slow(mode, dev=None):
        calls.append(mode)
        gate.wait(3)
        return real(mode, dev)
    monkeypatch.setattr(nt.ctx.devices, "connect_treadmill", slow)
    nt._connect_tm()
    assert not nt.tm_connect.isEnabled()
    assert not any(b.isEnabled() for b in nt.mode_group.buttons())
    nt._connect_tm()                               # a second press while connecting does nothing
    nt.tm_connect.click()
    time.sleep(0.1)
    assert calls == ["sim"]
    gate.set()
    qtbot.waitUntil(lambda: nt.tm_connected and nt.tm_connect.isEnabled(), timeout=5000)
    assert calls == ["sim"]
    assert all(b.isEnabled() for b in nt.mode_group.buttons())


def test_same_mode_keeps_the_connection_and_says_ansluten(demo_window, qtbot):
    nt = _to_devices(demo_window, qtbot)
    tm = nt.ctx.devices.treadmill
    nt._mode_chosen([b for b in nt.mode_group.buttons()].index(nt.mode_group.checkedButton()))
    assert nt.tm_connected and nt.ctx.devices.treadmill is tm
    nt._connect_tm()                               # connect again: the manager keeps the link
    qtbot.waitUntil(lambda: nt.tm_connected and nt.tm_connect.isEnabled(), timeout=5000)
    assert nt.ctx.devices.treadmill is tm
    assert t("ui.dev.connected_mode", mode="X").startswith(t("ui.dev.connected"))
