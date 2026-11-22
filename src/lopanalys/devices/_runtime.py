"""Shared plumbing for the device backends: an asyncio loop in a thread and a reconnecting session.

Every connected device (FTMS treadmill, heart rate strap, QZ) owns one ``LoopThread``. The public,
synchronous API of a backend schedules coroutines on that loop, so callers on any thread (the test
runner, the UI) never block on Bluetooth for long and never touch asyncio themselves.

``SupervisedConnection`` keeps one connection alive: open it, wait until it is lost, back off and
open it again, publishing ``DeviceStatus`` on the bus at every step. The first attempt is
reported to the caller of ``connect()`` as an exception; later losses are retried until
``disconnect()``.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from typing import Any, Awaitable, Callable, Coroutine, Sequence, TypeVar

from lopanalys.core.bus import TOPIC_DEVICE_STATUS, TOPIC_LOG, Bus
from lopanalys.core.i18n import t
from lopanalys.devices.base import ConnState, DeviceStatus
from lopanalys.devices.errors import DeviceError

log = logging.getLogger(__name__)
T = TypeVar("T")

Clock = Callable[[], float]
DEFAULT_BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)


def uuid16(short: int) -> str:
    """Full 128-bit Bluetooth SIG UUID string for a 16-bit assigned number."""
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


class LoopThread:
    """An asyncio event loop running forever in a daemon thread."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("loop not started")
        return self._loop

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the loop thread (no-op if it already runs)."""
        if self.running:
            return
        ready = threading.Event()
        loop = asyncio.new_event_loop()

        def run() -> None:
            asyncio.set_event_loop(loop)
            loop.call_soon(ready.set)
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._loop = loop
        self._thread = threading.Thread(target=run, name=self._name, daemon=True)
        self._thread.start()
        ready.wait(5.0)

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Schedule a coroutine on the loop from any thread."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Run a coroutine on the loop and wait for its result (never call from the loop itself)."""
        return self.submit(coro).result(timeout)

    def call_soon(self, fn: Callable[..., Any], *args: Any) -> None:
        """Run a plain callback on the loop thread."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(fn, *args)

    def stop(self, timeout: float = 5.0) -> None:
        """Cancel every task on the loop, stop it and join the thread."""
        if not self.running or self._loop is None:
            return
        loop = self._loop

        async def _cancel_all() -> None:
            me = asyncio.current_task()
            tasks = [tk for tk in asyncio.all_tasks() if tk is not me]
            for tk in tasks:
                tk.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_cancel_all(), loop).result(timeout)
        except Exception:  # noqa: BLE001 - shutting down, nothing to report
            log.debug("cancel on stop failed", exc_info=True)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None and threading.current_thread() is not self._thread:
            self._thread.join(timeout)
        self._thread = None


def fire_and_log(fut: concurrent.futures.Future[Any], what: str) -> None:
    """Log the exception of a fire-and-forget future instead of losing it."""

    def done(f: concurrent.futures.Future[Any]) -> None:
        if f.cancelled():
            return
        exc = f.exception()
        if exc is not None:
            log.warning("%s failed: %s", what, exc)

    fut.add_done_callback(done)


class SupervisedConnection:
    """Base for a device connection that reconnects with backoff until ``disconnect()``.

    Subclasses implement ``_open()`` (connect and set up; raise on failure), ``_wait_lost()``
    (return when the connection is gone) and ``_close()`` (best-effort teardown).
    """

    device_kind = "treadmill"   # DeviceStatus.device

    def __init__(self, bus: Bus | None, *, name: str, clock: Clock = time.time,
                 reconnect: bool = True, connect_timeout_s: float = 25.0,
                 backoff_s: Sequence[float] = DEFAULT_BACKOFF_S) -> None:
        self._bus = bus
        self._name = name
        self._clock = clock
        self._reconnect = reconnect
        self._connect_timeout_s = connect_timeout_s
        self._backoff_s = tuple(backoff_s) or (1.0,)
        self._io = LoopThread(f"{type(self).__name__}-io")
        self._stopping = False
        self._connected = False
        self._supervisor: concurrent.futures.Future[None] | None = None
        self._status: DeviceStatus = DeviceStatus(self.device_kind, ConnState.DISCONNECTED, name)

    # --- status --------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def status(self) -> DeviceStatus:
        return self._status

    def _publish_status(self, state: ConnState, message: str = "", **extra: Any) -> None:
        self._status = DeviceStatus(self.device_kind, state, self._name, message, **extra)
        if self._bus is not None:
            self._bus.publish(TOPIC_DEVICE_STATUS, self._status)

    def _log(self, message: str) -> None:
        if self._bus is not None:
            self._bus.publish(TOPIC_LOG, message)

    # --- lifecycle -----------------------------------------------------------------------
    def _start_session(self) -> None:
        """Start the loop and the supervisor; block until the first attempt succeeds or fails."""
        if self._supervisor is not None and not self._supervisor.done():
            raise DeviceError(t("devices.error.already_connected", name=self._name))
        self._stopping = False
        self._io.start()
        first: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._supervisor = self._io.submit(self._supervise(first))
        try:
            first.result(self._connect_timeout_s)
        except concurrent.futures.TimeoutError:
            self._shutdown()
            self._publish_status(ConnState.ERROR, t("devices.error.timeout", name=self._name))
            raise DeviceError(t("devices.error.timeout", name=self._name)) from None
        except DeviceError as exc:
            self._shutdown()
            self._publish_status(ConnState.ERROR, str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - bleak/websocket errors become one coach message
            self._shutdown()
            msg = self._describe_error(exc)
            self._publish_status(ConnState.ERROR, msg)
            raise DeviceError(msg) from exc

    def _describe_error(self, exc: BaseException) -> str:
        """Turn a backend exception into a message a coach understands."""
        return t("devices.error.connect_failed", name=self._name, reason=str(exc) or type(exc).__name__)

    async def _supervise(self, first: concurrent.futures.Future[None]) -> None:
        attempt = 0
        while not self._stopping:
            self._publish_status(ConnState.CONNECTING, t("devices.status.connecting", name=self._name))
            try:
                if first.done():
                    # A reconnect has nobody waiting with a time limit, as the first connect has: a
                    # Bluetooth call that never comes back is given up, and the next attempt follows.
                    await asyncio.wait_for(self._open(), self._connect_timeout_s)
                else:
                    await self._open()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await self._safe_close()
                if not first.done():
                    first.set_exception(exc)
                    return
                log.info("%s: reconnect failed: %s", self._log_id, type(exc).__name__)
            else:
                if first.done():
                    log.info("%s: reconnected", self._log_id)
                attempt = 0
                self._connected = True
                self._publish_status(ConnState.CONNECTED, t("devices.status.connected", name=self._name),
                                     **self._status_extra())
                if not first.done():
                    first.set_result(None)
                try:
                    await self._wait_lost()
                finally:
                    self._connected = False
                    await self._safe_close()
                if self._stopping:
                    break
                log.info("%s: connection lost", self._log_id)
                if not self._reconnect:
                    self._publish_status(ConnState.DISCONNECTED, t("devices.status.lost", name=self._name))
                    return
            delay = self._backoff_s[min(attempt, len(self._backoff_s) - 1)]
            attempt += 1
            log.info("%s: reconnecting in %.0f s", self._log_id, delay)
            msg = t("devices.status.reconnecting", name=self._name, seconds=f"{delay:.0f}")
            self._publish_status(ConnState.CONNECTING, msg)
            await asyncio.sleep(delay)

    def _status_extra(self) -> dict[str, Any]:
        return {}

    @property
    def _log_id(self) -> str:
        # Kind and backend only: a Bluetooth name or address can carry a person's name.
        return f"{self.device_kind} ({type(self).__name__})"

    async def _safe_close(self) -> None:
        try:
            await asyncio.wait_for(self._close(), 5.0)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.debug("%s: close failed", self._name, exc_info=True)

    def _shutdown(self) -> None:
        self._stopping = True
        if self._io.running:
            try:
                self._io.run(self._safe_close(), 6.0)
            except Exception:  # noqa: BLE001
                log.debug("close on shutdown failed", exc_info=True)
            self._io.stop()
        self._connected = False
        self._supervisor = None

    def disconnect(self) -> None:
        """Close the connection and stop the background thread. Safe to call twice."""
        was_active = self._io.running
        self._shutdown()
        if was_active:
            self._publish_status(ConnState.DISCONNECTED, t("devices.status.disconnected", name=self._name))

    # --- subclass hooks ------------------------------------------------------------------
    async def _open(self) -> None:
        raise NotImplementedError

    async def _wait_lost(self) -> None:
        raise NotImplementedError

    async def _close(self) -> None:
        raise NotImplementedError

    # --- helpers for subclasses ------------------------------------------------------------
    def _schedule(self, coro: Coroutine[Any, Any, Any], what: str) -> concurrent.futures.Future[Any] | None:
        """Schedule a coroutine from a sync caller; returns None (and closes coro) if not running."""
        if not self._io.running:
            coro.close()
            return None
        fut = self._io.submit(coro)
        fire_and_log(fut, f"{self._name}: {what}")
        return fut


async def maybe_await(value: Awaitable[T] | T) -> T:
    """Await a value if it is awaitable (fakes in tests may be sync)."""
    if asyncio.iscoroutine(value) or isinstance(value, asyncio.Future):
        return await value  # type: ignore[no-any-return]
    return value  # type: ignore[return-value]
