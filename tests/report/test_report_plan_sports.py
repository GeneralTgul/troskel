"""The printed training plan with several sports (#666, step 4): one colour per sport, the sports table on
the cover, the bike's and the SkiErg's targets under *Dina zoner*, the week's time per sport, the workouts
of a day in clock order and the week's load."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import time
from html import unescape

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.core.zone_colors import GARMIN, SPORT_COLORS, sport_color
from lopanalys.plan import build_plan
from lopanalys.plan.models import PLAN_SPORTS
from lopanalys.report.plan_builder import _compact, render_plan_html

from plan import cases as c


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _text(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<style>.*?</style>", "", html, flags=re.S))))


def _cover(html: str) -> str:
    return html[:html.index('<section class="section">')]


@pytest.fixture(scope="module")
def plans():
    set_language("sv")
    return {k: build_plan(c.CASES[k]) for k in ("half_12", "run_bike_12", "four_sports_marathon", "bike_skierg_tested",
                                                 "bike_alone_26", "ski_main")}


def test_every_sport_has_its_own_colour_and_none_is_a_zone_colour():
    colours = [sport_color(sp) for sp in (*PLAN_SPORTS, "strength")]
    assert len(set(colours)) == len(colours)
    assert not set(colours) & {x.upper() for x in GARMIN} and not set(colours) & set(GARMIN)
    assert set(SPORT_COLORS) >= set(PLAN_SPORTS)


def test_one_sport_has_no_sports_table_and_no_sport_bar(plans):
    html = render_plan_html(plans["half_12"])
    text = _text(html)
    assert 'class="plan-sports"' not in html and "Veckans tid per idrott" not in text
    assert 'class="kind-plan"' in html and "Bygger på" in text      # the basis stays a fact of its own
    assert "Belastning" in text                                       # the load is on every week page


def test_sports_table_with_share_role_and_test(plans):
    cover = _text(_cover(render_plan_html(plans["four_sports_marathon"])))
    # shares are weights: 60/30/20/10 of 120
    assert "Löpning Huvudidrott 50 % Laktattrappa · 13 november 2026" in cover
    assert "Cykel Stödjande 25 % Inget test" in cover
    assert "Skidor Stödjande 17 % Inget test" in cover                # the SkiErg has no test to lend
    assert "Simning Underhåll 8 % Inget test: upplevd ansträngning" in cover
    assert "Bygger på" not in cover                                   # said per sport in the table
    cover = _text(_cover(render_plan_html(plans["run_bike_12"])))
    assert "Löpning Huvudidrott 67 %" in cover and "Cykel Stödjande 33 % Inget test" in cover


def test_sports_table_shows_each_sport_s_own_test(plans):
    html = render_plan_html(plans["bike_skierg_tested"])
    cover = _text(_cover(html))
    assert "Cykel Stödjande 27 % Laktattrappa · 1 november 2026" in cover
    assert "SkiErg Stödjande 18 % Laktattrappa · 1 november 2026" in cover
    assert f'background: {sport_color("bike")}' in _cover(html) and 'class="kind-plan multi"' in html


def test_your_zones_give_watt_and_time_per_500_m(plans):
    html = _cover(render_plan_html(plans["bike_skierg_tested"]))
    text = _text(html)
    zones = text[text.index("Dina zoner"):]
    assert zones.startswith("Dina zoner Löpning · ")
    bike = zones[zones.index("Cykel"):zones.index("SkiErg")]
    assert "140–150 180–210 W" in bike and "< 180 W" in bike and "> 240 W" in bike
    erg = zones[zones.index("SkiErg"):]
    assert "160–185 W · 2:10–2:04" in erg
    assert "< 160 W · > 2:10" in erg                                  # the easier end is the slower split
    assert html.count('class="cz-sport"') == 2


def test_the_main_sport_on_the_bike_shows_watt_in_the_zones(plans):
    text = _text(_cover(render_plan_html(plans["bike_alone_26"])))
    zones = text[text.index("Dina zoner"):]
    assert "180–210 W" in zones and "min/km" not in zones and 'class="cz-sport"' not in zones


def test_no_sport_rows_without_a_test_in_watt(plans):
    html = _cover(render_plan_html(plans["four_sports_marathon"]))
    assert 'class="cz-sport"' not in html


def test_week_page_has_the_time_per_sport_and_the_load(plans):
    plan = plans["four_sports_marathon"]
    html = render_plan_html(plan)
    text = _text(html)
    wk = plan.weeks[0]
    sm = wk.sport_minutes()
    assert len(sm) >= 3
    first = text[text.index("Vecka 1 "):text.index("Vecka 2 ")]
    tail = first[first.index("Veckans tid per idrott"):]
    names = {"run": "Löpning", "bike": "Cykel", "ski": "Skidor", "swim": "Simning"}
    for sp, m in sm.items():
        assert f"{names[sp]} {m} min" in tail
    assert f"Belastning {round(wk.load)} " in tail
    order = [names[sp] for sp, _ in sorted(sm.items(), key=lambda x: -x[1])]
    assert [n for n in sorted(order, key=tail.index)] == order       # the largest share first


def test_workout_titles_carry_the_sport_colour(plans):
    html = render_plan_html(plans["run_bike_12"])
    week = html[html.index('<section class="week">'):]
    for sp in ("run", "bike"):
        assert f'<div class="w-title"><i style="background: {sport_color(sp)}">' in week
    one = render_plan_html(plans["half_12"])
    assert sport_color("run") not in one[one.index('<section class="week">'):]   # one sport: the zone colour


def test_a_day_s_workouts_in_clock_order(plans):
    plan = plans["run_bike_12"]
    wk = plan.weeks[0]
    w = wk.workouts[0]
    late = replace(w, key=w.key + "-b", start=time(19), template_id="recovery")
    early = replace(w, key=w.key + "-a", start=time(6, 30))
    wk.workouts[:] = [late, *wk.workouts[1:], early]
    try:
        text = _text(render_plan_html(plan))
        first = text[text.index("Vecka 1 "):text.index("Vecka 2 ")]
        assert "06:30" in first and first.index("06:30") < first.index("19:00")
    finally:
        wk.workouts[:] = [w, *wk.workouts[1:-1]]


def test_compact_targets_for_the_narrow_cells():
    from lopanalys.plan.resolve import Target
    erg = Target("Z2", "Lätt", 113, 150, power_high_w=160, sport="skierg")
    assert _compact(erg) == {"hr": "113–150", "load": "< 160 W · > 2:10"}
    bike = Target("Z5", "Max", 160, 185, power_low_w=240, sport="bike")
    assert _compact(bike) == {"hr": "160–185", "load": "> 240 W"}


def test_english_sports_table(plans):
    set_language("en")
    text = _text(_cover(render_plan_html(plans["ski_main"])))
    assert "Main sport" in text and "Supporting" in text and "Maintenance" in text
    assert "Borrows the zones of SkiErg" in text
