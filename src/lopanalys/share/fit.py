"""A test as a FIT activity file: what Strava, Garmin Connect and TrainingPeaks import.

Pure Python, no dependencies and no Qt. ``encode_activity()`` builds the file from a session, its
step records and its 1 Hz samples; ``decode()`` is a small reader used to check what was written.

The file holds, in this order: file_id, file_creator, a timer start event, one ``record`` per second
with a ``lap`` after the last record of each lap, a timer stop event, one ``session`` and one
``activity``.

Sport and sub sport follow the test's sport (FIT profile, enums ``sport`` and ``sub_sport``):

=========  ==========================  ===========================================================
sport      sport / sub sport           in each record
=========  ==========================  ===========================================================
running    running / treadmill         speed, distance from the belt speed, grade, cadence, HR
cycling    cycling / indoor_cycling    power, cadence (rpm), HR; no speed or distance
SkiErg     fitness_equipment /         power, stroke rate as cadence, speed and distance from the
           indoor_skiing               power with Concept2's formula (W = 2.80 v^3), as the PM5 shows
=========  ==========================  ===========================================================

The profile has no sport for a ski ergometer of its own: ``indoor_skiing`` (25) is the sub sport the
profile files under fitness equipment, the way ``indoor_rowing`` is for a rowing machine.

Units are those of the FIT profile: speed in m/s, distance in metres, grade in percent, power in watt,
and running cadence in revolutions per minute, which is half the steps per minute the program shows
(the odd step goes into ``fractional_cadence``). Pedal and stroke cadence are written as they are.

Laps: the warm-up and every stage are one lap each, the recovery after "Utmattad" is a cool-down lap,
and a stop (the belt standing still, or the athlete resting on the bike or the SkiErg for a blood
sample) is a rest lap. The samples carry no stage number, so the laps are laid out from the stages'
durations along the stretches where the athlete works.

Privacy: nothing in the file names the person. No name, no birth date, no weight.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from lopanalys.core.models import Session, StepRecord, TimeSample
from lopanalys.core.sport import Sport, profile, sport_of

FIT_EPOCH = 631065600            # 1989-12-31T00:00:00Z as Unix time
PROTOCOL_VERSION = 0x10          # FIT 1.0: no developer fields are used
PROFILE_VERSION = 2132
MANUFACTURER_DEVELOPMENT = 255
SPORT_RUNNING, SUB_SPORT_TREADMILL = 1, 1
SPORT_CYCLING, SUB_SPORT_INDOOR_CYCLING = 2, 6
SPORT_FITNESS_EQUIPMENT, SUB_SPORT_INDOOR_SKIING = 4, 25
INTENSITY = {"active": 0, "rest": 1, "warmup": 2, "cooldown": 3}
FILL_GAP_S = 10                  # a hole in the samples up to this long is filled with the last values
MIN_REST_S = 5                   # a shorter stop is not a lap of its own
MOVING_KMH = 1.0                 # below this the belt stands still (other sports: the profile's moving_load)
TOLERANCE_S = 5                  # a stage this close to complete is complete

# base type -> (FIT type byte, struct format, invalid value)
_T = {"enum": (0x00, "B", 0xFF), "uint8": (0x02, "B", 0xFF), "sint16": (0x83, "h", 0x7FFF),
      "uint16": (0x84, "H", 0xFFFF), "uint32": (0x86, "I", 0xFFFFFFFF), "uint32z": (0x8C, "I", 0)}
_BY_BYTE = {v[0]: (k, v[1], v[2]) for k, v in _T.items()}

# message name -> (global number, [(field name, field number, base type, scale)])
MESSAGES: dict[str, tuple[int, list[tuple[str, int, str, float]]]] = {
    "file_id": (0, [("type", 0, "enum", 1), ("manufacturer", 1, "uint16", 1), ("product", 2, "uint16", 1),
                    ("serial_number", 3, "uint32z", 1), ("time_created", 4, "uint32", 1)]),
    "file_creator": (49, [("software_version", 0, "uint16", 1)]),
    "event": (21, [("timestamp", 253, "uint32", 1), ("event", 0, "enum", 1), ("event_type", 1, "enum", 1),
                   ("event_group", 4, "uint8", 1)]),
    "record": (20, [("timestamp", 253, "uint32", 1), ("heart_rate", 3, "uint8", 1), ("cadence", 4, "uint8", 1),
                    ("distance", 5, "uint32", 100), ("speed", 6, "uint16", 1000), ("power", 7, "uint16", 1),
                    ("grade", 9, "sint16", 100),
                    ("fractional_cadence", 53, "uint8", 128)]),
    "lap": (19, [("message_index", 254, "uint16", 1), ("timestamp", 253, "uint32", 1), ("event", 0, "enum", 1),
                 ("event_type", 1, "enum", 1), ("start_time", 2, "uint32", 1),
                 ("total_elapsed_time", 7, "uint32", 1000), ("total_timer_time", 8, "uint32", 1000),
                 ("total_distance", 9, "uint32", 100), ("total_cycles", 10, "uint32", 1),
                 ("avg_speed", 13, "uint16", 1000), ("max_speed", 14, "uint16", 1000),
                 ("avg_heart_rate", 15, "uint8", 1), ("max_heart_rate", 16, "uint8", 1),
                 ("avg_cadence", 17, "uint8", 1), ("max_cadence", 18, "uint8", 1),
                 ("avg_power", 19, "uint16", 1), ("max_power", 20, "uint16", 1), ("total_ascent", 21, "uint16", 1), ("intensity", 23, "enum", 1), ("lap_trigger", 24, "enum", 1),
                 ("sport", 25, "enum", 1), ("sub_sport", 39, "enum", 1), ("avg_grade", 45, "sint16", 100)]),
    "session": (18, [("message_index", 254, "uint16", 1), ("timestamp", 253, "uint32", 1), ("event", 0, "enum", 1),
                     ("event_type", 1, "enum", 1), ("start_time", 2, "uint32", 1), ("sport", 5, "enum", 1),
                     ("sub_sport", 6, "enum", 1), ("total_elapsed_time", 7, "uint32", 1000),
                     ("total_timer_time", 8, "uint32", 1000), ("total_distance", 9, "uint32", 100),
                     ("total_cycles", 10, "uint32", 1), ("avg_speed", 14, "uint16", 1000),
                     ("max_speed", 15, "uint16", 1000), ("avg_heart_rate", 16, "uint8", 1),
                     ("max_heart_rate", 17, "uint8", 1), ("avg_cadence", 18, "uint8", 1),
                     ("max_cadence", 19, "uint8", 1), ("avg_power", 20, "uint16", 1),
                     ("max_power", 21, "uint16", 1), ("total_ascent", 22, "uint16", 1),
                     ("first_lap_index", 25, "uint16", 1), ("num_laps", 26, "uint16", 1),
                     ("trigger", 28, "enum", 1)]),
    "activity": (34, [("timestamp", 253, "uint32", 1), ("total_timer_time", 0, "uint32", 1000),
                      ("num_sessions", 1, "uint16", 1), ("type", 2, "enum", 1), ("event", 3, "enum", 1),
                      ("event_type", 4, "enum", 1), ("local_timestamp", 5, "uint32", 1)]),
}
_BY_NUMBER = {num: (name, {f[1]: f for f in fields}) for name, (num, fields) in MESSAGES.items()}

_CRC_TABLE = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)


class FitError(ValueError):
    """The test cannot be written as a FIT file (``reason``: "no_samples"), or a file is not valid FIT."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def crc16(data: bytes, crc: int = 0) -> int:
    """The FIT checksum (CRC-16/ARC), as in the FIT SDK."""
    for b in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[b & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[(b >> 4) & 0xF]
    return crc


# ---------------------------------------------------------------------------- writer
class _Writer:
    def __init__(self) -> None:
        self.body = bytearray()
        self._local: dict[str, int] = {}

    def add(self, name: str, **values: Any) -> None:
        number, fields = MESSAGES[name]
        if name not in self._local:                    # the definition goes in front of the first message
            local = self._local[name] = len(self._local)
            self.body += struct.pack("<BBBHB", 0x40 | local, 0, 0, number, len(fields))
            for _, num, base, _scale in fields:
                self.body += struct.pack("<BBB", num, struct.calcsize(_T[base][1]), _T[base][0])
        self.body.append(self._local[name])
        for fname, _num, base, scale in fields:
            _byte, fmt, invalid = _T[base]
            v = values.get(fname)
            if v is None:
                raw = invalid
            else:
                raw = int(round(float(v) * scale))
                lo, hi = (-0x8000, 0x7FFE) if fmt == "h" else (0, invalid - 1 if invalid else (1 << 32) - 1)
                raw = min(max(raw, lo), hi)
            self.body += struct.pack("<" + fmt, raw)
        unknown = set(values) - {f[0] for f in fields}
        if unknown:
            raise KeyError(f"{name}: {sorted(unknown)}")

    def file(self) -> bytes:
        head = struct.pack("<BBHI4s", 14, PROTOCOL_VERSION, PROFILE_VERSION, len(self.body), b".FIT")
        head += struct.pack("<H", crc16(head))
        data = head + bytes(self.body)
        return data + struct.pack("<H", crc16(data))


# ---------------------------------------------------------------------------- the sport
def _skierg_mps(power_w: float) -> float:
    """Speed on a SkiErg at a power, as the PM5 counts it: W = 2.80 v^3 (Concept2)."""
    return (max(power_w, 0.0) / 2.80) ** (1.0 / 3.0)


@dataclass(frozen=True)
class SportFit:
    """What a test's sport means for the file."""
    sport: int
    sub_sport: int
    moving: float                                   # load at or above which the athlete works
    power: bool                                     # the load is watt (written as power)
    speed_mps: Any                                  # load -> m/s, or None: the sport has no speed
    cadence_per_cycle: float                        # program cadence / FIT cadence (running: 2 steps a stride)
    grade: bool                                     # the load has an incline (the treadmill)


SPORTS: dict[Sport, SportFit] = {
    Sport.RUN: SportFit(SPORT_RUNNING, SUB_SPORT_TREADMILL, MOVING_KMH, False, lambda v: v / 3.6, 2.0, True),
    Sport.BIKE: SportFit(SPORT_CYCLING, SUB_SPORT_INDOOR_CYCLING, profile(Sport.BIKE).moving_load, True,
                         None, 1.0, False),
    Sport.SKIERG: SportFit(SPORT_FITNESS_EQUIPMENT, SUB_SPORT_INDOOR_SKIING, profile(Sport.SKIERG).moving_load,
                           True, _skierg_mps, 1.0, False),
}


def sport_fit(session: Session) -> SportFit:
    """The FIT sport of a test (a test from before sport existed is a run)."""
    return SPORTS[sport_of(session.sport)]


# ---------------------------------------------------------------------------- the timeline
@dataclass
class _Sec:
    t: int
    hr: int | None
    speed: float | None       # the load: km/h on the treadmill, watt on the bike and the SkiErg
    incline: float | None
    cadence: float | None     # as the program has it: steps, pedal revolutions or strokes a minute
    dist: float = 0.0         # metres covered when this second begins (0 without a speed)
    moving: bool = False      # the athlete works: the load at or above the sport's ``moving``
    mps: float | None = None  # speed in m/s, None when the sport has none


@dataclass
class Lap:
    """Seconds ``start`` (inclusive) to ``end`` on the test clock; ``kind`` is an INTENSITY key."""
    start: int
    end: int
    kind: str
    stage: int | None = None      # StepRecord.index for a warm-up or stage lap


def _timeline(samples: Iterable[TimeSample], kind: SportFit = SPORTS[Sport.RUN]) -> list[_Sec]:
    """One row per whole second. Short holes are filled with the last values, long ones stay holes.
    Seconds before the athlete first works (the countdown) are left out."""
    by_t: dict[int, TimeSample] = {}
    for x in samples:
        by_t[int(round(x.t_s))] = x
    out: list[_Sec] = []
    for t in sorted(by_t):
        x = by_t[t]
        if out and 1 < t - out[-1].t <= FILL_GAP_S:
            p = out[-1]
            out += [_Sec(k, p.hr, p.speed, p.incline, p.cadence) for k in range(p.t + 1, t)]
        out.append(_Sec(t, int(x.hr) if x.hr else None, x.speed_kmh, x.incline_pct, x.cadence_spm or None))
    for x in out:
        x.moving = x.speed is not None and x.speed >= kind.moving
        x.mps = kind.speed_mps(x.speed) if kind.speed_mps is not None and x.speed is not None else None
    first = next((i for i, s in enumerate(out) if s.moving), None)
    out = out[first:] if first is not None else out
    for prev, cur in zip(out, out[1:]):
        step = cur.t - prev.t
        cur.dist = prev.dist + ((prev.mps or 0.0) if step == 1 else 0.0)   # nothing is covered inside a hole
    return out


def _stretches(secs: list[_Sec]) -> list[tuple[int, int, bool]]:
    """(first index, index after the last, moving) for each run of seconds that follow each other
    and are all moving or all standing still. Short stops are joined to the stretch before them."""
    raw: list[list[Any]] = []
    for i, s in enumerate(secs):
        if raw and raw[-1][2] == s.moving and secs[i - 1].t == s.t - 1:
            raw[-1][1] = i + 1
        else:
            raw.append([i, i + 1, s.moving])
    out: list[list[Any]] = []
    for a, b, moving in raw:
        joins = out and secs[a - 1].t == secs[a].t - 1
        if joins and ((not moving and b - a < MIN_REST_S) or out[-1][2] == moving):
            out[-1][1] = b
        else:
            out.append([a, b, moving])
    return [(a, b, m) for a, b, m in out]


def _median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2]


def plan_laps(secs: list[_Sec], records: Iterable[StepRecord], warmup: set[int],
              exhaustion_t_s: float | None = None) -> list[Lap]:
    """Lay the stages out along the seconds where the belt moves.

    Each stage takes its own duration from the moving stretch it starts in; a stage cut in two by an
    operator pause goes on in the next stretch. What is left of a stretch when its stage is complete
    (the belt slowing down for the blood sample) stays in that stage's lap, unless it runs at the
    next stage's speed. Everything after ``exhaustion_t_s`` is one cool-down lap."""
    if not secs:
        return []
    queue = [[r, max(1.0, float(r.duration_s))] for r in sorted(records, key=lambda r: r.index)]
    laps: list[Lap] = []
    cool_from = None
    if exhaustion_t_s is not None:
        cool_from = next((i for i, s in enumerate(secs) if s.t >= exhaustion_t_s), None)

    def put(a: int, b: int, kind: str, stage: int | None = None) -> None:
        if b <= a:
            return
        if laps and laps[-1].kind == kind and laps[-1].stage == stage and laps[-1].end == secs[a].t:
            laps[-1].end = secs[b - 1].t + 1
        else:
            laps.append(Lap(secs[a].t, secs[b - 1].t + 1, kind, stage))

    for a, b, moving in _stretches(secs):
        if cool_from is not None and a >= cool_from:
            put(a, b, "cooldown" if moving else "rest")
            continue
        if not moving:
            put(a, b, "rest")
            continue
        end = min(b, cool_from) if cool_from is not None else b
        pos, last = a, None
        while pos < end and queue:
            r, left = queue[0]
            room = end - pos
            if last is not None and room < left - TOLERANCE_S:
                # too short for the whole stage: the belt slowing down, unless it runs at the stage's speed
                speeds = [s.speed for s in secs[pos:end] if s.speed is not None]
                if not speeds or abs(_median(speeds) - r.speed_kmh) > max(0.7, 0.1 * r.speed_kmh):
                    break
            take = int(round(min(room, left)))
            if take <= 0:
                queue.pop(0)
                continue
            last = "warmup" if r.index in warmup else "active", r.index
            put(pos, pos + take, *last)
            pos += take
            if left - take <= TOLERANCE_S:
                queue.pop(0)
            else:
                queue[0][1] = left - take
        if pos < end:                                  # what is left belongs to the stage that just ran
            kind, stage = last if last is not None else ("active", None)
            if laps and last is not None:
                laps[-1].end = secs[end - 1].t + 1
            else:
                put(pos, end, kind, stage)
        if end < b:
            put(end, b, "cooldown")
    # the laps follow each other without holes, also over a hole in the samples
    for prev, cur in zip(laps, laps[1:]):
        prev.end = cur.start
    laps[-1].end = secs[-1].t
    return [lp for lp in laps if lp.end > lp.start] or [Lap(secs[0].t, secs[-1].t, "active")]


# ---------------------------------------------------------------------------- the file
def _summary(secs: list[_Sec], a: int, b: int, kind: SportFit = SPORTS[Sport.RUN]) -> dict[str, Any]:
    """Totals for the seconds with t in [a, b]: the lap ends on the record that starts the next.
    Power is averaged over every second of the lap, the rests at 0 W included, as a watch does."""
    inside = [s for s in secs if a <= s.t < b] or [s for s in secs if s.t == a]
    edge = [s for s in secs if a <= s.t <= b]
    elapsed = float(b - a)
    has_speed = kind.speed_mps is not None
    dist = edge[-1].dist - edge[0].dist if edge and has_speed else 0.0
    hr = [s.hr for s in inside if s.hr]
    cad = [s.cadence / kind.cadence_per_cycle for s in inside if s.cadence]
    speeds = [s.mps for s in inside if s.mps is not None]
    watts = [s.speed for s in inside if s.speed is not None] if kind.power else []
    grades = [s.incline for s in inside if s.incline is not None and s.moving] if kind.grade else []
    ascent = sum((s.mps or 0.0) * max(s.incline or 0.0, 0.0) / 100 for s in inside)
    return {"total_elapsed_time": elapsed, "total_timer_time": elapsed,
            "total_distance": dist if has_speed else None,
            "total_cycles": round(sum(cad) / 60) if cad else None,
            "avg_speed": dist / elapsed if elapsed > 0 and has_speed else None,
            "max_speed": max(speeds) if speeds else None,
            "avg_heart_rate": round(sum(hr) / len(hr)) if hr else None, "max_heart_rate": max(hr) if hr else None,
            "avg_cadence": round(sum(cad) / len(cad)) if cad else None,
            "max_cadence": int(max(cad)) if cad else None,
            "avg_power": round(sum(watts) / len(watts)) if watts else None,
            "max_power": round(max(watts)) if watts else None,
            "total_ascent": round(ascent) if grades else None}


def encode_activity(session: Session, records: Iterable[StepRecord], samples: Iterable[TimeSample]) -> bytes:
    """The test as the bytes of a FIT activity file. ``records``: every stage, the warm-up included.

    Raises FitError("no_samples") for a test without a time series (there is nothing to put in it)."""
    kind = sport_fit(session)
    secs = _timeline(samples, kind)
    if len(secs) < 2:
        raise FitError("no_samples")
    started = session.started if session.started.tzinfo else session.started.replace(tzinfo=timezone.utc)
    t0 = int(started.timestamp()) - FIT_EPOCH

    def ts(t: int) -> int:
        return t0 + t

    stages = (session.protocol or {}).get("stages", [])
    warm = {i for i, st in enumerate(stages) if st.get("is_warmup")}
    laps = plan_laps(secs, list(records), warm, (session.result or {}).get("exhaustion_t_s"))
    first, last = secs[0].t, secs[-1].t
    w = _Writer()
    w.add("file_id", type=4, manufacturer=MANUFACTURER_DEVELOPMENT, product=0, serial_number=session.id or 1,
          time_created=ts(first))
    w.add("file_creator", software_version=100)
    w.add("event", timestamp=ts(first), event=0, event_type=0, event_group=0)             # timer start
    lap_i = 0
    for s in secs:
        while lap_i < len(laps) - 1 and s.t >= laps[lap_i].end:
            _lap(w, secs, laps, lap_i, ts, kind)
            lap_i += 1
        rpm = s.cadence / kind.cadence_per_cycle if s.cadence else None
        w.add("record", timestamp=ts(s.t), heart_rate=s.hr, cadence=int(rpm) if rpm is not None else None,
              distance=s.dist if kind.speed_mps is not None else None, speed=s.mps,
              power=s.speed if kind.power else None, grade=s.incline if kind.grade else None,
              fractional_cadence=rpm - int(rpm) if rpm is not None else None)
    for i in range(lap_i, len(laps)):
        _lap(w, secs, laps, i, ts, kind)
    w.add("event", timestamp=ts(last), event=0, event_type=4, event_group=0)              # timer stop all
    total = _summary(secs, first, last, kind)
    w.add("session", message_index=0, timestamp=ts(last), event=8, event_type=1, start_time=ts(first),
          sport=kind.sport, sub_sport=kind.sub_sport, first_lap_index=0, num_laps=len(laps), trigger=0,
          **total)
    local = int(started.astimezone().utcoffset().total_seconds()) if started.astimezone().utcoffset() else 0
    w.add("activity", timestamp=ts(last), total_timer_time=float(last - first), num_sessions=1, type=0, event=26,
          event_type=1, local_timestamp=ts(last) + local)
    return w.file()


def _lap(w: _Writer, secs: list[_Sec], laps: list[Lap], i: int, ts: Any, kind: SportFit) -> None:
    lp = laps[i]
    tot = _summary(secs, lp.start, lp.end, kind)
    grades = [s.incline for s in secs if lp.start <= s.t < lp.end and s.incline is not None and s.moving] \
        if kind.grade else []
    w.add("lap", message_index=i, timestamp=ts(lp.end), event=9, event_type=1, start_time=ts(lp.start),
          intensity=INTENSITY[lp.kind], lap_trigger=7 if i == len(laps) - 1 else 0, sport=kind.sport,
          sub_sport=kind.sub_sport, avg_grade=sum(grades) / len(grades) if grades else None, **tot)


def file_name(session: Session) -> str:
    """A neutral file name: the test's id and date, never the person."""
    day = session.started.astimezone().strftime("%Y-%m-%d") if session.started else "test"
    return f"test-{session.id or 0}-{day}.fit"


# ---------------------------------------------------------------------------- reader (for checks)
def decode(data: bytes) -> dict[str, list[dict[str, Any]]]:
    """Read a FIT file written by this module (or any file without developer fields or compressed
    timestamps) into ``{message name: [fields]}``, values scaled to their units. Both checksums are
    verified. Raises FitError("header" | "crc" | "data")."""
    if len(data) < 16 or data[0] not in (12, 14) or data[8:12] != b".FIT":
        raise FitError("header")
    hsize = data[0]
    size = struct.unpack_from("<I", data, 4)[0]
    if hsize == 14 and struct.unpack_from("<H", data, 12)[0] not in (0, crc16(data[:12])):
        raise FitError("crc")
    if len(data) != hsize + size + 2 or crc16(data[:-2]) != struct.unpack_from("<H", data, len(data) - 2)[0]:
        raise FitError("crc")
    out: dict[str, list[dict[str, Any]]] = {}
    defs: dict[int, tuple[int, list[tuple[int, int, int]]]] = {}
    pos, end = hsize, hsize + size
    try:
        while pos < end:
            head = data[pos]
            pos += 1
            if head & 0x80 or head & 0x20:
                raise FitError("data")                 # compressed timestamp or developer data
            if head & 0x40:
                _res, arch, number, n = struct.unpack_from("<BBHB", data, pos)
                if arch != 0:
                    raise FitError("data")
                pos += 5
                defs[head & 0x0F] = (number, [struct.unpack_from("<BBB", data, pos + 3 * k) for k in range(n)])
                pos += 3 * n
                continue
            number, fields = defs[head & 0x0F]
            name, known = _BY_NUMBER.get(number, (f"unknown_{number}", {}))
            row: dict[str, Any] = {}
            for num, fsize, base in fields:
                _tname, fmt, invalid = _BY_BYTE[base]
                raw = struct.unpack_from("<" + fmt, data, pos)[0]
                pos += fsize
                fname, _n, _b, scale = known.get(num, (f"field_{num}", num, "", 1))
                row[fname] = None if raw == invalid else (raw / scale if scale != 1 else raw)
            out.setdefault(name, []).append(row)
    except (KeyError, struct.error) as e:
        raise FitError("data") from e
    return out


def fit_time(value: int) -> datetime:
    """A FIT timestamp as a UTC datetime."""
    return datetime.fromtimestamp(value + FIT_EPOCH, tz=timezone.utc)
