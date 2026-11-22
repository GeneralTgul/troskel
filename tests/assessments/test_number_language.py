"""Numbers in the texts of the tests follow the language: a decimal comma in Swedish, a point in English."""
import pytest

from lopanalys.assessments import library
from lopanalys.assessments.compare import compare_lactate
from lopanalys.assessments.lactate import analyze
from lopanalys.core.i18n import set_language
from lopanalys.core.models import StepRecord


@pytest.fixture(autouse=True)
def _back_to_swedish():
    yield
    set_language("sv")


def _test(shift=0.0, noise=0.0):
    rows = [(9.0, 1.1), (10.0, 1.2), (11.0, 1.6 + noise), (12.0, 2.3 - noise), (13.0, 3.6 + noise), (14.0, 6.0)]
    return analyze([StepRecord(i + 1, v + shift, 1.0, 240, 130 + 8 * i, lactate_mmol=la)
                    for i, (v, la) in enumerate(rows)], max_hr=190)


@pytest.mark.parametrize("lang, sign, other", [("sv", ",", "."), ("en", ".", ",")])
def test_speeds_and_shares_use_the_decimal_sign_of_the_language(lang, sign, other):
    set_language(lang)
    assert library.fmt_kmh(12.5) == f"12{sign}5"
    assert library.fmt_pct(1.5) == f"1{sign}5"
    assert library.fmt_pct(2.0) == "2"
    desc = library.lactate_step(start_kmh=9.5, step_kmh=1.5).description
    assert f"9{sign}5" in desc and f"1{sign}5" in desc and f"9{other}5" not in desc


@pytest.mark.parametrize("lang, sign, other", [("sv", ",", "."), ("en", ".", ",")])
def test_the_comparison_with_the_last_test_uses_the_decimal_sign_of_the_language(lang, sign, other):
    set_language(lang)
    summary = compare_lactate(_test(shift=0.5), _test()).summary
    assert f"+0{sign}5" in summary and f"+0{other}5" not in summary


def test_the_poor_fit_warning_has_a_decimal_point_in_english():
    set_language("en")
    res = _test(noise=1.2)
    text = next(w.text for w in res.warnings if w.code == "poor_fit")
    assert f"{res.r2:.2f}" in text
    set_language("sv")
    text = next(w.text for w in _test(noise=1.2).warnings if w.code == "poor_fit")
    assert f"{res.r2:.2f}".replace(".", ",") in text
