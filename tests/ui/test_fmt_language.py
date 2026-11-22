"""Numbers in the interface follow the language, as in the report and the texts from the analysis.

Before 2026-11-22 ``ui.fmt.num`` always wrote the Swedish decimal comma, so the English interface
showed "LT2 13,4 km/h" beside analysis texts with "13.4"."""
from __future__ import annotations

from lopanalys.core import i18n
from lopanalys.ui import fmt


def test_decimal_sign_follows_the_language():
    i18n.set_language("en")
    try:
        assert fmt.num(12.5) == "12.5"
        assert fmt.load(12.0) == "12.0 km/h"
        assert fmt.speed(12.0) == "5:00 min/km (12.0 km/h)"
        assert fmt.km(2350) == "2.35 km"
    finally:
        i18n.set_language("sv")
    assert fmt.num(12.5) == "12,5"
    assert fmt.speed(12.0) == "5:00 min/km (12,0 km/h)"
    assert fmt.num(None) == "–" and fmt.num(181.4, 0) == "181"
