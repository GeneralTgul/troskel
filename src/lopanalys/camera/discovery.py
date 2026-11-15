"""Finds the sources the coach can pick: system cameras, GoPros on USB, and recent files/streams.

System cameras: on Windows the DirectShow device names come from ``pygrabber`` (MIT, optional; it
lists devices in the same order as OpenCV's DirectShow indices), so the picker shows
"Logitech BRIO" rather than "Kamera 0". Without pygrabber, or on Linux/macOS, devices are listed by
index (Linux: /dev/video* with names from sysfs). Probing a camera's modes means opening it, which
takes up to a second per camera on Windows, so ``discover_sources`` only probes when asked; the live
source picks the best mode itself when it opens.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Iterable, Sequence

from lopanalys.camera.base import FrameSource, SourceInfo, SourceKind
from lopanalys.camera.file_source import VIDEO_SUFFIXES, FileSource, file_info
from lopanalys.camera.network import URL_RE, HttpStreamSource, is_http, stream_info
from lopanalys.camera.opencv_source import CameraError, OpenCvSource, probe_system_camera
from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

MAX_INDEX = 8
# Virtual cameras that only mirror another source; listed last, never auto-picked.
_VIRTUAL = re.compile(r"obs virtual|virtual cam|snap camera|xsplit|droidcam|nvidia broadcast", re.I)
# GoPro's own virtual camera (from the GoPro Webcam program). Never listed: opening it with OpenCV threw
# "Unknown C++ exception" and then corrupted the heap so the whole program died (real hardware 2026-11-14).
# The GoPro is used directly over USB instead (GOPRO_USB source).
_HIDDEN = re.compile(r"^gopro webcam", re.I)


def _dshow_names() -> list[str] | None:
    """DirectShow video input names in index order (Windows with pygrabber), else None."""
    if sys.platform != "win32":
        return None
    try:
        from pygrabber.dshow_graph import FilterGraph  # type: ignore[import-not-found]
        return list(FilterGraph().get_input_devices())
    except Exception as e:   # not installed or COM failure: fall back to numbered cameras
        log.info("pygrabber unavailable: %s", e)
        return None


def _linux_devices() -> list[tuple[str, str]]:
    """(index, name) of V4L2 capture nodes. Metadata nodes (every second /dev/video on UVC) are skipped."""
    out: list[tuple[str, str]] = []
    for dev in sorted(Path("/sys/class/video4linux").glob("video*"), key=lambda p: int(p.name[5:] or 0)):
        try:
            name = (dev / "name").read_text().strip()
            idx = (dev / "index").read_text().strip() if (dev / "index").exists() else "0"
        except OSError:
            continue
        if idx != "0":
            continue
        out.append((dev.name[5:], name))
    return out


def list_system_cameras(probe: bool = False) -> list[SourceInfo]:
    """System cameras. Without ``probe`` no device is opened (fast); with it each one is opened
    once to read its best mode, and cameras that cannot be opened are left out."""
    found: list[tuple[str, str]] = []
    names = _dshow_names()
    if names is not None:
        found = [(str(i), n) for i, n in enumerate(names)]
    elif sys.platform.startswith("linux"):
        found = _linux_devices()
    else:
        probe = True   # nothing to enumerate without opening: probe indices 0..MAX_INDEX
        found = [(str(i), "") for i in range(MAX_INDEX)]
    result: list[SourceInfo] = []
    misses = 0
    for sid, name in found:
        if name and _HIDDEN.search(name):
            log.info("skipping %s (camera %s): GoPro's virtual camera, the GoPro is used over USB", name, sid)
            continue
        label = name or t("camera.label.system", n=sid)
        if probe:
            info = probe_system_camera(sid)
            if info is None:
                misses += 1
                if not name and misses >= 2:
                    break
                continue
            result.append(SourceInfo(SourceKind.SYSTEM, sid, label, info.width, info.height, info.fps))
        else:
            result.append(SourceInfo(SourceKind.SYSTEM, sid, label))
    result.sort(key=lambda i: bool(_VIRTUAL.search(i.label)))
    return result


def _recent_infos(recent: Iterable[str]) -> list[SourceInfo]:
    out: list[SourceInfo] = []
    seen: set[str] = set()
    for item in recent:
        item = item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        if URL_RE.match(item):
            out.append(stream_info(item))
            continue
        p = Path(item)
        if p.suffix.lower() in VIDEO_SUFFIXES and p.is_file():
            try:
                out.append(file_info(p))
            except CameraError:
                log.info("a recent video file is not readable any more")
    return out


def discover_sources(recent: Sequence[str] = (), *, probe: bool = False, gopro: bool = True,
                     gopro_timeout_s: float = 0.8) -> list[SourceInfo]:
    """All sources that can be opened now: GoPros on USB first (they are what the coach plugged in
    for this), then system cameras, then recent files and stream URLs from ``recent``."""
    sources: list[SourceInfo] = []
    if gopro:
        try:
            from lopanalys.camera.gopro import GoProUsb
            for cam in GoProUsb.find(timeout_s=gopro_timeout_s):
                sources.append(cam.source_info())
        except Exception:
            log.exception("GoPro discovery failed")
    try:
        sources.extend(list_system_cameras(probe=probe))
    except Exception:
        log.exception("system camera discovery failed")
    sources.extend(_recent_infos(recent))
    return sources


def make_source(info: SourceInfo, **kwargs: object) -> FrameSource:
    """Builds the right FrameSource for ``info``. Extra keyword arguments go to the source class
    (e.g. ``pace="realtime", loop=True`` for a file in demo mode, ``on_status=`` for live ones)."""
    if info.kind == SourceKind.FILE:
        return FileSource(info, **kwargs)  # type: ignore[arg-type]
    if info.kind == SourceKind.RTSP and is_http(info.id):
        # The phone as a camera over HTTP: the MJPEG reader of our own, FFmpeg when it is something else.
        return HttpStreamSource(info, **kwargs)
    if info.kind in (SourceKind.SYSTEM, SourceKind.RTSP):
        return OpenCvSource(info, **kwargs)  # type: ignore[arg-type]
    if info.kind == SourceKind.GOPRO_USB:
        from lopanalys.camera.gopro import GoProUsb, GoProWebcamSource
        return GoProWebcamSource(GoProUsb.from_source_id(info.id), info=info, **kwargs)  # type: ignore[arg-type]
    raise CameraError(t("camera.error.unknown_kind", kind=str(info.kind)))
