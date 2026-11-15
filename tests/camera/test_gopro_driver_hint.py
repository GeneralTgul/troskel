"""windows_driver_hint(): a GoPro that Windows bound to the serial driver (real hardware 2026-11-13)."""
import sys
import types

from lopanalys.camera import gopro
from lopanalys.core.i18n import t

USB = r"SYSTEM\CurrentControlSet\Enum\USB"
GOPRO = USB + r"\VID_2672&PID_0050\C3000000000830"


def fake_winreg(tree: dict[str, dict]) -> types.ModuleType:
    """tree: key path -> {"keys": [...], "values": {name: value}}"""
    m = types.ModuleType("winreg")
    m.HKEY_LOCAL_MACHINE = object()

    class Key:
        def __init__(self, path): self.path = path
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def open_key(root, path):
        if path not in tree:
            raise OSError(path)
        return Key(path)
    m.OpenKey = open_key
    m.QueryInfoKey = lambda k: (len(tree[k.path].get("keys", [])), len(tree[k.path].get("values", {})), 0)
    m.EnumKey = lambda k, i: tree[k.path]["keys"][i]
    m.EnumValue = lambda k, i: (list(tree[k.path]["values"].items())[i][0], list(tree[k.path]["values"].items())[i][1], 1)

    def query(k, name):
        if name not in tree[k.path].get("values", {}):
            raise OSError(name)
        return (tree[k.path]["values"][name], 1)
    m.QueryValueEx = query
    return m


def tree(service: str, live_port: str | None) -> dict[str, dict]:
    return {
        r"HARDWARE\DEVICEMAP\SERIALCOMM": {"values": {r"\Device\USBSER000": live_port} if live_port else {}},
        USB: {"keys": ["VID_046D&PID_C52B", "VID_2672&PID_0050"]},
        USB + r"\VID_2672&PID_0050": {"keys": ["C3000000000830"]},
        GOPRO: {"values": {"Service": service}},
        GOPRO + r"\Device Parameters": {"values": {"PortName": "COM3"}},
    }


def check(monkeypatch, service, live_port):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(tree(service, live_port)))
    return gopro.windows_driver_hint()


def test_gopro_on_serial_driver_gives_the_hint(monkeypatch):
    assert check(monkeypatch, "usbser", "COM3") == t("camera.gopro.hint.windows_driver", port="COM3")


def test_no_hint_when_the_camera_is_unplugged(monkeypatch):
    assert check(monkeypatch, "usbser", None) is None       # stale registry entry, port not live
    assert check(monkeypatch, "usbser", "COM7") is None     # another serial device is live


def test_no_hint_with_the_right_driver(monkeypatch):
    assert check(monkeypatch, "usb_rndisx", "COM3") is None


def test_no_hint_outside_windows():
    assert gopro.windows_driver_hint() is None
