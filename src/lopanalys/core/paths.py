"""Where the app keeps its data. Windows: %APPDATA%\\Lopanalys. Elsewhere: ~/.local/share/lopanalys.

LOPANALYS_HOME overrides everything (tests use a tmp dir).

Demo mode (``--demo``) has a data folder of its own, ``demo`` inside the real one, so the program can
be shown to a customer without a single real person on the screen.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


DEMO_FOLDER = "demo"
_demo = False


def set_demo(on: bool) -> None:
    """Demo mode on: every path below is inside the demo folder. Set once, before anything is opened."""
    global _demo
    _demo = bool(on)


def is_demo() -> bool:
    return _demo


def real_data_dir() -> Path:
    """The coach's data folder, also in demo mode."""
    if os.environ.get("LOPANALYS_HOME"):
        p = Path(os.environ["LOPANALYS_HOME"])
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", Path.home())) / "Lopanalys"
    else:
        p = Path.home() / ".local" / "share" / "lopanalys"
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    p = real_data_dir()
    if _demo:
        p = p / DEMO_FOLDER
        p.mkdir(parents=True, exist_ok=True)
    return p


def sub(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "lopanalys.sqlite"


def recordings_dir() -> Path:
    return sub("recordings")


def models_dir() -> Path:
    """Pose model files (.task). Downloaded on first run if missing, see pose/models.py."""
    return sub("models")


def reports_dir() -> Path:
    return sub("reports")


def keyframes_dir() -> Path:
    return sub("keyframes")
