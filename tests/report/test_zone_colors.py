"""The zones have the Garmin watch's colours: grey, blue, green, orange, red."""
from lopanalys.core.zone_colors import GARMIN, zone_color


def test_five_zones_are_the_watch_colours():
    assert [zone_color(i, 5) for i in range(5)] == GARMIN


def test_other_systems_start_grey_and_end_red():
    for n in (3, 4, 7):
        assert zone_color(0, n) == GARMIN[0]
        assert zone_color(n - 1, n) == GARMIN[-1]
