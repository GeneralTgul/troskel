"""Compares the gait analysis of a film with the watch's HRM-Pro+ running dynamics, at the film's rate and lower ones.

The check of the running technique, the part of the analysis most in doubt:
the same run is filmed in 120 fps and recorded on the watch with the HRM-Pro+ strap. This tool

1. analyses the film at its own rate and as a camera with a lower rate would have seen it (every 2nd,
   4th and 5th frame of a 120 fps film = 60, 30 and 24 fps), from the poses ``tools/granska_analys.py``
   cached (``poses.pkl``), so the pose estimator runs once;
2. cuts the run into windows (default 30 s) and takes the median per window of each value;
3. compares per window with the watch: cadence, ground contact time and vertical oscillation (what the
   strap measures), and every value at the lower rates with the same value at the full rate;
4. prints, per value and rate, the bias, the spread and the mean error, whether it is within the
   tolerance, and the lowest tested rate that holds: the requirements for ``pose/reliability.py``.

    python tools/jamfor_garmin.py POSES.pkl --height 180 [--view side_left] [--fit RUN.fit]
           [--video FILM.MP4] [--offset UNIX] [--window 30] [--every 1,2,4,5] [--out DIR]

Clock: the film's time 0 is matched to the watch by the cadence and contact time over the run (three
speeds give a clear match), starting from the film's creation time (``--video``, the GoPro writes its
local time) when it is given. ``--offset`` sets the Unix time of the film's first frame by hand.

Without ``--fit`` only the comparison between rates is made. Video, poses and FIT files are a person's
health data: they never go into the repository. Run with a memory cap on a shared machine:
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 python tools/jamfor_garmin.py …
"""
from __future__ import annotations

import argparse
import csv
import os
import pickle
import struct
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LOPANALYS_NO_DOWNLOAD", "1")

# what counts as agreement, per window median (units of the value). Cadence and contact time as in
# docs/PROV.md (Steghändelser); the angles 3 degrees, about the width of a severity band.
TOLERANCE: dict[str, float] = {
    "cadence_spm": 2.0, "ground_contact_ms": 15.0, "vertical_oscillation_cm": 1.0, "overstride_cm": 3.0,
    "shin_angle_deg": 3.0, "knee_flex_contact_deg": 3.0, "knee_flex_midstance_deg": 3.0,
    "trunk_lean_deg": 3.0, "hip_extension_toe_off_deg": 3.0, "foot_angle_deg": 3.0,
}
STEP_VALUES = [k for k in TOLERANCE if k != "cadence_spm"]
# film value -> DynSample field
WATCH = {"cadence_spm": "cadence_spm", "ground_contact_ms": "stance_time_ms",
         "vertical_oscillation_cm": "vertical_oscillation_cm"}
MIN_STEPS = 8          # a window with fewer steps is left out
MP4_EPOCH = 2082844800  # 1904-01-01 -> 1970-01-01


# ---------------------------------------------------------------------------------------- the film

@dataclass
class RateRun:
    """The analysis at one rate: the steps, the landing times and the achieved rate."""
    every: int
    fps: float
    steps: list[Any]
    contacts: list[float]


def analyse_rates(poses: Sequence[Any], times: Sequence[float], height: float, view: str,
                  speed: float | None, everies: Sequence[int]) -> list[RateRun]:
    from lopanalys.pose.offline import analyze_core
    from lopanalys.pose.reliability import decimate
    out = []
    for n in everies:
        ps = decimate(poses, n) if n > 1 else list(poses)
        ts = list(times[::n])
        core = analyze_core(ps, height, view, speed, times=ts, frames_total=len(ps))
        ev = core.events
        out.append(RateRun(n, float(core.analysis.fps or 0.0), core.analysis.steps,
                           sorted(c.t_ic for c in ev.contacts) if ev is not None else []))
    return out


def window_values(run: RateRun, t0: float, t1: float) -> dict[str, float | None]:
    """Medians of the steps with their landing in [t0, t1), and the cadence from the landings."""
    st = [s for s in run.steps if t0 <= s.t_contact < t1]
    out: dict[str, float | None] = {"steps": float(len(st))}
    ic = [t for t in run.contacts if t0 <= t < t1]
    out["cadence_spm"] = 60.0 * (len(ic) - 1) / (ic[-1] - ic[0]) if len(ic) >= 4 and ic[-1] > ic[0] else None
    for k in STEP_VALUES:
        vals = [getattr(s, k) for s in st if getattr(s, k, None) is not None]
        out[k] = float(np.median(vals)) if len(vals) >= MIN_STEPS // 2 else None
    return out


def mp4_creation_time(path: Path) -> float | None:
    """``mvhd`` creation time of an MP4 as seconds since 1970 *as written* (a GoPro writes its local time)."""
    def atoms(f, start: int, end: int):
        pos = start
        while pos + 8 <= end:
            f.seek(pos)
            head = f.read(8)
            if len(head) < 8:
                return
            size, kind = struct.unpack(">I4s", head)
            hdr = 8
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
                hdr = 16
            elif size == 0:
                size = end - pos
            if size < hdr:
                return
            yield kind, pos + hdr, pos + size
            pos += size
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            total = f.tell()
            for kind, a, b in atoms(f, 0, total):
                if kind != b"moov":
                    continue
                for k2, a2, _b2 in atoms(f, a, b):
                    if k2 == b"mvhd":
                        f.seek(a2)
                        ver = f.read(4)[0]
                        raw = struct.unpack(">Q", f.read(8))[0] if ver == 1 else struct.unpack(">I", f.read(4))[0]
                        return float(raw - MP4_EPOCH) if raw else None
    except (OSError, struct.error, IndexError):
        return None
    return None


def local_as_unix(naive_unix: float, tz: str = "Europe/Stockholm") -> float:
    """A wall-clock time stored as if it were UTC (the GoPro's) -> the real Unix time in ``tz``."""
    from zoneinfo import ZoneInfo
    wall = datetime.fromtimestamp(naive_unix, timezone.utc).replace(tzinfo=ZoneInfo(tz))
    return wall.timestamp()


# ---------------------------------------------------------------------------------------- the watch

def watch_per_second(samples: Sequence[Any], attr: str, half: float = 5.0) -> tuple[float, np.ndarray]:
    """``attr`` on a 1 Hz grid from the first sample, each second the median of the samples within
    +-half s (as the film's values, which are medians of the steps around each second)."""
    ts = np.array([s.t_unix for s in samples])
    vs = np.array([np.nan if getattr(s, attr) is None else float(getattr(s, attr)) for s in samples])
    t0 = float(np.floor(ts[0]))
    n = int(np.ceil(ts[-1] - t0)) + 1
    out = np.full(n, np.nan)
    lo = hi = 0
    for i in range(n):
        while lo < len(ts) and ts[lo] < t0 + i - half:
            lo += 1
        while hi < len(ts) and ts[hi] <= t0 + i + half:
            hi += 1
        w = vs[lo:hi]
        w = w[~np.isnan(w)]
        if len(w) >= 3:
            out[i] = float(np.median(w))
    return t0, out


def film_per_second(run: RateRun, n: int, half: float = 5.0) -> dict[str, np.ndarray]:
    cad, gct = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        v = window_values(run, i - half, i + half)
        cad[i] = np.nan if v["cadence_spm"] is None else v["cadence_spm"]
        gct[i] = np.nan if v["ground_contact_ms"] is None else v["ground_contact_ms"]
    return {"cadence_spm": cad, "stance_time_ms": gct}


def _z(x: np.ndarray) -> np.ndarray:
    m = ~np.isnan(x)
    if m.sum() < 5 or np.nanstd(x) == 0:
        return np.full_like(x, np.nan)
    return (x - np.nanmean(x)) / np.nanstd(x)


@dataclass
class Alignment:
    offset: float                     # Unix time of the film's first frame
    score: float | None               # mean correlation of cadence and contact time at the best lag
    method: str
    notes: list[str] = field(default_factory=list)


def align(run: RateRun, samples: Sequence[Any], duration: float, guess: float | None = None,
          search_s: float | None = None) -> Alignment:
    """The lag where the film's cadence and contact time follow the watch's best (1 s steps).

    With ``guess`` the search covers +-``search_s`` (default 15 min: the camera's clock may be off);
    without it the whole watch file."""
    n = int(duration) + 1
    film = {k: _z(v) for k, v in film_per_second(run, n).items()}
    t_first, t_last = samples[0].t_unix, samples[-1].t_unix
    if guess is not None:
        span = search_s if search_s is not None else 900.0
        lo, hi = max(t_first - duration, guess - span), min(t_last, guess + span)
    else:
        lo, hi = t_first - 0.5 * duration, t_last - 0.5 * duration
    grid: dict[str, tuple[float, np.ndarray]] = {k: watch_per_second(samples, k) for k in film}
    best: tuple[float, float] | None = None
    for off in np.arange(np.floor(lo), np.ceil(hi) + 1, 1.0):
        scores = []
        for k, fz in film.items():
            g0, gv = grid[k]
            i0 = int(round(off - g0))
            w = np.full(n, np.nan)
            a, b = max(0, i0), min(len(gv), i0 + n)
            if b > a:
                w[a - i0:b - i0] = gv[a:b]
            wz = _z(w)
            m = ~np.isnan(fz) & ~np.isnan(wz)
            if m.sum() >= max(30, n // 3):
                scores.append(float(np.mean(fz[m] * wz[m])))
        if scores:
            sc = float(np.mean(scores))
            if best is None or sc > best[1]:
                best = (float(off), sc)
    if best is None:
        if guess is None:
            raise SystemExit("the film and the watch file do not overlap: give --offset")
        return Alignment(guess, None, "creation time", ["no overlap to match on: the film's creation time is used"])
    al = Alignment(best[0], round(best[1], 2), "cadence and contact time")
    if best[1] < 0.5:
        al.notes.append(f"weak match (correlation {best[1]:.2f}): check the offset, or give --offset")
    if guess is not None:
        al.notes.append(f"{best[0] - guess:+.0f} s from the film's creation time")
    return al


# ---------------------------------------------------------------------------------------- comparison

@dataclass
class Stat:
    metric: str
    against: str          # "watch" | "full rate"
    fps: float
    n: int
    bias: float | None
    sd: float | None
    mae: float | None
    ok: bool | None


def stat(metric: str, against: str, fps: float, diffs: list[float]) -> Stat:
    if len(diffs) < 2:
        return Stat(metric, against, fps, len(diffs), None, None, None, None)
    d = np.array(diffs)
    mae = float(np.mean(np.abs(d)))
    return Stat(metric, against, fps, len(d), float(d.mean()), float(d.std(ddof=1)), mae,
                mae <= TOLERANCE.get(metric, float("inf")))


def compare(runs: list[RateRun], samples: Sequence[Any] | None, offset: float | None, duration: float,
            window: float) -> tuple[list[dict[str, Any]], list[Stat]]:
    """Window rows (film at each rate, and the watch) and the statistics per value and rate."""
    rows: list[dict[str, Any]] = []
    edges = np.arange(0.0, duration - 0.5 * window + 1e-9, window)
    full = runs[0]
    for t0 in edges:
        t1 = t0 + window
        row: dict[str, Any] = {"t0": round(float(t0), 1), "t1": round(float(t1), 1)}
        for r in runs:
            v = window_values(r, t0, t1)
            if v["steps"] < MIN_STEPS:
                continue
            for k, x in v.items():
                row[f"{k}@{r.fps:.0f}"] = x
        if samples is not None and offset is not None:
            sel = [s for s in samples if offset + t0 <= s.t_unix < offset + t1]
            for k, attr in WATCH.items():
                vals = [getattr(s, attr) for s in sel if getattr(s, attr) is not None]
                row[f"{k}@watch"] = float(np.median(vals)) if len(vals) >= 0.5 * window else None
            hr = [s.heart_rate for s in sel if s.heart_rate]
            spd = [s.speed_kmh for s in sel if s.speed_kmh]
            row["heart_rate@watch"] = float(np.median(hr)) if hr else None
            row["speed_kmh@watch"] = round(float(np.median(spd)), 1) if spd else None
        rows.append(row)
    stats: list[Stat] = []
    for r in runs:
        tag = f"{r.fps:.0f}"
        if samples is not None:
            for k in WATCH:
                d = [row[f"{k}@{tag}"] - row[f"{k}@watch"] for row in rows
                     if row.get(f"{k}@{tag}") is not None and row.get(f"{k}@watch") is not None]
                stats.append(stat(k, "watch", r.fps, d))
        if r is full:
            continue
        ftag = f"{full.fps:.0f}"
        for k in TOLERANCE:
            d = [row[f"{k}@{tag}"] - row[f"{k}@{ftag}"] for row in rows
                 if row.get(f"{k}@{tag}") is not None and row.get(f"{k}@{ftag}") is not None]
            stats.append(stat(k, "full rate", r.fps, d))
    return rows, stats


def requirements(runs: list[RateRun], stats: list[Stat]) -> dict[str, float | None]:
    """Per value, the lowest tested rate where every check that could be made holds (None: not even the
    full rate holds against the watch)."""
    out: dict[str, float | None] = {}
    for k in TOLERANCE:
        holds = []
        for r in runs:
            mine = [s for s in stats if s.metric == k and abs(s.fps - r.fps) < 1e-6 and s.ok is not None]
            if mine and all(s.ok for s in mine):
                holds.append(r.fps)
            elif not mine and r is runs[0]:
                holds.append(r.fps)        # nothing to check the full rate against: taken as it is
            else:
                break                      # a lower rate is only counted while every higher one holds
        out[k] = min(holds) if holds else None
    return out


def _fmt(x: float | None, d: int = 1) -> str:
    return "–" if x is None else f"{x:.{d}f}"


def report(runs: list[RateRun], stats: list[Stat], req: dict[str, float | None], al: Alignment | None) -> str:
    lines = []
    if al is not None:
        lines.append(f"clock: film starts {datetime.fromtimestamp(al.offset).isoformat(timespec='seconds')} "
                     f"({al.method}, match {_fmt(al.score, 2)})")
        lines += [f"  {n}" for n in al.notes]
    lines.append("rates: " + ", ".join(f"every {r.every} = {r.fps:.1f} fps, {len(r.steps)} steps" for r in runs))
    lines.append("")
    lines.append(f"{'value':28s} {'against':10s} {'fps':>5s} {'n':>3s} {'bias':>7s} {'sd':>7s} {'mae':>7s} {'tol':>5s}  ok")
    for s in stats:
        lines.append(f"{s.metric:28s} {s.against:10s} {s.fps:5.0f} {s.n:3d} {_fmt(s.bias):>7s} {_fmt(s.sd):>7s} "
                     f"{_fmt(s.mae):>7s} {TOLERANCE.get(s.metric, 0):5.1f}  "
                     f"{'–' if s.ok is None else 'ja' if s.ok else 'NEJ'}")
    lines.append("")
    lines.append("lowest tested rate that holds (the requirement for pose/reliability.py):")
    watched = {s.metric for s in stats if s.against == "watch" and s.ok is not None}
    for k, v in req.items():
        note = "" if v is None or k in watched else "  (the full rate itself is not checked: only the rates below it)"
        lines.append(f"  {k:28s} {'does not hold' if v is None else f'{v:.0f} fps'}{note}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("poses", type=Path, help="poses.pkl from tools/granska_analys.py")
    ap.add_argument("--height", type=float, required=True)
    ap.add_argument("--view", default="side_left")
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--fit", type=Path, default=None, help="the watch's FIT file of the same run")
    ap.add_argument("--video", type=Path, default=None, help="the film, for its creation time")
    ap.add_argument("--offset", type=float, default=None, help="Unix time of the film's first frame")
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--every", default="1,2,4,5", help="the rates as every N-th frame")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    with a.poses.open("rb") as f:
        d = pickle.load(f)
    poses, times = d["poses"], d["times"]
    everies = sorted({int(x) for x in a.every.split(",") if x.strip()})
    if everies[0] != 1:
        everies = [1] + everies
    runs = analyse_rates(poses, times, a.height, a.view, a.speed, everies)
    duration = float(times[-1] - times[0])
    samples, al = None, None
    if a.fit is not None:
        from lopanalys.share.fit_read import read_file
        samples = read_file(a.fit, strict=False)
        if not samples:
            raise SystemExit("no records in the FIT file")
        if a.offset is not None:
            al = Alignment(a.offset, None, "--offset")
        else:
            created = mp4_creation_time(a.video) if a.video else None
            guess = local_as_unix(created) if created else None
            al = align(runs[0], samples, duration, guess)
    rows, stats = compare(runs, samples, al.offset if al else None, duration, a.window)
    req = requirements(runs, stats)
    text = report(runs, stats, req, al)
    print(text, end="")
    out = a.out or a.poses.parent
    out.mkdir(parents=True, exist_ok=True)
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("t0", "t1"), k))
    with (out / "jamforelse.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    (out / "jamforelse.txt").write_text(text, encoding="utf-8")
    print(f"written to {out / 'jamforelse.txt'} and jamforelse.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
