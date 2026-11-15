"""Reviews a running, bike fit or SkiErg analysis frame by frame: every detected event with the frames around it, and every value per cycle.

The measurement must be trustworthy before anything else matters. This tool shows
where the analysis puts each initial contact, so a human can see whether it hits the right frame.

    python tools/granska_analys.py VIDEO --height 180 --view side_left [--model pose_landmarker_full.task]
           [--out DIR] [--contacts 6] [--around 4] [--seconds 60] [--every 4]

``--every N`` analyses every N-th frame only: a 120 fps film as a 30 fps camera would have given it
(the downsampling test). The poses are estimated once at the full rate; the results go to
``DIR/every<N>/`` so the sheets at both rates can be laid side by side. ``tools/jamfor_garmin.py`` does
the same for every rate at once and compares with the watch.

Pass 1 runs the pose estimator over the clip once and caches the poses next to the output (poses.pkl), so
later runs (after a change in pose/) only redo the analysis. Pass 2 writes:
  steps.csv            one row per step: side, times, frame numbers and every measured value
  summary.txt          the summary, the findings, the warnings and the detected facing
  contacts_<side>.png  per foot, N landings from the middle of the run: frames IC-around..IC+around, the
                       detected IC frame framed, heel/toe/ankle/knee of that foot drawn as dots and the other
                       foot's heel/toe as rings, and the foot height against the contact threshold
  toeoffs_<side>.png   the same around each toe-off

Bike fit and double poling (``--sport bike`` / ``--sport ski``) are reviewed the same way:
  cycles.csv           one row per pedal revolution or stroke with every measured value
  summary.txt          the summary, every range with its basis and measurement error, the findings, the
                       advice for the bike, the warnings and how far the measurement can be trusted
  bottoms.png, tops.png        bike: N revolutions, the frames around the detected bottom and top of the stroke,
                               with hip, knee and ankle drawn and the knee flexion in each frame
  starts.png, finishes.png     ski: N strokes, the frames around the start and the finish of the drive, with
                               shoulder, elbow, wrist and hip drawn and the trunk's lean in each frame
``--synthetic`` makes a film of the synthetic cyclist or skier first (no camera, no pose model), to try the tool.

Video and poses never go into the repository: they are a person's health data. Run with a memory cap on a
shared machine: systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 python tools/granska_analys.py …
"""
from __future__ import annotations

import argparse
import csv
import os
import pickle
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LOPANALYS_NO_DOWNLOAD", "1")

THUMB_H = 280


def estimate_poses(video: Path, model: Path | None, cache: Path, seconds: float | None) -> tuple[list, list]:
    if cache.is_file():
        with cache.open("rb") as f:
            d = pickle.load(f)
        print(f"poses from cache: {len(d['poses'])} frames")
        return d["poses"], d["times"]
    import cv2

    from lopanalys.pose.estimator import make_estimator
    from lopanalys.pose.offline import recorded_times
    if model is not None:
        os.environ["LOPANALYS_POSE_MODEL"] = str(model)
    est = make_estimator("full", "video")
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    stamps = recorded_times(video, total)
    poses, times = [], []
    i = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        tt = stamps[i] if stamps is not None and i < len(stamps) else i / fps
        if seconds and tt - (times[0] if times else tt) > seconds:
            break
        poses.append(est.process(img, tt - (stamps[0] if stamps else 0.0), i))
        times.append(tt - (stamps[0] if stamps else 0.0))
        i += 1
        if i % 300 == 0:
            print(f"  {i}/{total} frames")
    cap.release()
    est.close()
    with cache.open("wb") as f:
        pickle.dump({"poses": poses, "times": times}, f)
    print(f"poses: {len(poses)} frames, {sum(p is not None for p in poses)} with a person")
    return poses, times


def write_steps(core, out: Path) -> None:
    steps = core.analysis.steps
    if not steps:
        return
    rows = [asdict(s) if hasattr(s, "__dataclass_fields__") else dict(vars(s)) for s in steps]
    keys = [k for k in rows[0] if not isinstance(rows[0][k], (list, dict))]
    with (out / "steps.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (r[k].value if hasattr(r[k], "value") else r[k]) for k in keys})


def write_summary(core, out: Path) -> None:
    a = core.analysis
    lines = [f"view {a.view}, facing {a.facing}, fps {a.fps}, steps {len(a.steps)}, frames {a.frames}"]
    if a.summary is not None:
        for k, v in asdict(a.summary).items():
            if v is not None and not isinstance(v, (list,)):
                lines.append(f"  {k}: {v}")
    lines.append("findings:")
    lines += [f"  {f.code} {f.severity.value}: {f.value} {f.unit}" for f in a.findings]
    lines.append("warnings:")
    lines += [f"  {w}" for w in a.warnings]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def contact_sheets(video: Path, core, out: Path, n_contacts: int, around: int, event: str = "contact") -> None:
    """Sheets around each landing (event="contact", i_ic) or toe-off (event="toeoff", i_to)."""

    def at(c):
        return c.i_ic if event == "contact" else c.i_to

    import cv2
    from PIL import Image, ImageDraw

    from lopanalys.pose.gait import foot_height
    from lopanalys.pose.types import Side
    ev, kin = core.events, core.kin
    want: dict[Side, list] = {}
    for side in (Side.LEFT, Side.RIGHT):
        cs = [c for c in ev.contacts if c.side == side and at(c) is not None]
        mid = len(cs) // 2                                   # the middle of the run, not the start-up
        want[side] = cs[max(0, mid - n_contacts // 2):max(0, mid - n_contacts // 2) + n_contacts]
    legs = [f"{p}_{n}" for p in ("l", "r") for n in ("knee", "ankle", "heel", "foot")]
    box_of_row: dict[int, tuple[int, int, int, int]] = {}
    for cs in want.values():
        for c in cs:
            rows = [r for r in range(int(round(at(c))) - around, int(round(at(c))) + around + 1) if 0 <= r < len(kin)]
            pts = np.concatenate([kin.lm(n)[rows] for n in legs])
            pts = pts[~np.isnan(pts).any(axis=1)]
            if not len(pts):
                continue
            x0, y0 = pts.min(axis=0)
            x1, y1 = pts.max(axis=0)
            y0 = max(y0, y1 - 0.75 * (x1 - x0))               # the feet, not the whole leg
            m = 0.08 * max(x1 - x0, y1 - y0)
            box = (max(0, int(x0 - m)), max(0, int(y0 - m)), min(kin.width, int(x1 + m)), min(kin.height, int(y1 + m)))
            for r in rows:
                box_of_row[r] = box
    frame_of_row = {r: int(kin.frame_index[r]) for r in box_of_row}
    box_of_frame = {frame_of_row[r]: box_of_row[r] for r in box_of_row}
    needed = set(box_of_frame)
    frames: dict[int, np.ndarray] = {}
    cap = cv2.VideoCapture(str(video))
    i = 0
    last = max(needed) if needed else -1
    while i <= last:
        ok = cap.grab()
        if not ok:
            break
        if i in needed:
            _, img = cap.retrieve()
            x0, y0, x1, y1 = box_of_frame[i]
            crop = img[y0:y1, x0:x1]
            frames[i] = cv2.resize(crop, (int(crop.shape[1] * THUMB_H / crop.shape[0]), THUMB_H))[:, :, ::-1]
        i += 1
    cap.release()
    names = ["heel", "foot", "ankle", "knee", "hip"]
    colours = {"heel": (255, 60, 60), "foot": (60, 160, 255), "ankle": (255, 220, 0), "knee": (0, 230, 120),
               "hip": (255, 255, 255)}
    for side, cs in want.items():
        if not cs:
            continue
        evs = core.events_series if getattr(core, "events_series", None) is not None else kin
        hgt_ev, _ = foot_height(evs, side)
        hgt = np.interp(kin.t, evs.t, hgt_ev)
        other = Side.RIGHT if side == Side.LEFT else Side.LEFT
        thr = ev.threshold_px.get(side) if isinstance(ev.threshold_px, dict) else ev.threshold_px
        tiles = []
        for c in cs:
            row_tiles = []
            r_ic = int(round(at(c)))
            for r in range(r_ic - around, r_ic + around + 1):
                if r not in frame_of_row or frame_of_row[r] not in frames:
                    continue
                img = Image.fromarray(frames[frame_of_row[r]]).copy()
                bx0, by0, bx1, by1 = box_of_row[r]
                sx = img.width / (bx1 - bx0)
                sy = img.height / (by1 - by0)
                d = ImageDraw.Draw(img)
                for nm in ("heel", "foot"):                  # the other foot: hollow rings
                    x, y = kin.side_lm(other, nm)[r]
                    if not np.isnan(x):
                        x, y = (x - bx0) * sx, (y - by0) * sy
                        d.ellipse([x - 6, y - 6, x + 6, y + 6], outline=colours[nm], width=2)
                for nm in names:
                    x, y = kin.side_lm(side, nm)[r]
                    if not np.isnan(x):
                        x, y = (x - bx0) * sx, (y - by0) * sy
                        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=colours[nm])
                fh = hgt[r] if r < len(hgt) else float("nan")
                d.rectangle([0, 0, img.width, 16], fill=(0, 0, 0))
                d.text((3, 2), (f"{r - r_ic:+d}  h={fh:.0f}px thr={thr:.0f}" if not np.isnan(fh) else f"{r - r_ic:+d}")
                       + (f"  {c.method}" if r == r_ic and getattr(c, "method", "height") != "height" else ""),
                       fill=(255, 255, 255))
                if r == r_ic:
                    d.rectangle([0, 0, img.width - 1, img.height - 1], outline=(255, 0, 255), width=4)
                row_tiles.append(img)
            if row_tiles:
                tiles.append(row_tiles)
        if not tiles:
            continue
        tw = max(t.width for r in tiles for t in r)
        sheet = Image.new("RGB", (tw * (2 * around + 1), THUMB_H * len(tiles)), (20, 20, 20))
        for y, r in enumerate(tiles):
            for x, t_img in enumerate(r):
                sheet.paste(t_img, (x * tw, y * THUMB_H))
        name = f"{'contacts' if event == 'contact' else 'toeoffs'}_{side.value}.png"
        sheet.save(out / name)
        print(f"{name}: {len(tiles)} {'landings' if event == 'contact' else 'toe-offs'}")


def write_cycles(core, out: Path) -> None:
    rows = core.analysis.cycles
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with (out / "cycles.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})


def write_technique_summary(core, out: Path) -> None:
    a = core.analysis
    lines = [f"{a.kind}, view {a.view}, facing {a.facing}, fps {a.fps}, cycles {len(a.cycles)}, frames {a.frames}",
             f"quality: {a.quality.get('level')} {a.quality.get('reasons')}", "values (value, ok range, basis, +-error, level):"]
    for k, r in a.ranges.items():
        lines.append(f"  {k}: {r['value']} {r['unit']}  [{r['text'] or '-'}]  {r['basis']}  +-{r['uncertainty']}  "
                     f"{r['severity'] or 'no verdict'}{' (within error)' if r['within_error'] else ''}")
    lines.append("findings:")
    lines += [f"  {f.code} {f.severity.value}: {f.value} {f.unit}  {f.explanation}" for f in a.findings]
    lines.append("advice:")
    lines += [f"  {x['part']} {x['action']} certain={x['certain']}: {x['text']}" for x in a.advice]
    lines.append("warnings:")
    lines += [f"  {w}" for w in a.warnings]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def event_sheet(video: Path, kin, events: list[float], names: list[str], value, name: str, out: Path,
                around: int) -> None:
    """A sheet with one line per event: the frames ``around`` the detected row, the detected one framed,
    the landmarks ``names`` drawn as dots and ``value(row)`` written in each frame."""
    import cv2
    from PIL import Image, ImageDraw

    colours = [(255, 60, 60), (60, 160, 255), (255, 220, 0), (0, 230, 120), (255, 255, 255)]
    rows_of = {int(round(e)): [r for r in range(int(round(e)) - around, int(round(e)) + around + 1) if 0 <= r < len(kin)]
               for e in events}
    box_of_row: dict[int, tuple[int, int, int, int]] = {}
    for rows in rows_of.values():
        pts = np.concatenate([kin.lm(n)[rows] for n in names])
        pts = pts[~np.isnan(pts).any(axis=1)]
        if not len(pts):
            continue
        (x0, y0), (x1, y1) = pts.min(axis=0), pts.max(axis=0)
        m = 0.15 * max(x1 - x0, y1 - y0, 20)
        box = (max(0, int(x0 - m)), max(0, int(y0 - m)), min(kin.width, int(x1 + m)), min(kin.height, int(y1 + m)))
        for r in rows:
            box_of_row[r] = box
    frame_of_row = {r: int(kin.frame_index[r]) for r in box_of_row}
    needed = set(frame_of_row.values())
    frames: dict[int, np.ndarray] = {}
    cap = cv2.VideoCapture(str(video))
    i = 0
    while needed and i <= max(needed):
        if not cap.grab():
            break
        if i in needed:
            frames[i] = cap.retrieve()[1][:, :, ::-1]
        i += 1
    cap.release()
    lines = []
    for mid, rows in rows_of.items():
        tiles = []
        for r in rows:
            if r not in box_of_row or frame_of_row[r] not in frames:
                continue
            x0, y0, x1, y1 = box_of_row[r]
            crop = frames[frame_of_row[r]][y0:y1, x0:x1]
            img = Image.fromarray(np.ascontiguousarray(crop)).resize((max(1, int(crop.shape[1] * THUMB_H / crop.shape[0])), THUMB_H))
            sx, sy = img.width / (x1 - x0), img.height / (y1 - y0)
            d = ImageDraw.Draw(img)
            for nm, col in zip(names, colours):
                x, y = kin.lm(nm)[r]
                if not np.isnan(x):
                    d.ellipse([(x - x0) * sx - 4, (y - y0) * sy - 4, (x - x0) * sx + 4, (y - y0) * sy + 4], fill=col)
            d.rectangle([0, 0, img.width, 16], fill=(0, 0, 0))
            d.text((3, 2), f"{r - mid:+d}  {value(r)}", fill=(255, 255, 255))
            if r == mid:
                d.rectangle([0, 0, img.width - 1, img.height - 1], outline=(255, 0, 255), width=4)
            tiles.append(img)
        if tiles:
            lines.append(tiles)
    if not lines:
        return
    tw = max(t.width for r in lines for t in r)
    sheet = Image.new("RGB", (tw * (2 * around + 1), THUMB_H * len(lines)), (20, 20, 20))
    for y, r in enumerate(lines):
        for x, t_img in enumerate(r):
            sheet.paste(t_img, (x * tw, y * THUMB_H))
    sheet.save(out / name)
    print(f"{name}: {len(lines)} events")


def review_technique(a, out: Path, poses: list, times: list) -> None:
    """Bike fit (``--sport bike``) or double poling (``--sport ski``)."""
    if a.sport == "bike":
        from lopanalys.pose.bike.analysis import analyze_core
    else:
        from lopanalys.pose.ski.analysis import analyze_core
    core = analyze_core(poses, a.height, a.view, times=times, frames_total=len(poses))
    write_cycles(core, out)
    write_technique_summary(core, out)
    print(f"{core.analysis.kind}: {len(core.analysis.cycles)} cycles, quality {core.analysis.quality.get('level')}")
    rows = core.analysis.cycles
    if core.kin is None or not rows or core.series is None:
        return
    mid = len(rows) // 2
    pick = rows[max(0, mid - a.contacts // 2):max(0, mid - a.contacts // 2) + a.contacts]
    pre = "r_" if a.view == "side_right" else "l_"
    ser = core.series
    if a.sport == "bike" and "knee_flex" in ser:
        names = [pre + n for n in ("hip", "knee", "ankle", "foot")]
        val = lambda r: f"knee {ser['knee_flex'][r]:.0f}"      # noqa: E731
        event_sheet(a.video, core.kin, [r["row_bottom"] for r in pick], names, val, "bottoms.png", out, a.around)
        event_sheet(a.video, core.kin, [r["row_top"] for r in pick], names, val, "tops.png", out, a.around)
    elif a.sport == "ski":
        names = [pre + n for n in ("shoulder", "elbow", "wrist", "hip")]
        val = lambda r: f"trunk {ser['trunk_lean'][r]:.0f} elbow {ser['elbow'][r]:.0f}"      # noqa: E731
        event_sheet(a.video, core.kin, [r["row_start"] for r in pick], names, val, "starts.png", out, a.around)
        event_sheet(a.video, core.kin, [r["row_bottom"] for r in pick], names, val, "finishes.png", out, a.around)


def make_synthetic(a, out: Path) -> None:
    """A film of the synthetic cyclist or skier and its poses in the cache, so the tool runs without a model."""
    from lopanalys.pose.types import View
    if a.sport == "bike":
        from lopanalys.pose.bike import synthetic as syn
        p = syn.CyclistParams(view=View(a.view), height_cm=a.height, duration_s=a.seconds or 12.0)
    else:
        from lopanalys.pose.ski import synthetic as syn      # type: ignore[no-redef]
        p = syn.SkierParams(view=View(a.view), height_cm=a.height, duration_s=a.seconds or 14.0)
    a.video = Path(syn.write_video(str(out / f"synthetic-{a.sport}.mp4"), p))
    poses, times = syn.generate(p)
    with (out / "poses.pkl").open("wb") as f:
        pickle.dump({"poses": poses, "times": times}, f)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video", type=Path, nargs="?", help="the film (not needed with --synthetic)")
    ap.add_argument("--sport", choices=("run", "bike", "ski"), default="run", help="what the film shows")
    ap.add_argument("--synthetic", action="store_true", help="bike/ski: make a film of the synthetic athlete first")
    ap.add_argument("--height", type=float, required=True, help="the athlete's height in cm")
    ap.add_argument("--view", default="side_left", help="side_left, side_right, rear or front (bike: side or front)")
    ap.add_argument("--speed", type=float, default=None, help="treadmill speed km/h, when known")
    ap.add_argument("--model", type=Path, default=None, help="pose_landmarker_full.task (no download)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--contacts", type=int, default=6)
    ap.add_argument("--around", type=int, default=4)
    ap.add_argument("--seconds", type=float, default=None, help="only the first N seconds")
    ap.add_argument("--every", type=int, default=1, help="analyse every N-th frame (120 fps / 4 = 30 fps)")
    a = ap.parse_args(argv)
    if a.synthetic and (a.sport == "run" or a.out is None):
        ap.error("--synthetic needs --sport bike or ski, and --out")
    if a.video is None and not a.synthetic:
        ap.error("a video is needed")
    out = a.out or a.video.with_suffix("").parent / (a.video.stem + "-granskning")
    out.mkdir(parents=True, exist_ok=True)
    if a.synthetic:
        make_synthetic(a, out)
    poses, times = estimate_poses(a.video, a.model, out / "poses.pkl", None if a.synthetic else a.seconds)
    if a.sport != "run":
        review_technique(a, out, poses, times)
        print(f"written to {out}")
        return
    from lopanalys.pose.offline import analyze_core
    if a.every > 1:
        from lopanalys.pose.reliability import decimate
        poses, times = decimate(poses, a.every, renumber=False), list(times[::a.every])
        out = out / f"every{a.every}"
        out.mkdir(exist_ok=True)
    core = analyze_core(poses, a.height, a.view, a.speed, times=times, frames_total=len(poses))
    write_steps(core, out)
    write_summary(core, out)
    if core.kin is not None:
        contact_sheets(a.video, core, out, a.contacts, a.around)
        contact_sheets(a.video, core, out, a.contacts, a.around, event="toeoff")
    print(f"written to {out}")


if __name__ == "__main__":
    main()
