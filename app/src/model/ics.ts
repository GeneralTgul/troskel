/**
 * The plan as a calendar file (iCalendar, RFC 5545): a port of lopanalys/plan/ics.py. One VEVENT per workout,
 * in floating local time when the workout has a start time, otherwise all day. The UID is the workout's stable
 * key with the programme's domain, so a file from the app and one from the coach update the same events.
 */
import { t } from "../i18n";
import { addDays } from "./dates";
import { duration, plain, workoutLines, workoutMinutes, workoutPurpose, workoutHow, workoutSeconds, planTitle } from "./plan";
import type { IsoDate, StoredPlan, StoredWorkout } from "./types";

export const PRODID = "-//Troskel//App//EN";
export const UID_DOMAIN = "lopanalys";

export function escape(text: string): string {
  return text.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,")
    .replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n/g, "\\n");
}

const enc = new TextEncoder();

/** Folds a line at 75 octets without splitting a UTF-8 character (section 3.1). */
export function fold(line: string): string {
  const out: string[] = [];
  let cur = "";
  let size = 0;
  for (const ch of line) {
    const b = enc.encode(ch).length;
    if (size + b > (out.length ? 74 : 75)) {
      out.push(cur);
      cur = "";
      size = 0;
    }
    cur += ch;
    size += b;
  }
  out.push(cur);
  return out.join("\r\n ");
}

function description(stored: StoredPlan, w: StoredWorkout): string {
  const lines: string[] = w.goal ? [] : [workoutPurpose(w), ""];
  if (w.goal) {
    const g = w.goal;
    const bits = [g.distance_km ? t("plan.goal.distance", { km: String(g.distance_km) }) : "", g.target].filter(Boolean);
    lines.push(bits.join(" · ") || t("plan.workout.race.purpose"));
  } else {
    lines.push(...workoutLines(stored, w).map(plain), "", workoutHow(w));
  }
  return lines.join("\n").trim();
}

const compact = (iso: IsoDate) => iso.replace(/-/g, "");

function localStamp(day: IsoDate, time: string, plusSeconds: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const [hh, mm, ss] = time.split(":").map(Number);
  const x = new Date(Date.UTC(y, m - 1, d, hh, mm, ss || 0) + plusSeconds * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${x.getUTCFullYear()}${p(x.getUTCMonth() + 1)}${p(x.getUTCDate())}T${p(x.getUTCHours())}${p(x.getUTCMinutes())}${p(x.getUTCSeconds())}`;
}

function event(stored: StoredPlan, w: StoredWorkout, stamp: string): string[] {
  const secs = workoutSeconds(w);
  const title = planTitle(stored, w);
  const summary = w.goal || !workoutMinutes(w) ? title : `${title} · ${duration(secs)}`;
  const out = ["BEGIN:VEVENT", `UID:${w.key}@${UID_DOMAIN}`, `DTSTAMP:${stamp}`];
  if (w.start != null && secs) {
    out.push(`DTSTART:${localStamp(w.date, w.start, 0)}`, `DTEND:${localStamp(w.date, w.start, secs)}`);
  } else {
    out.push(`DTSTART;VALUE=DATE:${compact(w.date)}`, `DTEND;VALUE=DATE:${compact(addDays(w.date, 1))}`);
  }
  out.push(`SUMMARY:${escape(summary)}`, `DESCRIPTION:${escape(description(stored, w))}`,
    `CATEGORIES:${escape(t("plan.ics.category"))}`, w.start ? "TRANSP:OPAQUE" : "TRANSP:TRANSPARENT", "END:VEVENT");
  return out;
}

function utcStamp(created: string): string {
  const d = new Date(created);
  return isNaN(d.getTime()) ? "19700101T000000Z" : d.toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
}

/** The workouts from `from` on (all of them without it) as iCalendar text with CRLF line ends. */
export function toIcs(stored: StoredPlan, opts: { from?: IsoDate } = {}): string {
  const stamp = utcStamp(stored.plan.created);
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", `PRODID:${PRODID}`, "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
    `X-WR-CALNAME:${escape(t("plan.ics.name", { name: stored.athlete }))}`];
  for (const wk of stored.plan.weeks)
    for (const w of wk.workouts) if (!opts.from || w.date >= opts.from) lines.push(...event(stored, w, stamp));
  lines.push("END:VCALENDAR");
  return lines.map(fold).join("\r\n") + "\r\n";
}
