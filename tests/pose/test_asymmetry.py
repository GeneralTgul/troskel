"""Left/right difference: no 200 % from opposite signs, and in a side view only the contact time."""
from types import SimpleNamespace

from lopanalys.pose.metrics import asymmetry
from lopanalys.pose.types import Side


def _steps(key, left, right, n=6):
    out = []
    for i in range(n):
        side, v = (Side.LEFT, left) if i % 2 == 0 else (Side.RIGHT, right)
        st = SimpleNamespace(side=side, **{k: None for k in (
            "ground_contact_ms", "overstride_cm", "shin_angle_deg", "knee_flex_contact_deg",
            "knee_flex_midstance_deg", "hip_extension_toe_off_deg", "pelvic_drop_deg",
            "knee_valgus_deg", "vertical_oscillation_cm")})
        setattr(st, key, v)
        out.append(st)
    return out


def test_opposite_signs_give_no_percentage():
    assert "overstride_cm" not in asymmetry(_steps("overstride_cm", 10.0, -10.0))


def test_side_view_compares_only_contact_time():
    assert asymmetry(_steps("knee_flex_midstance_deg", 40.0, 30.0), side_view=True) == {}
    assert "ground_contact_ms" in asymmetry(_steps("ground_contact_ms", 260.0, 240.0), side_view=True)


def test_ordinary_difference_is_kept():
    assert asymmetry(_steps("ground_contact_ms", 260.0, 240.0))["ground_contact_ms"] == 8.0
