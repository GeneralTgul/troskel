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


def test_every_test_kind_has_a_name_in_the_history_list():
    """Before 2026-11-22 a bike fit and a double poling test stood as "ui.testkind.bikefit" and
    "ui.testkind.ski" in Historik (the list and the filter): the keys were missing."""
    from lopanalys.core.models import TestKind
    for lang in ("sv", "en"):
        texts = _load(lang)
        missing = [k.value for k in TestKind if not texts.get(f"ui.testkind.{k.value}")]
        assert not missing, (lang, missing)
