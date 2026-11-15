"""The FIT file of a test: valid, read back, and with the right units and laps."""
import struct
from datetime import datetime, timezone

import pytest

from lopanalys.core.models import Session, StepRecord, TimeSample
from lopanalys.core.models import TestKind as Kind
from lopanalys.share import fit

START = datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)
STAGES = [(6.0, 0.0, 120, True), (10.0, 1.0, 180, False), (11.0, 1.0, 180, False), (12.0, 1.0, 180, False)]
PAUSE, COUNTDOWN, RAMP_DOWN = 30, 10, 3      # the belt takes three seconds to stop


def _test(exhausted_after: int | None = None, recovery: int = 60):
    """A lactate test as the runner saves it: a countdown, a warm-up, three stages with a stop of the
    belt for the blood sample after each (the belt slows down over a few seconds), cadence 171."""
    samples, records, t = [], [], 0
    for _ in range(COUNTDOWN):
        samples.append(TimeSample(float(t), 62, 0.0, 0.0, None))
        t += 1
    exh = None
    for i, (v, inc, dur, warm) in enumerate(STAGES):
        last = i == len(STAGES) - 1
        run = exhausted_after if (last and exhausted_after) else dur
        for k in range(run):
            samples.append(TimeSample(float(t), 120 + 12 * i + k // 30, v, inc, 171.0, "strap"))
            t += 1
        records.append(StepRecord(i, v, inc, float(run), hr_end=125 + 12 * i))
        if last and exhausted_after:
            exh = float(t)
            for k in range(recovery):
                samples.append(TimeSample(float(t), 170 - k // 2, 5.0, 0.0, 110.0, "strap"))
                t += 1
        elif not warm:
            for k in range(PAUSE):
                samples.append(TimeSample(float(t), 150 - k // 3, max(0.0, v - 3 * (k + 1)) if k < RAMP_DOWN else 0.0,
                                          inc, None))
                t += 1
    proto = {"stages": [{"speed_kmh": v, "incline_pct": inc, "duration_s": d, "is_warmup": w, "label": f"Steg {i}"}
                        for i, (v, inc, d, w) in enumerate(STAGES)]}
    result = {"exhaustion_t_s": exh} if exh is not None else {}
    return Session(7, 3, Kind.LACTATE_STEP, START, protocol=proto, result=result), records, samples


def _crc_arc(data: bytes) -> int:
    """CRC-16/ARC bit by bit, written apart from the table in the module."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def test_header_and_checksums():
    s, records, samples = _test()
    data = fit.encode_activity(s, records, samples)
    size, proto, profile, body, tag, head_crc = struct.unpack_from("<BBHI4sH", data)
    assert (size, tag) == (14, b".FIT") and len(data) == 14 + body + 2
    assert head_crc == _crc_arc(data[:12])
    assert struct.unpack_from("<H", data, len(data) - 2)[0] == _crc_arc(data[:-2])
    assert _crc_arc(b"123456789") == fit.crc16(b"123456789") == 0xBB3D           # the check value of CRC-16/ARC
    broken = bytearray(data)
    broken[200] ^= 0x01
    with pytest.raises(fit.FitError, match="crc"):
        fit.decode(bytes(broken))
    with pytest.raises(fit.FitError, match="crc"):
        fit.decode(data[:-10])
    with pytest.raises(fit.FitError, match="header"):
        fit.decode(b"not a fit file at all")


def test_reads_back_as_a_treadmill_run():
    s, records, samples = _test()
    m = fit.decode(fit.encode_activity(s, records, samples))
    assert list(m) == ["file_id", "file_creator", "event", "record", "lap", "session", "activity"]
    (fid,), (ses,), (act,) = m["file_id"], m["session"], m["activity"]
    assert fid["type"] == 4 and fid["manufacturer"] == 255 and fit.fit_time(fid["time_created"]).year == 2026
    assert (ses["sport"], ses["sub_sport"]) == (fit.SPORT_RUNNING, fit.SUB_SPORT_TREADMILL) == (1, 1)
    assert [(e["event"], e["event_type"]) for e in m["event"]] == [(0, 0), (0, 4)]       # timer start, stop all
    recs = m["record"]
    # one record per second from the moment the belt starts (the countdown is left out), none missing
    total = sum(d for _, _, d, _ in STAGES) + PAUSE * 3
    assert len(recs) == total
    times = [r["timestamp"] for r in recs]
    assert times == list(range(times[0], times[0] + total))
    assert fit.fit_time(times[0]) == START.replace(second=COUNTDOWN)
    first, mid = recs[0], recs[130]                       # the warm-up, then ten seconds into the first stage
    assert first["distance"] == 0 and first["speed"] == pytest.approx(6 / 3.6, abs=0.001)
    assert mid["speed"] == pytest.approx(10 / 3.6, abs=0.001) and mid["grade"] == 1.0 and mid["heart_rate"] == 132
    # running cadence in FIT is revolutions per minute: half the 171 steps, the half step as a fraction
    assert mid["cadence"] == 85 and mid["fractional_cadence"] == 0.5
    pause = recs[120 + 180 + 10]
    assert pause["speed"] == 0 and pause["cadence"] is None and pause["heart_rate"] is not None
    # the distance is the belt speed added up
    run = sum(v / 3.6 * d for v, _, d, _ in STAGES)
    slowing = sum(max(0.0, v - 3 * (k + 1)) / 3.6 for v, _, _, w in STAGES if not w for k in range(RAMP_DOWN))
    assert recs[-1]["distance"] == pytest.approx(run + slowing, abs=0.05)
    assert all(a["distance"] <= b["distance"] for a, b in zip(recs, recs[1:]))
    assert ses["total_distance"] == pytest.approx(recs[-1]["distance"], abs=0.01)
    assert ses["total_elapsed_time"] == ses["total_timer_time"] == total - 1 == act["total_timer_time"]
    assert ses["max_heart_rate"] == max(r["heart_rate"] for r in recs) and ses["avg_cadence"] == 86
    assert ses["max_speed"] == pytest.approx(12 / 3.6, abs=0.001) and ses["num_laps"] == len(m["lap"])
    assert act["num_sessions"] == 1 and act["timestamp"] == times[-1] == ses["timestamp"]


def test_one_lap_per_stage_and_rest_laps_for_the_stops():
    s, records, samples = _test()
    m = fit.decode(fit.encode_activity(s, records, samples))
    laps, ses = m["lap"], m["session"][0]
    w, a, r = fit.INTENSITY["warmup"], fit.INTENSITY["active"], fit.INTENSITY["rest"]
    assert [lp["intensity"] for lp in laps] == [w, a, r, a, r, a, r]
    assert [lp["message_index"] for lp in laps] == list(range(7))
    # the seconds where the belt slows down belong to the stage's lap, the stop is a lap of its own
    assert [lp["total_elapsed_time"] for lp in laps] == [120, 183, 27, 183, 27, 183, 26]
    assert [round(lp["avg_speed"] * 3.6, 1) for lp in laps[:2]] == [6.0, 10.0 - 0.1]
    assert laps[2]["total_distance"] == 0 and laps[2]["avg_speed"] == 0
    assert all((lp["sport"], lp["sub_sport"]) == (1, 1) for lp in laps) and laps[1]["avg_grade"] == 1.0
    # laps follow each other without holes and add up to the session
    for prev, cur in zip(laps, laps[1:]):
        assert prev["timestamp"] == cur["start_time"]
    assert laps[0]["start_time"] == ses["start_time"] and laps[-1]["timestamp"] == ses["timestamp"]
    assert sum(lp["total_elapsed_time"] for lp in laps) == ses["total_elapsed_time"]
    assert sum(lp["total_distance"] for lp in laps) == pytest.approx(ses["total_distance"], abs=0.05)
    assert laps[1]["avg_heart_rate"] == round((sum(132 + k // 30 for k in range(180)) + 3 * 150) / 183)
    # the laps lie between the records they cover
    kinds = [name for name in _order(fit.encode_activity(s, records, samples)) if name in ("record", "lap")]
    assert kinds[:122] == ["record"] * 121 + ["lap"] or kinds[:121] == ["record"] * 120 + ["lap"]
    assert kinds[-1] == "lap"


def _order(data: bytes) -> list[str]:
    """Message names in file order."""
    names, defs, pos = [], {}, 14
    end = len(data) - 2
    while pos < end:
        head = data[pos]
        pos += 1
        if head & 0x40:
            number, n = struct.unpack_from("<HB", data, pos + 2)
            defs[head & 0xF] = (number, sum(data[pos + 5 + 3 * k + 1] for k in range(n)))
            pos += 5 + 3 * n
        else:
            number, size = defs[head & 0xF]
            names.append(next(k for k, v in fit.MESSAGES.items() if v[0] == number))
            pos += size
    return names


def test_recovery_after_exhaustion_is_a_cool_down_lap():
    s, records, samples = _test(exhausted_after=95, recovery=60)
    m = fit.decode(fit.encode_activity(s, records, samples))
    laps = m["lap"]
    kinds = [lp["intensity"] for lp in laps]
    assert kinds[-2:] == [fit.INTENSITY["active"], fit.INTENSITY["cooldown"]]
    assert laps[-2]["total_elapsed_time"] == 95 and laps[-1]["total_elapsed_time"] == 59
    assert round(laps[-1]["avg_speed"] * 3.6, 1) == 5.0 and laps[-1]["avg_cadence"] == 55
    assert laps[-1]["lap_trigger"] == 7 and laps[0]["lap_trigger"] == 0


def test_a_stage_cut_in_two_by_an_operator_pause_and_a_hole_in_the_samples():
    samples = [TimeSample(float(t), 130, 10.0, 0.0, 170.0) for t in range(0, 100)]
    samples += [TimeSample(float(t), 120, None, None, None) for t in range(100, 140)]      # the operator paused
    samples += [TimeSample(float(t), 135, 10.0, 0.0, 170.0) for t in range(140, 220)]
    samples += [TimeSample(float(t), 150, 12.0, 0.0, 174.0) for t in range(220, 300)]
    samples += [TimeSample(float(t), 152, 12.0, 0.0, 174.0) for t in range(304, 400)]      # four seconds lost
    samples += [TimeSample(float(t), 155, 12.0, 0.0, 174.0) for t in range(1000, 1010)]    # cut off and continued
    records = [StepRecord(0, 10.0, 0.0, 180.0), StepRecord(1, 12.0, 0.0, 190.0)]
    s = Session(9, 1, Kind.LACTATE_STEP, START, protocol={"stages": [{}, {}]})
    m = fit.decode(fit.encode_activity(s, records, samples))
    a, r = fit.INTENSITY["active"], fit.INTENSITY["rest"]
    assert [(lp["intensity"], lp["total_elapsed_time"]) for lp in m["lap"]] == [(a, 100), (r, 40), (a, 80), (a, 780), (a, 9)]   # continued: a new lap
    times = [x["timestamp"] - m["record"][0]["timestamp"] for x in m["record"]]
    assert times == list(range(0, 400)) + list(range(1000, 1010))        # the short hole is filled, the long one not
    assert m["record"][302]["speed"] == pytest.approx(12 / 3.6, abs=0.001)
    d = {t: x["distance"] for t, x in zip(times, m["record"])}
    assert d[1000] == d[399] and d[1001] > d[1000] and d[399] > d[398]   # nothing is run inside the hole


def test_without_a_time_series_there_is_no_file_and_the_name_is_neutral():
    s, records, _ = _test()
    with pytest.raises(fit.FitError, match="no_samples"):
        fit.encode_activity(s, records, [])
    with pytest.raises(fit.FitError):
        fit.encode_activity(s, records, [TimeSample(0.0, 60, None, None, None)])
    assert fit.file_name(s) == f"test-7-{START.astimezone():%Y-%m-%d}.fit"
    # values outside the field's range are clamped, not wrapped
    odd = [TimeSample(float(t), 300, 400.0, 500.0, 900.0) for t in range(5)]
    rec = fit.decode(fit.encode_activity(s, [], odd))["record"][1]
    assert rec["heart_rate"] == 254 and rec["cadence"] == 254 and rec["grade"] == pytest.approx(327.66)


def test_an_independent_reader_agrees():
    """fitdecode (MIT) reads the file with the names from Garmin's profile. Not a dependency of the
    program: the test is skipped where the package is not installed (pip install fitdecode)."""
    fitdecode = pytest.importorskip("fitdecode")
    import io
    s, records, samples = _test(exhausted_after=95)
    data = fit.encode_activity(s, records, samples)
    msgs: dict[str, list[dict]] = {}
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage):
                msgs.setdefault(frame.name, []).append({f.name: f.value for f in frame.fields})
    own = fit.decode(data)
    assert {k: len(v) for k, v in msgs.items()} == {k: len(v) for k, v in own.items()}
    assert not [k for k in msgs if k.startswith("unknown")]
    assert not [f for rows in msgs.values() for f in rows[0] if str(f).startswith("unknown")]
    ses = msgs["session"][0]
    assert (ses["sport"], ses["sub_sport"], ses["event"], ses["trigger"]) == ("running", "treadmill", "session", "activity_end")
    assert msgs["file_id"][0]["type"] == "activity" and msgs["file_id"][0]["manufacturer"] == "development"
    assert [e["event_type"] for e in msgs["event"]] == ["start", "stop_all"] and msgs["event"][0]["event"] == "timer"
    assert [lp["intensity"] for lp in msgs["lap"]][-2:] == ["active", "cooldown"] and msgs["lap"][0]["intensity"] == "warmup"
    assert msgs["lap"][-1]["lap_trigger"] == "session_end" and msgs["lap"][0]["event"] == "lap"
    rec, mine = msgs["record"][130], own["record"][130]
    assert rec["timestamp"] == fit.fit_time(mine["timestamp"])
    for key in ("heart_rate", "cadence", "fractional_cadence", "distance", "speed", "grade"):
        assert rec[key] == pytest.approx(mine[key]), key
    assert msgs["activity"][0]["type"] == "manual" and msgs["activity"][0]["event"] == "activity"
    assert ses["total_distance"] == pytest.approx(own["session"][0]["total_distance"])
    assert ses["start_time"] == START.replace(second=COUNTDOWN) and ses["avg_heart_rate"] == own["session"][0]["avg_heart_rate"]


def _erg_test(sport: str, stages, cadence: float, pause: int = 30):
    """A step test on the trainer or the SkiErg as the runner saves it: the load (watt) in
    ``speed_kmh``, cadence in rpm or strokes/min, and a rest at 0 W for the blood sample after each stage."""
    samples, records, t = [], [], 0
    for _ in range(COUNTDOWN):
        samples.append(TimeSample(float(t), 62, 0.0, 0.0, None))
        t += 1
    for i, (watt, dur, warm) in enumerate(stages):
        for k in range(dur):
            samples.append(TimeSample(float(t), 110 + 10 * i + k // 60, float(watt), 0.0, cadence, "ergometer"))
            t += 1
        records.append(StepRecord(i, float(watt), 0.0, float(dur), hr_end=120 + 10 * i))
        if not warm:
            for k in range(pause):
                samples.append(TimeSample(float(t), 140 - k // 3, 0.0, 0.0, None))
                t += 1
    proto = {"sport": sport, "stages": [{"speed_kmh": w, "incline_pct": 0.0, "duration_s": d, "is_warmup": wu}
                                        for w, d, wu in stages]}
    return Session(11, 3, Kind.LACTATE_STEP, START, protocol=proto, sport=sport), records, samples


BIKE = [(100, 300, True), (150, 240, False), (200, 240, False)]
SKI = [(80, 240, True), (120, 180, False), (160, 180, False)]


def test_a_bike_test_is_an_indoor_ride_with_power_and_no_speed():
    s, records, samples = _erg_test("bike", BIKE, 88.0)
    m = fit.decode(fit.encode_activity(s, records, samples))
    ses, laps, recs = m["session"][0], m["lap"], m["record"]
    assert (ses["sport"], ses["sub_sport"]) == (fit.SPORT_CYCLING, fit.SUB_SPORT_INDOOR_CYCLING) == (2, 6)
    assert all((lp["sport"], lp["sub_sport"]) == (2, 6) for lp in laps)
    # pedal cadence is written as it is (not halved like the running steps), power in watt, no speed
    mid = recs[300 + 10]
    assert mid["power"] == 150 and mid["cadence"] == 88 and mid["fractional_cadence"] == 0
    assert mid["speed"] is None and mid["distance"] is None and mid["grade"] is None
    assert recs[0]["power"] == 100 and fit.fit_time(recs[0]["timestamp"]) == START.replace(second=COUNTDOWN)
    w, a, r = fit.INTENSITY["warmup"], fit.INTENSITY["active"], fit.INTENSITY["rest"]
    assert [lp["intensity"] for lp in laps] == [w, a, r, a, r]
    assert [lp["total_elapsed_time"] for lp in laps] == [300, 240, 30, 240, 29]
    assert [lp["avg_power"] for lp in laps] == [100, 150, 0, 200, 0] and laps[3]["max_power"] == 200
    assert laps[1]["avg_cadence"] == 88 and laps[1]["total_cycles"] == round(88 * 240 / 60)
    assert ses["max_power"] == 200 and ses["avg_power"] == round(sum(x["power"] for x in recs[:-1]) / (len(recs) - 1))
    assert ses["total_distance"] is None and ses["avg_speed"] is None and laps[1]["total_ascent"] is None


def test_a_skierg_test_is_fitness_equipment_indoor_skiing_with_the_pm5_distance():
    s, records, samples = _erg_test("skierg", SKI, 42.0)
    m = fit.decode(fit.encode_activity(s, records, samples))
    ses, laps, recs = m["session"][0], m["lap"], m["record"]
    assert (ses["sport"], ses["sub_sport"]) == (fit.SPORT_FITNESS_EQUIPMENT, fit.SUB_SPORT_INDOOR_SKIING) == (4, 25)
    mid = recs[240 + 10]
    assert mid["power"] == 120 and mid["cadence"] == 42
    # speed from the power as the PM5 counts it, W = 2.80 v^3: 120 W is 2:22.9 per 500 m
    v = (120 / 2.80) ** (1 / 3)
    assert mid["speed"] == pytest.approx(v, abs=0.001) and 500 / v == pytest.approx(142.9, abs=0.1)
    dist = sum(((w / 2.80) ** (1 / 3)) * d for w, d, _ in SKI)
    assert ses["total_distance"] == pytest.approx(dist, abs=1.0)
    assert [lp["avg_power"] for lp in laps] == [80, 120, 0, 160, 0] and laps[2]["total_distance"] == 0
    assert laps[1]["total_cycles"] == 42 * 3 and ses["avg_cadence"] == 42 and mid["grade"] is None


def test_every_sport_reads_back_with_the_full_reader():
    """share.fit_read reads the whole binary layout (what a watch writes): it agrees with the file."""
    from lopanalys.share import fit_read
    for sport, stages, cad in (("bike", BIKE, 90.0), ("skierg", SKI, 40.0)):
        s, records, samples = _erg_test(sport, stages, cad)
        data = fit.encode_activity(s, records, samples)
        msgs = fit_read.read(data)
        assert len(msgs["record"]) == len(fit.decode(data)["record"]) and len(msgs["lap"]) == 5
        assert msgs["session"][0]["sport"] == fit.SPORTS[s.sport].sport
