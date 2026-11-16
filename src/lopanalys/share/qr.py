"""A QR code of a text as rows of dark and light modules, with the quiet zone around it. No Qt.

segno (BSD-3-Clause, pure Python) builds the code. The athlete's app (app/src/views/Qr.tsx) reads it with the
phone's camera. The largest QR code (version 40) holds 2 953 bytes at the lowest error correction, so a plan link
up to about 2 900 characters fits. A longer one goes by mail or as a ``.troskel`` file.
"""
from __future__ import annotations

QR_MAX = 2953      # bytes in a version 40 code, error correction L
BORDER = 4         # the quiet zone in modules, as the standard asks


def matrix(text: str) -> list[list[bool]] | None:
    """The code's modules, row by row (True: dark), quiet zone included; None when ``text`` does not fit."""
    import segno   # lazy: only the dialog and the tests need it
    try:
        q = segno.make_qr(text, error="l", boost_error=True, mode="byte", encoding="utf-8")
    except segno.DataOverflowError:
        return None
    return [[bool(m) for m in row] for row in q.matrix_iter(scale=1, border=BORDER)]


def fits(text: str) -> bool:
    return len(text.encode("utf-8")) <= QR_MAX


__all__ = ["BORDER", "QR_MAX", "fits", "matrix"]
