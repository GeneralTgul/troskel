"""Camera sources. Every source yields Frames with a monotonic timestamp in seconds.

Implementations (camera agent): system camera (OpenCV: USB webcams, capture cards, virtual
cameras), GoPro USB webcam mode (HERO8 and later, UDP MPEG-TS), video file (SD card recordings at
60-240 fps, analysed offline), RTSP/IP camera. No Qt imports here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Protocol

import numpy as np


class SourceKind(str, Enum):
    SYSTEM = "system"      # OpenCV device index / DirectShow name
    GOPRO_USB = "gopro_usb"
    FILE = "file"
    RTSP = "rtsp"


@dataclass(frozen=True)
class SourceInfo:
    kind: SourceKind
    id: str                 # device index, file path, URL or GoPro serial
    label: str              # shown in the source picker, e.g. "GoPro HERO8 Black (USB)"
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    live: bool = True       # False for files: analysis may run slower than real time


@dataclass
class Frame:
    image: np.ndarray       # BGR uint8, H x W x 3
    t: float                # seconds; files: pts from the container, live: time.perf_counter()
    index: int


class FrameSource(Protocol):
    info: SourceInfo

    def open(self) -> None: ...
    def frames(self) -> Iterator[Frame]:
        """Blocking iterator; ends when the source closes or a file ends."""
        ...
    def close(self) -> None: ...


def safe_url(url: str) -> str:
    """A source id without ``user:password@`` (a network camera's address in labels and log lines)."""
    import re
    return re.sub(r"//[^/@\s]*@", "//", str(url))


def discover() -> list[SourceInfo]:
    """All sources that can be opened right now. Implemented in camera/discovery.py."""
    from lopanalys.camera.discovery import discover_sources
    return discover_sources()


def open_source(info: SourceInfo) -> FrameSource:
    """Factory. Implemented in camera/discovery.py."""
    from lopanalys.camera.discovery import make_source
    return make_source(info)
