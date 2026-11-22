"""QZ (qdomyos-zwift) as a separate program: steer the treadmill through QZ's own websocket.

QZ is GPL-3. No QZ code is used here; this module only speaks the JSON messages QZ already
accepts on its websocket, learned by reading QZ's source (``templateinfosenderbuilder.cpp``,
``webserverinfosender.cpp``) and a local helper script ``qz_ws.ps1``. QZ itself, its settings and
its files are never changed; the app only talks to a QZ that is already running, and only when
the coach picks the QZ mode.

**One connection to the treadmill.** The treadmill accepts one Bluetooth connection at a time.
While QZ runs it holds the treadmill, so the FTMS mode needs QZ closed first
(``devices.ftms.close_qz``: "Stäng QZ först, bandet tar bara en anslutning åt gången"). The app
never closes QZ itself. ``qz_running()`` tells the DeviceManager whether QZ is up.

What was learned about QZ's websocket API
-----------------------------------------
Where:
  - QZ's built-in web server ("inner_QZWS" template) listens on HTTP port P and on websocket port
    P+1, all interfaces. QZ stores the websocket port in its settings, on Windows the registry
    ``HKCU\\Software\\Roberto Viola\\qDomyos-Zwift``, value ``template_inner_QZWS_wsport``.
  - Connect to ``ws://<host>:<wsport>/`` (path ``/``; ``/fetcher`` is a different HTTP proxy).
    Every client on ``/`` receives every message QZ sends, including replies to other clients.

Live data (QZ -> clients), once per second while a device is connected:
  ``{"msg": "workout", "content": {...}}`` where ``content`` has, among many others:
  ``deviceConnected`` (bool), ``devicePaused`` (bool), ``deviceType`` (1 = treadmill),
  ``deviceName``, ``speed`` (km/h), ``inclination`` (%), ``target_speed``, ``target_inclination``,
  ``heart`` (bpm, 0 when none), ``distance`` (odometer, km), ``elapsed_h/m/s``, ``cadence``,
  ``training_row_index`` (-1 without a loaded workout). Without a device the fields are absent.

Commands (client -> QZ), all ``{"msg": <name>, "content": {...}}``:
  - ``setspeed`` ``{"value": km/h}``: treadmill only; reply ``R_setspeed`` with ``{"value": v}``
    or ``{"value": null}`` when no treadmill is connected.
  - ``setresistance`` ``{"value": x}``: on a treadmill QZ calls ``changeInclination(x)``, so this
    sets the incline in %; reply ``R_setresistance``.
  - ``start`` / ``pause`` / ``stop`` (no content): QZ's own start, pause and stop buttons;
    replies ``R_start`` / ``R_pause`` / ``R_stop``. ``stop`` ends QZ's session (it may save and
    upload the activity), so this module pauses instead of stopping.
  - ``trainprogram_open_clicked`` ``{"url": "file:///C:/path/x.zwo"}`` loads a workout file and
    ``trainprogram_autostart_requested`` ``{}`` starts it (``qz_ws.ps1``; QZ upstream only
    starts it while the workout editor is open, our QZ build fixes that). Not used here: direct
    ``setspeed``/``setresistance`` is enough, so no workout files are written for QZ.

Speed and incline are therefore set directly; QZ forwards them to the treadmill over FTMS and the
treadmill ramps. QZ's Bluetooth heart rate (``heart``) is offered as ``QzHeartRate`` for the case
where QZ also holds the strap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import threading
import time
from typing import Any, Callable

from lopanalys.core.bus import TOPIC_HEART_RATE, TOPIC_TREADMILL, Bus
from lopanalys.core.i18n import t
from lopanalys.devices._runtime import Clock, SupervisedConnection, maybe_await
from lopanalys.devices.base import ConnState, DeviceInfo, HeartRateSample, TreadmillState
from lopanalys.devices.errors import DeviceError

log = logging.getLogger(__name__)

REGISTRY_KEY = r"Software\Roberto Viola\qDomyos-Zwift"
REGISTRY_VALUE = "template_inner_QZWS_wsport"
QZ_TREADMILL_TYPE = 1


def registry_port() -> int | None:
    """QZ's websocket port from the Windows registry, or None (not Windows, or QZ never ran)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, REGISTRY_VALUE)
        return int(str(value).strip())
    except (OSError, ValueError):
        return None


def qz_running(host: str = "127.0.0.1", port: int | None = None, timeout_s: float = 0.4) -> bool:
    """True if something listens on QZ's websocket port (QZ is running). Never touches QZ."""
    port = port if port is not None else registry_port()
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _default_ws_connect() -> Callable[..., Any]:
    from websockets.asyncio.client import connect
    return connect


class QzTreadmill(SupervisedConnection):
    """Treadmill steered through a running QZ. Thread-safe sync API (``devices.base.Treadmill``).

    ``port`` None reads the registry. ``ws_connect`` (default ``websockets.asyncio.client.connect``)
    is injected in tests. QZ does not report the treadmill's ranges, so they are parameters.
    """

    controllable = True
    device_kind = "treadmill"

    def __init__(self, bus: Bus | None = None, *, host: str = "127.0.0.1", port: int | None = None,
                 ws_connect: Callable[..., Any] | None = None, clock: Clock = time.time,
                 min_speed_kmh: float = 1.0, max_speed_kmh: float = 20.0, max_incline_pct: float = 15.0,
                 reply_timeout_s: float = 3.0, **kwargs: Any) -> None:
        super().__init__(bus, name=t("devices.qz.name"), clock=clock, **kwargs)
        self.host = host
        self.port = port
        self.min_speed_kmh = min_speed_kmh
        self.max_speed_kmh = max_speed_kmh
        self.max_incline_pct = max_incline_pct
        self.min_incline_pct = 0.0
        self._ws_connect = ws_connect
        self._reply_timeout_s = reply_timeout_s
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._lock = threading.Lock()
        self._state: TreadmillState | None = None
        self._target_speed: float | None = None
        self._target_incline: float | None = None
        self._stopped = False     # paused by the program: no speed goes out until a new target or start
        self._pause_unsent = False   # stop() while QZ was away: sent first thing on the reconnect
        self._waiters: dict[str, asyncio.Future[Any]] = {}
        self._apply_lock: asyncio.Lock | None = None   # one set of targets at a time, the newest last
        self._device_connected: bool | None = None
        self._hr_listeners: list[Callable[[HeartRateSample], None]] = []

    # --- Treadmill protocol ------------------------------------------------------------------
    @property
    def state(self) -> TreadmillState | None:
        with self._lock:
            return self._state

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        """QZ is not scanned for: returns the one QZ entry if QZ is running, else nothing."""
        port = self.port if self.port is not None else registry_port()
        if port and qz_running(self.host, port, min(timeout_s, 1.0)):
            return [DeviceInfo(address="qz", name=t("devices.qz.name"))]
        return []

    def connect(self, device: DeviceInfo | None = None) -> None:
        """Connect to QZ's websocket. Raises DeviceError if QZ is not running or has no treadmill."""
        self._start_session()

    def set_target(self, speed_kmh: float, incline_pct: float) -> None:
        """Send speed and incline to QZ (clamped); QZ passes them on to the treadmill."""
        speed = 0.0 if speed_kmh <= 0 else min(max(speed_kmh, self.min_speed_kmh), self.max_speed_kmh)
        incline = min(max(incline_pct, self.min_incline_pct), self.max_incline_pct)
        speed, incline = round(speed, 2), round(incline, 1)
        with self._lock:
            self._target_speed, self._target_incline = speed, incline
            self._stopped = False
            self._pause_unsent = False
        self._schedule(self._apply_targets(), "set_target")

    def start(self) -> None:
        """Press QZ's start. The Recoil may still need START on its panel."""
        with self._lock:
            self._stopped = False
            self._pause_unsent = False
        self._schedule(self._send_and_wait("start"), "start")

    def stop(self) -> None:
        """Pause in QZ (stops the belt). QZ's own stop is left to the user, since it ends QZ's session."""
        with self._lock:
            self._stopped = True
        self._schedule(self._pause_async(), "pause")

    # --- heart rate passthrough --------------------------------------------------------------
    def add_heart_rate_listener(self, fn: Callable[[HeartRateSample], None]) -> Callable[[], None]:
        """Call ``fn`` with each heart rate QZ reports. Returns an unsubscribe function."""
        self._hr_listeners.append(fn)
        return lambda: self._hr_listeners.remove(fn) if fn in self._hr_listeners else None

    # --- connection ------------------------------------------------------------------------
    def _resolve_port(self) -> int:
        port = self.port if self.port is not None else registry_port()
        if not port:
            raise DeviceError(t("devices.qz.no_port"))
        return port

    def _describe_error(self, exc: BaseException) -> str:
        if isinstance(exc, (ConnectionRefusedError, OSError, asyncio.TimeoutError)):
            return t("devices.qz.not_running")
        return super()._describe_error(exc)

    async def _open(self) -> None:
        port = self._resolve_port()
        connect = self._ws_connect or _default_ws_connect()
        self._ws = await connect(f"ws://{self.host}:{port}/", open_timeout=5, ping_interval=20,
                                 max_size=8 * 1024 * 1024)
        self._device_connected = None
        self._apply_lock = asyncio.Lock()
        self._reader = asyncio.create_task(self._read_loop())
        if self._pause_unsent and self._stopped:     # the pause that went nowhere while QZ was away
            await self._pause_async()
        await self._apply_targets()

    async def _wait_lost(self) -> None:
        assert self._reader is not None
        await asyncio.shield(self._reader)

    async def _close(self) -> None:
        reader, self._reader = self._reader, None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await maybe_await(ws.close())
            except Exception:  # noqa: BLE001
                pass
        if reader is not None and not reader.done():
            reader.cancel()
        for fut in self._waiters.values():
            if not fut.done():
                fut.cancel()
        self._waiters.clear()

    async def _read_loop(self) -> None:
        ws = self._ws
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(msg, dict):
                    self._handle(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - connection closed or broken
            log.info("QZ websocket ended: %s", exc)
        self._log(t("devices.status.lost", name=self._name))

    def _handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("msg")
        content = msg.get("content")
        if kind == "workout" and isinstance(content, dict):
            self._on_workout(content)
            return
        if isinstance(kind, str) and kind.startswith("R_"):
            fut = self._waiters.get(kind[2:])
            if fut is not None and not fut.done():
                fut.set_result(content)

    def _on_workout(self, c: dict[str, Any]) -> None:
        connected = bool(c.get("deviceConnected", False)) and c.get("deviceType", QZ_TREADMILL_TYPE) == QZ_TREADMILL_TYPE
        if connected != self._device_connected:
            self._device_connected = connected
            if connected:
                self._publish_status(ConnState.CONNECTED, t("devices.qz.connected_with",
                                                            name=str(c.get("deviceName") or "")))
            else:
                self._publish_status(ConnState.CONNECTED, t("devices.qz.no_treadmill"))
        if not connected:
            return
        speed = _num(c.get("speed"))
        incline = _num(c.get("inclination"))
        dist_km = _num(c.get("distance"))
        paused = bool(c.get("devicePaused", False))
        now = self._clock()
        with self._lock:
            st = TreadmillState(
                t=now, speed_kmh=speed, incline_pct=incline,
                distance_m=None if dist_km is None else dist_km * 1000.0,
                running=(speed or 0.0) > 0.05 and not paused,
                target_speed_kmh=self._target_speed if self._target_speed is not None else _num(c.get("target_speed")),
                target_incline_pct=self._target_incline if self._target_incline is not None
                else _num(c.get("target_inclination")),
            )
            self._state = st
        if self._bus is not None:
            self._bus.publish(TOPIC_TREADMILL, st)
        hr = _num(c.get("heart"))
        if hr is not None and hr > 0:
            sample = HeartRateSample(t=now, bpm=int(round(hr)))
            for fn in list(self._hr_listeners):
                try:
                    fn(sample)
                except Exception:  # noqa: BLE001
                    log.exception("QZ heart rate listener failed")

    async def _send(self, name: str, content: dict[str, Any] | None = None) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"msg": name, "content": content or {}}))

    async def _send_and_wait(self, name: str, content: dict[str, Any] | None = None) -> Any:
        """Send a command and wait for QZ's ``R_<name>`` reply; returns its content or None on timeout."""
        if self._ws is None:
            return None
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._waiters[name] = fut
        try:
            await self._send(name, content)
            return await asyncio.wait_for(fut, self._reply_timeout_s)
        except asyncio.TimeoutError:
            log.info("QZ did not answer %s", name)
            return None
        finally:
            if self._waiters.get(name) is fut:
                del self._waiters[name]

    async def _pause_async(self) -> None:
        """Pause in QZ. Without a socket nothing can be sent: the pause is kept for the reconnect."""
        self._pause_unsent = True
        if self._ws is None:
            return
        await self._send_and_wait("pause")           # raises when the socket is gone
        self._pause_unsent = False

    async def _apply_targets(self) -> None:
        if self._ws is None or self._apply_lock is None:
            return
        # One at a time, and the targets read when it is this one's turn: a second target sent while
        # QZ had not answered the first took over its wait, and the first one's incline went out last.
        async with self._apply_lock:
            with self._lock:
                speed, incline = self._target_speed, self._target_incline
                stopped = self._stopped
            if speed is not None and not stopped:      # not to a belt the program paused (a reconnect)
                reply = await self._send_and_wait("setspeed", {"value": speed})
                if isinstance(reply, dict) and reply.get("value") is None:
                    self._log(t("devices.qz.rejected"))
            if incline is not None:
                await self._send_and_wait("setresistance", {"value": incline})


class QzHeartRate:
    """Heart rate as QZ reports it (when QZ holds the strap). Needs a connected ``QzTreadmill``."""

    def __init__(self, qz: QzTreadmill, bus: Bus | None = None) -> None:
        self._qz = qz
        self._bus = bus
        self._last: HeartRateSample | None = None
        self._unsub: Callable[[], None] | None = None

    @property
    def last(self) -> HeartRateSample | None:
        return self._last

    def scan(self, timeout_s: float = 8.0) -> list[DeviceInfo]:
        return [DeviceInfo(address="qz", name=t("devices.qz.hr_name"))] if self._qz.connected else []

    def connect(self, device: DeviceInfo | None = None) -> None:
        if not self._qz.connected:
            raise DeviceError(t("devices.qz.hr_needs_qz"))
        if self._unsub is None:
            self._unsub = self._qz.add_heart_rate_listener(self._on_sample)

    def disconnect(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _on_sample(self, sample: HeartRateSample) -> None:
        self._last = sample
        if self._bus is not None:
            self._bus.publish(TOPIC_HEART_RATE, sample)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN -> None


__all__ = ["QzTreadmill", "QzHeartRate", "qz_running", "registry_port"]
