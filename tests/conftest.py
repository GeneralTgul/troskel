"""Test-wide settings: no test may download anything or connect outside the machine."""
import os

os.environ["LOPANALYS_NO_DOWNLOAD"] = "1"


# No test may connect outside the machine. Example addresses in the tests (192.0.2.50, a documentation
# address) were really dialled: selecting a saved camera starts its preview, and the preview opened
# http://192.0.2.50:8081/video (2026-11-15, stopped by the build machine's firewall). A TCP connect to
# anything but loopback fails at once, as if nothing answered. UDP connect sends nothing and is allowed.
# Only Python's sockets are seen: FFmpeg inside OpenCV connects on its own.
def _only_loopback(event: str, args: tuple) -> None:
    if event != "socket.connect":
        return
    import ipaddress
    import socket
    sock, addr = args
    if sock.family not in (socket.AF_INET, socket.AF_INET6) or sock.type != socket.SOCK_STREAM:
        return
    try:
        if ipaddress.ip_address(str(addr[0]).split("%")[0]).is_loopback:
            return
    except ValueError:
        pass
    raise ConnectionRefusedError(111, f"tests stay on this machine, not {addr[0]}:{addr[1]} (tests/conftest.py)")


def _install_only_loopback() -> None:
    import sys
    if not getattr(sys, "_lopanalys_only_loopback", False):   # an audit hook cannot be removed: add it once
        sys.addaudithook(_only_loopback)
        sys._lopanalys_only_loopback = True


_install_only_loopback()

# Test files must have unique names: the folders are not packages, and on Windows CI two files with the
# same name stop the whole collection ("import file mismatch", tests/pose and tests/ui, 2026-11-14).
def _duplicate_test_names() -> list[str]:
    from collections import Counter
    from pathlib import Path
    names = Counter(p.name for p in Path(__file__).parent.rglob("test_*.py"))
    return sorted(n for n, k in names.items() if k > 1)


if _duplicate_test_names():
    raise RuntimeError(f"test files with the same name in different folders: {_duplicate_test_names()}")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _delete_windows_after_each_test():
    """Every window a test opened is deleted afterwards, not only closed. A closed window stays alive, and
    app.setStyleSheet repolishes every live widget: on Windows CI the hundreds of windows the earlier tests left
    made the style sheet in test_pin_locks_at_start_and_after_idle take hours (the build stood in Tester for
    1.5 h, 2026-11-15)."""
    yield
    import sys
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
