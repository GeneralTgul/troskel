"""The athlete's own values (#777): thresholds, max HR, FTP and CSS typed into the app without a test, stored as a
capacity with ``source`` ``"self"`` (models.SELF). They give targets as a test does, a test replaces them in its sport,
and the follow-up of a test never compares with them."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language
from lopanalys.plan import RUNNING, Capacity, build_plan, codec, link, resolve
from lopanalys.plan import expected as ex
from lopanalys.plan.models import SELF
from lopanalys.report import plan_builder

from . import cases as c

OWN_RUN = Capacity(RUNNING, Anchors(190, 50, 160, 175, 11.5, 13.5), None, SELF)
OWN_BIKE = Capacity("bike", Anchors(180, None, None, None, None, 250.0, sport="bike"), None, SELF)
OWN_SKI = Capacity("ski", Anchors(185, None, 150, 170, sport="ski"), None, SELF)
OWN_SWIM = Capacity("swim", None, None, SELF, css_s100=110.0)


def test_own_values_are_marked_and_survive_the_codec_and_the_link():
    assert OWN_RUN.own_values and not c.TESTED.own_values
    for cap in (OWN_RUN, OWN_BIKE, OWN_SKI, OWN_SWIM):
        assert codec.capacity_from_dict(codec.capacity_to_dict(cap)) == cap
    plan = build_plan(c.request(capacity=(OWN_RUN,), weeks=8), plan_id="app-own")
    stored = link.stored_plan(plan)
    assert stored["request"]["capacity"][0]["source"] == "self"
    assert link.expand(link.link_payload(stored))["request"] == stored["request"]


def test_own_values_give_targets_as_a_test_does():
    own = resolve.targets(OWN_RUN)
    measured = resolve.targets(replace(OWN_RUN, tested=date(2026, 11, 1), source="Laktattrappa"))
    assert own == measured and own["Z3"].hr_low == 160 and own["Z4"].speed_high_kmh == 13.5
    # FTP and a max HR: zones from the max HR, and the sweet spot in watt from the FTP.
    assert resolve.band_targets(OWN_BIKE)["sweetspot"].power_text == "220–235 W"
    assert resolve.targets(OWN_SWIM)["Z4"].swim_low_s == 113.0


def test_a_test_replaces_own_values_in_its_sport_and_in_the_sport_that_borrows_it():
    plan = build_plan(c.request(capacity=(OWN_RUN, OWN_BIKE, OWN_SKI), weeks=8, roles=c.FOUR,
                                slots=c.for_sports(c.SEVEN, RUNNING, "bike", "ski", "swim")), plan_id="app-own")
    after = resolve.retest(plan, c.TESTED, from_date=date(2026, 12, 1))
    assert after.request.own_capacity(RUNNING) == c.TESTED
    assert after.request.own_capacity("bike") == OWN_BIKE                  # the other sports keep their own values
    assert after.capacity_on(RUNNING, date(2026, 11, 30)) == OWN_RUN        # earlier workouts keep their targets
    erg = resolve.retest(plan, c.SKIERG_TEST)
    assert erg.request.own_capacity("ski") is None and erg.request.capacity_for("ski") == c.SKIERG_TEST
    # A measured test in a borrowing sport is not the athlete's: only own values give way.
    measured_ski = replace(OWN_SKI, source="Laktattrappa", tested=date(2026, 11, 1))
    kept = resolve.retest(replace(plan, request=replace(plan.request, capacity=(measured_ski,))), c.SKIERG_TEST)
    assert kept.request.own_capacity("ski") == measured_ski


def test_the_printout_says_own_values():
    set_language("sv")
    assert plan_builder._basis(OWN_RUN) == "Egna värden"
    assert plan_builder._basis(OWN_SWIM) == "Egna värden"
    assert plan_builder._basis(Capacity(RUNNING, None, None, SELF)) == "Inget test"
    assert plan_builder._basis(c.TESTED).startswith("Laktattrappa")


def test_the_follow_up_does_not_read_the_plan_s_values():
    """*Mot schemat* takes the days of two tests in Tröskel: a plan on own values gives the same follow-up as one on
    a test, so a test is never measured against the athlete's own numbers."""
    tested = build_plan(c.request(weeks=12), plan_id="p7", today=c.START)
    own = replace(tested, request=replace(tested.request, capacity=(OWN_RUN,)))
    a = ex.followup(tested, RUNNING, date(2026, 11, 13), date(2026, 12, 14))
    b = ex.followup(own, RUNNING, date(2026, 11, 13), date(2026, 12, 14))
    assert a is not None and a.to_dict() == b.to_dict()
