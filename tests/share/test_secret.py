"""The stored SMTP password is never plain text."""
import json
import sys

import pytest

from lopanalys.share import secret


def test_round_trip_and_nothing_plain(tmp_path):
    stored = secret.protect("hemligt-applösenord 16")
    assert secret.reveal(stored) == "hemligt-applösenord 16"
    text = json.dumps(stored)
    assert "hemligt" not in text and stored["scheme"] in ("keyfile", "dpapi")
    assert secret.protect("hemligt-applösenord 16") != stored            # a fresh nonce every time
    if stored["scheme"] == "keyfile":                                    # Windows uses DPAPI: no key file
        key = tmp_path / "keys" / "secret.key"
        assert key.is_file() and (key.stat().st_mode & 0o077) == 0       # the key: the user only


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses DPAPI, bound to the account: no key file")
def test_unreadable_gives_none(tmp_path):
    stored = secret.protect("abc")
    (tmp_path / "keys" / "secret.key").unlink()                          # another PC: the key is not there
    assert secret.reveal(stored) is None
    secret.protect("x")                                                  # a new key cannot read the old value
    assert secret.reveal(stored) is None
    assert secret.reveal(None) is None and secret.reveal({}) is None and secret.reveal("abc") is None
    assert secret.reveal({"scheme": "keyfile", "data": "inte base64!"}) is None
