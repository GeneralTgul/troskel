"""A simulated GoPro for tests, demos and development without the camera.

``FakeGoPro`` serves the legacy (HERO8) or Open GoPro HTTP API on one local port, streams an
MPEG-TS video over UDP when the webcam is started (like the real camera does to port 8554), turns
"recording" into a new MP4 on its simulated SD card, and serves the media list and downloads.
It follows the endpoint list in ``camera.gopro`` and lets tests switch on the failures a coach
can meet (webcam unavailable, camera busy recording, camera vanishing).

    with FakeGoPro(api="legacy", video=Path("clip.mp4")) as cam:
        gp = GoProUsb("127.0.0.1", port=cam.port, stream_port=cam.stream_port)
"""
from __future__ import annotations

import json
import logging
import shutil
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np

log = logging.getLogger(__name__)


def make_ts(path: Path, seconds: float = 2.0, fps: float = 30.0, size: tuple[int, int] = (320, 180)) -> Path:
    """Writes a small MPEG-TS clip (MPEG-4 video, moving dot) with OpenCV's FFmpeg; no ffmpeg binary needed."""
    # A keyframe every 5 frames: a receiver that joins mid-stream can start decoding at once,
    # as with the real camera's stream.
    w = cv2.VideoWriter(str(path), cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"mp4v"), fps, size,
                        [cv2.VIDEOWRITER_PROP_KEY_INTERVAL, 5])
    if not w.isOpened():
        raise RuntimeError("OpenCV cannot write MPEG-TS")
    for i in range(int(seconds * fps)):
        img = np.full((size[1], size[0], 3), 40, np.uint8)
        x = int((i * 5) % size[0])
        cv2.circle(img, (x, size[1] // 2), 12, (0, 220, 255), -1)
        w.write(img)
    w.release()
    return path


def make_mp4(path: Path, seconds: float = 1.0, fps: float = 120.0, size: tuple[int, int] = (320, 180)) -> Path:
    """A small MP4 standing in for a high-fps clip on the SD card."""
    w = cv2.VideoWriter(str(path), cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(int(seconds * fps)):
        img = np.zeros((size[1], size[0], 3), np.uint8)
        cv2.circle(img, (int(i * 2) % size[0], size[1] // 2), 10, (255, 255, 255), -1)
        w.write(img)
    w.release()
    return path


class FakeGoPro:
    """The simulated camera. ``api`` "legacy" (HERO8) or "open" (HERO9+)."""

    def __init__(self, api: str = "legacy", *, serial: str = "C3441325123456", model: str | None = None,
                 sd_dir: Path | None = None, stream_ts: Path | None = None, clip: Path | None = None,
                 stream_port: int | None = None) -> None:
        self.api = api
        self.serial = serial
        self.model = model or ("HERO8 Black" if api == "legacy" else "HERO12 Black")
        self._tmp = tempfile.TemporaryDirectory(prefix="fakegopro-")
        root = Path(self._tmp.name)
        self.sd = sd_dir or root / "DCIM"
        (self.sd / "100GOPRO").mkdir(parents=True, exist_ok=True)
        self.stream_ts = stream_ts or make_ts(root / "stream.ts")
        self.clip = clip or make_mp4(root / "clip.mp4")
        self.stream_port = stream_port or _free_udp_port()
        # Read the clip length now: OpenCV serialises FFmpeg opens process-wide, so opening the
        # file in the sender thread would wait for the receiver's open, which waits for data.
        cap = cv2.VideoCapture(str(self.stream_ts), cv2.CAP_FFMPEG)
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 60
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        cap.release()
        self._stream_duration = max(n / fps, 0.5)
        # state the tests can inspect or change
        self.webcam_status = 0           # WebcamStatus.OFF
        self.webcam_error_on_start = 0   # set to e.g. 7 (UNAVAILABLE) to refuse
        self.recording = False
        self.settings: dict[int, int] = {}
        self.calls: list[str] = []
        self.has_info = True
        self.stream_paused = False
        self._counter = 0
        self._stream_stop = threading.Event()
        self._stream_thread: threading.Thread | None = None
        self._stream_target: tuple[str, int] | None = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._http_thread: threading.Thread | None = None

    # ---- lifecycle ----------------------------------------------------------------------------
    def start(self) -> "FakeGoPro":
        self._http_thread = threading.Thread(target=self._server.serve_forever, name="fakegopro-http", daemon=True)
        self._http_thread.start()
        return self

    def stop(self) -> None:
        self._stop_stream()
        self._server.shutdown()
        self._server.server_close()
        self._tmp.cleanup()

    def __enter__(self) -> "FakeGoPro":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def hostport(self) -> str:
        return f"127.0.0.1:{self.port}"

    # ---- behaviour ----------------------------------------------------------------------------
    def _start_stream(self, port: int) -> None:
        self._stop_stream()
        self._stream_stop.clear()
        self._stream_target = ("127.0.0.1", port)
        self._stream_thread = threading.Thread(target=self._stream, name="fakegopro-udp", daemon=True)
        self._stream_thread.start()

    def _stop_stream(self) -> None:
        self._stream_stop.set()
        if self._stream_thread is not None:
            self._stream_thread.join(timeout=2)
        self._stream_thread = None

    def _stream(self) -> None:
        """Sends the TS file over UDP in 7x188-byte datagrams, paced to its real duration, in a loop."""
        data = self.stream_ts.read_bytes()
        duration = self._stream_duration
        chunk = 188 * 7
        per_chunk = duration / max(1, len(data) / chunk)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            while not self._stream_stop.is_set():
                for i in range(0, len(data), chunk):
                    if self._stream_stop.is_set():
                        break
                    if self.stream_paused:
                        time.sleep(0.05)
                        continue
                    assert self._stream_target is not None
                    sock.sendto(data[i:i + chunk], self._stream_target)
                    time.sleep(per_chunk)
        finally:
            sock.close()

    def _record_stop(self) -> None:
        if not self.recording:
            return
        self.recording = False
        self._counter += 1
        name = f"GX01{self._counter:04d}.MP4" if self.api == "open" else f"GH01{self._counter:04d}.MP4"
        shutil.copy(self.clip, self.sd / "100GOPRO" / name)

    def media_list(self) -> dict[str, Any]:
        media = []
        for d in sorted(p for p in self.sd.iterdir() if p.is_dir()):
            fs = [{"n": f.name, "cre": str(int(f.stat().st_mtime)), "mod": str(int(f.stat().st_mtime)),
                   "s": str(f.stat().st_size)} for f in sorted(d.iterdir()) if f.is_file()]
            media.append({"d": d.name, "fs": fs})
        return {"id": "1", "media": media}

    # ---- HTTP ---------------------------------------------------------------------------------
    def _handler(self) -> type[BaseHTTPRequestHandler]:
        cam = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:   # quiet
                pass

            def _json(self, obj: Any, code: int = 200) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                cam.calls.append(self.path)
                try:
                    route = cam._route_legacy if cam.api == "legacy" else cam._route_open
                    result = route(u.path, q)
                except Exception:
                    log.exception("fake gopro failed on %s", self.path)
                    return self._json({"error": "internal"}, 500)
                if result is None:
                    return self._json({}, 404)
                if isinstance(result, Path):
                    data = result.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                code, obj = result
                self._json(obj, code)

        return Handler

    def _file(self, path: str) -> Path | None:
        rel = unquote(path[len("/videos/DCIM/"):])
        p = (self.sd / rel).resolve()
        return p if p.is_file() and self.sd.resolve() in p.parents else None

    def _webcam_start(self, q: dict[str, str]) -> tuple[int, dict[str, int]]:
        if self.webcam_error_on_start:
            return 200, {"status": self.webcam_status, "error": self.webcam_error_on_start}
        if self.recording:
            return 200, {"status": self.webcam_status, "error": 4}
        self.webcam_status = 2
        self._start_stream(int(q.get("port", self.stream_port)))
        return 200, {"status": 2, "error": 0}

    def _webcam_stop(self) -> tuple[int, dict[str, int]]:
        self._stop_stream()
        self.webcam_status = 1
        return 200, {"status": 1, "error": 0}

    def _route_legacy(self, path: str, q: dict[str, str]) -> Any:
        if path == "/gp/gpControl/info":
            if not self.has_info:
                return None
            return 200, {"info": {"model_name": self.model, "serial_number": self.serial,
                                  "firmware_version": "HD8.01.02.51.00"}}
        if path == "/gp/gpControl/status":
            return 200, {"status": {"8": int(self.recording)}, "settings": {str(k): v for k, v in self.settings.items()}}
        if path == "/gp/gpWebcam/START":
            return self._webcam_start(q)
        if path == "/gp/gpWebcam/STOP":
            return self._webcam_stop()
        if path == "/gp/gpWebcam/EXIT":
            self._stop_stream()
            self.webcam_status = 0
            return 200, {"status": 0, "error": 0}
        if path == "/gp/gpWebcam/STATUS":
            return 200, {"status": self.webcam_status, "error": 0}
        if path == "/gp/gpWebcam/SETTINGS":
            return 200, {"status": self.webcam_status, "error": 0}
        if path == "/gp/gpControl/command/mode":
            return 200, {}
        if path.startswith("/gp/gpControl/setting/"):
            _, sid, val = path.rsplit("/", 2)
            if self.recording:
                return 403, {}
            self.settings[int(sid)] = int(val)
            return 200, {}
        if path == "/gp/gpControl/command/shutter":
            if q.get("p") == "1":
                if self.webcam_status in (2, 3):
                    return 500, {}
                self.recording = True
            else:
                self._record_stop()
            return 200, {}
        if path == "/gp/gpMediaList":
            return 200, self.media_list()
        if path.startswith("/videos/DCIM/"):
            return self._file(path)
        return None

    def _route_open(self, path: str, q: dict[str, str]) -> Any:
        if path == "/gopro/version":
            return 200, {"version": "2.0"}
        if path == "/gopro/camera/info":
            return 200, {"model_number": 62, "model_name": self.model, "firmware_version": "H23.01.02.32.00",
                         "serial_number": self.serial, "ap_mac_addr": "000000000000", "ap_ssid": "GP-SIM"}
        if path in ("/gopro/camera/control/wired_usb", "/gopro/camera/keep_alive", "/gopro/camera/presets/set_group"):
            return 200, {}
        if path == "/gopro/webcam/start":
            return self._webcam_start(q)
        if path == "/gopro/webcam/stop":
            return self._webcam_stop()
        if path == "/gopro/webcam/exit":
            self._stop_stream()
            self.webcam_status = 0
            return 200, {"status": 0, "error": 0}
        if path == "/gopro/webcam/status":
            return 200, {"status": self.webcam_status, "error": 0}
        if path == "/gopro/camera/setting":
            if self.recording:
                return 403, {}
            self.settings[int(q["setting"])] = int(q["option"])
            return 200, {}
        if path == "/gopro/camera/shutter/start":
            if self.webcam_status in (2, 3):
                return 500, {}
            self.recording = True
            return 200, {}
        if path == "/gopro/camera/shutter/stop":
            self._record_stop()
            return 200, {}
        if path == "/gopro/media/list":
            return 200, self.media_list()
        if path.startswith("/videos/DCIM/"):
            return self._file(path)
        return None


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
