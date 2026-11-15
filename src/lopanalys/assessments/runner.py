"""Runs a test protocol: drives the treadmill (or prompts the operator), times stages, takes the
lactate pauses, collects 1 Hz samples and publishes what the TV shows.

Public API (shared with the UI)::

    r = TestRunner(protocol, treadmill, heart_rate, bus)
    r.start(); r.pause(); r.resume(); r.stop(); r.operator_ready(); r.exhausted()
    r.enter_lactate(stage_index, mmol); r.enter_rpe(stage_index, rpe); r.final_sprint()
    r.records -> list[StepRecord]   # completed non-warm-up stages (index = protocol stage index)
    r.warmup_records                # warm-up stages (baseline lactate is entered here)
    r.samples -> list[TimeSample]   # 1 Hz from start(), t_s in seconds since start()

Bus output:
  - ``TOPIC_TEST_STATE`` (core.bus): :class:`RunnerState`, about 4 Hz and on every phase change.
  - ``TOPIC_TEST_CUE`` (this module, "test_cue"): :class:`Cue`, one message per sound/visual cue.
    Kinds: ``countdown`` (each of the last 3 s before a change; text = "3"/"2"/"1"), ``go``
    (stage starts), ``pre_announce`` (15 s before a change), ``lactate`` (take the sample),
    ``operator`` (manual mode: set the treadmill now), ``finish`` (exhaustion/last stage),
    ``abort`` (stopped). The UI maps them to sounds (short beep, long beep, chime).

Phases: IDLE -> COUNTDOWN (controllable) or AWAIT_OPERATOR (manual) -> RUNNING <-> LACTATE_PAUSE /
AWAIT_OPERATOR ... -> FINISHED. ``exhausted()`` -> RECOVERY (walking, recovery HR) -> FINISHED.
``stop()`` -> ABORTED (belt stopped). In RECOVERY ``stop()`` just ends the recovery early.
A gait stage stopped after ``PARTIAL_GAIT_MIN_S`` is kept as a partial record (the film and the
live gait values of a technique test stopped half-way are not lost).

``start(from_index=k, t_offset_s=T)`` continues a test that was cut off: it begins with
stage ``k`` and its clock and samples go on from ``T`` seconds, so the records and samples saved
before the cut can be joined with the new ones.

Manual treadmills: before every change the TV shows "Sätt bandet på 12,0 km/h · 1 %" and the
stage clock waits for ``operator_ready()``; 15 s before each change it says what comes next.
If a controllable treadmill throws, the runner falls back to manual prompts rather than stopping.

Other sports: the load is watt on a trainer and on a SkiErg (the stage's ``speed_kmh``
holds it). Three kinds of ergometer:
  - controllable (trainer in ERG): the runner sets the watt, as it sets the belt's speed.
  - measured but not controllable (Concept2 PM5), and a ``free`` stage on a trainer (the 20 minute
    FTP test): *self-paced*. The TV shows the target, the stage clock runs without anyone
    confirming, and the stage's load is the **measured** mean watt, not the target
    (``SELF_PACED_SETTLE_S`` at the start of the stage is left out). A stage whose mean lies more
    than ``TARGET_BAND`` from the target is marked in ``stage_stats``.
  - neither (manual): the operator confirms, as with a manual treadmill.
``stage_stats`` has target, measured mean load and mean cadence per stage. An athlete who stops
(the load at 0 for the profile's ``stop_s``) ends a ramp as exhausted, as the panel's STOP does on
a treadmill.

Timing uses an injectable clock (``now()``, ``sleep(dt)``); tests use ``threaded=False`` and call
``tick()`` themselves with a fake clock. ``time_scale`` > 1 (demo mode) runs test time faster.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Protocol as TypingProtocol

from lopanalys.assessments import erg_values, ramp
from lopanalys.assessments.library import TreadmillLimits, fmt_kmh, fmt_pct
from lopanalys.assessments.library_power import power_limits
from lopanalys.assessments.protocol import Phase, Protocol, RunnerState, SprintOutlook, Stage
from lopanalys.core.bus import (TOPIC_GAIT_SUMMARY, TOPIC_HEART_RATE, TOPIC_LOG, TOPIC_TEST_CUE,
                                TOPIC_TEST_STATE, TOPIC_TREADMILL, Bus)
from lopanalys.core.i18n import t
from lopanalys.core.models import StepRecord, TimeSample
from lopanalys.core.sport import Sport, profile, sport_of

log = logging.getLogger(__name__)

PANEL_MOVING_KMH = 2.0   # the belt runs
PANEL_STILL_KMH = 0.5    # the belt stands still
PANEL_STOP_S = 2.0       # standing still this long in a running stage = stopped on the panel

SELF_PACED_SETTLE_S = 15.0   # self-paced stage: the first seconds (getting up to the load) are left out of its mean
TARGET_BAND = 0.05           # self-paced: a stage mean more than 5 % from the target is marked
LOW_CADENCE_WARN_S = 10.0    # bike in ERG: this long below the trainer's cadence limit before the TV says so

HR_WINDOW_S = 30.0       # stage HR = mean of the last 30 s
HR_STALE_S = 5.0         # a bus HR reading older than this is ignored
LACTATE_MIN, LACTATE_MAX = 0.0, 30.0
PARTIAL_GAIT_MIN_S = 20.0  # a gait stage stopped after this long is kept as a partial record
RPE_MIN, RPE_MAX = 6, 20
# The automatic final sprint (Protocol.final_sprint_auto: a runner alone on the treadmill has nobody
# to press "Slutspurt"). It replaces the next ramp step when either holds at the end of a step:
SPRINT_AFTER_S = 720.0       # the ramp has run 12 min (the top of the 8-12 min a max test should take)
SPRINT_MIN_RAMP_S = 240.0    # ... or, after at least 4 min of ramp, the heart rate levelled off:
SPRINT_PLATEAU_S = 60.0      #     it rose at most SPRINT_PLATEAU_BPM over the last 60 s
SPRINT_PLATEAU_BPM = 2.0


@dataclass
class Cue:
    """A one-shot sound/visual cue; see the module docstring for the kinds."""
    kind: str
    t_s: float
    text: str = ""
    stage_index: int = -1


class Clock(TypingProtocol):
    def now(self) -> float: ...
    def sleep(self, dt: float) -> None: ...


class SystemClock:
    """Monotonic wall clock. ``scale`` > 1 runs test time faster (demo mode)."""

    real = True

    def __init__(self, scale: float = 1.0) -> None:
        self.scale = scale if scale > 0 else 1.0
        self._t0 = time.perf_counter()

    def now(self) -> float:
        return self._t0 + (time.perf_counter() - self._t0) * self.scale

    def sleep(self, dt: float) -> None:
        time.sleep(dt / self.scale)


def stage_text(load: float, incline_pct: float, sport: Any = None) -> str:
    """The target as shown on the TV: '12,0 km/h · 1 %' for running, else the load with the sport's
    unit ('200 W'), and the pace where the sport has one ('200 W · 2:01 /500 m')."""
    p = profile(sport)
    if p.sport is Sport.RUN:
        return f"{fmt_kmh(load)} km/h · {fmt_pct(incline_pct)} %"
    text = f"{p.load_str(load).replace('.', ',')} {p.load_unit}"
    return f"{text} · {p.pace_str(load)} {p.pace_unit}" if p.pace_unit else text


class TestRunner:
    """Drives one test. Thread-safe: all public methods may be called from the UI thread."""

    __test__ = False  # not a pytest class

    def __init__(self, protocol: Protocol, treadmill: Any, heart_rate: Any, bus: Bus | None = None, *,
                 clock: Clock | None = None, threaded: bool = True, countdown_s: float = 10.0,
                 resume_countdown_s: float = 5.0, announce_s: float = 15.0, recovery_s: float = 65.0,
                 cooldown_kmh: float = 5.0, tick_s: float = 0.25, publish_hz: float = 4.0,
                 time_scale: float = 1.0, limits: TreadmillLimits | None = None) -> None:
        if not protocol.stages:
            raise ValueError(t("assessments.error.no_stages"))
        self.protocol = protocol
        self.sport = sport_of(protocol.sport)
        self.treadmill = treadmill
        self.heart_rate = heart_rate
        self.bus = bus or Bus()
        self.clock: Clock = clock or SystemClock(time_scale)
        self._threaded = threaded
        self.countdown_s = countdown_s
        self.resume_countdown_s = resume_countdown_s
        self.announce_s = announce_s
        self.recovery_s = recovery_s
        self._profile = profile(self.sport)
        self._run = self.sport is Sport.RUN
        # the easy load after exhaustion: walking speed on the belt, easy spinning on the trainer
        self.cooldown_kmh = cooldown_kmh if self._run else self._profile.cooldown_load
        self.tick_s = tick_s
        self._pub_period = 1.0 / publish_hz if publish_hz > 0 else 0.0

        self._controllable = bool(treadmill is not None and getattr(treadmill, "controllable", False))
        # measured but not set by the program (Concept2 PM5): the athlete holds the load
        self._measured_only = bool(treadmill is not None and not self._controllable
                                   and getattr(treadmill, "measured", False) and not self._run)
        if self._run:
            self._limits = TreadmillLimits.of(treadmill) if treadmill is not None else TreadmillLimits()
            if limits is not None:   # the coach's setting for the real belt (QZ cannot report it)
                self._limits = ramp.narrower(self._limits, limits)
        else:
            self._limits = power_limits(treadmill)
        self._stats: dict[int, dict[str, Any]] = {}
        self._drag: list[tuple[float, int]] = []    # (test time, drag factor) as a PM5 reports it
        self._low_cadence_since: float | None = None
        self._stages: list[Stage] = [self._clamp(s) for s in protocol.stages]
        self._n_protocol = len(self._stages)
        self._generated = 0
        self._open_ended_s = 0.0
        self._ramp_how = ""               # ramp.next_step: "" | "speed" | "incline" | "at_max"

        self._lock = threading.RLock()
        self._phase = Phase.IDLE
        self._idx = 0
        self._stage_elapsed = 0.0
        self._timer = 0.0                 # remaining s of COUNTDOWN / LACTATE_PAUSE / RECOVERY
        self._paused = False
        self._announced = False
        self._ticked: set[int] = set()
        self._belt_running = False
        self._prev_set: tuple[float, float] | None = None
        self._prompt = ""
        self._announcement = ""
        self._flash_until = 0.0           # on-the-move lactate prompt stays this long (total time)
        self._flash_text = ""
        self._t0: float | None = None
        self._last_tick: float | None = None
        self._last_pub = -1e9
        self._next_sample = 0.0
        self._stage_start_t = 0.0
        self._sprint_index: int | None = None
        self._sprint_ramp_s: float | None = None   # ramp time when the final sprint started
        self._sprint_reason = ""                   # "plateau", "time" or "manual"
        self._exhaustion_t: float | None = None
        self._belt_moved = False          # seen running since the program last stopped it on purpose
        self._still_since: float | None = None
        self._pending: list[int] = []
        self._early_values: dict[int, dict[str, Any]] = {}

        self._records: list[StepRecord] = []
        self._warmup: list[StepRecord] = []
        self._samples: list[TimeSample] = []
        self._hr_bus: tuple[int, float] | None = None
        self._cad_bus: tuple[float, float] | None = None   # strap cadence (RSC), steps/min and time
        self._tm_bus: Any = None
        self._gait: Any = None
        self._unsubs: list[Callable[[], None]] = []
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._state: RunnerState | None = None

    # ---------------------------------------------------------------- public API
    @property
    def records(self) -> list[StepRecord]:
        with self._lock:
            return list(self._records)

    @property
    def warmup_records(self) -> list[StepRecord]:
        with self._lock:
            return list(self._warmup)

    @property
    def samples(self) -> list[TimeSample]:
        with self._lock:
            return list(self._samples)

    @property
    def stage_stats(self) -> list[dict[str, Any]]:
        """Per completed stage: index, target and measured mean load, mean cadence, and whether a
        self-paced stage stayed within the band around its target. Empty for a treadmill."""
        with self._lock:
            return [dict(v) for _, v in sorted(self._stats.items())]

    @property
    def drag_factor(self) -> int | None:
        """The SkiErg's drag factor in the test: the median of what the PM5 reported during the
        stages that are not warm-up, else during the whole test. None when it never said."""
        with self._lock:
            work = [st["drag"] for i, st in sorted(self._stats.items())
                    if st.get("drag") and not (i < len(self._stages) and self._stages[i].is_warmup)]
            return erg_values.median_int(work) or erg_values.median_int([d for _, d in self._drag])

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def state(self) -> RunnerState | None:
        """Last published state."""
        return self._state

    @property
    def exhaustion_t_s(self) -> float | None:
        """Seconds from start to the exhaustion press (None if not exhausted)."""
        return self._exhaustion_t

    @property
    def done(self) -> bool:
        return self._phase in (Phase.FINISHED, Phase.ABORTED)

    @property
    def stages(self) -> list[Stage]:
        """Protocol stages plus those generated by an open-ended ramp so far."""
        with self._lock:
            return list(self._stages)

    def start(self, from_index: int = 0, t_offset_s: float = 0.0) -> None:
        """Start the test (countdown, or the operator prompt in manual mode).

        ``from_index``/``t_offset_s``: continue a cut-off test at that stage, with the clock at that time."""
        with self._lock:
            if self._phase != Phase.IDLE:
                return
            if not 0 <= from_index < len(self._stages):
                raise ValueError(t("assessments.error.stage_index", n=from_index))
            self._unsubs = [
                self.bus.subscribe(TOPIC_HEART_RATE, self._on_hr),
                self.bus.subscribe(TOPIC_TREADMILL, self._on_tm),
                self.bus.subscribe(TOPIC_GAIT_SUMMARY, self._on_gait),
            ]
            now = self.clock.now()
            offset = max(0.0, float(t_offset_s))
            self._t0, self._last_tick = now - offset, now
            self._next_sample = float(int(offset) + (1 if offset > int(offset) else 0))
            self._idx = from_index
            self._program_monitor(from_index)
            self._begin_stage(first=True)
            self._publish(force=True)
        if self._threaded:
            self._thread = threading.Thread(target=self._loop, name="TestRunner", daemon=True)
            self._thread.start()

    def pause(self) -> None:
        """Operator pause: freezes the clock, stops a controllable belt."""
        with self._lock:
            if self._paused or self._phase not in (Phase.RUNNING, Phase.COUNTDOWN, Phase.LACTATE_PAUSE,
                                                   Phase.AWAIT_OPERATOR):
                return
            self._paused = True
            self._belt_stop()
            self._prompt = t("assessments.prompt.paused")
            self._publish(force=True)

    def resume(self) -> None:
        """Continue after pause(): a short countdown, then the same stage where it stopped."""
        with self._lock:
            if not self._paused:
                return
            self._paused = False
            if self._phase in (Phase.RUNNING, Phase.COUNTDOWN):
                if self._auto:
                    self._phase = Phase.COUNTDOWN
                    self._timer = self.resume_countdown_s
                    self._ticked.clear()
                    self._prompt = t("assessments.prompt.resume")
                else:
                    self._await_operator()
            elif self._phase == Phase.LACTATE_PAUSE:
                self._prompt = t("assessments.prompt.lactate")
            else:
                self._prompt = self._operator_prompt(self._cur)
            self._publish(force=True)

    def stop(self) -> None:
        """Abort safely (belt stopped). In RECOVERY it just ends the test normally."""
        with self._lock:
            if self._phase == Phase.RECOVERY:
                self._finish()
                return
            if self.done:
                return
            if (self._phase == Phase.RUNNING and self._cur.record_gait
                    and self._stage_elapsed >= PARTIAL_GAIT_MIN_S):
                self._complete_stage(partial=True)
            self._phase = Phase.ABORTED
            self._paused = False
            self._belt_stop()
            self._prompt = self._pt("aborted") if self._controllable else self._pt("stop_belt")
            self._announcement = ""
            self._cue("abort")
            self._publish(force=True)
            self._end()

    def operator_ready(self) -> bool:
        """Manual mode: the operator has set the treadmill. Returns True if it was awaited."""
        with self._lock:
            if self._phase != Phase.AWAIT_OPERATOR or self._paused:
                return False
            self._run_stage()
            self._publish(force=True)
            return True

    def exhausted(self) -> None:
        """The athlete is done (open-ended ramp or early end): record the partial stage, recover."""
        with self._lock:
            if self.done or self._phase in (Phase.IDLE, Phase.RECOVERY):
                return
            now_s = self._now_s()
            if self._phase == Phase.RUNNING and self._stage_elapsed >= 1.0:
                self._complete_stage(partial=True)
            self._exhaustion_t = now_s
            self._paused = False
            self._phase = Phase.RECOVERY
            self._timer = self.recovery_s
            self._set_belt(self._limits.speed(self.cooldown_kmh), self._limits.incline(0.0), force_start=True)
            if self._controllable or not self._run:
                self._prompt = self._pt("recovery")
            else:
                self._prompt = t("assessments.prompt.recovery_manual", target=self._stage_text(self.cooldown_kmh, 0))
            self._announcement = t("assessments.prompt.lactate") if self._pending else ""
            self._cue("finish")
            self._publish(force=True)

    def final_sprint(self) -> bool:
        """Start the final sprint of an open-ended ramp (if the protocol allows one)."""
        with self._lock:
            p = self.protocol
            if (p.final_sprint_s <= 0 or self._phase != Phase.RUNNING or self._cur.is_warmup
                    or self._sprint_index is not None):
                return False
            self._sprint_ramp_s, self._sprint_reason = self._ramp_now_s(), "manual"
            if self._stage_elapsed >= 1.0:
                self._complete_stage(partial=True)
            self._stages.insert(self._idx + 1, self._sprint_stage())
            self._sprint_index = self._idx + 1
            self._next_stage(carry=0.0)
            self._publish(force=True)
            return True

    def _sprint_stage(self) -> Stage:
        p, cur = self.protocol, self._cur
        return self._clamp(Stage(cur.speed_kmh + p.final_sprint_kmh, cur.incline_pct, p.final_sprint_s,
                                 t("assessments.stage.sprint")))

    def _sprint_due(self) -> bool:
        """At the end of the current ramp step: should the automatic final sprint come next?"""
        p = self.protocol
        if (not self._run or not p.final_sprint_auto or p.final_sprint_s <= 0 or p.final_sprint_kmh <= 0
                or self._sprint_index is not None or self._cur.is_warmup or self._idx < self._ramp_start()):
            return False
        if self._sprint_stage().speed_kmh <= self._cur.speed_kmh + 1e-6:
            return False                  # at the highest speed: a sprint cannot go faster
        ramp_s = self._ramp_elapsed_s()
        if ramp_s >= SPRINT_AFTER_S - 1e-6:
            return True
        return ramp_s >= SPRINT_MIN_RAMP_S - 1e-6 and self._hr_levelled()

    def _ramp_start(self) -> int:
        i = ramp.ramp_start_index(self.protocol)
        return i if i is not None else self._n_protocol - 1

    def _ramp_elapsed_s(self) -> float:
        """Seconds of ramp run, counting the current step to its end."""
        i0 = self._ramp_start()
        done = sum(r.duration_s for r in self._records if r.index >= i0 and r.index != self._idx)
        return done + self._cur.duration_s

    def _ramp_now_s(self) -> float:
        """Seconds of ramp run up to now (0 before the ramp)."""
        i0 = self._ramp_start()
        if self._idx < i0 or self._cur.is_warmup:
            return 0.0
        done = sum(r.duration_s for r in self._records if r.index >= i0 and r.index != self._idx)
        return done + self._stage_elapsed

    def _hr_rise(self) -> float | None:
        """The heart rate's rise over the last SPRINT_PLATEAU_S: the highest of the last 10 s minus the
        mean of the first 10 s. None when the samples do not cover the window."""
        now = self._now_s()
        hrs = [(x.t_s, x.hr) for x in self._samples if x.hr and x.t_s >= now - SPRINT_PLATEAU_S - 1]
        if len(hrs) < SPRINT_PLATEAU_S * 0.8 or hrs[0][0] > now - SPRINT_PLATEAU_S + 5:
            return None
        first = [h for tt, h in hrs if tt <= hrs[0][0] + 10]
        last = [h for tt, h in hrs if tt >= now - 10]
        if not (first and last):
            return None
        return float(max(last) - sum(first) / len(first))

    def _hr_levelled(self) -> bool:
        rise = self._hr_rise()
        return rise is not None and rise <= SPRINT_PLATEAU_BPM

    def sprint_outlook(self) -> SprintOutlook | None:
        """How near the automatic final sprint is, for the coach's page. Changes nothing. None when
        the protocol does not start the sprint by itself."""
        with self._lock:
            p = self.protocol
            if not (self._run and p.open_ended and p.final_sprint_auto and p.final_sprint_s > 0
                    and p.final_sprint_kmh > 0):
                return None
            base = dict(hr_rise_bpm=None, limit_bpm=SPRINT_PLATEAU_BPM, min_ramp_s=SPRINT_MIN_RAMP_S,
                        after_s=SPRINT_AFTER_S)
            if self._sprint_index is not None:
                at = self._sprint_ramp_s or 0.0
                return SprintOutlook(True, at, remaining_s=0.0, started_s=at, reason=self._sprint_reason, **base)
            now = self._ramp_now_s()
            if self._idx < self._ramp_start() or self._cur.is_warmup:
                return SprintOutlook(False, 0.0, remaining_s=None, **base)
            base["hr_rise_bpm"] = self._hr_rise()
            end = self._ramp_elapsed_s()              # the current step's end; the sprint is decided there
            if end < SPRINT_AFTER_S - 1e-6:           # the steps an open ramp adds are as long as the last
                d = self._cur.duration_s if self._cur.duration_s > 0 else 60.0
                end += math.ceil((SPRINT_AFTER_S - end) / d - 1e-6) * d
            return SprintOutlook(True, now, remaining_s=max(0.0, end - now), **base)

    def enter_lactate(self, stage_index: int, mmol: float) -> None:
        """Store a lactate value (mmol/L) for a stage; may be entered any time, also afterwards."""
        try:
            v = float(mmol)
        except (TypeError, ValueError):
            raise ValueError(t("assessments.error.lactate")) from None
        if not (LACTATE_MIN <= v <= LACTATE_MAX):
            raise ValueError(t("assessments.error.lactate"))
        with self._lock:
            rec = self._find(stage_index)
            if rec is None:
                self._check_index(stage_index)
                self._early_values.setdefault(stage_index, {})["lactate_mmol"] = round(v, 2)
            else:
                rec.lactate_mmol = round(v, 2)
            if stage_index in self._pending:
                self._pending.remove(stage_index)
            self._publish(force=True)

    def enter_rpe(self, stage_index: int, rpe: int) -> None:
        """Store Borg RPE (6-20) for a stage."""
        try:
            f = float(rpe)
        except (TypeError, ValueError):
            raise ValueError(t("assessments.error.rpe")) from None
        v = int(f)
        if not (RPE_MIN <= v <= RPE_MAX) or not f.is_integer():
            raise ValueError(t("assessments.error.rpe"))
        with self._lock:
            rec = self._find(stage_index)
            if rec is None:
                self._check_index(stage_index)
                self._early_values.setdefault(stage_index, {})["rpe"] = v
            else:
                rec.rpe = v
            self._publish(force=True)

    def tick(self) -> None:
        """Advance the state machine to clock.now(). Called by the thread, or by tests."""
        with self._lock:
            if self._t0 is None or self.done:
                return
            now = self.clock.now()
            dt = max(0.0, now - (self._last_tick or now))
            self._last_tick = now
            self._sample(now)
            if not self._paused:
                self._check_panel_stop(now)
                self._advance(dt)
            self._publish()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def close(self) -> None:
        """Stop if running and release the bus subscriptions and the thread."""
        if not self.done and self._phase != Phase.IDLE:
            self.stop()
        self._end()
        self.join(2.0)

    # ---------------------------------------------------------------- state machine
    @property
    def _cur(self) -> Stage:
        return self._stages[self._idx]

    def _advance(self, dt: float) -> None:
        ph = self._phase
        if ph == Phase.COUNTDOWN:
            self._timer -= dt
            self._countdown_cues(self._timer)
            if self._timer <= 0:
                self._run_stage(carry=-self._timer)
        elif ph == Phase.RUNNING:
            self._stage_elapsed += dt
            remaining = self._cur.duration_s - self._stage_elapsed
            self._maybe_announce(remaining)
            self._countdown_cues(remaining)
            if self._flash_text and self._now_s() >= self._flash_until:
                self._flash_text = ""
                self._prompt = ""
            if remaining <= 0:
                self._complete_stage()
                self._after_stage(carry=-remaining)
        elif ph == Phase.LACTATE_PAUSE:
            self._timer -= dt
            self._countdown_cues(self._timer)
            if self._timer <= 0:
                self._next_stage(carry=0.0, after_pause=True)
        elif ph == Phase.RECOVERY:
            self._timer -= dt
            if self._timer <= 0:
                self._finish()

    def _check_panel_stop(self, now: float) -> None:
        """The athlete stopped the belt on the panel (alone on the treadmill the runner steps
        off and presses STOP; nobody presses Exhausted). A belt that stood still for PANEL_STOP_S during a
        running stage, after it had run since the program last stopped it (lactate pause), counts as that. A max test then ends as exhausted, any other test
        as stopped, and the belt is never commanded again. Lactate pauses are not running stages."""
        watched = self._controllable or self._measured_only
        if not self._run:
            # On a trainer or a SkiErg only a ramp to exhaustion ends this way: an athlete who stops
            # pedalling for a moment in a lactate step (the blood sample) must not end the test.
            watched = watched and self.protocol.open_ended and not self._cur.is_warmup
        if not watched or self._phase != Phase.RUNNING or self.treadmill is None:
            self._still_since = None
            return
        try:
            st = self.treadmill.state
        except Exception:
            return
        speed = getattr(st, "speed_kmh", None) if st is not None else None
        if speed is None:
            return
        moving, still, stop_s = ((PANEL_MOVING_KMH, PANEL_STILL_KMH, PANEL_STOP_S) if self._run else
                                 (self._profile.moving_load, self._profile.still_load, self._profile.stop_s))
        if speed >= moving:
            self._belt_moved = True
            self._still_since = None
            return
        if not self._belt_moved or speed > still:
            self._still_since = None
            return
        if self._still_since is None:
            self._still_since = now
            return
        if now - self._still_since >= stop_s:
            self._panel_stopped()

    def _panel_stopped(self) -> None:
        log.info("treadmill stopped on the panel during stage %d: %s", self._idx,
                 "exhausted" if self.protocol.open_ended else "stopped")
        self._belt_running = False          # already stopped: never start or set it again
        if self._run:
            self._controllable = False
        if self.protocol.open_ended and not self._cur.is_warmup:
            now_s = self._now_s()
            if self._stage_elapsed >= 1.0:
                self._complete_stage(partial=True)
            self._exhaustion_t = now_s
            self._phase = Phase.RECOVERY
            self._timer = self.recovery_s
            self._prompt = self._pt("recovery_panel")
            if not self._run and self.cooldown_kmh > 0:      # ERG must not stay on the last watt
                self._set_belt(self._limits.speed(self.cooldown_kmh), 0.0, force_start=True)
            self._announcement = t("assessments.prompt.lactate") if self._pending else ""
            self._cue("finish")
            self._publish(force=True)
        else:
            self.stop()

    def _begin_stage(self, first: bool = False) -> None:
        """Enter the current stage: countdown (controllable, first) or operator prompt (manual)."""
        self._announced = False
        self._ticked.clear()
        self._stage_elapsed = 0.0
        if self._auto:
            self._phase = Phase.COUNTDOWN
            self._timer = self.countdown_s
            self._prompt = self._pt("countdown", target=self._target_text(self._cur))
        else:
            self._await_operator()

    def _await_operator(self) -> None:
        self._phase = Phase.AWAIT_OPERATOR
        self._prompt = self._operator_prompt(self._cur)
        self._announcement = self._cur.label
        self._cue("operator", self._stage_text(self._cur.speed_kmh, self._cur.incline_pct))

    def _operator_prompt(self, s: Stage) -> str:
        return self._pt("set_belt", target=self._target_text(s))

    @property
    def _auto(self) -> bool:
        """The stage clock runs without the operator confirming: the program sets the load, or the
        ergometer measures what the athlete does (the TV shows the target)."""
        return self._controllable or self._measured_only

    def _self_paced(self, s: Stage) -> bool:
        """The athlete holds the load of this stage and the ergometer measures it."""
        measured = bool(self.treadmill is not None and getattr(self.treadmill, "measured", False))
        return not self._run and measured and (self._measured_only or s.free)

    def _target_text(self, s: Stage) -> str:
        if s.free:
            return self._pt("free_target", target=self._stage_text(s.speed_kmh, s.incline_pct))
        return self._stage_text(s.speed_kmh, s.incline_pct)

    def _pt(self, name: str, **kw: Any) -> str:
        """A prompt in the words of the sport: ``assessments.prompt.<sport>.<name>`` when the sport
        has its own text, else the treadmill's ``assessments.prompt.<name>``."""
        if not self._run:
            key = f"assessments.prompt.{self.sport.value}.{name}"
            text = t(key, **kw)
            if text != key:
                return text
        return t(f"assessments.prompt.{name}", **kw)

    def _run_stage(self, carry: float = 0.0) -> None:
        """Start (or continue after a pause) the current stage's clock."""
        s = self._cur
        if self._stage_elapsed == 0.0:
            self._stage_start_t = self._now_s() - carry
            self._announced = False
        was_controllable = self._controllable
        self._set_belt(s.speed_kmh, s.incline_pct, force_start=True, free=s.free)
        if was_controllable and not self._controllable:
            self._phase = Phase.AWAIT_OPERATOR    # belt failed: the operator takes over
            self._prompt = self._operator_prompt(s)
            self._cue("operator", self._stage_text(s.speed_kmh, s.incline_pct))
            return
        self._phase = Phase.RUNNING
        self._stage_elapsed += carry
        self._ticked.clear()
        self._low_cadence_since = None
        if not self._flash_text:
            # self-paced: the target stays on the TV for the whole stage
            self._prompt = self._pt("hold", target=self._target_text(s)) if self._self_paced(s) else ""
        self._announcement = ""
        self._cue("go", s.label)

    def _complete_stage(self, partial: bool = False) -> None:
        s = self._cur
        end_t = self._now_s()
        start_t = self._stage_start_t
        hrs_end = [x.hr for x in self._samples if x.hr and end_t - HR_WINDOW_S <= x.t_s <= end_t and x.t_s >= start_t]
        hrs_all = [x.hr for x in self._samples if x.hr and start_t <= x.t_s <= end_t]
        if not hrs_end:
            hr = self._current_hr()
            hrs_end = [hr] if hr else []
        load = s.speed_kmh
        if not self._run:
            load = self._stage_stats(s, start_t, end_t)
        rec = StepRecord(
            index=self._idx,
            speed_kmh=load,
            incline_pct=s.incline_pct,
            duration_s=round(min(self._stage_elapsed, s.duration_s) if not partial else self._stage_elapsed, 1),
            hr_end=round(sum(hrs_end) / len(hrs_end)) if hrs_end else None,
            hr_mean=round(sum(hrs_all) / len(hrs_all)) if hrs_all else None,
        )
        if s.record_gait and self._gait is not None:
            g = self._gait
            rec.gait = g.to_dict() if hasattr(g, "to_dict") else (dict(g) if isinstance(g, dict) else {})
        for k, v in self._early_values.pop(self._idx, {}).items():
            setattr(rec, k, v)
        (self._warmup if s.is_warmup else self._records).append(rec)
        if s.sample_lactate and rec.lactate_mmol is None:
            self._pending.append(self._idx)
        if not s.is_warmup and self._idx >= self._n_protocol - 1 and self.protocol.open_ended:
            self._open_ended_s += rec.duration_s

    def _stage_stats(self, s: Stage, start_t: float, end_t: float) -> float:
        """Note what the ergometer measured in the stage, and return the stage's load: the target
        when the program set it, the measured mean when the athlete held it (self-paced)."""
        paced = self._self_paced(s)
        settle = SELF_PACED_SETTLE_S if end_t - start_t >= 4 * SELF_PACED_SETTLE_S else 0.0
        window = [x for x in self._samples if start_t + settle <= x.t_s <= end_t]
        loads = [x.speed_kmh for x in window if x.speed_kmh is not None]
        cads = [x.cadence_spm for x in window if x.cadence_spm]
        measured = bool(self.treadmill is not None and getattr(self.treadmill, "measured", False))
        mean = round(sum(loads) / len(loads), 1) if loads and measured else None
        stat: dict[str, Any] = {"index": self._idx, "target": None if s.free else s.speed_kmh, "measured": mean,
                                "cadence": round(sum(cads) / len(cads), 1) if cads else None,
                                "self_paced": paced, "free": s.free}
        drag = erg_values.median_int([d for t, d in self._drag if start_t <= t <= end_t])
        if drag is not None:
            stat["drag"] = drag
        if paced and mean is not None and not s.free and s.speed_kmh > 0:
            stat["in_band"] = abs(mean - s.speed_kmh) <= TARGET_BAND * s.speed_kmh
        self._stats[self._idx] = stat
        return mean if paced and mean is not None else s.speed_kmh

    def _after_stage(self, carry: float) -> None:
        s = self._cur
        if self._sprint_index is not None and self._idx == self._sprint_index:
            self._stage_elapsed = 0.0   # already recorded; exhausted() must not record it again
            self.exhausted()
            return
        if s.sample_lactate:
            self._cue("lactate")
            if self.protocol.lactate_pause_s > 0:
                self._phase = Phase.LACTATE_PAUSE
                self._timer = self.protocol.lactate_pause_s
                self._ticked.clear()
                if self._auto:
                    self._belt_stop()
                self._prompt = t("assessments.prompt.lactate")
                nxt = self._peek_next()
                self._announcement = (t("assessments.prompt.next", target=self._target_text(nxt))
                                      if nxt else "")
                return
            self._flash_text = t("assessments.prompt.lactate")
            self._flash_until = self._now_s() + 20.0
        self._next_stage(carry=carry)

    def _peek_next(self) -> Stage | None:
        if self._idx + 1 < len(self._stages):
            return self._stages[self._idx + 1]
        if self.protocol.open_ended and self._sprint_due():
            return self._sprint_stage()
        if self.protocol.open_ended and self._open_ended_s < self.protocol.max_open_ended_s:
            return self._generate(peek=True)
        return None

    def _generate(self, peek: bool = False) -> Stage:
        p = self.protocol
        last = self._stages[-1] if self._sprint_index is None else self._stages[self._sprint_index - 1]
        v, inc, how = ramp.next_step(last.speed_kmh, last.incline_pct, p, self._limits)
        s = Stage(v, inc, last.duration_s, t("assessments.stage.ramp", n=self._generated + 2))
        if not peek:
            self._generated += 1
            self._ramp_note(how, s)
        return s

    def _ramp_note(self, how: str, s: Stage) -> None:
        """Say once on the TV when the belt's limit changes how the ramp goes on."""
        if how == self._ramp_how:
            return
        self._ramp_how = how
        if not how:
            return
        lim = self._limits
        if how == "speed":
            text = t("assessments.prompt.ramp_speed", incline=fmt_pct(lim.max_incline_pct),
                     step=fmt_kmh(self.protocol.overflow_speed_kmh))
        elif how == "incline":
            text = t("assessments.prompt.ramp_incline", speed=fmt_kmh(lim.max_speed_kmh),
                     step=fmt_pct(self.protocol.overflow_incline_pct))
        else:
            text = self._pt("ramp_at_max", target=self._stage_text(s.speed_kmh, s.incline_pct))
        log.info("ramp at the treadmill's limit: %s", how)
        self._flash_text = text
        self._flash_until = self._now_s() + 30.0
        self.bus.publish(TOPIC_LOG, text)

    def _next_stage(self, carry: float, after_pause: bool = False) -> None:
        prev = self._cur
        if self._idx + 1 >= len(self._stages):
            if self.protocol.open_ended and self._sprint_due():
                self._stages.append(self._sprint_stage())     # automatic final sprint: the last step
                self._sprint_index = self._idx + 1
                self._sprint_ramp_s = self._ramp_elapsed_s()
                self._sprint_reason = "time" if self._sprint_ramp_s >= SPRINT_AFTER_S - 1e-6 else "plateau"
                self._cue("pre_announce", t("assessments.prompt.sprint_now"))
            elif self.protocol.open_ended and self._open_ended_s < self.protocol.max_open_ended_s:
                self._stages.append(self._generate())
            elif self.protocol.open_ended:
                self.exhausted()   # safety cap reached
                return
            else:
                self._finish()
                return
        self._idx += 1
        nxt = self._cur
        self._stage_elapsed = 0.0
        self._announced = False
        self._ticked.clear()
        if self._auto:
            self._run_stage(carry=carry)
        else:
            same = (abs(nxt.speed_kmh - prev.speed_kmh) < 1e-6 and abs(nxt.incline_pct - prev.incline_pct) < 1e-6)
            if same and not after_pause:
                self._run_stage(carry=carry)
            else:
                self._await_operator()
        if self._flash_text:
            self._prompt = self._flash_text if self._phase == Phase.RUNNING else self._prompt

    def _maybe_announce(self, remaining: float) -> None:
        if self._announced or remaining > self.announce_s:
            return
        self._announced = True
        s = self._cur
        if s.sample_lactate:
            text = t("assessments.prompt.soon_lactate", s=int(round(self.announce_s)))
        else:
            nxt = self._peek_next()
            if nxt is None:
                text = t("assessments.prompt.soon_end", s=int(round(self.announce_s)))
            else:
                text = t("assessments.prompt.soon", s=int(round(self.announce_s)), target=self._target_text(nxt))
        self._announcement = text
        self._cue("pre_announce", text)

    def _countdown_cues(self, remaining: float) -> None:
        for n in (3, 2, 1):
            if remaining <= n and n not in self._ticked and remaining > n - 1:
                self._ticked.add(n)
                self._cue("countdown", str(n))

    def _finish(self) -> None:
        if self.done:
            return
        if self._phase != Phase.RECOVERY and self._belt_running:
            if self._run:
                self._set_belt(self._limits.speed(self.cooldown_kmh), self._limits.incline(0.0))
            else:
                self._belt_stop()            # a trainer is released when the test is over
        self._phase = Phase.FINISHED
        self._paused = False
        self._prompt = t("assessments.prompt.finished")
        self._announcement = t("assessments.prompt.lactate") if self._pending else ""
        if self._exhaustion_t is None:
            self._cue("finish")
        self._publish(force=True)
        self._end()

    def _end(self) -> None:
        for u in self._unsubs:
            try:
                u()
            except Exception:
                pass
        self._unsubs = []
        self._wake.set()

    # ---------------------------------------------------------------- treadmill
    def _clamp(self, s: Stage) -> Stage:
        return replace(s, speed_kmh=self._limits.speed(s.speed_kmh), incline_pct=self._limits.incline(s.incline_pct))

    def _stage_text(self, load: float, incline_pct: float) -> str:
        return stage_text(load, incline_pct, self.sport)

    def _set_belt(self, speed: float, incline: float, force_start: bool = False, free: bool = False) -> None:
        if not self._controllable:
            self._prev_set = (speed, incline)
            if self._measured_only:
                self._tell_ergometer(speed, incline, force_start)
            return
        try:
            if not self._belt_running and force_start:
                self.treadmill.start()
                self._belt_running = True
            if free and hasattr(self.treadmill, "set_free"):
                self.treadmill.set_free()        # no power target: the rider chooses
            elif speed > 0 or self._run:
                self.treadmill.set_target(speed, incline)
            else:
                self.treadmill.stop()            # no easy load is set for this sport: release
                self._belt_running = False
            if self._run and (self._prev_set != (speed, incline) or force_start):
                # the targets in the log, not just the FTMS control point
                log.info("stage %d: %.1f km/h, %s %%", self._idx, speed, fmt_pct(incline).replace(",", "."))
            elif not self._run and (self._prev_set != (speed, incline) or force_start):
                log.info("stage %d: %s", self._idx, "free" if free else f"{speed:.0f} W")
            self._prev_set = (speed, incline)
        except Exception as e:  # never let a belt error crash the test: fall back to manual prompts
            log.exception("treadmill command failed")
            self._fallback_manual(e)

    def monitor_intervals(self, from_index: int = 0) -> list[Any]:
        """The test as intervals for a monitor that can show a workout (Concept2 PM5): one per stage
        with its length, the pause for the blood sample as rest, and the target watt. An open-ended
        ramp is continued with the steps it will generate, as far as the monitor takes them."""
        from lopanalys.devices.csafe import MAX_INTERVALS, Interval
        pause = self.protocol.lactate_pause_s
        stages = list(self._stages[from_index:])
        if self.protocol.open_ended and stages:
            last, total = stages[-1], 0.0
            while len(stages) < MAX_INTERVALS and total < self.protocol.max_open_ended_s:
                v, inc, _how = ramp.next_step(last.speed_kmh, last.incline_pct, self.protocol, self._limits)
                last = Stage(v, inc, last.duration_s, "")
                stages.append(last)
                total += last.duration_s
        return [Interval(s.duration_s, pause if s.sample_lactate and pause > 0 else 0.0, None if s.free else s.speed_kmh)
                for s in stages[:MAX_INTERVALS]]

    def _program_monitor(self, from_index: int) -> None:
        """Show the stages on the ergometer's own monitor when it can (it does not block, and says on
        the log topic how it went). The TV is the test's clock either way; a failure changes nothing."""
        fn = getattr(self.treadmill, "program_workout", None) if self._measured_only else None
        if fn is None:
            return
        try:
            fn(self.monitor_intervals(from_index))
        except Exception:
            log.exception("programming the monitor failed")

    def _tell_ergometer(self, load: float, incline: float, start: bool) -> None:
        """A measured ergometer that cannot be set still gets the target (for its state, and the
        simulated skier follows it). Its errors never touch the test."""
        try:
            if start and not self._belt_running:
                self.treadmill.start()
                self._belt_running = True
            self.treadmill.set_target(load, incline)
        except Exception:
            log.exception("ergometer target failed")

    def _belt_stop(self) -> None:
        self._belt_moved = False            # a stop on purpose (lactate pause) is not a stop on the panel
        if self._measured_only and self._belt_running:
            self._belt_running = False
            try:
                self.treadmill.stop()
            except Exception:
                log.exception("ergometer stop failed")
            return
        if not self._controllable or not self._belt_running:
            return
        try:
            self.treadmill.stop()
        except Exception as e:
            log.exception("treadmill stop failed")
            self._fallback_manual(e)
        self._belt_running = False

    def _fallback_manual(self, err: Exception) -> None:
        self._controllable = False
        self._belt_running = False
        msg = t("assessments.error.treadmill" if self._run else "assessments.error.ergometer", err=str(err))
        self.bus.publish(TOPIC_LOG, msg)
        if self._phase in (Phase.RUNNING, Phase.COUNTDOWN):
            self._phase = Phase.AWAIT_OPERATOR
            self._prompt = self._operator_prompt(self._cur)
            self._announcement = msg

    # ---------------------------------------------------------------- data
    def _on_hr(self, s: Any) -> None:
        bpm = getattr(s, "bpm", None)
        if bpm:
            self._hr_bus = (int(bpm), self.clock.now())
        cad = getattr(s, "cadence_spm", None)
        if cad:
            self._cad_bus = (float(cad), self.clock.now())

    def _on_tm(self, s: Any) -> None:
        self._tm_bus = s

    def _on_gait(self, g: Any) -> None:
        self._gait = g

    def _current_cadence(self) -> tuple[float | None, str | None]:
        """The strap's cadence (RSC) while it is fresh, else the camera's."""
        if self._run and self._cad_bus and self.clock.now() - self._cad_bus[1] <= HR_STALE_S:
            return self._cad_bus[0], "strap"
        if not self._run:                   # pedal revolutions or strokes per minute, from the ergometer
            st = self._erg_state()
            cad = getattr(st, "cadence", None) if st is not None else None
            return (float(cad), "ergometer") if cad else (None, None)
        cad = getattr(self._gait, "cadence_spm", None) if self._gait is not None else None
        return cad, ("camera" if cad is not None else None)

    def _erg_state(self) -> Any:
        st = None
        if self.treadmill is not None:
            try:
                st = self.treadmill.state
            except Exception:
                st = None
        return st or self._tm_bus

    def _current_hr(self) -> int | None:
        if self._hr_bus and self.clock.now() - self._hr_bus[1] <= HR_STALE_S:
            return self._hr_bus[0]
        last = getattr(self.heart_rate, "last", None) if self.heart_rate is not None else None
        bpm = getattr(last, "bpm", None)
        return int(bpm) if bpm and 25 <= bpm <= 250 else None

    def _current_belt(self) -> tuple[float | None, float | None]:
        st = None
        if self.treadmill is not None:
            try:
                st = self.treadmill.state
            except Exception:
                st = None
        st = st or self._tm_bus
        if st is not None and getattr(st, "speed_kmh", None) is not None and (self._controllable or getattr(st, "measured", True)):
            return st.speed_kmh, st.incline_pct
        if self._measured_only:             # a PM5 that has not said anything yet: no reading, not the target
            return None, 0.0
        if self._phase in (Phase.RUNNING, Phase.RECOVERY) and self._prev_set is not None:
            return self._prev_set
        if self._phase == Phase.LACTATE_PAUSE and self._controllable:
            return 0.0, self._prev_set[1] if self._prev_set else None
        return None, None

    def _sample(self, now: float) -> None:
        assert self._t0 is not None
        el = now - self._t0
        if el + 1e-9 < self._next_sample:
            return
        speed, inc = self._current_belt()
        cad, cad_src = self._current_cadence()
        hr = self._current_hr()
        if not self._run:
            st = self._erg_state()
            df = getattr(st, "drag_factor", None) if st is not None else None
            if isinstance(df, (int, float)) and df > 0:
                self._drag.append((el, int(df)))
        while self._next_sample <= el + 1e-9:
            self._samples.append(TimeSample(t_s=round(self._next_sample, 3), hr=hr, speed_kmh=speed,
                                            incline_pct=inc, cadence_spm=cad, cadence_source=cad_src))
            self._next_sample += 1.0

    def _find(self, idx: int) -> StepRecord | None:
        for r in self._records + self._warmup:
            if r.index == idx:
                return r
        return None

    def _check_index(self, idx: int) -> None:
        if not (0 <= idx < len(self._stages)):
            raise ValueError(t("assessments.error.stage_index", n=idx))

    def _now_s(self) -> float:
        return (self.clock.now() - self._t0) if self._t0 is not None else 0.0

    def _cue(self, kind: str, text: str = "") -> None:
        self.bus.publish(TOPIC_TEST_CUE, Cue(kind, round(self._now_s(), 2), text, self._idx))

    def _publish(self, force: bool = False) -> None:
        now = self.clock.now()
        if not force and now - self._last_pub < self._pub_period - 1e-9:
            return
        self._last_pub = now
        s = self._cur if self._stages else None
        nxt = self._peek_next() if self._phase not in (Phase.FINISHED, Phase.ABORTED, Phase.RECOVERY) else None
        dur = s.duration_s if s else 0.0
        countdown = self._timer if self._phase in (Phase.COUNTDOWN, Phase.LACTATE_PAUSE, Phase.RECOVERY) else None
        speed, inc = self._current_belt()
        st = RunnerState(
            phase=self._phase,
            stage_index=self._idx,
            stage=s,
            next_stage=nxt,
            stage_elapsed_s=round(self._stage_elapsed, 2),
            stage_remaining_s=round(max(0.0, dur - self._stage_elapsed), 2),
            total_elapsed_s=round(self._now_s(), 2),
            prompt=self._prompt,
            completed=[asdict(r) for r in self._records],
            announcement=self._announcement,
            countdown_s=round(max(0.0, countdown), 2) if countdown is not None else None,
            paused=self._paused,
            hr_bpm=self._current_hr(),
            speed_kmh=speed,
            incline_pct=inc,
            controllable=self._controllable,
            pending_lactate=list(self._pending),
            sprint=self.sprint_outlook(),
            **self._erg_fields(s, speed),
        )
        self._state = st
        self.bus.publish(TOPIC_TEST_STATE, st)

    def _erg_fields(self, s: Stage | None, load: float | None) -> dict[str, Any]:
        """The extra state of a trainer or a SkiErg: target, cadence, and the two warnings."""
        if self._run:
            return {}
        st = self._erg_state()
        cad = getattr(st, "cadence", None) if st is not None else None
        running = self._phase == Phase.RUNNING and s is not None
        paced = bool(running and self._self_paced(s))
        target = None if s is None or s.free or self._phase not in (Phase.RUNNING, Phase.COUNTDOWN) else s.speed_kmh
        off = bool(paced and target and load is not None and self._stage_elapsed >= SELF_PACED_SETTLE_S
                   and abs(load - target) > TARGET_BAND * target)
        low = bool(running and getattr(st, "low_cadence", False))
        now = self.clock.now()
        if low and self._low_cadence_since is None:
            self._low_cadence_since = now
        elif not low:
            self._low_cadence_since = None
        warn = bool(low and self._low_cadence_since is not None and now - self._low_cadence_since >= LOW_CADENCE_WARN_S)
        return {"sport": self.sport.value, "target_load": target, "cadence": cad, "self_paced": paced,
                "off_target": off, "low_cadence": warn}

    def _loop(self) -> None:
        try:
            while not self.done:
                self.tick()
                if getattr(self.clock, "real", False):
                    # real time: wake early on stop; tick_s of real time (scaled clocks tick faster)
                    self._wake.wait(self.tick_s)
                else:
                    self.clock.sleep(self.tick_s)
        except Exception:
            log.exception("test runner crashed")
            with self._lock:
                if not self.done:
                    self._belt_stop()
                    self._phase = Phase.ABORTED
                    self._prompt = self._pt("crash") if not self._run else t("assessments.error.crash")
                    self._publish(force=True)
                    self._end()
