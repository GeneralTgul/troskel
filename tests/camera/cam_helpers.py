"""Helpers for the camera tests: a moving circle on a known fps, written with OpenCV."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def make_frame(i: int, w: int = 160, h: int = 120) -> np.ndarray:
    img = np.zeros((h, w, 3), np.uint8)
    x = 10 + (i * 3) % (w - 20)
    cv2.circle(img, (x, h // 2), 8, (0, 200, 255), -1)
    cv2.putText(img, str(i), (2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    return img


def write_video(path: Path, n: int, fps: float, size: tuple[int, int] = (160, 120), fourcc: str = "mp4v") -> Path:
    w = cv2.VideoWriter(str(path), cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*fourcc), fps, size)
    assert w.isOpened(), f"cannot write {path}"
    for i in range(n):
        w.write(make_frame(i, *size))
    w.release()
    return path


