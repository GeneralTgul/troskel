"""Manual camera tests from a terminal (no UI needed), e.g. on the gym PC with the GoPro plugged in.

    python -m lopanalys.camera list [--probe] [--recent FILE_OR_URL ...]
    python -m lopanalys.camera preview <id> [--seconds 10] [--record out.mp4] [--snapshot shot.jpg]
        <id>: a camera index ("0", "msmf:1"), a video file, an rtsp://, http:// or udp:// URL, or "gopro"
    python -m lopanalys.camera find [address]
        the phone as a camera: without an address, the cameras that announce themselves on the
        network (mDNS); with one ("192.0.2.50", "192.0.2.50:8081/video"), the streams that answer there
    python -m lopanalys.camera gopro-info [--host 172.2x.1yz.51[:port]]
    python -m lopanalys.camera gopro-download [--host ...] [--dest DIR] [--name GX010042.MP4]
    python -m lopanalys.camera gopro-record [--host ...] [--seconds 5] [--fps 120] [--dest DIR]
        both with ``--analyze --height 180 [--view side_left] [--speed 12]``: the downloaded clip goes
        straight through ``pose.offline.analyze_video`` (the 120 fps check), and the
        summary, the frame rate and the warnings (values marked uncertain or left out) are printed

``preview`` shows a window when this OpenCV build has one; the headless build prints the measured
frame rate, the dropped frames and the delay instead.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from lopanalys.camera.base import SourceInfo, SourceKind
from lopanalys.camera.discovery import discover_sources, make_source
from lopanalys.camera.file_source import file_info
from lopanalys.camera.network import (find_network_cameras, format_stats, probe_address, safe_url,
                                      stream_info)
from lopanalys.camera.gopro import ENDPOINT_SOURCES, GoProError, GoProUsb
from lopanalys.camera.opencv_source import CameraError
from lopanalys.camera.recorder import Recorder
from lopanalys.core import paths
from lopanalys.core.i18n import t


def _gopro(host: str | None) -> GoProUsb:
    if host:
        return GoProUsb._from_hostport(host).identify()
    found = GoProUsb.find(timeout_s=1.5)
    if not found:
        raise GoProError(t("camera.gopro.hint.not_found"), "not_found")
    return found[0]


def _info_for(text: str) -> SourceInfo:
    if text == "gopro":
        return _gopro(None).source_info()
    if "://" in text:
        return stream_info(text)
    if Path(text).suffix and Path(text).exists():
        return file_info(text)
    return SourceInfo(SourceKind.SYSTEM, text, t("camera.label.system", n=text))


def cmd_list(a: argparse.Namespace) -> int:
    sources = discover_sources(recent=a.recent or (), probe=a.probe)
    if not sources:
        print(t("camera.cli.none"))
        print(t("camera.gopro.hint.not_found"))
        return 1
    for s in sources:
        mode = f"{s.width}x{s.height} @ {s.fps:.0f}" if s.width and s.height and s.fps else ""
        print(f"{s.kind.value:10} {s.id:40} {s.label}  {mode}")
    if not any(s.kind == SourceKind.GOPRO_USB for s in sources):
        print("\n" + t("camera.gopro.hint.not_found"))
    return 0


def cmd_preview(a: argparse.Namespace) -> int:
    import cv2
    info = _info_for(a.id)
    src = make_source(info, pace="realtime") if info.kind == SourceKind.FILE else make_source(info)
    src.open()
    rec: Recorder | None = None
    window = True
    start = time.perf_counter()
    last_print = start
    n = 0
    try:
        for f in src.frames():
            n += 1
            if a.record and rec is None:
                h, w = f.image.shape[:2]
                rec = Recorder(a.record, getattr(src, "actual_fps", None) or info.fps or 30, (w, h),
                               meta={"source": info.label})
            if rec:
                rec.write(f)
            if a.snapshot and n == 1:
                cv2.imwrite(a.snapshot, f.image)
            if window:
                try:
                    cv2.imshow(info.label, f.image)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        break
                except cv2.error:
                    window = False
                    print(t("camera.cli.preview_help", fps="…"))
            now = time.perf_counter()
            if now - last_print >= 1.0:
                fps = getattr(src, "actual_fps", None)
                lag = (now - f.t) * 1000 if info.live else 0.0
                print(t("camera.cli.stats", w=f.image.shape[1], h=f.image.shape[0], fps=f"{fps or 0:.1f}",
                        dropped=getattr(src, "dropped", 0), lag=f"{lag:.0f}", index=f.index))
                last_print = now
            if a.seconds and now - start >= a.seconds:
                break
    finally:
        stats = getattr(src, "stats", None)
        if stats is not None:      # a network stream: frame rate, interval spread, skipped, reconnects
            print(format_stats(safe_url(info.id), stats.snapshot()))
        src.close()
        if rec:
            print(t("camera.cli.saved", path=rec.close()))
        if window:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass
    return 0


def cmd_find(a: argparse.Namespace) -> int:
    if a.address:
        found = probe_address(a.address, a.timeout)
        for r in found:
            print(t("camera.cli.stream_found", protocol=r.protocol, url=safe_url(r.url), ms=f"{r.ms:.0f}",
                    login=t("camera.cli.stream_login") if r.needs_login else ""))
    else:
        cams = find_network_cameras(timeout_s=a.timeout)
        for c in cams:
            print(f"{c.kind.value:10} {safe_url(c.id):40} {c.label}")
        found = cams   # type: ignore[assignment]
    if not found:
        print(t("camera.cli.stream_none", address=a.address or "mDNS"))
        return 1
    return 0


def cmd_gopro_info(a: argparse.Namespace) -> int:
    gp = _gopro(a.host)
    print(t("camera.cli.gopro_id", model=gp.model, serial=gp.serial or "?", firmware=gp.firmware or "?",
            api=gp.api.value if gp.api else "?"))
    print(t("camera.cli.gopro_state", host=gp.host, webcam=gp.webcam_status().name))
    media = gp.list_media()
    print(t("camera.cli.gopro_media", n=len(media)))
    if media:
        print(t("camera.cli.gopro_latest", path=media[-1].path, mb=f"{media[-1].size / 1e6:.0f}"))
    if a.verbose:
        for k, v in ENDPOINT_SOURCES.items():
            print(f"  {k}: {v}")
    return 0


def _progress(done: int, total: int) -> None:
    pct = done * 100 // total if total else 0
    print(f"\r{done / 1e6:.0f}/{total / 1e6:.0f} MB {pct}%", end="", flush=True)


def cmd_gopro_download(a: argparse.Namespace) -> int:
    gp = _gopro(a.host)
    videos = [m for m in gp.list_media() if m.is_video]
    if a.name:
        videos = [m for m in videos if m.name.upper() == a.name.upper()]
    if not videos:
        print(t("camera.gopro.err.no_new_file"))
        return 1
    path = gp.download(videos[-1], Path(a.dest or paths.recordings_dir()), _progress)
    print("\n" + t("camera.cli.saved", path=path))
    return _analyze(path, a)


def cmd_gopro_record(a: argparse.Namespace) -> int:
    gp = _gopro(a.host)
    clip = gp.record_clip(a.seconds, Path(a.dest or paths.recordings_dir()), fps=a.fps,
                          on_progress=lambda s, f: print(f"\r{t('camera.gopro.stage.' + s)} {f * 100:3.0f}%   ",
                                                         end="", flush=True))
    print()
    for w in clip.warnings:
        print(w)
    print(t("camera.cli.saved", path=clip.path))
    return _analyze(Path(clip.path), a)


def _analyze(path: Path, a: argparse.Namespace) -> int:
    """``--analyze``: the clip through the offline analysis, the result printed."""
    if not getattr(a, "analyze", False):
        return 0
    from lopanalys.pose.offline import AnalysisError, analyze_video
    try:
        g = analyze_video(path, a.height, a.view, speed_kmh=a.speed,
                          progress_cb=lambda f, *_: print(f"\r{f * 100:3.0f}%", end="", flush=True))
    except AnalysisError as e:
        print("\n" + str(e), file=sys.stderr)
        return 3
    print()
    print(f"{g.fps} fps, {len(g.steps)} steps, {g.duration_s} s")
    if g.summary is not None:
        for k, v in g.summary.to_dict().items():
            if isinstance(v, (int, float)) and k not in ("t_from", "t_to"):
                mark = {"uncertain": "  (uncertain)", "hidden": "  (left out)"}.get(g.reliability.get(k, ""), "")
                print(f"  {k}: {v:.1f}{mark}")
    for w in g.warnings:
        print(f"! {w}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m lopanalys.camera", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--probe", action="store_true")
    s.add_argument("--recent", nargs="*")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("preview")
    s.add_argument("id")
    s.add_argument("--seconds", type=float, default=0)
    s.add_argument("--record")
    s.add_argument("--snapshot")
    s.set_defaults(fn=cmd_preview)
    s = sub.add_parser("find")
    s.add_argument("address", nargs="?")
    s.add_argument("--timeout", type=float, default=1.5)
    s.set_defaults(fn=cmd_find)
    for name, fn in (("gopro-info", cmd_gopro_info), ("gopro-download", cmd_gopro_download),
                     ("gopro-record", cmd_gopro_record)):
        s = sub.add_parser(name)
        s.add_argument("--host")
        s.set_defaults(fn=fn)
        if name == "gopro-info":
            s.add_argument("-v", "--verbose", action="store_true")
        else:
            s.add_argument("--dest")
        if name == "gopro-download":
            s.add_argument("--name")
        if name == "gopro-record":
            s.add_argument("--seconds", type=float, default=5)
            s.add_argument("--fps", type=int, default=120)
        if name != "gopro-info":
            s.add_argument("--analyze", action="store_true")
            s.add_argument("--height", type=float, default=None)
            s.add_argument("--view", default="side_left")
            s.add_argument("--speed", type=float, default=None)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (CameraError, GoProError) as e:
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
