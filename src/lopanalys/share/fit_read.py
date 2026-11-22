"""Reads a FIT activity from a Garmin watch: every ``record`` with the running dynamics of an HRM-Pro+.

``share.fit.decode`` reads only what this program writes. A watch file also has compressed timestamp
headers, developer fields (Connect IQ apps, Stryd), big-endian messages, arrays and strings, so this
reader handles the whole binary layout and keeps only the messages it knows: ``record`` (1 Hz),
``lap``, ``session`` and ``file_id``. Both checksums are checked when ``strict``.

Running dynamics in the record (FIT profile, message 20), scaled to the units shown here:

=====================  =====  =====================================================
field                  unit   note
=====================  =====  =====================================================
cadence_spm            1/min  ``cadence`` (strides/min) x 2 plus ``fractional_cadence``
stance_time_ms         ms     ground contact time
vertical_oscillation   cm     the chest strap's (or pod's) rise and fall
vertical_ratio         %      vertical oscillation / step length
stance_balance         %      left foot's share of the contact time
step_length_m          m
speed_kmh              km/h   ``enhanced_speed`` before ``speed``
=====================  =====  =====================================================

Pure Python, no dependencies, no Qt.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lopanalys.share.fit import FIT_EPOCH, FitError, crc16

# base type number (low 5 bits) -> (struct char, size, invalid raw value or None for "all bits set")
_BASE: dict[int, tuple[str, int, Any]] = {
    0: ("B", 1, 0xFF), 1: ("b", 1, 0x7F), 2: ("B", 1, 0xFF), 3: ("h", 2, 0x7FFF), 4: ("H", 2, 0xFFFF),
    5: ("i", 4, 0x7FFFFFFF), 6: ("I", 4, 0xFFFFFFFF), 7: ("s", 1, None), 8: ("f", 4, "nan"),
    9: ("d", 8, "nan"), 10: ("B", 1, 0), 11: ("H", 2, 0), 12: ("I", 4, 0), 13: ("B", 1, 0xFF),
    14: ("q", 8, 0x7FFFFFFFFFFFFFFF), 15: ("Q", 8, 0xFFFFFFFFFFFFFFFF), 16: ("Q", 8, 0),
}

# global message number -> (name, {field number: (field name, scale, offset)})
_KNOWN: dict[int, tuple[str, dict[int, tuple[str, float, float]]]] = {
    0: ("file_id", {0: ("type", 1, 0), 1: ("manufacturer", 1, 0), 2: ("product", 1, 0),
                    3: ("serial_number", 1, 0), 4: ("time_created", 1, 0)}),
    18: ("session", {253: ("timestamp", 1, 0), 2: ("start_time", 1, 0), 5: ("sport", 1, 0),
                     6: ("sub_sport", 1, 0), 7: ("total_elapsed_time", 1000, 0)}),
    19: ("lap", {253: ("timestamp", 1, 0), 2: ("start_time", 1, 0), 7: ("total_elapsed_time", 1000, 0),
                 9: ("total_distance", 100, 0), 13: ("avg_speed", 1000, 0), 110: ("enhanced_avg_speed", 1000, 0),
                 254: ("message_index", 1, 0)}),
    20: ("record", {253: ("timestamp", 1, 0), 3: ("heart_rate", 1, 0), 4: ("cadence", 1, 0),
                    5: ("distance", 100, 0), 6: ("speed", 1000, 0), 73: ("enhanced_speed", 1000, 0),
                    39: ("vertical_oscillation", 10, 0), 40: ("stance_time_percent", 100, 0),
                    41: ("stance_time", 10, 0), 53: ("fractional_cadence", 128, 0),
                    83: ("vertical_ratio", 100, 0), 84: ("stance_time_balance", 100, 0),
                    85: ("step_length", 10, 0)}),
}


def _value(base: int, raw_bytes: bytes, big: bool) -> Any:
    num = base & 0x1F
    if num not in _BASE:
        return None
    ch, size, invalid = _BASE[num]
    if ch == "s":
        return raw_bytes.split(b"\0", 1)[0].decode("utf-8", "replace") or None
    if len(raw_bytes) < size:
        return None
    v = struct.unpack((">" if big else "<") + ch, raw_bytes[:size])[0]   # arrays: the first element
    if invalid == "nan":
        return None if v != v or raw_bytes[:size] == b"\xff" * size else v
    return None if v == invalid else v


def read(data: bytes, strict: bool = True) -> dict[str, list[dict[str, Any]]]:
    """``{message name: [fields]}`` for the known messages, values scaled. Raises FitError."""
    out: dict[str, list[dict[str, Any]]] = {}
    pos = 0
    if len(data) < 12:
        raise FitError("header")
    while pos + 12 <= len(data):                     # chained files: one header after the other
        hsize = data[pos]
        if hsize not in (12, 14) or data[pos + 8:pos + 12] != b".FIT":
            if pos == 0:
                raise FitError("header")
            break
        size = struct.unpack_from("<I", data, pos + 4)[0]
        end = pos + hsize + size
        if end + 2 > len(data):
            raise FitError("data")
        if strict and crc16(data[pos:end]) != struct.unpack_from("<H", data, end)[0]:
            raise FitError("crc")
        _read_body(data, pos + hsize, end, out)
        pos = end + 2
    return out


def _read_body(data: bytes, pos: int, end: int, out: dict[str, list[dict[str, Any]]]) -> None:
    defs: dict[int, tuple[int, bool, list[tuple[int, int, int]], int]] = {}
    last_ts: int | None = None
    try:
        while pos < end:
            head = data[pos]
            pos += 1
            ts_compressed = None
            if head & 0x80:                           # compressed timestamp header
                local = (head >> 5) & 0x03
                offset = head & 0x1F
                if last_ts is not None:
                    ts_compressed = (last_ts & ~0x1F) + offset
                    if offset < (last_ts & 0x1F):
                        ts_compressed += 0x20
                    last_ts = ts_compressed
            elif head & 0x40:                         # definition message
                local = head & 0x0F
                arch = data[pos + 1]
                big = arch == 1
                number = struct.unpack_from(">H" if big else "<H", data, pos + 2)[0]
                n = data[pos + 4]
                pos += 5
                fields = [tuple(data[pos + 3 * k:pos + 3 * k + 3]) for k in range(n)]
                pos += 3 * n
                dev_size = 0
                if head & 0x20:                       # developer fields: only their sizes matter
                    nd = data[pos]
                    pos += 1
                    dev_size = sum(data[pos + 3 * k + 1] for k in range(nd))
                    pos += 3 * nd
                defs[local] = (number, big, fields, dev_size)   # type: ignore[assignment]
                continue
            else:
                local = head & 0x0F
            number, big, fields, dev_size = defs[local]
            known = _KNOWN.get(number)
            row: dict[str, Any] = {}
            for num, fsize, base in fields:
                raw = data[pos:pos + fsize]
                pos += fsize
                if num == 253 and known is None:      # keep the clock running for unknown messages
                    v = _value(base, raw, big)
                    if v is not None:
                        last_ts = int(v)
                if known is None or num not in known[1]:
                    continue
                v = _value(base, raw, big)
                name, scale, off = known[1][num]
                if num == 253 and v is not None:
                    last_ts = int(v)
                row[name] = None if v is None else (v / scale - off if (scale != 1 or off) else v)
            pos += dev_size
            if known is None:
                continue
            if ts_compressed is not None and row.get("timestamp") is None:
                row["timestamp"] = ts_compressed
            out.setdefault(known[0], []).append(row)
    except (KeyError, IndexError, ValueError, TypeError, struct.error) as e:
        raise FitError("data") from e


@dataclass
class DynSample:
    """One second of the watch's record, in the program's units (None where the field is missing)."""
    t_unix: float
    heart_rate: int | None
    cadence_spm: float | None
    stance_time_ms: float | None
    vertical_oscillation_cm: float | None
    vertical_ratio_pct: float | None
    stance_balance_pct: float | None
    step_length_m: float | None
    speed_kmh: float | None


def unix(fit_ts: float) -> float:
    return float(fit_ts) + FIT_EPOCH


def running_dynamics(data: bytes, strict: bool = True) -> list[DynSample]:
    """Every ``record`` with a timestamp, oldest first."""
    out: list[DynSample] = []
    for r in read(data, strict).get("record", []):
        ts = r.get("timestamp")
        if ts is None:
            continue
        cad = r.get("cadence")
        spm = None if cad is None else 2.0 * (cad + (r.get("fractional_cadence") or 0.0))
        spd = r.get("enhanced_speed")
        if spd is None:
            spd = r.get("speed")
        vo = r.get("vertical_oscillation")
        sl = r.get("step_length")
        out.append(DynSample(
            t_unix=unix(ts), heart_rate=r.get("heart_rate"), cadence_spm=spm or None,
            stance_time_ms=r.get("stance_time") or None,
            vertical_oscillation_cm=None if not vo else vo / 10.0,
            vertical_ratio_pct=r.get("vertical_ratio") or None,
            stance_balance_pct=r.get("stance_time_balance") or None,
            step_length_m=None if not sl else sl / 1000.0,
            speed_kmh=None if spd is None else spd * 3.6))
    out.sort(key=lambda s: s.t_unix)
    return out


def read_file(path: str | Path, strict: bool = True) -> list[DynSample]:
    return running_dynamics(Path(path).read_bytes(), strict)


def utc(t_unix: float) -> datetime:
    return datetime.fromtimestamp(t_unix, tz=timezone.utc)
