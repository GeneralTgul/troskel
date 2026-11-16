"""Domain model shared by storage, assessments, reports and UI.

Plain dataclasses, no ORM. storage/ maps them to SQLite. Times are UTC datetimes; durations in
seconds; incline in percent; lactate in mmol/L; heart rate in bpm.

The *load* of a test is the quantity the protocol steps up: speed in km/h for running, power in
watt for cycling and SkiErg (``core.sport``). The fields are still called ``speed_kmh``; ``load``
is the name to read in new code, with the unit from the profile of the test's sport.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable

from lopanalys.core.sport import DEFAULT_SPORT, Sport, sport_of


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


@dataclass
class Athlete:
    id: int | None
    first_name: str
    last_name: str
    birth_date: date | None = None
    sex: Sex | None = None
    height_cm: float | None = None   # used to scale pixels to centimetres in gait analysis
    weight_kg: float | None = None
    max_hr: int | None = None        # running's max HR, known or measured; a max HR test updates it
    resting_hr: int | None = None
    notes: str = ""
    created: datetime | None = None
    # The consent from the signed form: {"date": "YYYY-MM-DD", "test": bool, "film": bool,
    # "store": bool, "contact": bool}. Empty: nothing is registered. It decides what the program does:
    # no film is recorded without "film", and without "store" everything is erased after the report.
    consent: dict[str, Any] = field(default_factory=dict)
    # Optional. The report and the FIT file are mailed here, only with the consent box "contact".
    email: str = ""
    # Schema v7: where max_hr came from ("entered" in the form, "test" from a max test) and when,
    # so the zones can say what they are built on; and the VO2max the athlete's watch shows, optional.
    max_hr_source: str = ""
    max_hr_date: date | None = None
    watch_vo2max: float | None = None
    watch_vo2max_date: date | None = None
    # Max HR in the other sports, {Sport value: bpm}. It differs between running, cycling and
    # double poling, so it is never copied from one sport to another. Running's is ``max_hr``.
    sport_max_hr: dict[str, int] = field(default_factory=dict)
    # Schema v10: the athlete's watch brand ("garmin", "huawei" ...), so the result says what to type
    # into that watch. Empty: not chosen.
    watch_brand: str = ""

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def consents(self, what: str) -> bool:
        """True when the person has consented to ``what`` ("test", "film", "store" or "contact")."""
        return bool((self.consent or {}).get(what))

    @property
    def consent_registered(self) -> bool:
        return bool((self.consent or {}).get("date")) or any(self.consents(k) for k in CONSENT_KEYS)

    def max_hr_for(self, sport: Any = None) -> int | None:
        """The max HR measured or entered for a sport (None: not known in that sport)."""
        sp = sport_of(sport)
        return self.max_hr if sp is DEFAULT_SPORT else self.sport_max_hr.get(sp.value)

    def set_max_hr(self, sport: Any, max_hr: int | None) -> None:
        sp = sport_of(sport)
        if sp is DEFAULT_SPORT:
            self.max_hr = max_hr
        elif max_hr:
            self.sport_max_hr[sp.value] = int(max_hr)
        else:
            self.sport_max_hr.pop(sp.value, None)


CONSENT_KEYS = ("test", "film", "store", "contact")

_EMAIL_RE = re.compile(r"[^@\s<>,;\"()\[\]\\]+@[^@\s<>,;\"()\[\]\\]+\.[^@\s<>,;\"()\[\]\\.]{2,}")


def valid_email(text: str) -> bool:
    """True for one plain address (``name@example.se``): no spaces, no line breaks, no display name."""
    return len(text) <= 254 and ".." not in text and _EMAIL_RE.fullmatch(text) is not None


class TestKind(str, Enum):
    __test__ = False  # not a pytest test class despite the name
    GAIT = "gait"              # running technique analysis
    LACTATE_STEP = "lactate"   # incremental step test with blood lactate
    MAX_HR = "max_hr"          # ramp to exhaustion
    HR_ZONES = "hr_zones"      # steady steps, then a ramp to exhaustion: zones with speed, no lactate
    FTP = "ftp"                # cycling: 20 minutes as hard as the rider can hold (FTP = 95 % of the mean power)
    BIKEFIT = "bikefit"        # cycling: the rider filmed on the trainer, analysed by pose.bike (pose.technique.BIKEFIT)
    SWIM_CSS = "swim_css"      # swimming: the 400 m and 200 m times written in by hand (assessments.css), no measurements
    SKI_TECHNIQUE = "ski"      # SkiErg: double poling filmed from the side, analysed by pose.ski (pose.technique.SKI)


# The kinds that end in a ramp to exhaustion and so measure max HR.
MAX_KINDS = (TestKind.MAX_HR, TestKind.HR_ZONES)


@dataclass
class Session:
    """One test occasion for one athlete. Raw data is stored per session."""
    id: int | None
    athlete_id: int
    kind: TestKind
    started: datetime
    ended: datetime | None = None
    protocol_name: str = ""
    protocol: dict[str, Any] = field(default_factory=dict)  # assessments.protocol.Protocol.to_dict()
    treadmill_mode: str = "manual"   # "ftms" | "qz" | "manual" | "sim"
    camera_source: str = ""          # human readable, e.g. "GoPro HERO8 (USB)"
    video_path: str | None = None
    temperature_c: float | None = None
    notes: str = ""
    result: dict[str, Any] = field(default_factory=dict)  # computed result, kind-specific JSON
    # "running" while the test runs (saved continuously), then "finished" | "stopped"; a test still
    # "running" when the program starts was cut off by a crash or power cut and becomes "interrupted".
    status: str = "finished"
    sport: Sport = DEFAULT_SPORT     # decides the unit of the load and whose max HR and zones apply

    def __post_init__(self) -> None:
        self.sport = sport_of(self.sport)


@dataclass
class StepRecord:
    """One completed protocol stage, with what was measured in it."""
    index: int
    speed_kmh: float
    incline_pct: float
    duration_s: float
    hr_end: int | None = None        # mean HR of the last 30 s of the stage
    hr_mean: int | None = None
    lactate_mmol: float | None = None
    rpe: int | None = None           # Borg 6-20
    gait: dict[str, Any] = field(default_factory=dict)  # pose.types.GaitSummary.to_dict() for the stage

    @property
    def load(self) -> float:
        """The stage's load in the unit of the test's sport (km/h for running)."""
        return self.speed_kmh


@dataclass
class TimeSample:
    """1 Hz time series stored per session (HR, speed, incline, cadence)."""
    t_s: float
    hr: int | None = None
    speed_kmh: float | None = None
    incline_pct: float | None = None
    cadence_spm: float | None = None
    cadence_source: str | None = None   # "strap" (the heart rate strap's RSC) or "camera"

    @property
    def load(self) -> float | None:
        """The load at this moment in the unit of the test's sport (km/h for running)."""
        return self.speed_kmh


MAX_SAMPLE_GAP_S = 5.0     # a longer gap between two samples adds no distance (the belt may have stood still)


def distance_m(samples: Iterable["TimeSample"], until_s: float | None = None) -> float:
    """The distance run on the belt in metres: the belt speed integrated over the samples (trapezoids),
    up to ``until_s`` if given. Gaps longer than ``MAX_SAMPLE_GAP_S`` count nothing."""
    total, prev = 0.0, None
    for x in samples:
        if until_s is not None and x.t_s > until_s:
            break
        if prev is not None and x.speed_kmh is not None and prev.speed_kmh is not None:
            dt = x.t_s - prev.t_s
            if 0 < dt <= MAX_SAMPLE_GAP_S:
                total += (x.speed_kmh + prev.speed_kmh) / 2 / 3.6 * dt
        prev = x
    return total
