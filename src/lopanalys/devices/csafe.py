"""CSAFE frames for a Concept2 PM5: put a workout on the monitor (pure functions, no Bluetooth).

ErgData and ErgZone set a workout up on the monitor (intervals with a length, a rest and a target
pace) by writing CSAFE frames to the PM5's control service (write 0x0021, answers on 0x0022). That
never changes the resistance (the damper is set by hand), but the monitor then shows the stage and
the target in front of the athlete. ``workout_frames`` builds the frames for a *variable interval*
workout: one interval per stage of a SkiErg test.

What is checked and what is not:
  - **Checked against open code** (droogmic/Py3Row ``pyrow/csafe``, read through a search):
    the standard start flag 0xF1, the stop flag 0xF2, byte stuffing (0xF3 before the byte, the byte
    replaced by ``byte & 0x03``, for 0xF0-0xF3), the checksum (XOR of the frame's contents before
    stuffing, flags left out, the checksum itself not stuffed), a command as (id, byte count,
    data), the wrapper 0x1A around PM-specific commands, and CSAFE_PM_SET_SPLITDURATION = 0x05
    with a type byte and four bytes.
  - **From memory of Concept2's "PM CSAFE Communication Definition", not checked:** the ids of
    the other PM-specific commands below, the workout and interval type numbers, the units
    (0.01 s for a duration and for the pace per 500 m, whole seconds for the rest), the order of
    the commands for one interval, the screen state that makes the monitor show the workout, the
    byte order of the multi-byte values (``PM_BYTE_ORDER``: most significant byte first, as the
    definition lists them; Py3Row sends the least significant first), whether a rest of 0 s is
    accepted, how many intervals the monitor takes, and that a frame may be split over several
    20-byte writes. Nothing here has met a real PM5: see docs/PROV.md.
"""
from __future__ import annotations

from dataclasses import dataclass

START, STOP, STUFF = 0xF1, 0xF2, 0xF3
PM_WRAPPER = 0x1A                     # CSAFE_SETUSERCFG1_CMD: carries the PM-specific commands
PM_BYTE_ORDER = "big"                 # not checked: the first thing to flip if the monitor shows wrong numbers

# PM-specific set commands (inside the wrapper). Only 0x05 is checked against open code.
PM_SET_WORKOUTTYPE = 0x01
PM_SET_WORKOUTDURATION = 0x03
PM_SET_RESTDURATION = 0x04
PM_SET_SPLITDURATION = 0x05
PM_SET_TARGETPACETIME = 0x06
PM_SET_SCREENSTATE = 0x13
PM_CONFIGURE_WORKOUT = 0x14
PM_SET_INTERVALTYPE = 0x17
PM_SET_WORKOUTINTERVALCOUNT = 0x18

WORKOUT_VARIABLE_INTERVAL = 8
INTERVAL_TIME = 0
DURATION_TIME = 0x00
SCREEN_WORKOUT, SCREEN_PREPARE_TO_ROW = 1, 1
MAX_INTERVALS = 30                    # kept well under what the monitor is said to take (50)
BLE_CHUNK = 20

# The status byte of an answer: bits 4-5 say how the previous frame was taken.
PREV_OK, PREV_REJECT, PREV_BAD, PREV_NOT_READY = 0x00, 0x10, 0x20, 0x30


@dataclass(frozen=True)
class Interval:
    """One stage as the monitor gets it."""
    duration_s: float
    rest_s: float = 0.0
    target_w: float | None = None     # shown as a target pace per 500 m


def checksum(contents: bytes) -> int:
    c = 0
    for b in contents:
        c ^= b
    return c


def stuff(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if 0xF0 <= b <= 0xF3:
            out += bytes([STUFF, b & 0x03])
        else:
            out.append(b)
    return bytes(out)


def unstuff(data: bytes) -> bytes:
    out, it = bytearray(), iter(data)
    for b in it:
        out.append(0xF0 | next(it, 0) if b == STUFF else b)
    return bytes(out)


def frame(contents: bytes) -> bytes:
    """A standard frame: start flag, the stuffed contents, the checksum, stop flag."""
    return bytes([START]) + stuff(contents) + stuff(bytes([checksum(contents)])) + bytes([STOP])


def unframe(data: bytes) -> bytes | None:
    """The contents of a frame, or None when the flags or the checksum are wrong."""
    b = bytes(data)
    if len(b) < 4 or b[0] != START or b[-1] != STOP:
        return None
    body = unstuff(b[1:-1])
    return body[:-1] if body and checksum(body[:-1]) == body[-1] else None


def command(cmd: int, data: bytes = b"") -> bytes:
    """One command: the id, then for a command with data the byte count and the data."""
    return bytes([cmd, len(data)]) + data if data else bytes([cmd])


def pm_frame(*commands: bytes) -> bytes:
    """A frame with PM-specific commands inside the wrapper."""
    inner = b"".join(commands)
    return frame(bytes([PM_WRAPPER, len(inner)]) + inner)


def _n(value: float, size: int) -> bytes:
    return max(0, min(256 ** size - 1, int(round(value)))).to_bytes(size, PM_BYTE_ORDER)


def pace_500m_s(watt: float) -> float:
    """Concept2: seconds per 500 m at a power (W = 2.80 / (s/m)^3)."""
    return 500.0 * (2.80 / watt) ** (1.0 / 3.0)


def interval_frame(number: int, iv: Interval) -> bytes:
    """The frame that defines interval ``number`` (0-based) of a variable interval workout."""
    cmds = [command(PM_SET_WORKOUTINTERVALCOUNT, bytes([number])),
            command(PM_SET_WORKOUTTYPE, bytes([WORKOUT_VARIABLE_INTERVAL])),
            command(PM_SET_INTERVALTYPE, bytes([INTERVAL_TIME])),
            command(PM_SET_WORKOUTDURATION, bytes([DURATION_TIME]) + _n(iv.duration_s * 100, 4)),
            command(PM_SET_RESTDURATION, _n(iv.rest_s, 2))]
    if iv.target_w and iv.target_w > 0:
        cmds.append(command(PM_SET_TARGETPACETIME, _n(pace_500m_s(iv.target_w) * 100, 4)))
    cmds.append(command(PM_CONFIGURE_WORKOUT, bytes([1])))
    return pm_frame(*cmds)


def workout_frames(intervals: list[Interval]) -> list[bytes]:
    """Every frame of a workout, in the order they are sent: one per interval (at most
    ``MAX_INTERVALS``), then the screen that shows the workout and waits for the first pull."""
    out = [interval_frame(i, iv) for i, iv in enumerate(intervals[:MAX_INTERVALS])]
    if out:
        out.append(pm_frame(command(PM_SET_SCREENSTATE, bytes([SCREEN_WORKOUT, SCREEN_PREPARE_TO_ROW]))))
    return out


def accepted(answer: bytes) -> bool:
    """True when the monitor's answer is a valid frame that says the frame before it was taken."""
    body = unframe(answer)
    return bool(body) and (body[0] & 0x30) == PREV_OK


def chunks(data: bytes, size: int = BLE_CHUNK) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


__all__ = ["Interval", "accepted", "checksum", "chunks", "command", "frame", "interval_frame", "pm_frame", "stuff",
           "unframe", "unstuff", "workout_frames", "MAX_INTERVALS", "PM_BYTE_ORDER"]
