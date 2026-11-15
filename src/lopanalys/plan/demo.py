"""An example plan from demo data, as PDF, calendar file and a picture of the first page.

    python -m lopanalys.plan.demo --weeks 12 --out exempel.pdf
    python -m lopanalys.plan.demo --weeks 26 --no-goal --out halvar.pdf --ics halvar.ics --lang en

The athlete is the demo runner of the test report, never a real person. The PDF step needs Qt
(report.pdf); on a Linux box without a display: ``source scripts/headless_env.sh`` first.
"""
from __future__ import annotations

import argparse
from datetime import date, time, timedelta
from pathlib import Path

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.i18n import set_language, t

from . import ics
from .engine import build_plan
from .models import RUNNING, Capacity, Goal, Plan, PlanRequest, Slot
from .periodize import first_monday

TEST_DATE = date(2026, 11, 13)
START = date(2026, 11, 16)


def demo_capacity() -> Capacity:
    """The demo runner's lactate test: LT1 13.2 km/h at 165 bpm, LT2 14.4 km/h at 174 bpm, max HR 192."""
    return Capacity(RUNNING, Anchors(max_hr=192, resting_hr=46, lt1_hr=165, lt2_hr=174, lt1_speed_kmh=13.2,
                                     lt2_speed_kmh=14.4), TEST_DATE, t("plan.demo.test"))


def demo_request(weeks: int = 12, *, goal: bool = True, level: str = "recreational", start: date = START,
                 name: str = "Exempel Löpare") -> PlanRequest:
    """Five slots a week (Monday, Tuesday, Thursday, Saturday, Sunday). With a goal: a half marathon on
    the Saturday of the last week, and a 10 km seeding race five weeks before it in plans of 10 weeks or more."""
    slots = (Slot(0, 45, start=time(18, 0)), Slot(1, 75, start=time(18, 0)), Slot(3, 75, start=time(18, 0)),
             Slot(5, 60, start=time(9, 0)), Slot(6, 120, start=time(9, 0)))
    goals: tuple[Goal, ...] = ()
    if goal:
        race = first_monday(start) + timedelta(weeks=weeks - 1, days=5)
        goals = (Goal(t("plan.demo.goal"), race, distance_km=21.1, target=t("plan.demo.goal_target")),)
        if weeks >= 10:
            goals += (Goal(t("plan.demo.milestone"), race - timedelta(weeks=5), distance_km=10, priority="B"),)
    return PlanRequest(name, start, slots, (demo_capacity(),), goals=goals, duration_weeks=None if goal else weeks,
                       level=level, recent_weekly_minutes={"beginner": None, "recreational": 200, "trained": 320}[level])


def demo_plan(weeks: int = 12, **kw) -> Plan:
    return build_plan(demo_request(weeks, **kw), plan_id="demo")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--weeks", type=int, default=12)
    ap.add_argument("--no-goal", action="store_true", help="a plan of a fixed length that ends with a test")
    ap.add_argument("--level", choices=("beginner", "recreational", "trained"), default="recreational")
    ap.add_argument("--lang", choices=("sv", "en"), default="sv")
    ap.add_argument("--out", type=Path, default=Path("exempel.pdf"), help="the PDF")
    ap.add_argument("--ics", type=Path, help="the calendar file (default: beside the PDF)")
    ap.add_argument("--png", type=Path, help="also save the first page as a picture")
    ap.add_argument("--html", type=Path, help="also save the HTML the PDF is made from")
    args = ap.parse_args(argv)

    set_language(args.lang)
    plan = demo_plan(args.weeks, goal=not args.no_goal, level=args.level)
    ics_path = args.ics or args.out.with_suffix(".ics")
    ics_path.parent.mkdir(parents=True, exist_ok=True)
    ics.write_ics(plan, str(ics_path))
    print(t("plan.demo.done", path=ics_path))

    from lopanalys.report import pdf                       # Qt: only here
    from lopanalys.report.builder import Branding
    from lopanalys.report.plan_builder import render_plan_html

    html = render_plan_html(plan, Branding(business_name="Nordisk Löpcoaching", coach_name="Anna Berg",
                                           contact="070-174 06 50 · anna@exempel.se"))
    if args.html:
        args.html.write_text(html, encoding="utf-8")
    out = pdf.html_to_pdf(html, args.out)
    print(t("plan.demo.done", path=out))
    if args.png:
        pdf.pdf_page_images(out, dpi=150)[0].save(str(args.png))
        print(t("plan.demo.done", path=args.png))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
