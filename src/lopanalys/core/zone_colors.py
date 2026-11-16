"""Zone colours as on a Garmin watch: grey, blue, green, orange, red.

One place for the programme, the TV and the report, so the printout matches the watch.
"""
from __future__ import annotations

GARMIN = ["#9AA3AD", "#2F80ED", "#2EAD4B", "#F2994A", "#E5484D"]   # zone 1..5


def zone_color(i: int, n: int = 5) -> str:
    """Colour of zone ``i`` (0-based) in a system with ``n`` zones.

    Five zones map one to one. Other systems are spread over the five colours by position, so the
    easiest zone is always grey and the hardest red (3 zones: grey, green, red; 7 zones: Friel).
    """
    if n <= 1:
        return GARMIN[0]
    if n == len(GARMIN):
        return GARMIN[max(0, min(i, 4))]
    return GARMIN[round(max(0, min(i, n - 1)) * (len(GARMIN) - 1) / (n - 1))]

# One colour per sport in a plan with several sports (#666, step 4): none of them is a zone colour, so a
# workout's sport and its zones can be told apart on the same line.
SPORT_COLORS = {"run": "#7E57C2", "bike": "#C2185B", "skierg": "#8D6E63", "ski": "#37474F", "swim": "#00ACC1",
                "strength": "#827717"}
OTHER_SPORT = "#607D8B"


def sport_color(sport: str) -> str:
    """The colour of a sport in the plan's printout and form."""
    return SPORT_COLORS.get(sport, OTHER_SPORT)
