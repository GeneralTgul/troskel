"""Audio cues before stage changes: short tones generated as WAV files on first use.

Uses QSoundEffect (Qt Multimedia) when it loads, otherwise the system beep, so a machine
without audio never breaks the test.
"""
from __future__ import annotations

import logging
import math
import struct
import wave
from pathlib import Path

from lopanalys.core import paths

log = logging.getLogger(__name__)

TONES = {"tick": (880.0, 0.12), "go": (1320.0, 0.45), "alert": (660.0, 0.3)}


def _write_tone(path: Path, freq: float, dur: float, rate: int = 44100) -> None:
    n = int(rate * dur)
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / (rate * 0.01), (n - i) / (rate * 0.03))  # soft attack/release
        frames += struct.pack("<h", int(12000 * env * math.sin(2 * math.pi * freq * i / rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class Cues:
    """Play named cues: 'tick' (countdown), 'go' (stage change), 'alert' (lactate, operator)."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._effects: dict[str, object] = {}
        self._qt_ok = False
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            d = paths.sub("cache")
            for name, (f, dur) in TONES.items():
                p = d / f"cue-{name}.wav"
                if not p.exists():
                    _write_tone(p, f, dur)
                e = QSoundEffect()
                e.setSource(QUrl.fromLocalFile(str(p)))
                e.setVolume(0.8)
                self._effects[name] = e
            self._qt_ok = True
        except Exception as e:  # no Qt Multimedia / no audio backend
            log.info("audio cues fall back to system beep: %s", e)

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        try:
            if self._qt_ok and name in self._effects:
                self._effects[name].play()  # type: ignore[attr-defined]
            else:
                from PySide6.QtWidgets import QApplication
                QApplication.beep()
        except Exception:
            log.exception("cue failed")
