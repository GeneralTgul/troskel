"""Keeps a password out of the database in plain text: the SMTP app password.

What is stored in the settings table is ``{"scheme": ..., "data": base64}``:

- **Windows** (``"dpapi"``): the Windows data protection API (``CryptProtectData``) through ctypes.
  The key belongs to the Windows account, so the value can only be read by the same user on the
  same PC. A database copied or restored on another PC cannot give the password away: the coach
  types it again there. No extra package, so nothing for PyInstaller to miss.
- **Elsewhere** (``"keyfile"``, the development machine): AES-256-GCM with a random key in a file
  outside the data folder (``~/.config/lopanalys/secret.key``, or ``LOPANALYS_KEY_DIR``), readable
  by the user only.

``reveal()`` returns None when the value cannot be read (another PC, another account, a lost key):
the caller then asks for the password again. Nothing here logs a secret.
"""
from __future__ import annotations

import base64
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ENTROPY = b"lopanalys.mail"       # binds the value to this use; not a secret


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes) -> Any:
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32   # type: ignore[attr-defined]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    src, _keep1 = blob(data)
    ent, _keep2 = blob(_ENTROPY)
    out = Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.restype = wintypes.BOOL
    ui_forbidden = 0x01
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, ui_forbidden, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _key_file() -> Path:
    folder = os.environ.get("LOPANALYS_KEY_DIR")
    return (Path(folder) if folder else Path.home() / ".config" / "lopanalys") / "secret.key"


def _key(create: bool) -> bytes | None:
    f = _key_file()
    try:
        key = f.read_bytes()
        if len(key) == 32:
            return key
    except OSError:
        pass
    if not create:
        return None
    key = os.urandom(32)
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(key)
    return key


def protect(text: str) -> dict[str, str]:
    """The secret in the form that is safe to store. Raises OSError when it cannot be protected."""
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        return {"scheme": "dpapi", "data": base64.b64encode(_dpapi(raw, True)).decode("ascii")}
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    key = _key(create=True)
    assert key is not None
    return {"scheme": "keyfile", "data": base64.b64encode(nonce + AESGCM(key).encrypt(nonce, raw, _ENTROPY)).decode("ascii")}


def reveal(stored: Any) -> str | None:
    """The secret back, or None when there is none or it cannot be read on this PC."""
    if not isinstance(stored, dict) or not stored.get("data"):
        return None
    try:
        raw = base64.b64decode(stored["data"])
        if stored.get("scheme") == "dpapi" and sys.platform == "win32":
            return _dpapi(raw, False).decode("utf-8")
        if stored.get("scheme") == "keyfile":
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = _key(create=False)
            if key is None:
                return None
            return AESGCM(key).decrypt(raw[:12], raw[12:], _ENTROPY).decode("utf-8")
    except Exception:  # noqa: BLE001 - another PC or account, a damaged value: ask again
        log.warning("a stored secret could not be read on this PC")
    return None
