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


def test_the_treadmill_used_before_is_listed_without_a_scan(demo_window, qtbot, monkeypatch):
    """The Recoil leaves FTMS out of its advertisement until START: the treadmill used last time is in
    the list at once, so Anslut connects to its address without a scan (2026-11-16)."""
    from lopanalys.devices.base import DeviceInfo
    nt = _to_devices(demo_window, qtbot)
    monkeypatch.setattr(nt.ctx.devices, "last_device",
                        lambda kind: DeviceInfo("AA:01", "SW1000EAI-0001") if kind == "treadmill" else None)
    nt._mode_chosen([m for m, _ in nt.modes].index("ftms"))
    assert nt.tm_list.count() == 1
    d = nt.tm_list.item(0).data(256)
    assert d.address == "AA:01" and "SW1000EAI-0001" in nt.tm_list.item(0).text()
    nt._add_remembered_tm([DeviceInfo("aa:01", "SW1000EAI-0001", -60)])    # found by the scan: not twice
    assert nt.tm_list.count() == 1


def test_a_lost_link_is_shown_while_the_device_reconnects(demo_window, qtbot):
    """Before 2026-11-22 the device step kept "Ansluten" when the treadmill or the strap dropped: the
    backends report a lost link as "connecting" (they try again), and the step only looked for "disconnected"."""
    from lopanalys.core.bus import TOPIC_DEVICE_STATUS
    from lopanalys.devices.base import ConnState, DeviceStatus
    nt = _to_devices(demo_window, qtbot)
    for device, pill in (("treadmill", nt.tm_status), ("heart_rate", nt.hr_status)):
        before = pill._text
        nt.ctx.bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus(device, ConnState.CONNECTING, "Band", ""))
        qtbot.waitUntil(lambda: pill._text != before, timeout=2000)
        assert pill._text == t("ui.dev.lost") and pill._state == "watch"
        nt.ctx.bus.publish(TOPIC_DEVICE_STATUS, DeviceStatus(device, ConnState.CONNECTED, "Band", ""))
        qtbot.waitUntil(lambda: pill._state == "ok", timeout=2000)
    assert nt.tm_connected and nt.hr_connected
