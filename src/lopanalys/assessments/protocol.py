"""Test protocols as data, and the runner state the UI shows.

A Protocol is a list of Stages. The runner (assessments agent) drives the treadmill (or prompts
the operator in manual mode), collects HR per stage, pauses for lactate samples, and ends on a
stop criterion (last stage, operator stop, or exhaustion button). No Qt imports.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from lopanalys.core.sport import sport_of


@dataclass
class Stage:
    speed_kmh: float
    incline_pct: float
    duration_s: float
    label: str = ""                   # e.g. "Uppvärmning", "Steg 3"
    sample_lactate: bool = False      # pause/marker at the end of the stage for a blood sample
    record_gait: bool = False         # store a gait summary for this stage
    is_warmup: bool = False
    free: bool = False                # no load is set: the athlete chooses it (the 20 minute FTP test);
                                      # ``speed_kmh`` is then only a number to aim at

    @property
    def load(self) -> float:
        """The stage's load in the unit of the protocol's sport (km/h for running)."""
        return self.speed_kmh


@dataclass
class Protocol:
    name: str
    kind: str                          # core.models.TestKind value
    stages: list[Stage]
    lactate_pause_s: float = 30.0      # 0 = sample while running
    open_ended: bool = False           # max HR / ramp: repeat the last increment until stop
    increment_speed_kmh: float = 0.0   # used when open_ended
    increment_incline_pct: float = 0.0
    description: str = ""
    # Added by the assessments agent (defaults keep older JSON loadable):
    final_sprint_s: float = 0.0        # >0: the coach can trigger a final sprint in an open-ended ramp
    final_sprint_kmh: float = 0.0      # speed added for the final sprint
    max_open_ended_s: float = 1800.0   # safety cap for the open-ended part (s)
    # past the treadmill's limits (assessments.ramp.next_step). An incline ramp at the highest
    # incline goes on with the speed, a speed ramp at the highest speed with the incline.
    overflow_speed_kmh: float = 0.5
    overflow_incline_pct: float = 1.0
    final_sprint_auto: bool = False    # the runner starts the sprint itself (nobody presses), see runner
    builder: str = ""                  # library builder that made it, e.g. "lactate_step"
    params: dict[str, Any] = field(default_factory=dict)  # builder arguments, to rebuild/edit
    sport: str = "run"                 # core.sport.Sport value; a protocol stored without it is running

    @property
    def increment_load(self) -> float:
        """The open-ended step in the unit of the protocol's sport."""
        return self.increment_speed_kmh

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Protocol":
        known = set(Protocol.__dataclass_fields__)
        d = {k: v for k, v in d.items() if k in known}
        stage_fields = set(Stage.__dataclass_fields__)
        d["stages"] = [Stage(**{k: v for k, v in s.items() if k in stage_fields}) for s in d["stages"]]
        d["sport"] = sport_of(d.get("sport")).value
        return Protocol(**d)


# Running technique is only judged on stages where the runner had time to find the stride: an even
# speed and an unchanged incline for at least this long (a max HR test is
# for the max HR and is not adapted for the running technique). A test is never bent to fit the film; a
# protocol that does not meet the rule is simply not combined with technique.
GAIT_MIN_STAGE_S = 60.0


def gait_compatible(p: Protocol) -> bool:
    """True when the protocol may be combined with running technique: every work stage lasts at
    least GAIT_MIN_STAGE_S, and the incline is the same in all of them (also in the stages an open
    ramp adds). An incline ramp never qualifies; lactate steps and a speed ramp with one-minute
    stages on a fixed incline do."""
    work = [s for s in p.stages if not s.is_warmup]
    if not work or any(s.duration_s < GAIT_MIN_STAGE_S for s in work):
        return False
    if len({round(s.incline_pct, 2) for s in work}) > 1:
        return False
    return not (p.open_ended and abs(p.increment_incline_pct) > 1e-9)


class Phase(str, Enum):
    IDLE = "idle"
    COUNTDOWN = "countdown"
    RUNNING = "running"
    LACTATE_PAUSE = "lactate_pause"   # waiting for the sample (and for the value to be typed)
    AWAIT_OPERATOR = "await_operator" # manual mode: waiting for "speed is set"
    RECOVERY = "recovery"             # after exhaustion: walking, recovery HR is recorded (added)
    FINISHED = "finished"
    ABORTED = "aborted"


@dataclass
class SprintOutlook:
    """How near the automatic final sprint is (``TestRunner.sprint_outlook``), for the coach's
    page. Times are seconds of ramp, counted from the ramp's first step."""
    in_ramp: bool                      # the ramp has started (False in the warm-up and the lactate steps)
    ramp_elapsed_s: float              # ramp run so far
    hr_rise_bpm: float | None          # the heart rate's rise over the last minute; None without enough data
    limit_bpm: float                   # the sprint starts when the rise is at most this ...
    min_ramp_s: float                  # ... once the ramp has run at least this long,
    after_s: float                     # or at the end of the step where the ramp has run this long
    remaining_s: float | None          # until that step end; None before the ramp
    started_s: float | None = None     # ramp time when the sprint started; None before it has
    reason: str = ""                   # why it started: "plateau", "time" or "manual" (the button)


@dataclass
class RunnerState:
    phase: Phase
    stage_index: int
    stage: Stage | None
    next_stage: Stage | None
    stage_elapsed_s: float
    stage_remaining_s: float
    total_elapsed_s: float
    prompt: str = ""                  # big text for the TV, e.g. "Sätt bandet på 12,0 km/h"
    completed: list[dict[str, Any]] = field(default_factory=list)  # core.models.StepRecord as dicts
    # Added by the assessments agent:
    announcement: str = ""            # smaller line under the prompt, e.g. "Om 15 s: 13,0 km/h · 1 %"
    countdown_s: float | None = None  # seconds left of a countdown/pause shown big, else None
    paused: bool = False              # operator pause (timers frozen)
    hr_bpm: int | None = None         # latest heart rate
    speed_kmh: float | None = None    # latest treadmill speed (target in manual mode)
    incline_pct: float | None = None
    controllable: bool = True         # False: manual treadmill, the operator sets the speed
    pending_lactate: list[int] = field(default_factory=list)  # stage indexes still lacking a value
    # Sports with an ergometer that reports more than a load:
    sport: str = "run"                # core.sport.Sport value: the unit of the load
    target_load: float | None = None  # what the stage asks for; ``speed_kmh`` is what is measured
    cadence: float | None = None      # pedal revolutions/min (bike) or strokes/min (SkiErg)
    self_paced: bool = False          # the athlete holds the load (SkiErg, a free stage): target and outcome differ
    off_target: bool = False          # self-paced and outside the band around the target right now
    low_cadence: bool = False         # bike in ERG: the cadence is too low for the trainer to hold the power
    sprint: SprintOutlook | None = None  # the automatic final sprint's outlook; None when the test has none

    @property
    def load(self) -> float | None:
        """The latest load in the unit of the protocol's sport (km/h for running)."""
        return self.speed_kmh
