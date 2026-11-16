"""Bluetooth LE scanning shared by the FTMS and heart rate backends and the DeviceManager."""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from lopanalys.core.i18n import t
from lopanalys.devices.base import DeviceInfo
from lopanalys.devices.errors import DeviceError

log = logging.getLogger(__name__)


def _default_scanner() -> Any:
    from bleak import BleakScanner
    return BleakScanner


FTMS_UUID = "00001826-0000-1000-8000-00805f9b34fb"
# Fitness Machine Type in the FTMS service data (flags byte, then a 16-bit field)
FTMS_TREADMILL, FTMS_ROWER, FTMS_INDOOR_BIKE = 0x0001, 0x0010, 0x0020


def ftms_machine_types(adv: Any) -> int | None:
    """The machine type bits an FTMS device advertises, or None when it does not say."""
    data = (getattr(adv, "service_data", None) or {})
    raw = next((v for k, v in data.items() if k.lower() == FTMS_UUID), None)
    if raw is None or len(raw) < 3:
        return None
    return int.from_bytes(bytes(raw)[1:3], "little")


def is_known(dev: Any, adv: Any, known: Iterable[DeviceInfo]) -> bool:
    """True when the advertisement is from a device connected before: the same address, or the same name.

    A treadmill does not always put its service in the advertisement: the Recoil showed FTMS only after
    START, while Zwift and QZ found it at once (2026-11-16). A device used before is found by its address
    or name instead."""
    address = str(getattr(dev, "address", "") or "").lower()
    names = {str(n).strip().lower() for n in (getattr(adv, "local_name", None), getattr(dev, "name", None)) if n}
    for k in known:
        if k.address and k.address.lower() == address:
            return True
        if k.name and k.name.strip().lower() in names:
            return True
    return False


async def discover(service_uuids: Iterable[str], timeout_s: float = 8.0,
                   scanner_cls: Any = None, accept: Callable[[Any, Any], bool] | None = None,
                   filter_scan: bool = True, known: Iterable[DeviceInfo] = ()) -> list[DeviceInfo]:
    """Scan for devices advertising any of ``service_uuids``; strongest signal first.

    The advertisement is filtered here as well, because the Windows backend does not always honour
    the scanner's service filter. ``accept(device, advertisement)`` narrows the list further, or
    with ``filter_scan=False`` decides alone (devices found by name, like a PM5).

    ``known``: devices connected before. Then the scan runs without the service filter, and a device
    counts if it advertises the service or matches a known address or name (``is_known``).
    """
    wanted = {u.lower() for u in service_uuids}
    known = list(known)
    scanner = scanner_cls or _default_scanner()
    try:
        kw = {"service_uuids": list(wanted)} if filter_scan and not known else {}
        found = await scanner.discover(timeout=timeout_s, return_adv=True, **kw)
    except Exception as exc:  # noqa: BLE001 - adapter missing or switched off
        log.warning("BLE scan failed: %s", exc)
        raise DeviceError(t("devices.error.no_bluetooth")) from exc
    out: list[DeviceInfo] = []
    for address, (dev, adv) in dict(found).items():
        uuids = {u.lower() for u in (getattr(adv, "service_uuids", None) or [])}
        uuids |= {u.lower() for u in (getattr(adv, "service_data", None) or {})}
        if filter_scan and not uuids & wanted:
            if not (known and is_known(dev, adv, known)):
                continue
            log.info("BLE scan: a device used before, found without the service in its advertisement")
        if accept is not None and not accept(dev, adv):
            continue
        name = getattr(adv, "local_name", None) or getattr(dev, "name", None) or address
        out.append(DeviceInfo(address=address, name=name, rssi=getattr(adv, "rssi", None)))
    out.sort(key=lambda d: d.rssi if d.rssi is not None else -999, reverse=True)
    return out


def ble_report(out_path: str, timeout_s: float = 15.0, scanner_cls: Any = None) -> int:
    """Write every Bluetooth advertisement heard in ``timeout_s`` to ``out_path``: name, address, signal,
    service UUIDs and service data (``Lopanalys.exe --ble-scan <file>``). Shows whether a treadmill puts
    FTMS (0x1826) in its advertisement before START. The file stays on the PC."""
    import asyncio

    async def run() -> list[str]:
        scanner = scanner_cls or _default_scanner()
        try:
            found = await scanner.discover(timeout=timeout_s, return_adv=True, scanning_mode="active")
        except TypeError:   # a scanner without scanning_mode
            found = await scanner.discover(timeout=timeout_s, return_adv=True)
        lines = []
        def strongest(x: Any) -> int:
            return -(getattr(x[1][1], "rssi", None) or -999)
        for address, (dev, adv) in sorted(dict(found).items(), key=strongest):
            data = {k: bytes(v).hex() for k, v in (getattr(adv, "service_data", None) or {}).items()}
            name = getattr(adv, "local_name", None) or getattr(dev, "name", None)
            uuids = list(getattr(adv, "service_uuids", None) or [])
            lines.append(f"{address}\tname={name!r}\trssi={getattr(adv, 'rssi', None)}"
                         f"\tservice_uuids={uuids}\tservice_data={data}")
        return lines

    try:
        lines = asyncio.run(run())
        text = "\n".join(lines + [f"ble-scan: {len(lines)} devices"])
        code = 0
    except Exception as exc:  # noqa: BLE001 - written to the file, the caller reads it
        text, code = f"ble-scan failed: {type(exc).__name__}: {exc}", 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return code


def scan_sync(service_uuids: Iterable[str], timeout_s: float = 8.0, scanner_cls: Any = None,
              accept: Callable[[Any, Any], bool] | None = None, filter_scan: bool = True,
              known: Iterable[DeviceInfo] = ()) -> list[DeviceInfo]:
    """Blocking scan, run on a fresh worker thread with its own event loop.

    A separate thread keeps bleak off the Qt thread (WinRT needs an MTA thread there) and away from
    any loop the caller may already run.
    """
    import asyncio
    import threading

    result: list[DeviceInfo] = []
    error: list[BaseException] = []

    def work() -> None:
        try:
            result.extend(asyncio.run(discover(service_uuids, timeout_s, scanner_cls, accept, filter_scan,
                                               list(known))))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            error.append(exc)

    th = threading.Thread(target=work, name="ble-scan", daemon=True)
    th.start()
    th.join(timeout_s + 10.0)
    if error:
        raise error[0]
    return result
