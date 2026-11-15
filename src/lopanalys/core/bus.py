"""A small thread-safe publish/subscribe bus that connects devices, analysis and UI.

Producers (camera, treadmill, heart rate, gait analysis, test runner) publish plain dataclass
messages on named topics. The UI subscribes and marshals onto the Qt thread itself. Nothing in
camera/, pose/, devices/ or assessments/ may import Qt, so all of it runs and tests headless.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable

# Topics. Payload types are named in the comment.
TOPIC_TREADMILL = "treadmill"        # devices.base.TreadmillState (ErgometerState from a trainer or a SkiErg)
TOPIC_ERGOMETER = TOPIC_TREADMILL    # the same topic under the name new code uses: one ergometer per test
TOPIC_STROKE = "stroke"              # devices.base.StrokeSample (one per SkiErg stroke)
TOPIC_HEART_RATE = "heart_rate"      # devices.base.HeartRateSample
TOPIC_GAIT_STEP = "gait_step"        # pose.types.StepMetrics (one per ground contact)
TOPIC_GAIT_SUMMARY = "gait_summary"  # pose.types.GaitSummary (rolling window, ~1 Hz)
TOPIC_GAIT_STEP_2 = "gait_step_2"        # the second camera view's steps (two views at once)
TOPIC_GAIT_SUMMARY_2 = "gait_summary_2"  # the second camera view's rolling summary
TOPIC_TECH_SUMMARY = "tech_summary"      # pose.technique_live.TechniqueLive (bike fit, double poling, ~1 Hz)
TOPIC_TECH_SUMMARY_2 = "tech_summary_2"  # the second camera view's technique values
TOPIC_TEST_STATE = "test_state"      # assessments.protocol.RunnerState
TOPIC_TEST_CUE = "test_cue"          # assessments.runner.Cue (sound and visual cues)
TOPIC_DEVICE_STATUS = "device_status"  # devices.base.DeviceStatus
TOPIC_LOG = "log"                    # str, user-visible status line


class Bus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Any], None]]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, fn: Callable[[Any], None]) -> Callable[[], None]:
        with self._lock:
            self._subs[topic].append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs[topic]:
                    self._subs[topic].remove(fn)
        return unsubscribe

    def publish(self, topic: str, payload: Any) -> None:
        with self._lock:
            subs = list(self._subs[topic])
        for fn in subs:
            try:
                fn(payload)
            except Exception:  # a broken subscriber must never stop a test
                import logging
                logging.getLogger(__name__).exception("subscriber failed on %s", topic)
