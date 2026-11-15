import json
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "lopanalys"


def _load(lang):
    return json.loads((SRC / "i18n" / f"ui.{lang}.json").read_text(encoding="utf-8"))


def test_sv_and_en_have_the_same_keys():
    assert set(_load("sv")) == set(_load("en"))


def test_every_literal_ui_key_exists():
    sv = _load("sv")
    used = set()
    for p in (SRC / "ui").rglob("*.py"):
        used |= set(re.findall(r'"(ui\.[a-z0-9_.]+)"', p.read_text(encoding="utf-8")))
    missing = sorted(k for k in used if k not in sv and not k.endswith("."))
    assert not missing, missing


def test_placeholders_match():
    sv, en = _load("sv"), _load("en")
    for k, v in sv.items():
        assert set(re.findall(r"{(\w+)}", v)) == set(re.findall(r"{(\w+)}", en[k])), k
