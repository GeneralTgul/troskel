"""The tests stay on this machine: tests/conftest.py refuses TCP connects outside loopback."""
import socket
import threading
import urllib.error
import urllib.request

import pytest


def test_a_documentation_address_is_never_dialled():
    with pytest.raises(ConnectionRefusedError, match="stay on this machine"):
        socket.create_connection(("192.0.2.50", 8081), timeout=1)
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen("http://192.0.2.50:8081/video", timeout=1)


def test_loopback_and_udp_still_work():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    t = threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True)
    t.start()
    socket.create_connection(srv.getsockname(), timeout=2).close()
    t.join(2)
    srv.close()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:   # sends nothing: finds the default route
        try:
            u.connect(("192.0.2.1", 9))
        except OSError as e:
            assert "stay on this machine" not in str(e)
