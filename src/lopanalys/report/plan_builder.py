"""The printed training plan: a Plan in, self-contained HTML out, in the test report's identity.

Page 1 is the overview (goal, blocks, every week as a bar in the watch's zone colours, the zones),
page 2 explains how to read the plan, then one page per week with a row per day to tick off.
The HTML goes through report.pdf like the test report. Targets are resolved from the athlete's
latest test when the page is rendered (plan.resolve), never stored.
"""
from __future__ import annotations

from datetime import date, timedelta
from html import escape
from typing import Any

from markupsafe import Markup

from lopanalys.core.i18n import language, set_language, t
from lopanalys.core.zone_colors import zone_color
from lopanalys.plan import library
from lopanalys.plan.models import WEEKDAY_KEYS, ZONES, Plan, Week
from lopanalys.plan.periodize import core_slots
from lopanalys.plan.resolve import targets
from lopanalys.plan.text import duration, hours, progress_axis, volume_start, workout_lines

from .builder import ASSETS, Branding, _css_str, _data_uri_image, _env, _footer_line, _safe_color, date_long
from .charts import INK, MUTED, RULE, fmt_num

# Blocks are phases in order, so they share one hue from light to dark; the taper is neutral.
BLOCK_FILL = {"base": "#CDEBE7", "build": "#8FD3CB", "peak": "#3DB5A9", "taper": "#DDE3E9"}
UNZONED = "#C3CBD3"          # the test itself: no zone
# The progression panel under the bars: the long run in the colour of its zone (Z2), the hard minutes in Z5's.
LINE_LONGEST, LINE_HARD = zone_color(1), zone_color(4)
GUIDE_ORDER = ("easy", "recovery", "long", "long_progressive", "fartlek", "hills", "threshold", "vo2max", "opener", "test")


def _day(d: date) -> str:
    return t("plan.fmt.day", day=d.day, month=t(f"plan.month.{d.month}"))


def _span(a: date, b: date) -> str:
    return f"{_day(a)} – {_day(b)}"


def _weekday(d: date | int) -> str:
    return t(f"plan.weekday.{WEEKDAY_KEYS[d if isinstance(d, int) else d.weekday()]}")


def _week_marker(week: Week, goal_date: date | None) -> str:
    """The word under a week in the overview: a race beats a test beats an easy week."""
    races = [w.goal for w in week.workouts if w.goal]
    if races:
        final = any(g.date == goal_date and g.priority == "A" for g in races)
        return t("plan.pdf.mark.goal" if final else "plan.pdf.mark.race")
    return {"test": t("plan.pdf.mark.test"), "easy": t("plan.pdf.mark.easy")}.get(week.kind, "")


def overview_svg(plan: Plan) -> str:
    """Blocks as a band and under it one stacked bar per week: minutes per zone in the watch's colours.
    Below the bars a panel of its own (its own minute axis, not a second axis on the bars) with two lines:
    the longest run of each week and the minutes in zones 3-5, so the progression shows even when the
    bars are as tall as each other.

    One time axis for all, so the eye reads which block a week belongs to. Units are 0.1 mm.
    """
    weeks, n = plan.weeks, len(plan.weeks)
    W, left, right = 1780, 78, 4
    col = (W - left - right) / n
    bar_w = min(col * 0.56, 84)
    band_y, band_h = 0, 62
    dense = n > 16
    top, H = (100 if dense else 122), 220     # dense: no totals above the bars
    bottom = top + H
    goal_date = plan.goal.date if plan.goal else None

    stacks = []
    for wk in weeks:
        zm = wk.zone_minutes()
        segs = [(z, zm.get(z, 0.0)) for z in ZONES]
        segs.append(("", max(0.0, wk.minutes - sum(zm.values()))))
        stacks.append([(z, m) for z, m in segs if m >= 1])
    peak = max([sum(m for _, m in s) for s in stacks] + [60])
    step = 60 if peak <= 6 * 60 else 120
    y_max = -(-peak // step) * step
    if y_max - peak < 0.12 * y_max:      # room for the totals above the bars
        y_max += step / 2

    def y(minutes: float) -> float:
        return bottom - H * minutes / y_max

    lines_top = bottom + 150                 # the progression panel: legend above, plot below
    lines_h = 120
    lines_bottom = lines_top + lines_h
    o: list[str] = [f'<svg class="plan-chart" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {lines_bottom + 6}" '
                    f'role="img" aria-label="{escape(t("plan.pdf.overview.title"))}">']
    # ---- block band
    for b in plan.blocks:
        x0 = left + (b.first_week - 1) * col + 3
        w = b.weeks * col - 6
        o.append(f'<rect x="{x0:.1f}" y="{band_y}" width="{w:.1f}" height="{band_h}" rx="9" fill="{BLOCK_FILL[b.kind]}"/>')
        label = t("plan.pdf.block_weeks", name=b.name, weeks=b.weeks)
        for text in (label, b.name, t(f"plan.block_short.{b.kind}")):
            if len(text) * 15 + 20 <= w:
                o.append(f'<text x="{x0 + w / 2:.1f}" y="{band_y + 40}" text-anchor="middle" font-size="27" '
                         f'font-weight="600" fill="{INK}">{escape(text)}</text>')
                break
    # ---- grid and y axis (hours)
    m = 0
    while m <= y_max:
        yy = y(m)
        o.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{W - right}" y2="{yy:.1f}" stroke="{INK if m == 0 else "#E8EDF1"}" '
                 f'stroke-width="{2.5 if m == 0 else 2}"/>')
        if m:
            o.append(f'<text x="{left - 14}" y="{yy + 9:.1f}" text-anchor="end" font-size="24" fill="{MUTED}">'
                     f'{t("plan.pdf.hours_axis", h=m // 60)}</text>')
        m += step
    # ---- bars
    gap = 3
    for i, (wk, segs) in enumerate(zip(weeks, stacks)):
        cx = left + (i + 0.5) * col
        x0 = cx - bar_w / 2
        total = sum(mm for _, mm in segs)
        y_top = y(total)
        if segs:
            rx = min(10, bar_w / 3)
            o.append(f'<clipPath id="pw{i}"><rect x="{x0:.1f}" y="{y_top:.1f}" width="{bar_w:.1f}" '
                     f'height="{bottom - y_top + rx:.1f}" rx="{rx:.1f}"/></clipPath><g clip-path="url(#pw{i})">')
            acc = 0.0
            for z, mm in segs:
                y1, y0 = y(acc + mm), y(acc)
                fill = zone_color(ZONES.index(z)) if z else UNZONED
                h = max(1.0, y0 - y1 - (gap if acc else 0))
                o.append(f'<rect x="{x0:.1f}" y="{y1:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{fill}"/>')
                acc += mm
            o.append("</g>")
        if not dense and total:
            o.append(f'<text x="{cx:.1f}" y="{y_top - 12:.1f}" text-anchor="middle" font-size="23" fill="{INK}">'
                     f'{hours(total)}</text>')
        if not dense or wk.index == 1 or wk.index % 4 == 0:
            o.append(f'<text x="{cx:.1f}" y="{bottom + 36}" text-anchor="middle" font-size="25" font-weight="600" '
                     f'fill="{INK}">{wk.index}</text>')
        mark = _week_marker(wk, goal_date)
        if mark and (not dense or wk.kind != "easy"):
            strong = wk.kind != "easy"
            o.append(f'<text x="{cx:.1f}" y="{bottom + 70}" text-anchor="middle" font-size="{19 if dense else 22}" '
                     f'font-weight="{700 if strong else 400}" fill="{INK if strong else MUTED}">'
                     f'{escape(mark[:1] if dense else mark)}</text>')
    o.append(f'<text x="{left - 14}" y="{bottom + 36}" text-anchor="end" font-size="22" fill="{MUTED}">'
             f'{escape(t("plan.pdf.week_axis"))}</text>')
    o += _progress_panel(plan, left, W - right, col, lines_top, lines_bottom)
    o.append("</svg>")
    return "".join(o)


def _progress_panel(plan: Plan, x0: float, x1: float, col: float, top: float, bottom: float) -> list[str]:
    """The longest run and the hard minutes per week as two lines with markers, each named in the
    legend above and labelled with its value at its first and its highest week."""
    weeks = plan.weeks
    series = [(t("plan.pdf.lines.longest"), LINE_LONGEST, [float(wk.longest_minutes) for wk in weeks]),
              (t("plan.pdf.lines.hard"), LINE_HARD, [wk.hard_minutes for wk in weeks])]
    y_max, step = progress_axis([v for _, _, vals in series for v in vals])

    def y(minutes: float) -> float:
        return bottom - (bottom - top) * minutes / y_max

    o: list[str] = [f'<g class="plan-lines" aria-label="{escape(t("plan.pdf.lines.title"))}">']
    lx = x0
    for name, colour, _ in series:          # the legend: a short line with its marker, then the name
        o.append(f'<line x1="{lx:.1f}" y1="{top - 40}" x2="{lx + 44:.1f}" y2="{top - 40}" stroke="{colour}" stroke-width="5"/>'
                 f'<circle cx="{lx + 22:.1f}" cy="{top - 40}" r="7" fill="{colour}" stroke="#fff" stroke-width="2.5"/>'
                 f'<text x="{lx + 56:.1f}" y="{top - 32}" font-size="24" fill="{INK}">{escape(name)}</text>')
        lx += 56 + len(name) * 13 + 60
    m = 0
    while m <= y_max:
        yy = y(m)
        o.append(f'<line x1="{x0}" y1="{yy:.1f}" x2="{x1}" y2="{yy:.1f}" stroke="{INK if m == 0 else "#E8EDF1"}" '
                 f'stroke-width="{2.5 if m == 0 else 2}"/>')
        if m:
            o.append(f'<text x="{x0 - 14}" y="{yy + 8:.1f}" text-anchor="end" font-size="22" fill="{MUTED}">{m}</text>')
        m += step
    r = 6 if len(weeks) <= 16 else 4.5
    for _, colour, vals in series:
        pts = [(x0 + (i + 0.5) * col, y(v)) for i, v in enumerate(vals)]
        o.append(f'<polyline points="{" ".join(f"{x:.1f},{yy:.1f}" for x, yy in pts)}" fill="none" stroke="{colour}" '
                 f'stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>')
        o += [f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="{r}" fill="{colour}" stroke="#fff" stroke-width="2"/>' for x, yy in pts]
        best = max(range(len(vals)), key=lambda i: (vals[i], -i))
        for i in sorted({0, best}):
            x, yy = pts[i]
            o.append(f'<text x="{x:.1f}" y="{yy - 14:.1f}" text-anchor="middle" font-size="21" font-weight="600" '
                     f'fill="{INK}">{round(vals[i])}</text>')
    o.append("</g>")
    return o


def _zone_bar(zm: dict[str, float]) -> list[dict[str, Any]]:
    total = sum(zm.values())
    return [{"code": z, "color": zone_color(i), "minutes": round(zm.get(z, 0.0)),
             "pct": 100 * zm.get(z, 0.0) / total} for i, z in enumerate(ZONES) if total and zm.get(z, 0.0) >= 0.5]


def _goal_line(g: Any) -> str:
    return " · ".join(x for x in (g.name, t("plan.goal.distance", km=fmt_num(g.distance_km, 1 if g.distance_km % 1 else 0))
                                  if g.distance_km else "", date_long(g.date), g.target) if x)


def _context(plan: Plan, branding: Branding) -> dict[str, Any]:
    req = plan.request
    cap = req.capacity_for(req.sports[0])
    zt = targets(cap, plan.zone_system)
    goal = plan.goal
    slots = core_slots(req)
    load = [wk.minutes for wk in plan.weeks if wk.kind == "load"] or [wk.minutes for wk in plan.weeks]
    sessions = [len([w for w in wk.workouts if not w.goal]) for wk in plan.weeks if wk.kind == "load"]
    business = branding.business_name or branding.coach_name or t("report.app_name")

    # ---- cover
    period = {"k": t("plan.pdf.fact.period"), "v": f"{date_long(plan.start)} – {date_long(plan.end)}"}
    if cap.anchors is None:
        basis = t("plan.pdf.fact.no_test")
    else:
        basis = " · ".join(x for x in (cap.source, date_long(cap.tested) if cap.tested else "") if x) or "–"
        if cap.estimated:
            basis += " · " + t("assessments.zones.estimated").lower()
        if cap.speed_from_hr:
            basis += " · " + t("assessments.hrzones.speed_from_hr").lower()
    start_minutes, source = volume_start(plan)
    # Three columns: a wide field (two columns) is always followed by a narrow one.
    narrow = [period, {"k": t("plan.pdf.fact.based_on"), "v": basis},
              {"k": t("plan.pdf.fact.level"), "v": t("plan.pdf.fact.level_default" if source == "default"
                                                   else "plan.pdf.fact.level_value", level=t(f"plan.level.{req.level}"),
                                                    hours=hours(req.recent_weekly_minutes if source == "capped"
                                                                else start_minutes))},
              {"k": t("plan.pdf.fact.days"), "v": ", ".join(
                  t("plan.pdf.fact.slot", day=_weekday(s.weekday), minutes=s.max_minutes) for s in slots)}]
    wide = [{"k": t("plan.pdf.fact.goal"), "v": _goal_line(goal) if goal else t("plan.pdf.fact.no_goal"), "wide": True}]
    wide += [{"k": t(f"plan.pdf.fact.goal_{g.priority.lower()}"), "v": _goal_line(g), "wide": True}
             for g in sorted(req.goals, key=lambda g: g.date) if g is not goal and plan.start <= g.date <= plan.end]
    facts = []
    for w in wide:
        facts += [w, narrow.pop(0) if narrow else {"k": "", "v": ""}]
    facts += narrow
    if branding.coach_name:
        facts.append({"k": t("report.cover.coach"), "v": branding.coach_name})

    tiles = [
        {"label": t("plan.pdf.tile.weeks"), "value": str(len(plan.weeks)), "unit": t("plan.pdf.unit.weeks"),
         "sub": _span(plan.start, plan.end)},
        {"label": t("plan.pdf.tile.sessions"), "value": (str(max(sessions)) if sessions else "–"),
         "unit": t("plan.pdf.unit.sessions"), "sub": t("plan.pdf.tile.sessions_sub", n=len(plan.workouts))},
        {"label": t("plan.pdf.tile.volume"), "value": f"{hours(min(load))}–{hours(max(load))}", "unit": t("plan.pdf.unit.hours"),
         "sub": t("plan.pdf.tile.volume_sub")},
        {"label": t("plan.pdf.tile.easy"), "value": str(round(100 * plan.easy_share)), "unit": "%",
         "sub": t("plan.pdf.tile.easy_sub")},
    ]
    zones = [{"code": z, "color": zone_color(i), "name": zt[z].name if z in zt else t(f"assessments.zones.garmin.{z}.name"),
              "hr": (f"{zt[z].hr_low}–{zt[z].hr_high}" if z in zt and zt[z].hr_low and zt[z].hr_high else ""),
              "hr_text": zt[z].hr_text if z in zt else "", "pace": zt[z].pace_text if z in zt else "",
              "purpose": t(f"assessments.zones.garmin.{z}.purpose")} for i, z in enumerate(ZONES)]

    # ---- the guide: only the workouts this plan uses
    used = {w.template_id for w in plan.workouts}
    guide = []
    for tid in GUIDE_ORDER:
        if tid in used:
            tpl = library.get(tid)
            guide.append({"title": t(f"plan.workout.{tid}.title"), "purpose": t(f"plan.workout.{tid}.purpose"),
                          "how": t(f"plan.workout.{tid}.how"), "zone": tpl.top_zone or "",
                          "color": zone_color(ZONES.index(tpl.top_zone)) if tpl.top_zone else UNZONED})
    blocks = [{"name": b.name, "color": BLOCK_FILL[b.kind],
               "weeks": t("plan.pdf.week_range", a=b.first_week, b=b.last_week) if b.weeks > 1
               else t("plan.pdf.week_one", a=b.first_week),
               "dates": _span(b.start, b.start + timedelta(days=7 * b.weeks - 1)), "reason": b.reason.text}
              for b in plan.blocks]

    # ---- one page per week
    weeks = []
    block_names = {b.kind: b.name for b in plan.blocks}
    for wk in plan.weeks:
        by_day: dict[date, list] = {}
        for w in wk.workouts:
            by_day.setdefault(w.date, []).append(w)
        days = []
        for i in range(7):
            d = wk.monday + timedelta(days=i)
            items = []
            for w in by_day.get(d, []):
                top = max((p.zone for p in w.parts if p.zone), default="")
                lines = [] if w.goal else workout_lines(w, plan.capacity_on(w.sport, w.date), plan.zone_system)
                for line in lines:
                    line["color"] = zone_color(ZONES.index(line["zone"])) if line["zone"] else UNZONED
                race = " · ".join(x for x in (t("plan.goal.distance", km=fmt_num(w.goal.distance_km, 1 if w.goal.distance_km % 1 else 0))
                                              if w.goal.distance_km else "", w.goal.target) if x) if w.goal else ""
                items.append({"title": w.title, "purpose": (race or w.purpose),
                              "duration": duration(w.seconds) if w.seconds else "", "lines": lines,
                              "start": f"{w.start:%H:%M}" if w.start else "", "race": bool(w.goal),
                              "quality": w.quality, "color": zone_color(ZONES.index(top)) if top else INK})
            days.append({"weekday": _weekday(d), "date": _day(d), "workouts": items})
        weeks.append({"index": wk.index, "dates": f"{date_long(wk.monday)} – {date_long(wk.sunday)}",
                      "block": block_names.get(wk.block, ""), "kind": t(f"plan.week.kind.{wk.kind}"),
                      "notes": [n.text for n in wk.notes], "total": hours(wk.minutes),
                      "sessions": len([w for w in wk.workouts if not w.goal]),
                      "easy_pct": round(100 * wk.easy_share), "days": days, "zone_bar": _zone_bar(wk.zone_minutes())})

    title = t("plan.pdf.title")
    return {
        "lang": language(), "title": title, "athlete_name": req.athlete_name,
        "kicker": t("plan.pdf.kicker_test" if cap.anchors is not None and not cap.estimated else "plan.pdf.kicker"),
        "headline": goal.name if goal else t("plan.pdf.headline_weeks", weeks=len(plan.weeks)),
        "branding": branding, "business": business,
        "initials": "".join(w[0] for w in business.split()[:2]).upper() or "L",
        "logo": _data_uri_image(branding.logo_path, 800) if branding.logo_path else None,
        "accent": _safe_color(branding.accent_color), "font_dir": (ASSETS / "fonts").as_uri(),
        "created": date_long(plan.created), "facts": facts, "tiles": tiles, "zones": zones,
        "zones_estimated": cap.anchors is not None and cap.estimated, "has_targets": bool(zt),
        "zones_sub": t("plan.pdf.zones.estimated_hr" if cap.anchors is not None and cap.speed_from_hr else
                       "plan.pdf.zones.estimated" if cap.anchors is not None and cap.estimated else
                       "plan.pdf.zones.sub" if zt else "plan.pdf.zones.none"),
        "overview": Markup(overview_svg(plan)), "has_unzoned": any(w.template_id == "test" for w in plan.workouts),
        "unzoned": UNZONED, "rule": RULE,
        "blocks": blocks, "guide": guide, "notes": [n.text for n in plan.notes], "weeks": weeks,
        "rules": [t(f"plan.pdf.rule.{i}") for i in range(1, 7)],
        "footer_left_css": Markup(_css_str(
            _footer_line(branding.business_name, branding.coach_name, *branding.contact.split(" · ")) or business)
            + ' "\\A" ' + _css_str(t("report.disclaimer.short"))),
        "footer_page_css": Markup(
            _css_str(t("report.footer.page") + " ") + " counter(page) " + _css_str(" " + t("report.footer.of") + " ")
            + " counter(pages) " + '"\\A" ' + _css_str(f"{req.athlete_name} · {title}")),
    }


def render_plan_html(plan: Plan, branding: Branding | None = None, language_code: str | None = None) -> str:
    """The plan as one self-contained HTML document (A4 print CSS), for report.pdf.html_to_pdf."""
    old_lang = language()
    if language_code and language_code != old_lang:
        set_language(language_code)
    try:
        return _env().get_template("plan.html.j2").render(**_context(plan, branding or Branding()))
    finally:
        if language() != old_lang:
            set_language(old_lang)


__all__ = ["overview_svg", "render_plan_html"]
