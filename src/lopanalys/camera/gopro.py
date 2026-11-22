"""GoPro HERO8 Black and later over USB: live webcam preview, recording high-fps clips on the SD card,
and downloading them for offline analysis. No GoPro software is needed on the PC.

How the USB link works
----------------------
With the camera's USB setting on *GoPro Connect* (Inställningar → Anslutningar → USB-anslutning →
GoPro Connect) the camera appears as a USB network adapter. The camera has the address
``172.2X.1YZ.51`` where X, Y, Z are the last three digits of its serial number, and the PC gets an
address on the same /24 (usually ``.50``-``.54``). Discovery therefore looks for local IPv4
addresses matching ``172.2x.1xx.*`` and talks to ``.51`` on that subnet.

Two API families
----------------
* **Legacy** (HERO8 Black, also HERO9 for webcam): port 80. Webcam ``/gp/gpWebcam/START?res=1080``
  (``&port=`` for the UDP port), ``/gp/gpWebcam/STOP``, ``/gp/gpWebcam/SETTINGS?fov=0|4|6``;
  camera control ``/gp/gpControl/command/shutter?p=1|0``, ``/gp/gpControl/command/mode?p=0``,
  ``/gp/gpControl/setting/<id>/<value>``; media list ``/gp/gpMediaList`` and files under
  ``/videos/DCIM/<folder>/<file>`` on port 8080.
* **Open GoPro** (HERO9 Black with Open GoPro firmware, HERO10-13): everything on port 8080.
  ``/gopro/webcam/start?res=12&fov=4&port=8554&protocol=TS``, ``/gopro/webcam/stop``,
  ``/gopro/webcam/exit``, ``/gopro/webcam/status``, ``/gopro/camera/shutter/start|stop``,
  ``/gopro/camera/setting?setting=<id>&option=<value>``, ``/gopro/camera/presets/set_group?id=1000``,
  ``/gopro/media/list``, ``/gopro/camera/keep_alive``, ``/gopro/camera/control/wired_usb?p=1``,
  ``/videos/DCIM/<folder>/<file>``.

The webcam stream is MPEG-TS over UDP from the camera to the PC, port 8554 by default.

Where each endpoint comes from (checked 2026-11-13): see ``ENDPOINT_SOURCES``. Endpoints marked
UNVERIFIED there are educated guesses that must be proven on a real camera (docs/PROV.md); the code
treats them as optional and never fails only because one of them is missing.
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from lopanalys.camera.base import Frame, SourceInfo, SourceKind
from lopanalys.camera.opencv_source import LOW_LATENCY_FFMPEG, CameraError, OpenCvSource, SourceStatus
from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

DEFAULT_STREAM_PORT = 8554
LEGACY_PORT = 80
OPEN_GOPRO_PORT = 8080
MEDIA_PORT = 8080
USB_SUBNET = re.compile(r"^172\.2(\d)\.1(\d)(\d)\.(\d{1,3})$")

# Where every endpoint is documented. "UNVERIFIED" = not found in any source we could read;
# used best-effort only and listed in docs/PROV.md.
ENDPOINT_SOURCES: dict[str, str] = {
    "/gp/gpWebcam/START?res=&port=": "goprowifihack HERO9/GoPro-Connect.md (res 1080/720/480); port= from gopro_as_webcam_on_linux",
    "/gp/gpWebcam/STOP": "goprowifihack HERO9/GoPro-Connect.md",
    "/gp/gpWebcam/SETTINGS?fov=": "goprowifihack HERO9/GoPro-Connect.md (0 wide, 4 linear, 6 narrow)",
    "/gp/gpWebcam/STATUS": "UNVERIFIED (not in the docs we read; used only if it answers)",
    "/gp/gpWebcam/EXIT": "UNVERIFIED (Open GoPro has webcam/exit; legacy equivalent assumed)",
    "/gp/gpControl/command/shutter?p=1|0": "goprowifihack HERO7/HERO7-Commands.md (HERO8 uses HERO7 commands)",
    "/gp/gpControl/command/mode?p=0": "goprowifihack HERO7/HERO7-Commands.md (video mode)",
    "/gp/gpControl/setting/2/9": "goprowifihack HERO7-Commands.md: 1080p",
    "/gp/gpControl/setting/3/1|5|8": "goprowifihack HERO7-Commands.md: 120/60/30 fps",
    "/gp/gpControl/setting/4/4": "goprowifihack HERO7-Commands.md: FOV Linear (HERO7 id; UNVERIFIED on HERO8)",
    "/gp/gpControl/info": "UNVERIFIED (model/serial; falls back to the IP when missing)",
    ":8080/gp/gpMediaList": "goprowifihack HERO7/Mediabrowsing.md",
    ":8080/videos/DCIM/<d>/<n>": "goprowifihack HERO7/Mediabrowsing.md",
    "/gopro/webcam/start?res&fov&port&protocol": "OpenGoPro SDK api/http_commands.py, models/streaming.py",
    "/gopro/webcam/stop|exit|status|preview": "OpenGoPro SDK api/http_commands.py",
    "/gopro/camera/shutter/{start|stop}": "OpenGoPro SDK api/http_commands.py",
    "/gopro/camera/setting?setting=&option=": "OpenGoPro SDK api/builders.py",
    "/gopro/camera/control/wired_usb?p=1": "OpenGoPro SDK gopro_wired.py (sent on connect)",
    "/gopro/camera/keep_alive": "OpenGoPro SDK api/http_commands.py",
    "/gopro/camera/presets/set_group?id=1000": "OpenGoPro SDK api/http_commands.py (id 1000 = video group: UNVERIFIED value)",
    "/gopro/camera/info": "OpenGoPro SDK api/http_commands.py (HERO12+)",
    "/gopro/media/list": "OpenGoPro SDK api/http_commands.py",
    "IP 172.2{X}.1{Y}{Z}.51, port 8080": "OpenGoPro SDK gopro_wired.py",
    "Open GoPro setting values 2=9 (1080), 3=1 (120 fps), 121=4 (Linear)": "OpenGoPro docs (fps 3/1 confirmed; 2/9 and 121/4 UNVERIFIED)",
}


class GoProApi(str, Enum):
    LEGACY = "legacy"      # HERO8 (and HERO9 without Open GoPro firmware)
    OPEN = "open"          # Open GoPro: HERO9 (fw 1.70+), HERO10-13


class WebcamStatus(IntEnum):
    """Open GoPro WebcamStatus (models/streaming.py). Legacy START answers with the same numbers."""
    OFF = 0
    IDLE = 1
    HIGH_POWER_PREVIEW = 2
    LOW_POWER_PREVIEW = 3
    UNKNOWN = -1


class WebcamError(IntEnum):
    """Open GoPro WebcamError (models/streaming.py)."""
    SUCCESS = 0
    SET_PRESET = 1
    SET_WINDOW_SIZE = 2
    EXEC_STREAM = 3
    SHUTTER = 4
    COM_TIMEOUT = 5
    INVALID_PARAM = 6
    UNAVAILABLE = 7
    EXIT = 8


# Open GoPro webcam resolution and FOV enums (models/streaming.py)
OPEN_RES = {480: 4, 720: 7, 1080: 12}
OPEN_FOV = {"wide": 0, "narrow": 2, "superview": 3, "linear": 4}
LEGACY_FOV = {"wide": 0, "linear": 4, "narrow": 6}

# Recording settings: (setting id, {value: option}) per API family
LEGACY_SETTINGS = {
    "resolution": (2, {1080: 9}),
    "fps": (3, {120: 1, 60: 5, 30: 8}),
    "lens": (4, {"linear": 4}),
}
OPEN_SETTINGS = {
    "resolution": (2, {1080: 9}),
    "fps": (3, {120: 1, 60: 5, 30: 8}),
    "lens": (121, {"wide": 0, "narrow": 2, "superview": 3, "linear": 4}),
}


class GoProError(CameraError):
    """A GoPro problem with a translated message for the coach. ``code`` is machine readable."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MediaItem:
    """One file on the SD card."""
    folder: str            # "100GOPRO"
    name: str              # "GX010042.MP4"
    size: int = 0          # bytes
    created: float = 0.0   # unix time (camera clock)
    modified: float = 0.0

    @property
    def path(self) -> str:
        return f"{self.folder}/{self.name}"

    @property
    def is_video(self) -> bool:
        return self.name.upper().endswith(".MP4")


@dataclass
class RecordedClip:
    """Result of ``GoProUsb.record_clip``."""
    path: Path
    media: MediaItem
    warnings: list[str] = field(default_factory=list)


# Cameras found by the latest searches, by serial: (host, port, model, api, firmware).
_KNOWN: dict[str, tuple[str, int | None, str | None, "GoProApi | None", str | None]] = {}


def ip_from_serial(serial: str) -> str:
    """Camera USB address from the serial number: 172.2X.1YZ.51 with XYZ = the last three digits."""
    digits = re.sub(r"\D", "", serial)
    if len(digits) < 3:
        raise ValueError(f"serial without three digits: {serial!r}")
    x, y, z = digits[-3:]
    return f"172.2{x}.1{y}{z}.51"


def local_ipv4_addresses() -> list[str]:
    """IPv4 addresses of this PC's network adapters (via ifaddr, MIT; falls back to the hostname)."""
    try:
        import ifaddr  # type: ignore[import-not-found]
        out = []
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str):
                    out.append(ip.ip)
        return out
    except Exception:
        try:
            return list({ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
        except OSError:
            return []


def usb_camera_candidates(addresses: Iterable[str] | None = None) -> list[str]:
    """Camera addresses (x.x.x.51) on every local subnet that looks like a GoPro USB link."""
    out: list[str] = []
    for a in addresses if addresses is not None else local_ipv4_addresses():
        m = USB_SUBNET.match(a)
        if m and a.rsplit(".", 1)[1] != "51":
            cam = a.rsplit(".", 1)[0] + ".51"
            if cam not in out:
                out.append(cam)
    return out


class GoProUsb:
    """One GoPro on a USB network link.

    ``host`` is the camera address. ``port`` forces one HTTP port for every call (simulator,
    tests); real cameras use 80 (legacy) / 8080 (Open GoPro and media). Every method raises
    GoProError with a Swedish message when the camera refuses or does not answer.
    """

    def __init__(self, host: str, *, port: int | None = None, serial: str | None = None,
                 model: str | None = None, api: GoProApi | None = None,
                 stream_port: int = DEFAULT_STREAM_PORT, timeout_s: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.serial = serial
        self.model = model
        self.firmware: str | None = None
        self.api = api
        self.stream_port = stream_port
        self.timeout_s = timeout_s
        self._lock = threading.RLock()
        self.webcam_active = False

    def __repr__(self) -> str:
        return f"GoProUsb({self.host!r}, model={self.model!r}, serial={self.serial!r}, api={self.api})"

    # ---- discovery ----------------------------------------------------------------------------
    @classmethod
    def find(cls, timeout_s: float = 0.8, candidates: Iterable[str] | None = None) -> list["GoProUsb"]:
        """GoPros that answer on a USB link right now. ``candidates`` overrides the addresses to try
        (each "host" or "host:port"); by default they come from the local network adapters.
        A GoPro set to MTP instead of GoPro Connect has no network link and is not found."""
        hosts = list(candidates) if candidates is not None else usb_camera_candidates()
        if not hosts:
            # Logged so a missing GoPro in the list can be told apart from a camera that didn't answer.
            log.info("GoPro search: no USB network link (adapters: %s)", ", ".join(local_ipv4_addresses()) or "-")
            return []

        def probe(h: str) -> GoProUsb | None:
            cam = cls._from_hostport(h, timeout_s=timeout_s)
            try:
                cam.identify()
                return cam
            except GoProError:
                return None

        with ThreadPoolExecutor(max_workers=min(8, len(hosts))) as ex:
            found = [c for c in ex.map(probe, hosts) if c is not None]
        log.info("GoPro search: tried %s, found %s", ", ".join(hosts), found or "nothing")
        for c in found:
            c.timeout_s = 3.0
            if c.serial:
                _KNOWN[c.serial] = (c.host, c.port, c.model, c.api, c.firmware)
        return found

    @classmethod
    def _from_hostport(cls, hostport: str, **kw: Any) -> "GoProUsb":
        host, sep, port = hostport.rpartition(":")
        if sep and port.isdigit() and host:
            return cls(host, port=int(port), **kw)
        return cls(hostport, **kw)

    @classmethod
    def from_source_id(cls, source_id: str) -> "GoProUsb":
        """The camera behind a ``SourceInfo.id``: "host[:port]" or a serial number (looked up
        among connected cameras, else its address is computed from the serial)."""
        if re.match(r"^[\d.]+(:\d+)?$", source_id) or source_id.startswith("localhost"):
            return cls._from_hostport(source_id)
        known = _KNOWN.get(source_id)
        if known is not None:
            # Found by the search a moment ago: no second search when the preview opens.
            host, port, model, api, firmware = known
            cam = cls(host, port=port, serial=source_id, model=model, api=api)
            cam.firmware = firmware
            return cam
        for cam in cls.find():
            if cam.serial == source_id:
                return cam
        return cls(ip_from_serial(source_id), serial=source_id)

    @property
    def source_id(self) -> str:
        if self.port is not None:
            return f"{self.host}:{self.port}"
        return self.serial or self.host

    def source_info(self) -> SourceInfo:
        """SourceInfo for the live webcam stream (1080p30 by default)."""
        label = t("camera.label.gopro", model=self.model or "GoPro")
        return SourceInfo(SourceKind.GOPRO_USB, self.source_id, label, 1920, 1080, 30.0, live=True)

    # ---- HTTP ---------------------------------------------------------------------------------
    def _url(self, path: str, port: int) -> str:
        p = self.port if self.port is not None else port
        return f"http://{self.host}:{p}{path}"

    def _get(self, path: str, port: int, *, timeout: float | None = None) -> Any:
        """GET and parse JSON (empty body -> {}). Raises GoProError(code=http_<n>|unreachable)."""
        url = self._url(path, port)
        try:
            with urllib.request.urlopen(url, timeout=timeout or self.timeout_s) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise GoProError(t("camera.gopro.err.refused", path=path.split("?")[0], code=e.code), f"http_{e.code}") from e
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as e:
            # HTTPException: an answer that is not HTTP, or one broken off mid-way
            raise GoProError(t("camera.gopro.err.unreachable", host=self.host), "unreachable") from e
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except ValueError:
            return {"raw": body.decode("utf-8", "replace")}

    def _try(self, path: str, port: int, **kw: Any) -> Any | None:
        try:
            return self._get(path, port, **kw)
        except GoProError:
            return None

    @property
    def _cmd_port(self) -> int:
        return OPEN_GOPRO_PORT if self.api == GoProApi.OPEN else LEGACY_PORT

    # ---- identity -----------------------------------------------------------------------------
    def identify(self) -> "GoProUsb":
        """Works out the API family, model, serial and firmware. Raises GoProError if nothing answers."""
        with self._lock:
            ver = self._try("/gopro/version", OPEN_GOPRO_PORT)
            if isinstance(ver, dict) and ver and "raw" not in ver:
                self.api = GoProApi.OPEN
                self._try("/gopro/camera/control/wired_usb?p=1", OPEN_GOPRO_PORT)
                info = self._try("/gopro/camera/info", OPEN_GOPRO_PORT) or {}
                self._apply_info(info if isinstance(info, dict) else {})
                self.model = self.model or "GoPro"
                return self
            info = self._try("/gp/gpControl/info", LEGACY_PORT)
            if isinstance(info, dict) and info:
                self.api = GoProApi.LEGACY
                self._apply_info(info.get("info", info))
                self.model = self.model or "GoPro HERO8 Black"
                return self
            # Some firmware may not have /info: any answer from the webcam or status API will do.
            for path in ("/gp/gpControl/status", "/gp/gpWebcam/STATUS"):
                if self._try(path, LEGACY_PORT) is not None:
                    self.api = GoProApi.LEGACY
                    self.model = self.model or "GoPro"
                    return self
        raise GoProError(t("camera.gopro.err.unreachable", host=self.host), "unreachable")

    def _apply_info(self, info: dict[str, Any]) -> None:
        self.model = str(info.get("model_name") or self.model or "") or None
        self.serial = str(info.get("serial_number") or self.serial or "") or None
        self.firmware = str(info.get("firmware_version") or "") or None

    def _ensure_api(self) -> GoProApi:
        if self.api is None:
            self.identify()
        assert self.api is not None
        return self.api

    # ---- webcam -------------------------------------------------------------------------------
    def stream_url(self) -> str:
        """FFmpeg URL that receives the camera's UDP MPEG-TS stream on this PC."""
        # fifo_size is counted in 188-byte packets. Keep it small (about 0.7 MB, well under a second of
        # video): a big buffer turns every hiccup into a queue that is never drained, i.e. permanent delay.
        # With overrun_nonfatal the oldest data is dropped instead and the picture catches up.
        return f"udp://@0.0.0.0:{self.stream_port}?overrun_nonfatal=1&fifo_size=4096"

    def start_webcam(self, res: int = 1080, fov: str = "linear") -> WebcamStatus:
        """Starts the webcam stream (``res`` 1080/720/480, ``fov`` wide/linear/narrow).
        Raises GoProError with what the coach must do when the camera refuses."""
        api = self._ensure_api()
        with self._lock:
            if api == GoProApi.OPEN:
                q = urllib.parse.urlencode({"res": OPEN_RES.get(res, 12), "fov": OPEN_FOV.get(fov, 4),
                                            "port": self.stream_port, "protocol": "TS"})
                resp = self._webcam_call(f"/gopro/webcam/start?{q}", OPEN_GOPRO_PORT)
            else:
                resp = self._webcam_call(f"/gp/gpWebcam/START?res={res}&port={self.stream_port}", LEGACY_PORT)
                if fov in LEGACY_FOV:
                    self._try(f"/gp/gpWebcam/SETTINGS?fov={LEGACY_FOV[fov]}", LEGACY_PORT)
            self._check_webcam_error(resp)
            self.webcam_active = True
            return _status_of(resp)

    def _webcam_call(self, path: str, port: int) -> Any:
        try:
            return self._get(path, port, timeout=max(self.timeout_s, 8.0))
        except GoProError as e:
            if e.code in ("http_404", "http_403"):
                raise GoProError(t("camera.gopro.err.no_webcam_mode"), "no_webcam") from e
            if e.code.startswith("http_5"):
                raise GoProError(t("camera.gopro.err.busy"), "busy") from e
            raise

    @staticmethod
    def _check_webcam_error(resp: Any) -> None:
        if not isinstance(resp, dict):
            return
        try:
            err = WebcamError(int(resp.get("error", 0)))
        except (TypeError, ValueError):
            return
        if err == WebcamError.SUCCESS:
            return
        key = {
            WebcamError.SHUTTER: "camera.gopro.err.recording",
            WebcamError.UNAVAILABLE: "camera.gopro.err.unavailable",
            WebcamError.INVALID_PARAM: "camera.gopro.err.invalid_param",
            WebcamError.COM_TIMEOUT: "camera.gopro.err.timeout",
            WebcamError.SET_PRESET: "camera.gopro.err.set_preset",
        }.get(err, "camera.gopro.err.webcam_failed")
        raise GoProError(t(key, code=int(err)), f"webcam_{err.name.lower()}")

    def webcam_status(self) -> WebcamStatus:
        """Current webcam state, or UNKNOWN when the camera has no status endpoint."""
        api = self._ensure_api()
        if api == GoProApi.OPEN:
            resp = self._try("/gopro/webcam/status", OPEN_GOPRO_PORT)
        else:
            resp = self._try("/gp/gpWebcam/STATUS", LEGACY_PORT)
        return _status_of(resp)

    def stop_webcam(self, exit_mode: bool = True) -> None:
        """Stops the stream and, with ``exit_mode``, leaves webcam mode so the camera can record.
        Never raises: a camera that already stopped is fine."""
        api = self.api or self._safe_api()
        with self._lock:
            if api == GoProApi.OPEN:
                self._try("/gopro/webcam/stop", OPEN_GOPRO_PORT)
                if exit_mode:
                    self._try("/gopro/webcam/exit", OPEN_GOPRO_PORT)
            elif api == GoProApi.LEGACY:
                self._try("/gp/gpWebcam/STOP", LEGACY_PORT)
                if exit_mode:
                    self._try("/gp/gpWebcam/EXIT", LEGACY_PORT)   # UNVERIFIED on HERO8
            self.webcam_active = False

    def _safe_api(self) -> GoProApi | None:
        try:
            return self._ensure_api()
        except GoProError:
            return None

    def keep_alive(self) -> bool:
        """Tells the camera a client is present (Open GoPro). True if the camera answered."""
        api = self._safe_api()
        if api == GoProApi.OPEN:
            return self._try("/gopro/camera/keep_alive", OPEN_GOPRO_PORT) is not None
        return self._try("/gp/gpWebcam/STATUS", LEGACY_PORT) is not None or \
            self._try("/gp/gpControl/status", LEGACY_PORT) is not None

    # ---- recording ----------------------------------------------------------------------------
    def set_mode(self, fps: int = 120, resolution: int = 1080, lens: str = "linear") -> list[str]:
        """Switches to video and asks for ``resolution``/``fps``/``lens``. Best effort: returns the
        translated warnings for every setting the camera refused (the coach can set it by hand)."""
        api = self._ensure_api()
        warnings: list[str] = []
        table = OPEN_SETTINGS if api == GoProApi.OPEN else LEGACY_SETTINGS
        with self._lock:
            if api == GoProApi.OPEN:
                self._try("/gopro/camera/presets/set_group?id=1000", OPEN_GOPRO_PORT)
            else:
                self._try("/gp/gpControl/command/mode?p=0", LEGACY_PORT)
            # Resolution first: the allowed fps depend on it, and the lens on both.
            for key, value, label in (("resolution", resolution, f"{resolution}p"), ("fps", fps, t("camera.gopro.what.fps", fps=fps)),
                                      ("lens", lens, t(f"camera.gopro.lens.{lens}"))):
                sid, options = table[key]
                opt = options.get(value)  # type: ignore[call-overload]
                ok = False
                if opt is not None:
                    path = (f"/gopro/camera/setting?setting={sid}&option={opt}" if api == GoProApi.OPEN
                            else f"/gp/gpControl/setting/{sid}/{opt}")
                    ok = self._try(path, self._cmd_port) is not None
                if not ok:
                    warnings.append(t("camera.gopro.warn.setting", what=label))
        return warnings

    def start_recording(self) -> None:
        """Presses the shutter (starts a video on the SD card)."""
        api = self._ensure_api()
        path = "/gopro/camera/shutter/start" if api == GoProApi.OPEN else "/gp/gpControl/command/shutter?p=1"
        try:
            self._get(path, self._cmd_port)
        except GoProError as e:
            if e.code == "unreachable":
                raise
            raise GoProError(t("camera.gopro.err.record_start"), "record_start") from e

    def stop_recording(self) -> None:
        """Stops the video. Raises GoProError if the camera does not answer."""
        api = self._ensure_api()
        path = "/gopro/camera/shutter/stop" if api == GoProApi.OPEN else "/gp/gpControl/command/shutter?p=0"
        try:
            self._get(path, self._cmd_port)
        except GoProError as e:
            if e.code == "unreachable":
                raise
            raise GoProError(t("camera.gopro.err.record_stop"), "record_stop") from e

    # ---- media --------------------------------------------------------------------------------
    def list_media(self) -> list[MediaItem]:
        """Every file on the SD card, oldest first."""
        api = self._ensure_api()
        path = "/gopro/media/list" if api == GoProApi.OPEN else "/gp/gpMediaList"
        data = self._try(path, MEDIA_PORT)
        if data is None and api == GoProApi.LEGACY and self.port is None:
            data = self._try(path, LEGACY_PORT)   # USB docs show it without :8080
        if data is None:
            raise GoProError(t("camera.gopro.err.media_list"), "media_list")
        return parse_media_list(data)

    def media_url(self, media: MediaItem) -> str:
        return self._url(f"/videos/DCIM/{urllib.parse.quote(media.path)}", MEDIA_PORT)

    def download(self, media: MediaItem, dest_dir: str | Path,
                 progress_cb: Callable[[int, int], None] | None = None,
                 cancel: threading.Event | None = None) -> Path:
        """Downloads ``media`` to ``dest_dir`` (a ``.part`` file renamed when complete) and returns
        the path. ``progress_cb(done_bytes, total_bytes)``; ``cancel`` aborts and removes the part."""
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / media.name
        part = target.with_name(target.name + ".part")
        url = self.media_url(media)
        try:
            with urllib.request.urlopen(url, timeout=max(self.timeout_s, 10.0)) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or media.size or 0)
                done = 0
                while True:
                    if cancel is not None and cancel.is_set():
                        raise GoProError(t("camera.gopro.err.cancelled"), "cancelled")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
            if total and done != total:
                raise GoProError(t("camera.gopro.err.download_short", name=media.name), "download_short")
            shutil.move(str(part), str(target))
            return target
        except GoProError:
            part.unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as e:
            part.unlink(missing_ok=True)
            raise GoProError(t("camera.gopro.err.download", name=media.name), "download") from e

    # ---- the precise workflow -----------------------------------------------------------------
    def record_clip(self, seconds: float, dest_dir: str | Path, *, fps: int = 120, resolution: int = 1080,
                    lens: str = "linear", on_progress: Callable[[str, float], None] | None = None,
                    cancel: threading.Event | None = None, settle_s: float = 1.0,
                    appear_timeout_s: float = 30.0) -> RecordedClip:
        """Stops the webcam, records ``seconds`` at ``fps`` on the SD card, waits for the file and
        downloads it. ``on_progress(stage, fraction)`` with stages "prepare", "recording",
        "saving", "download". Blocking: run it off the UI thread."""
        def progress(stage: str, frac: float) -> None:
            if on_progress:
                on_progress(stage, max(0.0, min(1.0, frac)))

        def cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        progress("prepare", 0.0)
        self.stop_webcam(exit_mode=True)
        if settle_s:
            time.sleep(settle_s)
        warnings = self.set_mode(fps=fps, resolution=resolution, lens=lens)
        before = {m.path for m in self.list_media()}
        progress("prepare", 1.0)
        self.start_recording()
        start = time.perf_counter()
        try:
            while (elapsed := time.perf_counter() - start) < seconds:
                progress("recording", elapsed / seconds)
                if cancelled():
                    break
                time.sleep(min(0.1, seconds - elapsed))
        finally:
            self.stop_recording()
        if cancelled():
            raise GoProError(t("camera.gopro.err.cancelled"), "cancelled")
        progress("recording", 1.0)
        media = self._wait_for_new_video(before, appear_timeout_s, progress, cancel)
        path = self.download(media, dest_dir, lambda d, tot: progress("download", d / tot if tot else 0.0), cancel)
        progress("download", 1.0)
        return RecordedClip(path, media, warnings)

    def _wait_for_new_video(self, before: set[str], timeout_s: float,
                            progress: Callable[[str, float], None], cancel: threading.Event | None) -> MediaItem:
        """Polls the media list until a new MP4 appears and its size has stopped changing."""
        deadline = time.perf_counter() + timeout_s
        last_size: dict[str, int] = {}
        while time.perf_counter() < deadline:
            if cancel is not None and cancel.is_set():
                raise GoProError(t("camera.gopro.err.cancelled"), "cancelled")
            try:
                items = [m for m in self.list_media() if m.is_video and m.path not in before]
            except GoProError:
                items = []   # the camera is busy writing the file
            if items:
                newest = max(items, key=lambda m: (m.created, m.path))
                if last_size.get(newest.path) == newest.size and newest.size > 0:
                    return newest
                last_size[newest.path] = newest.size
            progress("saving", 1.0 - (deadline - time.perf_counter()) / timeout_s)
            time.sleep(0.5)
        raise GoProError(t("camera.gopro.err.no_new_file"), "no_new_file")


def _status_of(resp: Any) -> WebcamStatus:
    if isinstance(resp, dict) and "status" in resp:
        try:
            return WebcamStatus(int(resp["status"]))
        except (TypeError, ValueError):
            pass
    return WebcamStatus.UNKNOWN


def parse_media_list(data: Any) -> list[MediaItem]:
    """Parses the media list JSON (same shape for legacy and Open GoPro):
    {"media": [{"d": "100GOPRO", "fs": [{"n": "GX010001.MP4", "cre": "...", "mod": "...", "s": "..."}]}]}"""
    def num(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    out: list[MediaItem] = []
    for folder in (data or {}).get("media", []) if isinstance(data, dict) else []:
        d = str(folder.get("d", ""))
        for f in folder.get("fs", []) or []:
            name = str(f.get("n", ""))
            if not d or not name:
                continue
            out.append(MediaItem(d, name, int(num(f.get("s"))), num(f.get("cre")), num(f.get("mod"))))
    out.sort(key=lambda m: (m.created, m.path))
    return out


class GoProWebcamSource:
    """Live FrameSource for a GoPro's USB webcam stream (1080p30 by default).

    ``open()`` starts webcam mode and the UDP receiver; while it runs a keep-alive thread pings the
    camera and restarts webcam mode if the camera dropped out of it. ``close()`` stops the stream
    and leaves webcam mode, so the camera can record afterwards.
    """

    def __init__(self, gopro: GoProUsb, *, info: SourceInfo | None = None, res: int = 1080, fov: str = "linear",
                 on_status: Callable[[SourceStatus], None] | None = None, keepalive_s: float = 2.5,
                 open_timeout_s: float = 12.0, stall_s: float = 3.0, attempt_s: float = 4.0) -> None:
        self.gopro = gopro
        self.info = info or gopro.source_info()
        self.res = res
        self.fov = fov
        self.on_status = on_status
        self.keepalive_s = keepalive_s
        self.open_timeout_s = open_timeout_s   # total time for the stream to come up
        self.attempt_s = attempt_s             # one receiver attempt; then webcam mode is started again
        self.stall_s = stall_s
        self._stream: OpenCvSource | None = None
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._paused = threading.Event()
        self._ka: threading.Thread | None = None

    @property
    def actual_fps(self) -> float | None:
        return self._stream.actual_fps if self._stream else None

    @property
    def dropped(self) -> int:
        return self._stream.dropped if self._stream else 0

    def cancel_open(self) -> None:
        """Makes a running ``open()`` give up at its next step (called by the camera lease on close)."""
        self._cancel.set()

    def open(self) -> None:
        """Starts webcam mode and the receiver. Raises GoProError/CameraError in Swedish.

        Every step is logged with its time, so a slow start on the gym PC shows where the time goes.
        When the receiver gets nothing within ``attempt_s``, webcam mode is started again and the
        receiver tries once more, until ``open_timeout_s``: on the gym PC the first start after the camera
        had left webcam mode gave no stream, while the next start a little later did.
        """
        self._stop.clear()
        self._cancel.clear()
        cam = getattr(self.gopro, "source_id", "?")
        t0 = lap = time.perf_counter()

        def step(what: str) -> None:
            nonlocal lap
            now = time.perf_counter()
            log.info("GoPro %s: %s in %.2f s (%.2f s since open)", cam, what, now - lap, now - t0)
            lap = now

        def check_cancel() -> None:
            if self._cancel.is_set():
                log.info("GoPro %s: start cancelled after %.2f s", cam, time.perf_counter() - t0)
                self.gopro.stop_webcam(exit_mode=False)
                raise CameraError(t("camera.error.closed"))

        # GoPro's own Webcam program holds the camera and the stream port; close it first.
        closed_app = close_webcam_app()
        step("closed GoPro Webcam" if closed_app else "checked for GoPro Webcam")
        check_cancel()
        status = self.gopro.start_webcam(self.res, self.fov)
        api = getattr(self.gopro, "api", None)
        step(f"webcam start ({status.name}, {api.value if api else '?'} API)")
        if status == WebcamStatus.IDLE:
            # IDLE means webcam mode is on but not streaming: ask once more right away.
            try:
                status = self.gopro.start_webcam(self.res, self.fov)
                step(f"webcam start again after IDLE ({status.name})")
            except GoProError as e:
                step(f"second webcam start failed ({e})")
        stream_info = SourceInfo(SourceKind.GOPRO_USB, self.gopro.stream_url(), self.info.label,
                                 self.info.width, self.info.height, self.info.fps)
        self._stream = OpenCvSource(stream_info, ffmpeg_options=LOW_LATENCY_FFMPEG, on_status=self.on_status,
                                    open_timeout_s=self.attempt_s, stall_s=self.stall_s,
                                    before_reconnect=self._restart_webcam)
        deadline = lap + self.open_timeout_s
        attempt = 0
        while True:
            attempt += 1
            check_cancel()
            self._stream.open_timeout_s = max(1.0, min(self.attempt_s, deadline - time.perf_counter()))
            try:
                self._stream.open()
                step(f"stream open on port {self.gopro.stream_port} (attempt {attempt}, mode {self._stream.mode})")
                break
            except CameraError as e:
                step(f"no stream on port {self.gopro.stream_port} (attempt {attempt}: {e})")
                if self._cancel.is_set() or time.perf_counter() + 1.0 > deadline:
                    self.gopro.stop_webcam(exit_mode=False)
                    self._stream = None
                    log.warning("GoPro %s: no stream after %.1f s and %d attempts", cam,
                                time.perf_counter() - t0, attempt)
                    if self._cancel.is_set():
                        raise CameraError(t("camera.error.closed")) from e
                    raise GoProError(t("camera.gopro.err.no_stream", port=self.gopro.stream_port), "no_stream") from e
            # Start webcam mode again: the camera may have taken the first start without streaming.
            try:
                st = self.gopro.webcam_status()
                if st in (WebcamStatus.HIGH_POWER_PREVIEW, WebcamStatus.LOW_POWER_PREVIEW):
                    self.gopro.stop_webcam(exit_mode=False)
                self.gopro.start_webcam(self.res, self.fov)
                step(f"webcam started again (status was {st.name})")
            except GoProError as e:
                step(f"webcam restart failed ({e})")
        self._ka = threading.Thread(target=self._keepalive, name="gopro-keepalive", daemon=True)
        self._ka.start()

    def frames(self) -> Iterator[Frame]:
        if self._stream is None:
            self.open()
        assert self._stream is not None
        yield from self._stream.frames()

    def latest(self) -> Frame | None:
        return self._stream.latest() if self._stream else None

    def close(self) -> None:
        """Stops the receiver, the keep-alive thread and webcam mode. Safe to call twice."""
        self._stop.set()
        if self._stream is not None:
            self._stream.close()
        if self._ka is not None and self._ka is not threading.current_thread():
            self._ka.join(timeout=self.gopro.timeout_s + 1)
        self._ka = None
        if self._stream is not None:
            self.gopro.stop_webcam(exit_mode=True)
            self._stream = None

    def __enter__(self) -> "GoProWebcamSource":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_clip(self, seconds: float, dest_dir: str | Path, *, resume: bool = True,
                    **kwargs: Any) -> RecordedClip:
        """The precise workflow while the live view runs: pauses the webcam stream (``frames()``
        simply waits, the consumer keeps its loop), records ``seconds`` on the SD card at high fps,
        downloads the clip and, with ``resume``, starts the webcam again. Keyword arguments go to
        ``GoProUsb.record_clip`` (fps, on_progress, cancel, ...). Blocking: run it off the UI thread."""
        self._paused.set()
        try:
            return self.gopro.record_clip(seconds, dest_dir, **kwargs)
        finally:
            if resume and not self._stop.is_set():
                self._paused.clear()
                try:
                    self.gopro.start_webcam(self.res, self.fov)
                except GoProError:
                    log.warning("webcam did not restart after the clip; the receiver keeps trying")

    def _restart_webcam(self) -> None:
        """Called by the receiver before each reconnect: the camera may have left webcam mode."""
        if self._paused.is_set():
            raise GoProError("paused", "paused")   # the receiver backs off and tries again later
        if self._stop.is_set():
            return
        status = self.gopro.webcam_status()
        if status not in (WebcamStatus.HIGH_POWER_PREVIEW, WebcamStatus.LOW_POWER_PREVIEW):
            self.gopro.start_webcam(self.res, self.fov)

    def _keepalive(self) -> None:
        while not self._stop.wait(self.keepalive_s):
            if self._paused.is_set():
                continue
            try:
                self.gopro.keep_alive()
            except Exception:
                log.debug("keep-alive failed", exc_info=True)


GOPRO_USB_VENDOR = "VID_2672"


def windows_driver_hint() -> str | None:
    """What the coach must do when Windows has bound a connected GoPro to the wrong driver, else None.

    Windows 11 has no network driver of its own for the GoPro (it presents itself as RNDIS with the
    classic serial-port class codes), so without GoPro's driver the camera shows up as "USB Serial
    Device (COMx)" and never gets a network address: discovery then finds nothing (seen on the first
    real installation, 2026-11-13). Detected from the registry: a GoPro USB device served by ``usbser``
    whose COM port is live right now.
    """
    import sys
    if sys.platform != "win32":
        return None
    try:
        import winreg
        live: set[str] = set()
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as k:
                for i in range(winreg.QueryInfoKey(k)[1]):
                    live.add(str(winreg.EnumValue(k, i)[1]).upper())
        except OSError:
            return None   # no serial ports at all
        base = r"SYSTEM\CurrentControlSet\Enum\USB"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as usb:
            ids = [winreg.EnumKey(usb, i) for i in range(winreg.QueryInfoKey(usb)[0])]
        for dev in ids:
            if not dev.upper().startswith(GOPRO_USB_VENDOR):
                continue
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{dev}") as dk:
                serials = [winreg.EnumKey(dk, i) for i in range(winreg.QueryInfoKey(dk)[0])]
            for serial in serials:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{dev}\\{serial}") as ik:
                        service = str(winreg.QueryValueEx(ik, "Service")[0]).lower()
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        f"{base}\\{dev}\\{serial}\\Device Parameters") as pk:
                        port = str(winreg.QueryValueEx(pk, "PortName")[0]).upper()
                except OSError:
                    continue
                if service == "usbser" and port in live:
                    return t("camera.gopro.hint.windows_driver", port=port)
    except Exception:  # noqa: BLE001 - a hint must never break discovery
        log.exception("GoPro driver check failed")
    return None


WEBCAM_APP_EXE = "GoPro Webcam.exe"


def _run_hidden(args: list[str], timeout: float = 5.0) -> str:
    import subprocess
    import sys
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=flags)
    return r.stdout or ""


def webcam_app_running() -> bool:
    """True when GoPro's Webcam program runs (Windows only).

    It is needed once, for its driver, but while it runs it keeps the camera in its own webcam mode,
    takes the stream port and offers a virtual camera that crashes OpenCV. On the gym PC it started with
    Windows, and the live view then failed and the program died.
    """
    import sys
    if sys.platform != "win32":
        return False
    try:
        out = _run_hidden(["tasklist", "/FI", f"IMAGENAME eq {WEBCAM_APP_EXE}", "/NH", "/FO", "CSV"])
    except Exception:  # noqa: BLE001 - a check must never break the camera
        log.exception("tasklist failed")
        return False
    return WEBCAM_APP_EXE.lower() in out.lower()


_APP_CHECK_S = 30.0          # tasklist takes a second or more on Windows: a "not running" holds this long
_app_checked_at = -1e9


def close_webcam_app() -> bool:
    """Closes GoPro's Webcam program if it runs; True when it was running. Never raises."""
    global _app_checked_at
    import sys
    if sys.platform == "win32" and time.monotonic() - _app_checked_at < _APP_CHECK_S:
        return False
    if not webcam_app_running():
        if sys.platform == "win32":
            _app_checked_at = time.monotonic()
        return False
    _app_checked_at = -1e9
    try:
        _run_hidden(["taskkill", "/F", "/IM", WEBCAM_APP_EXE])
        log.info("closed %s, which held the GoPro", WEBCAM_APP_EXE)
        time.sleep(1.0)   # let the camera leave the program's webcam mode
    except Exception:  # noqa: BLE001
        log.exception("taskkill %s failed", WEBCAM_APP_EXE)
    return True
