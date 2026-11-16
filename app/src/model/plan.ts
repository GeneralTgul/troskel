/**
 * What the views read out of a stored plan: the numbers of models.py (seconds, minutes, zone time) and the
 * texts of text.py and resolve.py, in the current language. Nothing here changes the plan.
 */
import { multiSport } from "../engine/models";
import { has, t } from "../i18n";
import type {
  IsoDate, StoredNote, StoredPart, StoredPlan, StoredWeek, StoredWorkout, ZoneCode, ZoneTarget, ZoneTargets,
} from "./types";

/** Python's round(): halves go to the even number, so minutes match the printout. */
export function pyRound(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

export const partWork = (p: StoredPart) => p.repeat * p.duration_s;
export const partTotal = (p: StoredPart) => partWork(p) + (p.repeat - 1) * p.rest_s;
export const workoutSeconds = (w: StoredWorkout) => w.parts.reduce((s, p) => s + partTotal(p), 0);
export const workoutMinutes = (w: StoredWorkout) => pyRound(workoutSeconds(w) / 60);
/** Week.minutes: the endurance minutes; strength sessions come on top. */
export const weekMinutes = (wk: StoredWeek) =>
  wk.workouts.reduce((s, w) => s + (w.sport === "strength" ? 0 : workoutMinutes(w)), 0);

export function zoneSeconds(w: StoredWorkout): Partial<Record<ZoneCode, number>> {
  const out: Partial<Record<ZoneCode, number>> = {};
  for (const p of w.parts) {
    if (p.zone == null) continue;
    out[p.zone] = (out[p.zone] ?? 0) + partWork(p);
    if (p.repeat > 1 && p.rest_s) out[p.rest_zone] = (out[p.rest_zone] ?? 0) + (p.repeat - 1) * p.rest_s;
  }
  return out;
}

export function weekZoneMinutes(wk: StoredWeek): Partial<Record<ZoneCode, number>> {
  const out: Partial<Record<ZoneCode, number>> = {};
  for (const w of wk.workouts)
    for (const [z, s] of Object.entries(zoneSeconds(w)) as [ZoneCode, number][]) out[z] = (out[z] ?? 0) + s / 60;
  return out;
}

/** The zone a workout is about, for its colour and its targets: the zone of the work in an interval
 * session, otherwise the zone with the most time (strides in a long run do not make it a hard day). */
export function mainZone(w: StoredWorkout): ZoneCode | null {
  let best: ZoneCode | null = null;
  for (const p of w.parts) if (p.kind === "work" && p.zone && (!best || p.zone > best)) best = p.zone;
  if (best) return best;
  let most = 0;
  for (const [z, s] of Object.entries(zoneSeconds(w)) as [ZoneCode, number][]) if (s > most) [best, most] = [z, s];
  return best;
}

/** The template's title (plan.models.Workout.title_in): in a sport other than running, and in every sport of a plan
 * with several (`multi`), with the sport's name first. */
export function workoutTitle(w: StoredWorkout, multi = false): string {
  if (w.goal) return w.goal.name;
  if (w.sport === "run" && !multi) return t(`plan.workout.${w.template_id}.title`);
  const any = `plan.workout.${w.template_id}.title_any`;
  return t("plan.workout.in_sport", { sport: t(`plan.sport.${w.sport}`),
    title: t(has(any) ? any : `plan.workout.${w.template_id}.title`) });
}
/** The title in its plan (Plan.title): with the sport first when the plan has several sports or strength. */
export const planTitle = (stored: StoredPlan, w: StoredWorkout) => workoutTitle(w, multiSport(stored.request));
export const workoutPurpose = (w: StoredWorkout) => t(`plan.workout.${w.template_id}.purpose`);
export const workoutHow = (w: StoredWorkout) => t(`plan.workout.${w.template_id}.how`);

export function noteText(n: StoredNote): string {
  return t(n.key, Object.fromEntries(n.args));
}

export function workoutsOn(stored: StoredPlan, day: IsoDate): StoredWorkout[] {
  const out: StoredWorkout[] = [];
  for (const wk of stored.plan.weeks) for (const w of wk.workouts) if (w.date === day) out.push(w);
  return out.sort((a, b) => (a.start ?? "").localeCompare(b.start ?? ""));
}

export function weekOf(stored: StoredPlan, monday: IsoDate): StoredWeek | undefined {
  return stored.plan.weeks.find((w) => w.monday === monday);
}

export const planStart = (s: StoredPlan) => s.plan.weeks[0]?.monday;
export const planEnd = (s: StoredPlan) => {
  const last = s.plan.weeks[s.plan.weeks.length - 1];
  return last ? addDaysIso(last.monday, 6) : undefined;
};

function addDaysIso(iso: IsoDate, n: number): IsoDate {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

/** The targets a workout on `day` is shown with: the test that applied that day (Plan.capacity_on). */
export function targetsOn(stored: StoredPlan, sport: string, day: IsoDate): ZoneTargets {
  const log = [...stored.targets_log].sort((a, b) => a.until.localeCompare(b.until));
  for (const e of log) if (e.sport === sport && day < e.until) return e.targets;
  return stored.targets[sport] ?? {};
}

// --- texts, as text.py and resolve.py -------------------------------------------------------------------

export function duration(seconds: number): string {
  if (seconds < 60 || (seconds < 120 && seconds % 60)) return t("plan.fmt.seconds", { s: seconds });
  const m = pyRound(seconds / 60);
  if (m < 60) return t("plan.fmt.minutes", { m });
  const h = Math.floor(m / 60);
  return m % 60 === 0 ? t("plan.fmt.hours", { h }) : t("plan.fmt.hours_minutes", { h, m: m % 60 });
}

/** Weekly volume as "h:mm". */
export function hours(minutes: number): string {
  const m = pyRound(minutes);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

/** Pace "m:ss" (min/km) at a speed in km/h, "" without one. */
export function paceStr(kmh: number | null | undefined): string {
  if (!kmh || kmh <= 0) return "";
  const sec = pyRound(3600 / kmh);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

const APPROX = "≈";
const mark = (v: string, approx: boolean) => (v && approx ? APPROX : "") + v;

/** Time per 500 m "m:ss" at a power on the SkiErg (Concept2: W = 2.80 / (s/m)^3), "" without one. */
export function splitStr(watt: number | null | undefined): string {
  if (!watt || watt <= 0) return "";
  const sec = pyRound(500 * Math.cbrt(2.8 / watt));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

/** resolve._range: both ends, or one with the text for the easier or the harder end. */
function range(lo: string, hi: string, key: string): string {
  if (lo && hi) return t(`plan.target.${key}`, { lo, hi });
  if (hi) return t(`plan.target.${key}_slower`, { v: hi });
  return lo ? t(`plan.target.${key}_faster`, { v: lo }) : "";
}

export function hrText(tg: ZoneTarget): string {
  if (tg.hr_low && tg.hr_high) return t("plan.target.hr", { lo: tg.hr_low, hi: tg.hr_high });
  if (tg.hr_high) return t("plan.target.hr_below", { hi: tg.hr_high });
  return tg.hr_low ? t("plan.target.hr_above", { lo: tg.hr_low }) : "";
}

/** Time per 100 m "m:ss" in swimming (resolve.swim_str), "" without one. */
export function swimStr(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "";
  const sec = pyRound(seconds);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

/** Running: min/km. The SkiErg: time per 500 m from the watt. Swimming: time per 100 m from a CSS test. The bike
 * and the others: "" (resolve.Target.pace_text). */
export function paceText(tg: ZoneTarget, sport = "run"): string {
  if (sport === "swim") return range(swimStr(tg.swim_low_s), swimStr(tg.swim_high_s), "swim");
  if (sport === "skierg")
    return range(mark(splitStr(tg.power_low_w), tg.speed_low_approx), mark(splitStr(tg.power_high_w), tg.speed_high_approx), "split");
  if (sport !== "run") return "";
  return range(mark(paceStr(tg.speed_low_kmh), tg.speed_low_approx), mark(paceStr(tg.speed_high_kmh), tg.speed_high_approx), "pace");
}

/** "180–215 W", or "" without watt (resolve.Target.power_text). */
export function powerText(tg: ZoneTarget): string {
  const lo = tg.power_low_w ? String(tg.power_low_w) : "";
  const hi = tg.power_high_w ? String(tg.power_high_w) : "";
  return range(mark(lo, tg.speed_low_approx), mark(hi, tg.speed_high_approx), "power");
}

export function targetText(tg: ZoneTarget | undefined, sport = "run"): string {
  if (!tg) return "";
  return [hrText(tg), powerText(tg), paceText(tg, sport)].filter(Boolean).join(" · ");
}

export function zoneName(zone: string): string {
  const key = `assessments.zones.garmin.${zone}.name`;
  return has(key) ? t(key) : zone;
}

export interface PartLine {
  kind: string;
  what: string;
  zone: string;
  zoneName: string;
  hr: string;
  power: string;
  pace: string;
  target: string;
  rest: string;
  rpe: string;
}

/** Parts whose name says running ("Löpning", "Nedjogg", "lugn jogg"): another sport takes the word without it. */
const RUN_WORDED_PARTS = ["steady", "cooldown"];

export function partLine(part: StoredPart, targets: ZoneTargets, sport = "run"): PartLine {
  const tg = part.zone ? targets[part.zone] : undefined;
  let what = part.duration_s ? duration(part.duration_s) : "";
  if (part.repeat > 1) what = t("plan.fmt.repeat", { n: part.repeat, each: what });
  const any = sport === "run" ? "" : "_any";
  let rest = "";
  if (part.repeat > 1 && part.rest_s)
    rest = t(part.rest_zone === "Z2" ? `plan.part.rest_jog${any}` : "plan.part.rest", { d: duration(part.rest_s) });
  return {
    kind: t(`plan.part.${part.kind}${RUN_WORDED_PARTS.includes(part.kind) ? any : ""}`), what, zone: part.zone ?? "",
    zoneName: part.zone ? zoneName(part.zone) : "", hr: tg ? hrText(tg) : "", power: tg ? powerText(tg) : "",
    pace: tg ? paceText(tg, sport) : "", target: targetText(tg, sport), rest, rpe: t("plan.part.rpe", { v: part.rpe }),
  };
}

export function workoutLines(stored: StoredPlan, w: StoredWorkout): PartLine[] {
  const tg = targetsOn(stored, w.sport, w.date);
  return w.parts.map((p) => partLine(p, tg, w.sport));
}

/** A part as one line: "Intervaller: 4 × 6 min i Z4 (170–174 slag/min · 4:21–4:10 min/km), vila 1 min". */
export function plain(line: PartLine): string {
  let s = `${line.kind}: ${line.what}`.replace(/[:\s]+$/, "").trimEnd();
  if (line.zone) {
    s += " " + t("plan.fmt.in_zone", { zone: line.zone });
    if (line.target) s += ` (${line.target})`;
  }
  if (line.rest) s += ", " + line.rest;
  return s;
}
