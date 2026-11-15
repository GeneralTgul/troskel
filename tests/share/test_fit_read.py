"""Reading a watch's FIT file: running dynamics, compressed timestamps, developer fields, big-endian."""
import struct

import pytest

from lopanalys.share import fit, fit_read

T0 = 1_100_000_000          # FIT time (seconds since 1989-12-31)


def _definition(local: int, number: int, fields: list[tuple[int, int, int]], big: bool = False,
                dev: list[tuple[int, int, int]] | None = None) -> bytes:
    head = 0x40 | local | (0x20 if dev else 0)
    out = struct.pack("<BBB", head, 0, 1 if big else 0) + struct.pack(">H" if big else "<H", number)
    out += bytes([len(fields)]) + b"".join(bytes(f) for f in fields)
    if dev:
        out += bytes([len(dev)]) + b"".join(bytes(f) for f in dev)
    return out


def _file(body: bytes) -> bytes:
    hdr = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    hdr += struct.pack("<H", fit.crc16(hdr))
    data = hdr + body
    return data + struct.pack("<H", fit.crc16(data))


# record: timestamp, heart_rate, cadence, fractional_cadence, enhanced_speed, vertical_oscillation,
# stance_time, step_length, vertical_ratio, stance_time_balance
REC = [(253, 4, 0x86), (3, 1, 0x02), (4, 1, 0x02), (53, 1, 0x02), (73, 4, 0x86), (39, 2, 0x84),
       (41, 2, 0x84), (85, 2, 0x84), (83, 2, 0x84), (84, 2, 0x84)]


def _record(ts: int, hr=150, cad=85, frac=64, speed_mms=3333, vo=852, st=2581, sl=11760, vr=725, bal=5012,
            big: bool = False, local: int = 0) -> bytes:
    e = ">" if big else "<"
    return bytes([local]) + struct.pack(e + "IBBBIHHHHH", ts, hr, cad, frac, speed_mms, vo, st, sl, vr, bal)


def test_running_dynamics_in_the_programs_units():
    body = _definition(0, 20, REC) + _record(T0) + _record(T0 + 1, st=0xFFFF)
    s = fit_read.running_dynamics(_file(body))
    assert len(s) == 2
    a = s[0]
    assert a.t_unix == T0 + fit.FIT_EPOCH
    assert a.heart_rate == 150
    assert a.cadence_spm == pytest.approx(171.0)            # 85.5 strides/min
    assert a.stance_time_ms == pytest.approx(258.1)
    assert a.vertical_oscillation_cm == pytest.approx(8.52)
    assert a.step_length_m == pytest.approx(1.176)
    assert a.vertical_ratio_pct == pytest.approx(7.25)
    assert a.stance_balance_pct == pytest.approx(50.12)
    assert a.speed_kmh == pytest.approx(12.0, abs=0.01)
    assert s[1].stance_time_ms is None                      # invalid value -> missing


def test_compressed_timestamps_developer_fields_and_big_endian():
    # local 1: a record without its own timestamp, read through compressed headers; a developer field
    # of 3 bytes follows every message (a Connect IQ app's), and an unknown message in between.
    no_ts = [f for f in REC if f[0] != 253]
    body = _definition(0, 20, REC) + _record(T0 + 30)          # T0+30: low 5 bits = 30 when T0 % 32 == 0
    body += _definition(1, 20, no_ts, dev=[(0, 3, 0)])
    rec_no_ts = _record(0)[5:]                               # drop the local byte and the timestamp
    for off in (31, 1):                                      # 31, then wraps past 32 -> +3 s
        body += bytes([0x80 | (1 << 5) | off]) + rec_no_ts + b"\x01\x02\x03"
    body += _definition(2, 9999, [(0, 2, 0x84)]) + bytes([2]) + b"\x00\x00"
    body += _definition(3, 20, REC, big=True) + _record(T0 + 40, big=True, local=3)
    assert T0 % 32 == 0
    s = fit_read.running_dynamics(_file(body))
    assert [x.t_unix - fit.FIT_EPOCH for x in s] == [T0 + 30, T0 + 31, T0 + 33, T0 + 40]
    assert all(x.cadence_spm == pytest.approx(171.0) for x in s)
    assert s[-1].stance_time_ms == pytest.approx(258.1)     # big-endian read right


def test_bad_files_are_refused():
    with pytest.raises(fit.FitError):
        fit_read.read(b"not a fit file at all")
    good = _file(_definition(0, 20, REC) + _record(T0))
    bad = good[:20] + bytes([good[20] ^ 0xFF]) + good[21:]
    with pytest.raises(fit.FitError):
        fit_read.read(bad)
    assert fit_read.read(bad, strict=False)                 # without the checksum it still reads


def test_reads_the_programs_own_file():
    from .test_fit import _test
    session, records, samples = _test()
    data = fit.encode_activity(session, records, samples)
    s = fit_read.running_dynamics(data)
    assert len(s) == len(fit.decode(data)["record"])
    assert any(x.cadence_spm == pytest.approx(171.0) for x in s)
