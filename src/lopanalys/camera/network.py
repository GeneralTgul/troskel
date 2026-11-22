"""Network cameras by address: the phone as a camera (RTSP or MJPEG over HTTP on the Wi-Fi).

Three parts, none of them with Qt:

* ``MjpegSource``: an HTTP ``multipart/x-mixed-replace`` reader of its own (no FFmpeg in between).
  A reader thread takes the parts off the socket as fast as they come and decodes only the newest
  complete picture, so a slow consumer or a burst on the Wi-Fi never builds up a delay. It reconnects
  with back-off like ``OpenCvSource``. RTSP goes through ``OpenCvSource`` (FFmpeg) as before.
* Finding the phone: ``normalize_address`` turns what the coach typed ("192.0.2.50", "…:8081/video",
  a full URL) into candidates, ``probe_address`` asks each candidate whether a stream answers there
  (HTTP: the content type; RTSP: an ``OPTIONS`` request), and ``mdns_browse`` asks the local network
  for ``_rtsp._tcp`` and ``_http._tcp`` services (Bonjour) with a small mDNS client of its own.
* ``StreamStats``: frame rate, the spread of the frame intervals and what was skipped, logged about
  every 10 s per stream. The delay that can be measured without a clock in the picture is the part
  the program adds (queueing: ``backlog_ms`` and skipped pictures); the camera-to-network part shows
  as the lag between the two views' steps (``pose.multiview``) and is tested with a clock on the gym PC.

Which phone apps serve which address is in ``PHONE_APPS``. None of it is checked against a source
(the dev box has no internet route): it is what the probe tries first, and docs/PROV.md has the
rows for the real test.
"""
from __future__ import annotations

import base64
import http.client
import logging
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import cv2
import numpy as np

from lopanalys.camera.base import Frame, SourceInfo, SourceKind, safe_url
from lopanalys.camera.opencv_source import CameraError, FpsMeter, SourceState, SourceStatus
from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

URL_RE = re.compile(r"^(rtsp|rtsps|rtmp|http|https|udp|srt)://", re.I)
STATS_EVERY_S = 10.0


@dataclass(frozen=True)
class PhoneApp:
    """One phone app and the addresses it is said to serve. ``verified`` stays False until someone
    has seen the stream open on the gym PC (docs/PROV.md)."""
    name: str
    platform: str                 # "ios" | "android" | "both"
    candidates: tuple[str, ...]   # URL templates with {host}
    note: str = ""
    verified: bool = False


# Not checked against a source (no internet on the dev box, 2026-11-14): ports and paths from memory.
PHONE_APPS: tuple[PhoneApp, ...] = (
    PhoneApp("IP Camera Lite", "ios", ("http://{host}:8081/video", "rtsp://{host}:8554/live"),
             "the app shows its own address; default user and password are said to be admin/admin"),
    PhoneApp("DroidCam", "both", ("http://{host}:4747/video",),
             "also a virtual webcam with its Windows client, then it is an ordinary camera in the list"),
    PhoneApp("IP Webcam", "android", ("http://{host}:8080/video", "rtsp://{host}:8080/h264_ulaw.sdp")),
)
# Tried after the apps' own addresses: common defaults of IP cameras.
GENERIC_CANDIDATES: tuple[str, ...] = ("rtsp://{host}:554/", "http://{host}:8080/mjpeg", "http://{host}/video")
MJPEG_PATHS: tuple[str, ...] = ("/video", "/mjpeg", "/live", "/")


def stream_info(url: str) -> SourceInfo:
    """The SourceInfo of a stream address (the kind for every network stream is RTSP, as before)."""
    return SourceInfo(SourceKind.RTSP, url, t("camera.label.stream", url=safe_url(url)))


def is_http(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def candidate_urls(host: str) -> list[str]:
    """Every address worth trying on a bare host, the phone apps' first, without duplicates."""
    out: list[str] = []
    for tpl in [c for app in PHONE_APPS for c in app.candidates] + list(GENERIC_CANDIDATES):
        u = tpl.format(host=host)
        if u not in out:
            out.append(u)
    return out


def normalize_address(text: str) -> list[str]:
    """What the coach typed -> the addresses to try, best first.

    A full URL is taken as it is. "host:port/path" becomes http:// (and rtsp:// for the usual RTSP
    ports). A bare host or IP address becomes every known candidate (``candidate_urls``).
    """
    text = text.strip().strip("<>").strip()
    if not text:
        return []
    if URL_RE.match(text):
        return [text]
    if "://" in text:
        return []
    creds, _, rest = text.rpartition("@")
    hostport, slash, path = rest.partition("/")
    host, colon, port = hostport.partition(":")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", host or "") or (colon and not port.isdigit()):
        return []
    auth = f"{creds}@" if creds else ""
    if not colon and not slash:
        return [u.replace("://", f"://{auth}", 1) for u in candidate_urls(host)]
    tail = f"{auth}{hostport}{slash}{path}"
    if port in ("554", "8554"):
        return [f"rtsp://{tail}", f"http://{tail}"]
    if not slash:   # a port but no path: the usual MJPEG paths on it, then RTSP
        return [f"http://{auth}{hostport}{p}" for p in MJPEG_PATHS[:2]] + [f"rtsp://{auth}{hostport}/"]
    return [f"http://{tail}", f"rtsp://{tail}"]


def split_credentials(text: str) -> tuple[str, str, str]:
    """``user:password@`` out of an address (a URL or what the coach typed): (address, user, password).
    The user and password come back decoded; the address has no credentials left."""
    text = text.strip()
    scheme, sep, rest = text.partition("://")
    if not sep:
        scheme, rest = "", text
    hostpart, slash, tail = rest.partition("/")
    if "@" not in hostpart:
        return text, "", ""
    creds, _, host = hostpart.rpartition("@")
    user, _, password = creds.partition(":")
    bare = f"{scheme}://" if sep else ""
    return f"{bare}{host}{slash}{tail}", unquote(user), unquote(password)


def with_credentials(text: str, user: str, password: str) -> str:
    """The address with ``user:password@`` put in (any earlier ones replaced). They are
    percent-encoded, so a password with ``@``, ``:`` or ``/`` stays one password. No user: no login."""
    bare, _, _ = split_credentials(text)
    if not user:
        return bare
    creds = quote(user, safe="") + (":" + quote(password, safe="") if password else "") + "@"
    scheme, sep, rest = bare.partition("://")
    return f"{scheme}://{creds}{rest}" if sep else f"{creds}{bare}"


# =============================================================================================
# Statistics
# =============================================================================================

class StreamStats:
    """Counts what a network stream delivers. ``tick`` per received picture, ``snapshot`` any time."""

    def __init__(self, window: int = 120) -> None:
        self._t: deque[float] = deque(maxlen=window)
        self._lock = threading.Lock()
        self.frames = 0
        self.skipped = 0            # received but never decoded: a newer picture was already there
        self.bytes = 0
        self.reconnects = 0
        self.connect_ms: float | None = None
        self.first_frame_ms: float | None = None
        self.backlog_ms = 0.0       # how far behind the stream's own clock the reading is (RTSP)
        self._bytes_at = (time.perf_counter(), 0)
        self._logged = time.perf_counter()

    def tick(self, now: float, nbytes: int = 0) -> None:
        with self._lock:
            self._t.append(now)
            self.frames += 1
            self.bytes += nbytes

    def snapshot(self) -> dict[str, float | int | None]:
        """fps, the median and 95th percentile of the frame interval (ms), kbit/s and the counters."""
        with self._lock:
            ts = list(self._t)
            now = time.perf_counter()
            t0, b0 = self._bytes_at
            kbit = (self.bytes - b0) * 8 / 1000 / (now - t0) if now > t0 else 0.0
            self._bytes_at = (now, self.bytes)
        iv = sorted(b - a for a, b in zip(ts, ts[1:]))
        fps = (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) >= 5 and ts[-1] > ts[0] else None
        return {"fps": fps,
                "interval_ms": iv[len(iv) // 2] * 1000 if iv else None,
                "interval_p95_ms": iv[min(len(iv) - 1, int(len(iv) * 0.95))] * 1000 if iv else None,
                "kbit_s": kbit, "frames": self.frames, "skipped": self.skipped, "reconnects": self.reconnects,
                "connect_ms": self.connect_ms, "first_frame_ms": self.first_frame_ms,
                "backlog_ms": self.backlog_ms}

    def log_if_due(self, what: str, every_s: float = STATS_EVERY_S) -> None:
        now = time.perf_counter()
        if now - self._logged < every_s:
            return
        self._logged = now
        log.info("%s", format_stats(what, self.snapshot()))


def format_stats(what: str, s: dict[str, Any]) -> str:
    """One log line: frame rate, interval spread, bit rate, skipped pictures, reconnects, backlog."""
    def f(v: Any, fmt: str = "{:.0f}") -> str:
        return fmt.format(v) if v is not None else "?"
    return (f"stream {what}: {f(s.get('fps'), '{:.1f}')} fps, interval {f(s.get('interval_ms'))} ms "
            f"(p95 {f(s.get('interval_p95_ms'))} ms), {f(s.get('kbit_s'))} kbit/s, {s.get('skipped', 0)} skipped, "
            f"{s.get('reconnects', 0)} reconnects, backlog {f(s.get('backlog_ms'))} ms, "
            f"connect {f(s.get('connect_ms'))} ms, first frame {f(s.get('first_frame_ms'))} ms")


# =============================================================================================
# MJPEG over HTTP
# =============================================================================================

class NotMjpeg(CameraError):
    """The address answers, but not with a multipart MJPEG stream (FFmpeg may still read it)."""


def _request(url: str) -> urllib.request.Request:
    """A GET for ``url``; ``user:password@`` in the address becomes a Basic Authorization header."""
    parts = urlsplit(url)
    headers = {"User-Agent": "Lopanalys", "Accept": "multipart/x-mixed-replace, image/jpeg, */*"}
    netloc = parts.netloc
    if "@" in netloc:
        creds, _, netloc = netloc.rpartition("@")
        user, _, password = creds.partition(":")
        creds = f"{unquote(user)}:{unquote(password)}"
        headers["Authorization"] = "Basic " + base64.b64encode(creds.encode()).decode()
    return urllib.request.Request(urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")), headers=headers)


def _boundary(content_type: str) -> bytes | None:
    m = re.search(r'boundary="?([^";,\s]+)"?', content_type or "", re.I)
    if not m:
        return None
    b = m.group(1)
    return (b if b.startswith("--") else "--" + b).encode()


class MjpegParser:
    """Cuts a ``multipart/x-mixed-replace`` byte stream into JPEG pictures.

    ``feed(chunk)`` returns the complete pictures in the chunk (as bytes). Parts with a
    Content-Length are cut by length; without one the picture runs to the next boundary, or, when the
    server sends no usable boundary at all, from the JPEG start marker to the end marker.
    """

    MAX_BUFFER = 32 * 1024 * 1024

    def __init__(self, boundary: bytes | None) -> None:
        self.boundary = boundary
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buf += chunk
        out: list[bytes] = []
        while True:
            img = self._next()
            if img is None:
                break
            if img:
                out.append(img)
        if len(self._buf) > self.MAX_BUFFER:   # garbage without any picture: start over
            self._buf.clear()
        return out

    def _next(self) -> bytes | None:
        buf = self._buf
        if self.boundary is None:
            return self._by_markers()
        start = buf.find(self.boundary)
        if start < 0:
            # Some servers announce one boundary and send another: fall back to the JPEG markers.
            if len(buf) > 512 * 1024 and buf.find(b"\xff\xd8") >= 0:
                self.boundary = None
                return self._by_markers()
            return None
        head_end = buf.find(b"\r\n\r\n", start)
        sep = 4
        if head_end < 0:
            head_end, sep = buf.find(b"\n\n", start), 2
            if head_end < 0:
                return None
        headers = bytes(buf[start + len(self.boundary):head_end]).decode("latin-1", "replace")
        body = head_end + sep
        m = re.search(r"content-length:\s*(\d+)", headers, re.I)
        if m:
            n = int(m.group(1))
            if len(buf) < body + n:
                return None
            img = bytes(buf[body:body + n])
            del buf[:body + n]
            return img
        nxt = buf.find(self.boundary, body)
        if nxt < 0:
            return None
        img = bytes(buf[body:nxt]).rstrip(b"\r\n")
        del buf[:nxt]
        return img

    def _by_markers(self) -> bytes | None:
        buf = self._buf
        a = buf.find(b"\xff\xd8")
        if a < 0:
            del buf[:max(0, len(buf) - 1)]
            return None
        b = buf.find(b"\xff\xd9", a + 2)
        if b < 0:
            if a:
                del buf[:a]
            return None
        img = bytes(buf[a:b + 2])
        del buf[:b + 2]
        return img


@dataclass
class _Slot:
    frame: Frame | None = None
    seq: int = 0
    cond: threading.Condition = field(default_factory=threading.Condition)


class MjpegSource:
    """A live MJPEG-over-HTTP stream with a newest-picture-only reader thread and reconnect.

    Same interface as ``OpenCvSource``: ``open()``, ``frames()``, ``latest()``, ``close()``,
    ``actual_fps``, ``dropped``, ``state``, ``mode`` and ``on_status``; plus ``stats`` (StreamStats).
    ``open()`` raises ``NotMjpeg`` when the address answers with something else.
    """

    def __init__(self, info: SourceInfo, *, on_status: Callable[[SourceStatus], None] | None = None,
                 open_timeout_s: float = 6.0, stall_s: float = 3.0, max_reconnect_s: float = 5.0,
                 max_attempts: int | None = None, opener: Callable[..., Any] | None = None) -> None:
        self.info = info
        self.on_status = on_status
        self.open_timeout_s = open_timeout_s
        self.stall_s = stall_s
        self.max_reconnect_s = max_reconnect_s
        self.max_attempts = max_attempts
        self._opener = opener or urllib.request.urlopen
        self._resp: Any = None
        self._parser: MjpegParser | None = None
        self._slot = _Slot()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._meter = FpsMeter()
        self._grabbed = 0
        self._delivered = 0
        self.stats = StreamStats()
        self.state = SourceState.CLOSED
        self.mode: tuple[int, int, float] | None = None
        self._what = safe_url(info.id)

    # ---- public -------------------------------------------------------------------------------
    @property
    def actual_fps(self) -> float | None:
        return self._meter.fps

    @property
    def dropped(self) -> int:
        """Pictures received but never handed to the consumer (it was busy)."""
        return max(0, self._grabbed - self._delivered)

    def open(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._set_state(SourceState.OPENING, t("camera.status.opening", label=self.info.label))
        t0 = time.perf_counter()
        self._connect()
        self.stats.connect_ms = (time.perf_counter() - t0) * 1000
        self._opened_at = t0
        self._set_state(SourceState.RUNNING, t("camera.status.running", label=self.info.label))
        self._thread = threading.Thread(target=self._reader, name="cam-mjpeg", daemon=True)
        self._thread.start()

    def latest(self) -> Frame | None:
        return self._slot.frame

    def frames(self) -> Iterator[Frame]:
        last = 0
        while not self._stop.is_set():
            with self._slot.cond:
                self._slot.cond.wait_for(lambda: self._slot.seq != last or self._stop.is_set(), timeout=0.5)
                if self._stop.is_set() or self.state == SourceState.FAILED:
                    break
                if self._slot.seq == last or self._slot.frame is None:
                    continue
                last = self._slot.seq
                frame = self._slot.frame
            self._delivered += 1
            yield frame
        if self.state == SourceState.FAILED:
            raise CameraError(t("camera.error.lost", label=self.info.label))

    def close(self) -> None:
        self._stop.set()
        with self._slot.cond:
            self._slot.cond.notify_all()
        self._disconnect()        # unblocks a reader waiting on the socket
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=self.open_timeout_s + 2)
        self._thread = None
        if self.state != SourceState.FAILED:
            self._set_state(SourceState.CLOSED, t("camera.status.closed", label=self.info.label))

    def __enter__(self) -> "MjpegSource":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- internals ----------------------------------------------------------------------------
    def _set_state(self, state: SourceState, message: str = "") -> None:
        self.state = state
        if self.on_status:
            try:
                self.on_status(SourceStatus(state, message, self.actual_fps, self.dropped))
            except Exception:
                log.exception("on_status failed")

    def _connect(self) -> None:
        try:
            resp = self._opener(_request(self.info.id), timeout=self.open_timeout_s)
        except urllib.error.HTTPError as e:
            log.info("stream %s: HTTP %s", self._what, e.code)
            key = "camera.error.stream_auth" if e.code in (401, 403) else "camera.error.rtsp_open"
            raise CameraError(t(key, url=self._what)) from e
        except (OSError, ValueError, http.client.HTTPException) as e:   # the last: an answer that is not HTTP
            log.info("stream %s: %s", self._what, e)
            raise CameraError(t("camera.error.rtsp_open", url=self._what)) from e
        ctype = resp.headers.get("Content-Type", "") if getattr(resp, "headers", None) is not None else ""
        if "multipart" not in ctype.lower():
            try:
                resp.close()
            except Exception:
                pass
            raise NotMjpeg(t("camera.error.not_mjpeg", url=self._what, type=ctype or "?"))
        self._resp = resp
        self._parser = MjpegParser(_boundary(ctype))
        # The connect timeout also became the read timeout: a silent stream must not block for good.
        try:
            resp.fp.raw._sock.settimeout(max(self.stall_s, 1.0))   # type: ignore[attr-defined]
        except Exception:
            pass

    def _disconnect(self) -> None:
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                log.debug("closing the stream failed", exc_info=True)

    def _publish(self, image: np.ndarray, now: float) -> None:
        self._grabbed += 1
        self._meter.tick(now)
        if self.stats.first_frame_ms is None:
            self.stats.first_frame_ms = (now - self._opened_at) * 1000
            self.mode = (image.shape[1], image.shape[0], 0.0)
        frame = Frame(image=image, t=now, index=self._grabbed - 1)
        with self._slot.cond:
            self._slot.frame = frame
            self._slot.seq += 1
            self._slot.cond.notify_all()

    def _reader(self) -> None:
        last_ok = time.perf_counter()
        while not self._stop.is_set():
            resp = self._resp
            chunk = b""
            try:
                if resp is not None:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            except (OSError, ValueError, AttributeError, http.client.HTTPException):
                chunk = b""        # also a chunked stream cut off mid-way (IncompleteRead): reconnect
            now = time.perf_counter()
            if chunk and self._parser is not None:
                pictures = self._parser.feed(chunk)
                if pictures:
                    # Several complete pictures in one read: the reader was behind. Only the newest
                    # is decoded, the others are counted, so a burst never becomes a delay.
                    for old in pictures[:-1]:
                        self.stats.tick(now, len(old))
                        self.stats.skipped += 1
                    self.stats.tick(now, len(pictures[-1]))
                    image = cv2.imdecode(np.frombuffer(pictures[-1], np.uint8), cv2.IMREAD_COLOR)
                    if image is not None and image.size:
                        self._publish(image, now)
                        last_ok = now
                        self.stats.log_if_due(self._what)
                continue
            if self._stop.is_set():
                break
            if chunk == b"" and resp is not None and now - last_ok < self.stall_s:
                # A read timeout or a short pause: wait for the stall limit before reconnecting.
                time.sleep(0.02)
                if not getattr(resp, "closed", False) and not _at_eof(resp):
                    continue
            if not self._reconnect():
                if not self._stop.is_set():
                    self._set_state(SourceState.FAILED, t("camera.error.lost", label=self.info.label))
                break
            last_ok = time.perf_counter()

    def _reconnect(self) -> bool:
        self._disconnect()
        self._meter.reset()
        delay, attempt = 0.5, 0
        while not self._stop.is_set():
            if self.max_attempts is not None and attempt >= self.max_attempts:
                return False
            attempt += 1
            self._set_state(SourceState.RECONNECTING,
                            t("camera.status.reconnecting", label=self.info.label, n=attempt))
            try:
                self._connect()
                if self._stop.is_set():
                    self._disconnect()
                    return False
                self.stats.reconnects += 1
                log.info("stream %s: connected again (attempt %d)", self._what, attempt)
                self._set_state(SourceState.RUNNING, t("camera.status.running", label=self.info.label))
                return True
            except Exception as e:
                log.info("stream %s: reconnect failed: %s", self._what, e)
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, self.max_reconnect_s)
        return False


def _at_eof(resp: Any) -> bool:
    """True when the HTTP response has ended (the server closed the stream)."""
    try:
        fp = getattr(resp, "fp", None)
        return fp is None or bool(getattr(resp, "isclosed", lambda: False)())
    except Exception:
        return True


class HttpStreamSource:
    """An http(s) address: the MJPEG reader first, FFmpeg (``OpenCvSource``) when the address serves
    something else (HLS, an MPEG-TS stream). Everything else is passed on to the source that opened."""

    def __init__(self, info: SourceInfo, **kwargs: Any) -> None:
        self.info = info
        self._kwargs = kwargs
        mj = {k: v for k, v in kwargs.items() if k in ("on_status", "open_timeout_s", "stall_s", "max_reconnect_s",
                                                         "max_attempts", "opener")}
        self.inner: Any = MjpegSource(info, **mj)

    def __getattr__(self, name: str) -> Any:
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def open(self) -> None:
        try:
            self.inner.open()
        except NotMjpeg as e:
            from lopanalys.camera.opencv_source import OpenCvSource
            log.info("stream %s is not MJPEG (%s): trying FFmpeg", safe_url(self.info.id), e)
            kw = {k: v for k, v in self._kwargs.items() if k != "opener"}
            self.inner = OpenCvSource(self.info, **kw)
            self.inner.open()

    def frames(self) -> Iterator[Frame]:
        return self.inner.frames()

    def close(self) -> None:
        self.inner.close()


# =============================================================================================
# Probing an address
# =============================================================================================

@dataclass(frozen=True)
class ProbeResult:
    url: str
    ok: bool
    protocol: str = ""          # "mjpeg" | "rtsp" | "http" (answers, but not MJPEG)
    needs_login: bool = False
    detail: str = ""
    ms: float = 0.0


def probe_url(url: str, timeout_s: float = 1.5) -> ProbeResult:
    """Does a stream answer at ``url``? Never raises. HTTP: a GET that reads only the headers.
    RTSP: an OPTIONS request on a plain TCP connection (no FFmpeg, so it takes milliseconds)."""
    t0 = time.perf_counter()

    def done(ok: bool, protocol: str = "", login: bool = False, detail: str = "") -> ProbeResult:
        return ProbeResult(url, ok, protocol, login, detail, (time.perf_counter() - t0) * 1000)

    low = url.lower()
    if is_http(low):
        try:
            resp = urllib.request.urlopen(_request(url), timeout=timeout_s)
        except urllib.error.HTTPError as e:
            return done(e.code in (401, 403), "mjpeg", e.code in (401, 403), f"HTTP {e.code}")
        except (OSError, ValueError, http.client.HTTPException) as e:
            return done(False, detail=str(e))
        try:
            ctype = resp.headers.get("Content-Type", "")
        finally:
            resp.close()
        if "multipart" in ctype.lower():
            return done(True, "mjpeg", detail=ctype)
        return done(False, "http", detail=ctype)
    if low.startswith(("rtsp://", "rtsps://")):
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port or 554
        if not host:
            return done(False, detail="no host")
        bare = urlunsplit((parts.scheme, f"{host}:{port}", parts.path or "/", parts.query, ""))
        try:
            with socket.create_connection((host, port), timeout=timeout_s) as s:
                s.settimeout(timeout_s)
                s.sendall(f"OPTIONS {bare} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: Lopanalys\r\n\r\n".encode())
                reply = s.recv(512).decode("latin-1", "replace")
        except OSError as e:
            return done(False, detail=str(e))
        m = re.match(r"RTSP/1\.\d\s+(\d{3})", reply)
        if not m:
            return done(False, detail=reply[:40].strip())
        code = int(m.group(1))
        return done(code in (200, 401), "rtsp", code == 401, f"RTSP {code}")
    return done(False, detail="unsupported scheme")


def probe_address(text: str, timeout_s: float = 1.5) -> list[ProbeResult]:
    """Every candidate of ``normalize_address(text)`` that answers with a stream, best first.
    The candidates are asked at the same time, so a host that is not there costs one timeout."""
    urls = normalize_address(text)
    results: list[ProbeResult | None] = [None] * len(urls)

    def work(i: int, u: str) -> None:
        results[i] = probe_url(u, timeout_s)

    threads = [threading.Thread(target=work, args=(i, u), daemon=True, name="cam-probe") for i, u in enumerate(urls)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout_s + 2)
    found = [r for r in results if r is not None and r.ok]
    for r in results:
        if r is not None:
            log.info("probe %s: %s %s (%.0f ms)", safe_url(r.url), "ok" if r.ok else "no", r.detail, r.ms)
    return found


# =============================================================================================
# mDNS (Bonjour): which cameras announce themselves on the local network
# =============================================================================================

MDNS_GROUP = ("224.0.0.251", 5353)
MDNS_SERVICES = ("_rtsp._tcp.local", "_http._tcp.local")
_PTR, _TXT, _A, _SRV = 12, 16, 1, 33


@dataclass
class MdnsService:
    instance: str               # "IP Camera._rtsp._tcp.local"
    service: str                # "_rtsp._tcp.local"
    host: str = ""              # SRV target
    address: str = ""           # IPv4
    port: int = 0
    txt: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.instance[: -len(self.service) - 1] if self.instance.endswith("." + self.service) else self.instance

    def urls(self) -> list[str]:
        """The addresses to probe for this service."""
        if not self.address or not self.port:
            return []
        path = self.txt.get("path", "")
        if self.service.startswith("_rtsp"):
            return [f"rtsp://{self.address}:{self.port}/{path.lstrip('/')}"]
        paths = [path] if path else list(MJPEG_PATHS)
        return [f"http://{self.address}:{self.port}/{p.lstrip('/')}" for p in paths]


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        raw = label.encode("utf-8")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def mdns_query(names: Iterable[tuple[str, int]], unicast: bool = True) -> bytes:
    """A DNS query packet for (name, record type) pairs; ``unicast`` asks for a direct reply (QU)."""
    names = list(names)
    pkt = struct.pack("!HHHHHH", 0, 0, len(names), 0, 0, 0)
    for name, rtype in names:
        pkt += _encode_name(name) + struct.pack("!HH", rtype, 0x8001 if unicast else 0x0001)
    return pkt


def _read_name(data: bytes, pos: int, depth: int = 0) -> tuple[str, int]:
    labels: list[str] = []
    end: int | None = None
    while True:
        if pos >= len(data) or depth > 16:
            raise ValueError("bad name")
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:      # compression pointer
            if pos + 1 >= len(data):
                raise ValueError("bad pointer")
            ptr = ((n & 0x3F) << 8) | data[pos + 1]
            if end is None:
                end = pos + 2
            pos, depth = ptr, depth + 1
            continue
        labels.append(data[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
    return ".".join(labels), (end if end is not None else pos)


def parse_mdns(data: bytes) -> list[tuple[str, int, Any]]:
    """The records of a DNS packet as (name, type, value): PTR -> target name, SRV -> (host, port),
    A -> dotted address, TXT -> {key: value}. Unknown types and broken packets are left out."""
    out: list[tuple[str, int, Any]] = []
    try:
        _, _, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
        pos = 12
        for _ in range(qd):
            _, pos = _read_name(data, pos)
            pos += 4
        for _ in range(an + ns + ar):
            name, pos = _read_name(data, pos)
            rtype, _, _, rdlen = struct.unpack("!HHIH", data[pos:pos + 10])
            pos += 10
            rdata = data[pos:pos + rdlen]
            if rtype == _PTR:
                out.append((name, rtype, _read_name(data, pos)[0]))
            elif rtype == _SRV and rdlen >= 7:
                port = struct.unpack("!H", rdata[4:6])[0]
                out.append((name, rtype, (_read_name(data, pos + 6)[0], port)))
            elif rtype == _A and rdlen == 4:
                out.append((name, rtype, socket.inet_ntoa(rdata)))
            elif rtype == _TXT:
                txt: dict[str, str] = {}
                i = 0
                while i < len(rdata):
                    n = rdata[i]
                    k, _, v = rdata[i + 1:i + 1 + n].decode("utf-8", "replace").partition("=")
                    if k:
                        txt[k.lower()] = v
                    i += 1 + n
                out.append((name, rtype, txt))
            pos += rdlen
    except (ValueError, struct.error, IndexError):
        log.debug("unreadable mDNS packet (%d bytes)", len(data))
    return out


def _local_ipv4() -> list[str]:
    """This computer's IPv4 addresses (the query goes out on each: the GoPro's USB network is one too)."""
    found: list[str] = []
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = res[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    try:   # the address of the default route, when the host name does not resolve to it
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def mdns_browse(services: Sequence[str] = MDNS_SERVICES, timeout_s: float = 1.5, *,
                targets: Sequence[tuple[str, int]] | None = None) -> list[MdnsService]:
    """Ask the local network which ``services`` are announced (Bonjour). Never raises.

    One-shot queries from an ordinary UDP port with the unicast bit, so nothing has to bind port
    5353 (Bonjour itself may hold it on Windows). ``targets`` replaces the multicast group in tests.
    """
    found: dict[str, MdnsService] = {}
    hosts: dict[str, str] = {}
    socks: list[socket.socket] = []
    try:
        if targets is None:
            for ip in _local_ipv4() or [""]:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                    if ip:
                        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                        s.bind((ip, 0))
                    socks.append(s)
                except OSError as e:
                    log.debug("mDNS socket on %s failed: %s", ip, e)
        else:
            socks.append(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        dests = list(targets) if targets is not None else [MDNS_GROUP]

        def send(pkt: bytes) -> None:
            for s in socks:
                for d in dests:
                    try:
                        s.sendto(pkt, d)
                    except OSError as e:
                        log.debug("mDNS send failed: %s", e)

        def take(data: bytes, sender: str) -> None:
            for name, rtype, value in parse_mdns(data):
                if rtype == _PTR:
                    svc = next((x for x in services if name.lower().rstrip(".") == x.lower()), None)
                    if svc:
                        found.setdefault(value, MdnsService(value, svc))
                elif rtype == _A:
                    hosts[name.lower()] = value
            for name, rtype, value in parse_mdns(data):
                item = found.get(name)
                if item is None:
                    continue
                if rtype == _SRV:
                    item.host, item.port = value
                    item.address = item.address or sender
                elif rtype == _TXT:
                    item.txt.update(value)

        send(mdns_query([(s, _PTR) for s in services]))
        end = time.monotonic() + timeout_s
        asked: set[str] = set()
        for s in socks:
            s.setblocking(False)
        import select
        while socks:
            left = end - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select(socks, [], [], min(left, 0.25))
            for s in ready:
                try:
                    data, addr = s.recvfrom(9000)
                except OSError:
                    continue
                take(data, addr[0])
            # Instances announced without their SRV record: ask for it once.
            missing = [i.instance for i in found.values() if not i.port and i.instance not in asked]
            if missing:
                asked.update(missing)
                send(mdns_query([(m, _SRV) for m in missing] + [(m, _TXT) for m in missing]))
    except Exception:
        log.exception("mDNS search failed")
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass
    for item in found.values():
        if item.host and hosts.get(item.host.lower()):
            item.address = hosts[item.host.lower()]
    out = [i for i in found.values() if i.port and i.address]
    log.info("mDNS: %d services found (%s)", len(out), ", ".join(f"{i.name} {i.address}:{i.port}" for i in out) or "none")
    return out


def find_network_cameras(timeout_s: float = 1.5, probe_timeout_s: float = 1.0) -> list[SourceInfo]:
    """Cameras that announce themselves with mDNS and really answer with a stream, as SourceInfo."""
    out: list[SourceInfo] = []
    seen: set[str] = set()
    for svc in mdns_browse(timeout_s=timeout_s):
        for url in svc.urls():
            if url in seen:
                continue
            seen.add(url)
            r = probe_url(url, probe_timeout_s)
            if r.ok:
                label = t("camera.label.stream_named", name=svc.name, url=safe_url(url))
                out.append(SourceInfo(SourceKind.RTSP, url, label))
                break
    return out
