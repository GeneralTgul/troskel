"""Find or download the MediaPipe Pose Landmarker model (``.task``).

Search order for ``pose_landmarker_<variant>.task``:
  1. ``LOPANALYS_POSE_MODEL`` (a path to a .task file; for support and tests),
  2. the ``models/`` folder bundled next to the program (PyInstaller puts it in ``sys._MEIPASS``;
     the Windows build bakes in the full model with a ``.sha256`` sidecar),
  3. the app's model folder ``core.paths.models_dir()``,
  4. download into the model folder (Google's public model bucket), with progress.

Integrity: a ``<model>.sha256`` sidecar is checked when present. A downloaded file gets one written
after it passed the sanity checks (size, zip container), so later corruption on disk is caught.
Pin a known hash in ``KNOWN_SHA256`` to require it for downloads.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from lopanalys.core import paths
from lopanalys.core.i18n import t

log = logging.getLogger(__name__)

VARIANTS = ("lite", "full", "heavy")
_BASE = "https://storage.googleapis.com/mediapipe-models/pose_landmarker"
# The same URL the Windows build uses; "1" is the first published version as a fallback.
URLS = {v: [f"{_BASE}/pose_landmarker_{v}/float16/latest/pose_landmarker_{v}.task",
            f"{_BASE}/pose_landmarker_{v}/float16/1/pose_landmarker_{v}.task"] for v in VARIANTS}
# Approximate sizes (bytes) for a sanity check; the real files are ~5.5, ~9 and ~29 MB.
MIN_SIZE = {"lite": 2_000_000, "full": 4_000_000, "heavy": 15_000_000}
KNOWN_SHA256: dict[str, str] = {}   # variant -> lowercase hex, fill in when verified on the gym PC

ProgressCb = Callable[..., None]   # (bytes done, bytes total or 0) or (fraction 0..1)


def call_progress(cb: Callable[..., None] | None, fraction: float, *extra: object) -> None:
    """Call a progress callback that takes either (fraction) or (fraction/value, more...)."""
    if cb is None:
        return
    import inspect
    try:
        params = [p for p in inspect.signature(cb).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        varargs = any(p.kind == p.VAR_POSITIONAL for p in inspect.signature(cb).parameters.values())
        n = len(params)
    except (TypeError, ValueError):
        n, varargs = 1, False
    try:
        if varargs or n >= 1 + len(extra):
            cb(fraction, *extra)
        else:
            cb(fraction)
    except Exception:
        log.exception("progress callback failed")


class ModelError(RuntimeError):
    """The model is missing or damaged. The message is localised and meant for the coach."""


def filename(variant: str) -> str:
    return f"pose_landmarker_{variant}.task"


def bundled_dirs() -> list[Path]:
    """Folders where an installed program keeps its bundled models."""
    out: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(Path(meipass) / "models")
    exe_dir = Path(sys.executable).resolve().parent
    out += [exe_dir / "models", exe_dir / "_internal" / "models"]
    out.append(Path(__file__).resolve().parents[3] / "models")   # source checkout: repo/models
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_sidecar(path: Path) -> str | None:
    """The hex digest in ``<model>.sha256`` (any encoding PowerShell might have written)."""
    side = path.with_name(path.name + ".sha256")
    if not side.exists():
        return None
    raw = side.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            m = re.search(r"[0-9a-fA-F]{64}", raw.decode(enc))
        except UnicodeDecodeError:
            continue
        if m:
            return m.group(0).lower()
    return None


def verify(path: Path, variant: str | None = None) -> bool:
    """True when the file matches its sidecar (or the pinned hash); files without either pass."""
    expected = _read_sidecar(path) or (KNOWN_SHA256.get(variant) if variant else None)
    if not expected:
        return True
    return sha256_of(path) == expected


def _sane(path: Path, variant: str) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < MIN_SIZE.get(variant, 1_000_000):
        return False
    if not zipfile.is_zipfile(path):
        # .task files are zip bundles of .tflite models; accept anyway if big enough, but say so
        log.warning("model %s is not a zip container; trusting its size (%d bytes)", path, size)
    return True


def find_model(variant: str = "full") -> Path | None:
    """An existing, intact model file, or None. Never downloads."""
    env = os.environ.get("LOPANALYS_POSE_MODEL")
    if env and Path(env).is_file():
        return Path(env)
    name = filename(variant)
    for d in bundled_dirs() + [paths.models_dir()]:
        p = d / name
        if p.is_file():
            if verify(p, variant):
                return p
            log.error("model %s does not match its checksum; skipped", p)
    return None


def _report_bytes(cb: ProgressCb, done: int, total: int) -> None:
    """Two-argument callbacks get (bytes done, bytes total); one-argument ones a fraction."""
    import inspect
    try:
        n = len([p for p in inspect.signature(cb).parameters.values()
                 if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)])
    except (TypeError, ValueError):
        n = 2
    try:
        if n >= 2:
            cb(done, total)
        else:
            cb(done / total if total else 0.0)
    except Exception:
        log.exception("progress callback failed")


def model_path(variant: str = "full") -> Path:
    """Where the model is (or will be once downloaded). Use ``.exists()`` to test."""
    return find_model(variant) or paths.models_dir() / filename(variant)


def is_downloaded(variant: str = "full") -> bool:
    """True when an intact model is available without downloading."""
    return find_model(variant) is not None


def download(variant: str, dest: Path, progress_cb: ProgressCb | None = None, timeout: float = 30.0) -> Path:
    """Download a model to ``dest`` (atomic: .part then rename). Raises ModelError.

    With ``LOPANALYS_NO_DOWNLOAD=1`` (set by scripts/headless_env.sh and the test suite) nothing
    is fetched: a dev box without the model must not reach the internet.
    """
    if os.environ.get("LOPANALYS_NO_DOWNLOAD") == "1":
        raise ModelError(t("pose.error.model_missing", reason="LOPANALYS_NO_DOWNLOAD=1",
                           name=dest.name, folder=str(dest.parent)))
    last_err = ""
    tmp = dest.with_name(dest.name + ".part")
    for url in URLS[variant]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Lopanalys"})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        _report_bytes(progress_cb, done, total)
            if total and done != total:      # the connection closed early: the reader just ends
                last_err = f"incomplete download ({done} of {total} bytes)"
                continue
            if not _sane(tmp, variant):
                last_err = "file too small"
                continue
            digest = sha256_of(tmp)
            pinned = KNOWN_SHA256.get(variant)
            if pinned and digest != pinned:
                raise ModelError(t("pose.error.model_corrupt", name=dest.name))
            os.replace(tmp, dest)
            dest.with_name(dest.name + ".sha256").write_text(digest + "\n", encoding="utf-8")
            return dest
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = str(getattr(e, "reason", e))
            log.warning("model download from %s failed: %s", url, last_err)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    raise ModelError(t("pose.error.model_missing", reason=last_err or "?", name=dest.name,
                       folder=str(dest.parent)))


def ensure_model(variant: str = "full", progress_cb: ProgressCb | None = None) -> Path:
    """Path to an intact ``pose_landmarker_<variant>.task``, downloading it if needed.

    ``variant``: "lite" (fastest), "full" (default, real time on a normal laptop) or "heavy" (most
    accurate, for offline analysis). Raises ModelError with a coach-readable message.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown model variant {variant!r}")
    found = find_model(variant)
    if found:
        return found
    dest = paths.models_dir() / filename(variant)
    if dest.exists():   # present but failed its checksum
        try:
            dest.unlink()
        except OSError:
            raise ModelError(t("pose.error.model_corrupt", name=dest.name))
    return download(variant, dest, progress_cb)
