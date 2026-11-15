from lopanalys.assessments.protocol import Protocol, Stage
from lopanalys.core.bus import Bus
from lopanalys.core.i18n import t


def test_protocol_roundtrip():
    p = Protocol("x", "lactate", [Stage(8, 1, 240, sample_lactate=True)])
    assert Protocol.from_dict(p.to_dict()) == p


def test_bus_survives_bad_subscriber():
    b, got = Bus(), []
    b.subscribe("a", lambda _: 1 / 0)
    b.subscribe("a", got.append)
    b.publish("a", 5)
    assert got == [5]


def test_missing_translation_returns_key():
    assert t("nomodule.nokey") == "nomodule.nokey"
