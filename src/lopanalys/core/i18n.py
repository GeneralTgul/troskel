"""Translations. Swedish is the source language; English ships from the start.

Each module owns its own files, so parallel work never collides:
    src/lopanalys/i18n/<module>.sv.json   {"key": "Svensk text"}
    src/lopanalys/i18n/<module>.en.json   {"key": "English text"}
Use: from lopanalys.core.i18n import t;  t("report.lactate.title")
The first dotted part of the key is the module (file) name. A missing key returns the
Swedish text, then the key itself, so a missing translation never crashes the app.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "i18n"
_lang = "sv"


def set_language(lang: str) -> None:
    global _lang
    _lang = lang
    _load.cache_clear()


def language() -> str:
    return _lang


@lru_cache(maxsize=None)
def _load(module: str, lang: str) -> dict[str, str]:
    p = _DIR / f"{module}.{lang}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def t(key: str, **fmt: object) -> str:
    module = key.split(".", 1)[0]
    text = _load(module, _lang).get(key) or _load(module, "sv").get(key) or key
    return text.format(**fmt) if fmt else text
