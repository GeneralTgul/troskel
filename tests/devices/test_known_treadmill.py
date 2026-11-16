"""A treadmill that leaves FTMS out of its advertisement until START (the Recoil, 2026-11-16) is still
found when it was connected before: by its address or its name. An unknown device without FTMS is not."""
from __future__ import annotations

import asyncio
import struct

import pytest

from lopanalys.core.bus import Bus
from lopanalys.core.i18n import t
from lopanalys.devices._scan import ble_report, discover
from lopanalys.devices.base import DeviceInfo
from lopanalys.devices.errors import DeviceError
from lopanalys.devices.ftms import FTMS_SERVICE, SUPPORTED_SPEED_RANGE, FtmsTreadmill, require_ftms
from lopanalys.devices.manager import DeviceManager

from .fakes import FakeBle, make_scanner

NEAR = [
    ("AA:01", "SW1000EAI-0001", -60, []),           # the treadmill, before START: no service
    ("BB:02", "Some speaker", -50, []),               # an unknown device without FTMS
    ("CC:03", "Other tread", -70, [FTMS_SERVICE]),    # another treadmill that advertises FTMS
]


def _scan(known):
    scanner = make_scanner(NEAR)
    found = asyncio.run(discover([FTMS_SERVICE], 0.1, scanner, known=known))
    return [d.address for d in found], scanner.calls[-1]


def test_a_known_address_is_found_without_the_service():
    found, call = _scan([DeviceInfo("aa:01", "")])
    assert found == ["AA:01", "CC:03"]
    assert "service_uuids" not in call              # no service filter in the scan itself


def test_a_known_name_is_found_without_the_service():
    found, _ = _scan([DeviceInfo("FF:FF", "SW1000EAI-0001")])
    assert found == ["AA:01", "CC:03"]


def test_an_unknown_device_without_the_service_is_not_found():
    found, call = _scan([])
    assert found == ["CC:03"]
    assert call.get("service_uuids") == [FTMS_SERVICE]   # without a known device the filter stays


def test_the_manager_finds_the_treadmill_used_before():
    store = {"treadmill": DeviceInfo("AA:01", "SW1000EAI-0001")}
    m = DeviceManager(Bus(), scanner_cls=make_scanner(NEAR), recall=store.get, qz_check=lambda: False)
    assert [d.address for d in m.scan_treadmills(0.1)] == ["AA:01", "CC:03"]
    m2 = DeviceManager(Bus(), scanner_cls=make_scanner(NEAR), recall=lambda _k: None, qz_check=lambda: False)
    assert [d.address for d in m2.scan_treadmills(0.1)] == ["CC:03"]


def test_qz_holding_the_treadmill_is_said_instead_of_a_scan():
    scanner = make_scanner(NEAR)
    m = DeviceManager(Bus(), scanner_cls=scanner, qz_check=lambda: True)
    with pytest.raises(DeviceError) as exc:
        m.scan_treadmills(0.1)
    assert str(exc.value) == t("devices.ftms.qz_has_it")
    assert scanner.calls == []


class _Services:
    def __init__(self, has: bool) -> None:
        self.has = has

    def get_service(self, uuid):
        return object() if self.has and uuid == FTMS_SERVICE else None


def test_require_ftms():
    class C:
        services = _Services(False)
    with pytest.raises(RuntimeError, match="service"):
        require_ftms(C())
    C.services = _Services(True)
    require_ftms(C())
    require_ftms(object())                         # a backend without a service list: not checked here


def test_a_device_without_ftms_gives_a_clear_message():
    ble = FakeBle(reads={SUPPORTED_SPEED_RANGE: struct.pack("<HHH", 100, 1800, 10)})
    ble.client_cls.services = _Services(False)
    tm = FtmsTreadmill(Bus(), client_cls=ble.client_cls)
    with pytest.raises(DeviceError) as exc:
        tm.connect(DeviceInfo("AA:01", "SW1000EAI-0001"))
    assert str(exc.value).startswith(t("devices.ftms.not_ftms", name="SW1000EAI-0001"))
    tm.disconnect()


def test_ble_report_writes_what_is_advertised(tmp_path):
    out = tmp_path / "ble.txt"
    assert ble_report(str(out), 0.1, make_scanner(NEAR)) == 0
    text = out.read_text(encoding="utf-8")
    assert "SW1000EAI-0001" in text and FTMS_SERVICE in text and "3 devices" in text
