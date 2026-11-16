"""DeviceManager: modes, scanning, remembered devices, the QZ/FTMS exclusion, disconnect_all."""
from __future__ import annotations

import struct
import threading

import pytest

from lopanalys.core.bus import TOPIC_TREADMILL, Bus
from lopanalys.core.i18n import t
from lopanalys.devices.base import ConnState, DeviceInfo
from lopanalys.devices.errors import DeviceError
from lopanalys.devices.ftms import FTMS_SERVICE, SUPPORTED_SPEED_RANGE, FtmsTreadmill
from lopanalys.devices.heart_rate import HR_SERVICE, BleHeartRate
from lopanalys.devices.manager import DeviceManager
from lopanalys.devices.manual import ManualTreadmill
from lopanalys.devices.sim import SimHeartRate, SimTreadmill

from .fakes import FakeBle, make_scanner

ENTRIES = [
    ("AA", "SW1000EAI-0001", -70, [FTMS_SERVICE]),
    ("HH", "HRM-Pro+:1", -50, [HR_SERVICE]),
    ("H2", "Polar H10", -80, [HR_SERVICE]),
]


def manager(qz=False, **kw):
    ble = FakeBle(reads={SUPPORTED_SPEED_RANGE: struct.pack("<HHH", 100, 1800, 10)})
    store: dict[str, DeviceInfo] = {}
    m = DeviceManager(Bus(), client_cls=ble.client_cls, scanner_cls=make_scanner(ENTRIES),
                      remember=store.__setitem__, recall=store.get, qz_check=lambda: qz,
                      sim_time_scale=50.0, **kw)
    return m, ble, store


def test_scans_split_by_service():
    m, _ble, _store = manager()
    assert [d.name for d in m.scan_treadmills(0.1)] == ["SW1000EAI-0001"]
    assert [d.address for d in m.scan_heart_rate(0.1)] == ["HH", "H2"]


def test_ftms_connects_nearest_and_remembers():
    m, ble, store = manager()
    try:
        tm = m.connect_treadmill("ftms")
        assert isinstance(tm, FtmsTreadmill) and m.treadmill is tm and m.treadmill_mode == "ftms"
        assert ble.client.address == "AA"
        assert store["treadmill"].address == "AA"
        assert m.status("treadmill").state == ConnState.CONNECTED
        hr = m.connect_heart_rate()
        assert isinstance(hr, BleHeartRate) and store["heart_rate"].address == "HH"
    finally:
        m.disconnect_all()
    assert m.treadmill is None and m.heart_rate is None
    assert m.status("treadmill").state == ConnState.DISCONNECTED


def test_ftms_uses_remembered_device_without_scanning():
    m, ble, store = manager()
    store["treadmill"] = DeviceInfo("ZZ", "Remembered")
    try:
        m.connect_treadmill("ftms")
        assert ble.client.address == "ZZ"
    finally:
        m.disconnect_all()


def test_ftms_refused_while_qz_runs():
    m, ble, _store = manager(qz=True)
    with pytest.raises(DeviceError) as exc:
        m.connect_treadmill("ftms")
    assert str(exc.value) == t("devices.ftms.qz_has_it")
    assert "QZ" in str(exc.value) and "läget QZ" in str(exc.value)
    assert ble.clients == []


def test_no_treadmill_found():
    m = DeviceManager(Bus(), scanner_cls=make_scanner([]), qz_check=lambda: False)
    with pytest.raises(DeviceError) as exc:
        m.connect_treadmill("ftms")
    assert t("devices.ftms.close_qz") in str(exc.value)


def test_bluetooth_off():
    m = DeviceManager(Bus(), scanner_cls=make_scanner([], fail=True), qz_check=lambda: False)
    with pytest.raises(DeviceError) as exc:
        m.scan_heart_rate(0.1)
    assert str(exc.value) == t("devices.error.no_bluetooth")


def test_sim_pair_runs_and_disconnect_all_cleans_threads():
    before = threading.active_count()
    bus = Bus()
    got = []
    bus.subscribe(TOPIC_TREADMILL, got.append)
    m = DeviceManager(bus, qz_check=lambda: False, sim_time_scale=100.0)
    tm = m.connect_treadmill("sim")
    hr = m.connect_heart_rate("sim")
    assert isinstance(tm, SimTreadmill) and isinstance(hr, SimHeartRate)
    tm.set_target(10.0, 1.0)
    tm.start()
    import time
    time.sleep(0.3)
    assert hr.last is not None and got
    m.disconnect_all()
    m.disconnect_all()
    time.sleep(0.1)
    assert threading.active_count() <= before


def test_manual_and_unknown_mode():
    m, _ble, _store = manager()
    tm = m.connect_treadmill("manual")
    assert isinstance(tm, ManualTreadmill) and tm.controllable is False
    with pytest.raises(DeviceError):
        m.connect_treadmill("bike")
    with pytest.raises(DeviceError) as exc:
        m.connect_heart_rate("qz")
    assert str(exc.value) == t("devices.qz.hr_needs_qz")
    m.close()


def test_switching_modes_replaces_treadmill():
    m, ble, _store = manager()
    try:
        m.connect_treadmill("ftms")
        first = ble.client
        m.connect_treadmill("sim")
        assert not first.is_connected and isinstance(m.treadmill, SimTreadmill)
    finally:
        m.disconnect_all()
