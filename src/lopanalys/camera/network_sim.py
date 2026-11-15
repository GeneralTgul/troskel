"""A phone-camera stand-in for tests: MJPEG over HTTP, an RTSP port that answers OPTIONS, and mDNS.

``FakePhoneCamera`` serves ``multipart/x-mixed-replace`` on 127.0.0.1 at a chosen frame rate. It can
be made to drop the stream (``drop()``: every connection is cut and new ones are refused until
``resume()``), to ask for a login, and to leave out Content-Length. ``FakeRtspPort`` answers an RTSP
OPTIONS request, ``FakeMdns`` answers PTR/SRV/TXT/A queries for one service. No Qt, no network
beyond the loopback.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import cv2
import numpy as np

from lopanalys.camera.network import _A, _PTR, _SRV, _TXT, _encode_name


def numbered_frame(i: int, w: int = 320, h: int = 180) -> np.ndarray:
    """A picture with a moving dot and its number (so a test can see which one arrived)."""
    img = np.zeros((h, w, 3), np.uint8)
    cv2.circle(img, (10 + (i * 5) % (w - 20), h // 2), 8, (0, 200, 255), -1)
    cv2.putText(img, str(i), (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return img


class FakePhoneCamera:
    """``with FakePhoneCamera(fps=15) as cam: open(cam.url)``."""

    def __init__(self, fps: float = 15.0, size: tuple[int, int] = (320, 180), *, path: str = "/video",
                 content_length: bool = True, login: tuple[str, str] | None = None,
                 frame: Callable[[int], np.ndarray] | None = None, burst: int = 1) -> None:
        self.fps, self.size, self.path = fps, size, path
        self.content_length, self.login = content_length, login
        self.burst = burst                  # pictures sent back to back each tick (a Wi-Fi burst)
        self._frame = frame or (lambda i: numbered_frame(i, *size))
        self._dropped = threading.Event()
        self._stop = threading.Event()
        self.connections = 0
        self.sent = 0
        cam = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: object) -> None:   # quiet
                pass

            def do_GET(self) -> None:   # noqa: N802
                if cam._dropped.is_set():
                    self.send_error(503)
                    return
                if self.path.split("?")[0] != cam.path:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html>phone</html>")
                    return
                if cam.login is not None:
                    import base64
                    want = "Basic " + base64.b64encode(":".join(cam.login).encode()).decode()
                    if self.headers.get("Authorization") != want:
                        self.send_response(401)
                        self.send_header("WWW-Authenticate", 'Basic realm="cam"')
                        self.end_headers()
                        return
                cam.connections += 1
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                i = 0
                t0 = time.perf_counter()
                try:
                    while not cam._stop.is_set() and not cam._dropped.is_set():
                        out = b""
                        for _ in range(cam.burst):
                            ok, jpg = cv2.imencode(".jpg", cam._frame(cam.sent), [cv2.IMWRITE_JPEG_QUALITY, 70])
                            data = jpg.tobytes()
                            head = b"--frame\r\nContent-Type: image/jpeg\r\n"
                            if cam.content_length:
                                head += f"Content-Length: {len(data)}\r\n".encode()
                            out += head + b"\r\n" + data + b"\r\n"
                            cam.sent += 1
                        self.wfile.write(out)      # a burst leaves in one piece
                        self.wfile.flush()
                        i += 1
                        wait = t0 + i / cam.fps - time.perf_counter()
                        if wait > 0:
                            time.sleep(wait)
                except OSError:
                    pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True, name="fake-phone")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.path}"

    def drop(self) -> None:
        """The phone goes away: running streams end and new connections get an error."""
        self._dropped.set()

    def resume(self) -> None:
        self._dropped.clear()

    def __enter__(self) -> "FakePhoneCamera":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._srv.shutdown()
        self._srv.server_close()


class FakeRtspPort:
    """Answers one RTSP request per connection with ``code`` (200, 401, 404)."""

    def __init__(self, code: int = 200) -> None:
        self.code = code
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self.requests: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="fake-rtsp")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                c, _ = self._sock.accept()
            except OSError:
                continue
            with c:
                try:
                    self.requests.append(c.recv(1024).decode("latin-1"))
                    c.sendall(f"RTSP/1.0 {self.code} X\r\nCSeq: 1\r\nPublic: OPTIONS, DESCRIBE\r\n\r\n".encode())
                except OSError:
                    pass

    def __enter__(self) -> "FakeRtspPort":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(1)
        self._sock.close()


def _rr(name: str, rtype: int, rdata: bytes) -> bytes:
    return _encode_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def mdns_answer(records: list[bytes]) -> bytes:
    return struct.pack("!HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


class FakeMdns:
    """A unicast mDNS responder for one service instance on 127.0.0.1 (``mdns_browse(targets=[...])``).

    ``split``: answer the PTR query with the PTR only, so the client has to ask for SRV and TXT.
    """

    def __init__(self, service: str, instance: str, port: int, *, txt: dict[str, str] | None = None,
                 address: str = "127.0.0.1", split: bool = False) -> None:
        self.service, self.instance, self.port = service, f"{instance}.{service}", port
        self.txt, self.address, self.split = txt or {}, address, split
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.1)
        self.target = self._sock.getsockname()
        self.queries = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="fake-mdns")

    def _records(self, ptr: bool, rest: bool) -> list[bytes]:
        host = "phone.local"
        out = []
        if ptr:
            out.append(_rr(self.service, _PTR, _encode_name(self.instance)))
        if rest:
            out.append(_rr(self.instance, _SRV, struct.pack("!HHH", 0, 0, self.port) + _encode_name(host)))
            txt = b"".join(bytes([len(x)]) + x for x in (f"{k}={v}".encode() for k, v in self.txt.items()))
            out.append(_rr(self.instance, _TXT, txt or b"\x00"))
            out.append(_rr(host, _A, socket.inet_aton(self.address)))
        return out

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(2048)
            except OSError:
                continue
            self.queries += 1
            asks_ptr = _encode_name(self.service) + struct.pack("!H", _PTR) in data
            asks_srv = _encode_name(self.instance) + struct.pack("!H", _SRV) in data
            recs = self._records(asks_ptr, asks_srv or (asks_ptr and not self.split))
            if recs:
                self._sock.sendto(mdns_answer(recs), addr)

    def __enter__(self) -> "FakeMdns":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(1)
        self._sock.close()
