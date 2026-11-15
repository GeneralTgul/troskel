"""The plan as a calendar file (iCalendar, RFC 5545) that a phone or a calendar program imports.

One VEVENT per workout. A slot with a start time gives an event in floating local time (it shows at
that clock time wherever the phone is); without one the workout is an all-day event. The UID is the
workout's stable key, so importing a changed plan updates the events instead of doubling them.
The description carries the targets resolved from the athlete's latest test at the time of export.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lopanalys.core.i18n import t

from .models import Plan, Workout
from .text import duration, plain, workout_lines

PRODID = "-//Lopanalys//Training plan//EN"
UID_DOMAIN = "lopanalys"


def escape(text: str) -> str:
    """TEXT value escaping (RFC 5545 section 3.3.11)."""
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold a content line at 75 octets without splitting a UTF-8 character (section 3.1)."""
    out, cur, size = [], "", 0
    for ch in line:
        b = len(ch.encode("utf-8"))
        if size + b > (75 if not out else 74):   # continuation lines start with one space
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += b
    out.append(cur)
    return "\r\n ".join(out)


def _description(w: Workout, plan: Plan) -> str:
    lines = [w.purpose, ""] if not w.goal else []
    if w.goal:
        g = w.goal
        lines.append(" · ".join(x for x in (t("plan.goal.distance", km=f"{g.distance_km:g}") if g.distance_km else "",
                                             g.target) if x) or t("plan.workout.race.purpose"))
    else:
        cap = plan.capacity_on(w.sport, w.date)
        lines += [plain(line) for line in workout_lines(w, cap, plan.zone_system)]
        lines += ["", w.how]
    return "\n".join(lines).strip()


def _event(w: Workout, plan: Plan, stamp: str) -> list[str]:
    summary = w.title if w.goal or not w.minutes else f"{w.title} · {duration(w.seconds)}"
    out = ["BEGIN:VEVENT", f"UID:{w.key}@{UID_DOMAIN}", f"DTSTAMP:{stamp}"]
    if w.start is not None and w.seconds:
        begin = datetime.combine(w.date, w.start)
        out += [f"DTSTART:{begin:%Y%m%dT%H%M%S}", f"DTEND:{begin + timedelta(seconds=w.seconds):%Y%m%dT%H%M%S}"]
    else:
        out += [f"DTSTART;VALUE=DATE:{w.date:%Y%m%d}", f"DTEND;VALUE=DATE:{w.date + timedelta(days=1):%Y%m%d}"]
    out += [f"SUMMARY:{escape(summary)}", f"DESCRIPTION:{escape(_description(w, plan))}",
            f"CATEGORIES:{escape(t('plan.ics.category'))}", "TRANSP:OPAQUE" if w.start else "TRANSP:TRANSPARENT",
            "END:VEVENT"]
    return out


def to_ics(plan: Plan) -> str:
    """The whole plan as iCalendar text with CRLF line ends. Write it as UTF-8 bytes to a .ics file."""
    stamp = plan.created.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = t("plan.ics.name", name=plan.request.athlete_name)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{escape(name)}"]
    for w in plan.workouts:
        lines += _event(w, plan, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def write_ics(plan: Plan, path: str) -> None:
    with open(path, "wb") as f:
        f.write(to_ics(plan).encode("utf-8"))
