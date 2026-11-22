"""Connecting again (real hardware 2026-11-14): no second link to the same device, the old one is
dropped first, and connects and disconnects are logged without names or addresses."""
from __future__ import annotations

import logging
import struct
import threading

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, Bus
from lopanalys.devices.base import ConnState, DeviceInfo
from lopanalys.devices.ftms import FTMS_SERVICE, SUPPORTED_SPEED_RANGE
from lopanalys.devices.heart_rate import HR_SERVICE
from lopanalys.devices.manager import DeviceManager

from .fakes import FakeBle, make_scanner

BAND = DeviceInfo(address="AA", name="SW1000EAI-0001")
BAND2 = DeviceInfo(address="AB", name="Other belt")
STRAP = DeviceInfo(address="HH", name="HRM-Pro+:Kim")


def manager():
    ble = FakeBle(reads={SUPPORTED_SPEED_RANGE: struct.pack("<HHH", 100, 1800, 10)})
    bus = Bus()
    m = DeviceManager(bus, client_cls=ble.client_cls,
                      scanner_cls=make_scanner([("AA", BAND.name, -60, [FTMS_SERVICE]),
                                                ("HH", STRAP.name, -50, [HR_SERVICE])]),
                      qz_check=lambda: False, settle_s=0.0, sim_time_scale=50.0)
    return m, ble, bus


def test_connect_again_keeps_the_connected_band():
    m, ble, _bus = manager()
    try:
        tm = m.connect_treadmill("ftms", BAND)
        assert m.connect_treadmill("ftms", BAND) is tm
        assert len(ble.clients) == 1 and ble.client.is_connected
        hr = m.connect_heart_rate(STRAP)
        assert m.connect_heart_rate(STRAP) is hr
        assert len(ble.clients) == 2
    finally:
        m.disconnect_all()


def test_another_band_drops_the_old_link_first():
    m, ble, _bus = manager()
    try:
        m.connect_treadmill("ftms", BAND)
        first = ble.client
        m.connect_treadmill("ftms", BAND2)
        assert not first.is_connected and ble.client.address == "AB" and ble.client.is_connected
        assert [c.is_connected for c in ble.clients] == [False, True]
    finally:
        m.disconnect_all()


def test_lost_band_is_connected_again_from_scratch():
    m, ble, _bus = manager()
    try:
        tm = m.connect_treadmill("ftms", BAND)
        m.treadmill._reconnect = False
        ble.client.drop()
        ble.wait_for(lambda: not tm.connected)
        tm2 = m.connect_treadmill("ftms", BAND)
        assert tm2 is not tm and tm2.connected
        assert sum(c.is_connected for c in ble.clients) == 1
    finally:
        m.disconnect_all()


def test_two_connects_at_once_give_one_link():
    m, ble, _bus = manager()
    out: list = []
    try:
        ths = [threading.Thread(target=lambda: out.append(m.connect_treadmill("ftms", BAND))) for _ in range(3)]
        for th in ths:
            th.start()
        for th in ths:
            th.join(10)
        assert len(out) == 3 and all(x is out[0] for x in out)
        assert len(ble.clients) == 1
    finally:
        m.disconnect_all()


def test_old_link_is_gone_before_the_new_one_opens():
    m, ble, bus = manager()
    states: list = []
    bus.subscribe(TOPIC_DEVICE_STATUS, lambda st: states.append((st.device, st.state)))
    try:
        m.connect_treadmill("sim")
        m.connect_treadmill("ftms", BAND)
        tm = [s for d, s in states if d == "treadmill"]
        i_dis = tm.index(ConnState.DISCONNECTED)
        assert ConnState.CONNECTING in tm[i_dis:]
        assert tm[-1] == ConnState.CONNECTED
    finally:
        m.disconnect_all()


def test_info_log_without_names_or_addresses(caplog):
    m, ble, _bus = manager()
    with caplog.at_level(logging.INFO, logger="lopanalys.devices"):
        m.connect_treadmill("ftms", BAND)
        m.connect_heart_rate(STRAP)
        m.treadmill._backoff_s = (0.05,)
        ble.clients[0].drop()
        ble.wait_for(lambda: any("reconnected" in r.getMessage() for r in caplog.records), 5)
        m.disconnect_all()
    text = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    for needed in ("treadmill (ftms): connecting", "treadmill (ftms): connected", "connection lost",
                   "reconnected", "heart rate (ble): connected", "treadmill (ftms): disconnected",
                   "heart rate (ble): disconnected"):
        assert needed in text, needed
    for secret in ("SW1000", "HRM-Pro", "Kim", "AA", "HH"):
        assert secret not in text, secret


def test_a_reconnect_that_hangs_is_given_up_and_tried_again():
    """Only the first connect had a time limit: a call that never came back in a reconnect left the
    device in "connecting" for ever (before 2026-11-23)."""
    import asyncio

    from lopanalys.devices._runtime import SupervisedConnection

    class Conn(SupervisedConnection):
        opens = 0

        async def _open(self):
            self.opens += 1
            self.lost = asyncio.Event()
            if self.opens == 2:
                await asyncio.Event().wait()         # the reconnect never comes back

        async def _wait_lost(self):
            await self.lost.wait()

        async def _close(self):
            pass

    c = Conn(None, name="x", connect_timeout_s=0.3, backoff_s=(0.01,))
    try:
        c._start_session()
        assert c.connected
        c._io.call_soon(lambda: c.lost.set())
        deadline = threading.Event()
        for _ in range(100):
            if c.opens >= 3 and c.connected:
                break
            deadline.wait(0.05)
        assert c.opens >= 3 and c.connected and c.status.state == ConnState.CONNECTED
    finally:
        c.disconnect()
