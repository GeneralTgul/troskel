"""Live gait analysis for the TV view: camera -> pose -> overlay, steps and a rolling summary.

Two threads:
  * the capture thread reads the source as fast as it delivers and keeps only the newest frame,
  * the analysis thread always takes the newest frame, so it never falls behind the camera: when the
    computer is slower than the camera, frames are skipped (``LiveResult.dropped``) and the achieved
    rate is reported (``fps_analysis``).
About once a second the last ``window_s`` seconds of poses run through the same chain as offline
analysis (filters, gait events, metrics). New contacts are published on ``TOPIC_GAIT_STEP`` and the
rolling summary on ``TOPIC_GAIT_SUMMARY``. The treadmill speed (``TOPIC_TREADMILL``) is picked up
from the bus for speed-dependent reference values.

Two views at once are two LiveAnalyzers, each with its own source and estimator. The second one
gets a ``name`` (its log lines say which view they are about), its own bus ``topics`` and a
``max_fps``: its analysis takes at most that many frames a second, so the main view keeps the
processor. ``latest_frame()`` is the newest captured frame whether or not it was analysed, so the
recording of a throttled view still holds every frame.

Bike fit and double poling (``technique`` "bikefit" or "ski"): the picture gets the skeleton without
the runner's angles, and the window runs through ``pose.technique_live`` instead of the gait chain. Its
values (the knee at the bottom, the hip, the trunk; the elbow, hip and knee of the stroke) are
published on the third topic, ``TOPIC_TECH_SUMMARY`` by default.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable


from lopanalys.core.bus import TOPIC_GAIT_STEP, TOPIC_GAIT_SUMMARY, TOPIC_LOG, TOPIC_TECH_SUMMARY, TOPIC_TREADMILL, Bus
from lopanalys.core.i18n import t
from lopanalys.pose.annotate import draw_live_overlay
from lopanalys.pose.filters import PoseSmoother
from lopanalys.pose.offline import analyze_core
from lopanalys.pose.types import GaitSummary, LiveResult, Pose, StepMetrics, View

log = logging.getLogger(__name__)


MIN_STEP_GAP_S = 0.12      # a "new" landing closer than this to the last published one is the same landing


class _Rate:
    """Events per second, exponentially smoothed."""

    def __init__(self, alpha: float = 0.1) -> None:
        self.alpha = alpha
        self.value = 0.0
        self._last: float | None = None

    def tick(self, now: float) -> None:
        if self._last is not None and now > self._last:
            inst = 1.0 / (now - self._last)
            self.value = inst if self.value == 0 else (1 - self.alpha) * self.value + self.alpha * inst
        self._last = now


class LiveAnalyzer:
    """Runs pose and gait analysis on a live ``camera.base.FrameSource``.

    ``estimator``: a ``pose.estimator.PoseEstimator``, or a factory returning one (called in the
    analysis thread; default: MediaPipe ``model_variant`` in video mode). Model loading errors are
    reported through ``latest().status`` and ``TOPIC_LOG``, never raised into the UI thread.
    """

    def __init__(self, source: Any, bus: Bus | None, height_cm: float | None, view: View | str, *,
                 estimator: Any = None, model_variant: str = "full", window_s: float = 12.0,
                 publish_every_s: float = 1.0, speed_kmh: float | None = None, open_source: bool = True,
                 name: str = "", topics: tuple[str, str] = (TOPIC_GAIT_STEP, TOPIC_GAIT_SUMMARY),
                 max_fps: float | None = None, labels: bool = True, technique: str | None = None,
                 tech_topic: str = TOPIC_TECH_SUMMARY) -> None:
        self.source = source
        self.technique = technique        # "bikefit" | "ski": the window runs through pose.technique_live
        self.tech_topic = tech_topic
        self.labels = labels and technique is None   # False: the skeleton without the runner's angles
        self.bus = bus
        self.height_cm = height_cm
        self.view = View(view)
        if technique is not None:
            from lopanalys.pose.technique_live import window_s as tech_window
            window_s = max(window_s, tech_window(technique))
        self.window_s = window_s
        self.publish_every_s = publish_every_s
        self.speed_kmh = speed_kmh
        self.name = name
        self.step_topic, self.summary_topic = topics
        self.max_fps = max_fps            # may be changed while running (pose.multiview.balance_second_rate)
        self._tag = f"live[{name}]" if name else "live"
        self._estimator_arg = estimator
        self._variant = model_variant
        self._open_source = open_source
        self._stop = threading.Event()
        self._new_frame = threading.Condition()
        self._frame: Any = None
        self._frame_seq = 0
        self._taken_seq = 0
        self._source_done = False
        self._latest: LiveResult | None = None
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._poses: deque[tuple[float, Pose | None]] = deque()
        self._smoother = PoseSmoother()
        self._cam_rate, self._ana_rate = _Rate(), _Rate()
        self.dropped = 0
        self._last_stats_log = 0.0
        self._last_step_t = -1e9
        self._summary: GaitSummary | None = None
        self.tech_summary: Any = None     # pose.technique_live.TechniqueLive, bike fit and double poling
        self._status = t("pose.live.starting")
        self._unsub: Callable[[], None] | None = None
        self.error: str | None = None
        self.steps_published = 0

    # --- public ------------------------------------------------------------------------------------

    def start(self) -> None:
        """Start the capture and analysis threads (returns at once)."""
        if self._threads:
            return
        self._stop.clear()
        self._source_done = False
        if self.bus is not None:
            self._unsub = self.bus.subscribe(TOPIC_TREADMILL, self._on_treadmill)
        for name, fn in (("pose-capture", self._capture_loop), ("pose-analysis", self._analysis_loop)):
            th = threading.Thread(target=fn, name=name, daemon=True)
            th.start()
            self._threads.append(th)

    def stop(self, timeout: float = 3.0) -> None:
        """Stop both threads, close the source and the estimator. Safe to call twice."""
        self._stop.set()
        with self._new_frame:
            self._new_frame.notify_all()
        try:
            self.source.close()
        except Exception:
            log.exception("closing the source failed")
        for th in self._threads:
            th.join(timeout)
        self._threads = []
        if self._unsub:
            self._unsub()
            self._unsub = None

    def latest(self) -> LiveResult | None:
        """The newest result (frame, pose, overlay, rolling summary), or None before the first frame."""
        with self._lock:
            return self._latest

    def latest_frame(self) -> Any:
        """The newest frame from the camera, analysed or not (None before the first one)."""
        return self._frame

    def rates(self) -> tuple[float, float]:
        """(frames per second from the camera, frames per second analysed)."""
        return self._cam_rate.value, self._ana_rate.value

    @property
    def summary(self) -> GaitSummary | None:
        return self._summary

    @property
    def running(self) -> bool:
        return any(th.is_alive() for th in self._threads)

    @property
    def source_lost(self) -> bool:
        """True when the camera stopped delivering by itself (not through ``stop()``)."""
        return self._source_done and not self._stop.is_set()

    # --- threads -----------------------------------------------------------------------------------

    def _on_treadmill(self, state: Any) -> None:
        sp = getattr(state, "speed_kmh", None)
        if sp is not None and sp > 0:
            self.speed_kmh = float(sp)

    def _capture_loop(self) -> None:
        try:
            if self._open_source:
                self.source.open()
            for frame in self.source.frames():
                if self._stop.is_set():
                    break
                self._cam_rate.tick(time.perf_counter())
                with self._new_frame:
                    if self._frame is not None and self._frame_seq != self._taken_seq and not self.max_fps:
                        self.dropped += 1            # the analysis did not take the previous one
                    self._frame = frame
                    self._frame_seq += 1
                    self._new_frame.notify()
        except Exception as e:
            if not self._stop.is_set():
                log.exception("camera source failed")
                self._set_status(t("pose.live.source_ended"))
                self.error = str(e)
        finally:
            if not self._stop.is_set():
                self._set_status(t("pose.live.source_ended"))
            with self._new_frame:
                self._source_done = True
                self._new_frame.notify_all()

    def _make_estimator(self) -> Any:
        arg = self._estimator_arg
        if arg is None:
            from lopanalys.pose.estimator import make_estimator
            return make_estimator(self._variant, "video")
        if callable(arg) and not hasattr(arg, "process"):
            return arg()
        return arg

    def _analysis_loop(self) -> None:
        try:
            est = self._make_estimator()
        except Exception as e:
            self.error = str(e)
            self._set_status(str(e))
            log.error("pose estimator could not start: %s", e)
            return
        last_seq = 0
        next_publish = time.perf_counter() + self.publish_every_s
        next_take = 0.0
        try:
            while not self._stop.is_set():
                if self.max_fps:
                    # A throttled view waits for its turn and then takes the newest frame. The frames
                    # it leaves are its plan, not a sign of a slow computer: they are not counted.
                    wait = next_take - time.perf_counter()
                    if wait > 0 and self._stop.wait(wait):
                        break
                    next_take = max(next_take, time.perf_counter() - 1.0 / self.max_fps) + 1.0 / self.max_fps
                with self._new_frame:
                    while self._frame_seq == last_seq and not self._stop.is_set() and not self._source_done:
                        self._new_frame.wait(0.2)
                    if self._stop.is_set() or (self._frame_seq == last_seq and self._source_done):
                        break
                    frame, last_seq = self._frame, self._frame_seq
                    self._taken_seq = last_seq
                self._process(est, frame)
                now = time.perf_counter()
                if now >= next_publish:
                    next_publish = now + self.publish_every_s
                    self._publish()
        except Exception as e:
            log.exception("live analysis failed")
            self.error = str(e)
            self._set_status(t("pose.error.mediapipe", reason=str(e)))
        finally:
            try:
                if self._estimator_arg is None or (callable(self._estimator_arg) and not hasattr(self._estimator_arg, "process")):
                    est.close()
            except Exception:
                log.exception("closing the estimator failed")

    def _process(self, est: Any, frame: Any) -> None:
        t0 = time.perf_counter()
        pose = est.process(frame.image, frame.t, frame.index)
        self._ana_rate.tick(time.perf_counter())
        self._poses.append((frame.t, pose))
        while self._poses and frame.t - self._poses[0][0] > self.window_s:
            self._poses.popleft()
        shown = self._smoother(pose) if pose is not None else None
        if pose is None:
            self._smoother.reset()
        overlay = draw_live_overlay(frame.image, shown, self.view, self._summary if self.labels else None,
                                    labels=self.labels)
        cam, ana = self._cam_rate.value, self._ana_rate.value
        if pose is None:
            status = t("pose.live.no_runner")
        elif cam > 0 and ana < 0.6 * (min(cam, self.max_fps) if self.max_fps else cam):
            status = t("pose.live.slow", fps=f"{ana:.0f}")
        else:
            status = ""
        res = LiveResult(frame=frame, pose=pose, overlay=overlay, summary=self._summary, fps_camera=cam,
                         fps_analysis=ana, dropped=self.dropped,
                         latency_ms=max(0.0, (time.perf_counter() - t0) * 1000.0), status=status)
        with self._lock:
            self._latest = res
            self._status = status
        now = time.perf_counter()
        if now - self._last_stats_log >= 10.0:   # numbers for support: where does the live delay come from?
            self._last_stats_log = now
            h, w = frame.image.shape[:2]
            log.info("%s: camera %.1f fps, analysis %.1f fps%s, %d skipped, pose+overlay %.0f ms, frame age %.0f ms, %dx%d",
                     self._tag, cam, ana, f" (at most {self.max_fps:.0f})" if self.max_fps else "", self.dropped,
                     res.latency_ms, (t0 - frame.t) * 1000.0, w, h)

    def _set_status(self, text: str) -> None:
        with self._lock:
            self._status = text
            if self._latest is not None:
                self._latest.status = text
        if self.bus is not None and text:
            self.bus.publish(TOPIC_LOG, text)

    def _publish(self) -> None:
        """Re-analyse the window and publish new steps and the summary."""
        data = list(self._poses)
        if sum(1 for _, p in data if p is not None) < 20:
            return
        times = [tt for tt, _ in data]
        poses = [p for _, p in data]
        if self.technique is not None:
            self._publish_technique(poses, times)
            return
        try:
            core = analyze_core(poses, self.height_cm, self.view, self.speed_kmh, times=times)
        except Exception:
            log.exception("window analysis failed")
            return
        a = core.analysis
        if a.summary is not None and a.summary.steps >= 4:
            self._summary = a.summary
            if self.bus is not None:
                self.bus.publish(self.summary_topic, a.summary)
        end = times[-1]
        # The window is analysed again each time, and a landing that was published then comes out a
        # few milliseconds off. Two landings are never closer than this (0.2 s at 300 steps a minute).
        new: list[StepMetrics] = [s for s in a.steps if s.t_contact > self._last_step_t + MIN_STEP_GAP_S
                                  and s.t_toe_off is not None and s.t_toe_off < end - 0.25]
        for s in sorted(new, key=lambda x: x.t_contact):
            self._last_step_t = s.t_contact
            self.steps_published += 1
            if self.bus is not None:
                self.bus.publish(self.step_topic, s)


    def _publish_technique(self, poses: list[Pose | None], times: list[float]) -> None:
        """Bike fit and double poling: the window through the analysis after the test, the values to the TV."""
        from lopanalys.pose.technique_live import summarize
        try:
            live = summarize(self.technique or "", poses, times, self.height_cm, self.view)
        except Exception:
            log.exception("%s: technique window analysis failed", self._tag)
            return
        self.tech_summary = live
        if self.bus is not None:
            self.bus.publish(self.tech_topic, live)
