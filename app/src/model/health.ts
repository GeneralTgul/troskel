/**
 * The phone's health data in the app: the workouts the watch recorded and the night's values (sleep, resting heart
 * rate, HRV). They come in a link, `<app>#h=<data>`, which anything on the phone can make: on an iPhone a Shortcut
 * that reads Apple Health (the web app cannot read HealthKit itself), later the app's own reading of Health Connect
 * and HealthKit. The data stays on the device, like the plan.
 *
 * From the data the app works out two things, with the hall screen's rules (#513 in the author's setup):
 * - `autoMarks`: which planned workouts were done. Same day and sport first, the longest session counting; then the
 *   same sport another day of the week, at least 20 minutes, the nearest day and at a tie the later. A workout is
 *   never marked missed by the data, and a mark the athlete set wins.
 * - `dayForm`: whether today is a tired day. One strong signal or two weak ones: HRV under 90 % (80 %) of the
 *   athlete's own mean of the 28 nights before, resting heart rate 5 (8) beats over its 28-day mean, under 6 h
 *   (4.5 h) of sleep last night. A mean needs at least 14 values.
 */
import { addDays, mondayOf } from "./dates";
import { workoutsOn } from "./plan";
import type { Mark, TrainingLog } from "./log";
import type { IsoDate, StoredPlan, StoredWorkout } from "./types";

export const HEALTH_KIND = "troskel-health";
export const HEALTH_FORMAT = 1;
/** How many days the app keeps: 28 for the means, the week, and a little over. */
export const KEEP_DAYS = 42;
const MOVED_MIN = 20;

export interface HealthWorkout {
  /** A plan sport ("run", "bike", "skierg", "ski", "swim"), "strength" or "other" (see `sportOf`). */
  sport: string;
  /** Local start, ISO with the offset ("2026-11-16T18:02:00+01:00"); the date is its first ten characters. */
  start: string;
  minutes: number;
  hr?: number;
  hr_max?: number;
}

export interface HealthDay {
  /** The day the athlete woke up: the night's sleep belongs to it. */
  date: IsoDate;
  sleep_h?: number;
  rhr?: number;
  hrv?: number;
}

export interface HealthData {
  /** When the data was read on the phone. */
  read: string;
  workouts: HealthWorkout[];
  days: HealthDay[];
}

export class HealthError extends Error {
  constructor(public readonly code: "not_health" | "too_new" | "broken") {
    super(code);
  }
}

// The names HealthKit (HKWorkoutActivityType, also as the Shortcuts app writes them), Health Connect
// (ExerciseSessionRecord.EXERCISE_TYPE_*) and Strava use, in lower case without spaces, "_" and "-".
const SPORTS: Record<string, string> = {
  run: "run", running: "run", treadmill: "run", runningtreadmill: "run", trailrun: "run", virtualrun: "run",
  bike: "bike", cycling: "bike", biking: "bike", bikingstationary: "bike", ride: "bike", virtualride: "bike",
  indoorcycling: "bike", gravelride: "bike", mountainbikeride: "bike", handcycling: "bike",
  skierg: "skierg", ski: "ski", crosscountryskiing: "ski", skiing: "ski", nordicski: "ski", rollerski: "ski",
  backcountryski: "ski",
  swim: "swim", swimming: "swim", swimmingpool: "swim", swimmingopenwater: "swim", poolswim: "swim",
  strength: "strength", traditionalstrengthtraining: "strength", functionalstrengthtraining: "strength",
  strengthtraining: "strength", weightlifting: "strength", weighttraining: "strength", crossfit: "strength",
  highintensityintervaltraining: "strength",
};

/** The app's sport for a name from Health, Health Connect or Strava ("Running", "EXERCISE_TYPE_BIKING"); "other". */
export function sportOf(name: string): string {
  const k = String(name ?? "").toLowerCase().replace(/^(hkworkoutactivitytype|exercise_type_)/, "").replace(/[\s_-]/g, "");
  return SPORTS[k] ?? "other";
}

/** Plan sport `planned` is done by a session of `done`: the same sport, and on skis SkiErg and skis stand in for
 * each other, as on the hall screen. */
export function fits(planned: string, done: string): boolean {
  if (planned === done) return true;
  const ski = ["ski", "skierg"];
  return ski.includes(planned) && ski.includes(done);
}

const num = (v: unknown): number | undefined => {
  const n = typeof v === "string" ? Number(v.replace(",", ".")) : typeof v === "number" ? v : NaN;
  return Number.isFinite(n) && n > 0 ? n : undefined;
};

/** Checks the data just enough that the views can trust it; anything broken is left out. */
export function checkHealth(value: unknown): HealthData {
  const v = value as Record<string, unknown> | null;
  if (!v || typeof v !== "object" || v.kind !== HEALTH_KIND) throw new HealthError("not_health");
  if (typeof v.format !== "number" || v.format > HEALTH_FORMAT) throw new HealthError("too_new");
  const workouts: HealthWorkout[] = [];
  for (const w of Array.isArray(v.workouts) ? v.workouts : []) {
    const start = String(w?.start ?? "");
    const minutes = num(w?.minutes);
    if (!/^\d{4}-\d\d-\d\d/.test(start) || !minutes) continue;
    const out: HealthWorkout = { sport: sportOf(w.sport), start, minutes: Math.round(minutes) };
    const hr = num(w.hr), max = num(w.hr_max);
    if (hr) out.hr = Math.round(hr);
    if (max) out.hr_max = Math.round(max);
    workouts.push(out);
  }
  return { read: typeof v.read === "string" ? v.read : "", workouts, days: byDay(Array.isArray(v.days) ? v.days : []) };
}

/**
 * The days, one per date. A Shortcut writes one entry per sample, so entries of the same date are put together:
 * the sleep is added up (the night's parts), HRV and resting heart rate are the mean.
 */
function byDay(entries: unknown[]): HealthDay[] {
  const acc = new Map<IsoDate, { sleep: number[]; rhr: number[]; hrv: number[] }>();
  for (const e of entries) {
    const d = e as Record<string, unknown> | null;
    const date = String(d?.date ?? "").slice(0, 10);
    if (!d || !/^\d{4}-\d\d-\d\d$/.test(date)) continue;
    const a = acc.get(date) ?? { sleep: [], rhr: [], hrv: [] };
    const sleep = num(d.sleep_h), rhr = num(d.rhr), hrv = num(d.hrv);
    if (sleep) a.sleep.push(sleep);
    if (rhr) a.rhr.push(rhr);
    if (hrv) a.hrv.push(hrv);
    acc.set(date, a);
  }
  const sum = (v: number[]) => v.reduce((s, x) => s + x, 0);
  const round = (x: number) => Math.round(x * 100) / 100;
  return [...acc].sort(([a], [b]) => a.localeCompare(b)).map(([date, a]) => {
    const out: HealthDay = { date };
    if (a.sleep.length) out.sleep_h = round(sum(a.sleep));
    if (a.rhr.length) out.rhr = round(sum(a.rhr) / a.rhr.length);
    if (a.hrv.length) out.hrv = round(sum(a.hrv) / a.hrv.length);
    return out;
  });
}

/** The `h=` data of a link or a bare fragment, or null when there is none. */
export function healthData(link: string): string | null {
  const hash = link.includes("#") ? link.slice(link.indexOf("#") + 1) : link;
  for (const part of hash.split("&")) if (part.startsWith("h=")) return part.slice(2).trim();
  return null;
}

/**
 * Reads the `h=` data: the JSON in base64url or base64 (a Shortcut's "Base64 Encode"), or the JSON itself,
 * URL-encoded. No compression, so that a Shortcut can make it.
 */
export function decodeHealth(data: string): HealthData {
  let text: string;
  try {
    const raw = data.includes("%") ? decodeURIComponent(data) : data;
    if (raw.trimStart().startsWith("{")) text = raw;
    else {
      const b64 = raw.replace(/\s/g, "").replace(/-/g, "+").replace(/_/g, "/");
      const bin = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
      text = new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
    }
  } catch {
    throw new HealthError("broken");
  }
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    throw new HealthError("broken");
  }
  return checkHealth(json);
}

const dateOf = (w: HealthWorkout): IsoDate => w.start.slice(0, 10);
const startMs = (w: HealthWorkout) => Date.parse(w.start);

/**
 * New data over the kept: a value of a day in the new data replaces the kept one, and a workout that overlaps a kept one by
 * more than half of the shorter (the watch and Zwift for the same ride) is one workout, the longer. Only the last
 * KEEP_DAYS days up to `today` are kept.
 */
export function mergeHealth(old: HealthData | null, add: HealthData, today: IsoDate): HealthData {
  const from = addDays(today, -KEEP_DAYS);
  const byDate = new Map<IsoDate, HealthDay>();
  for (const d of [...(old?.days ?? []), ...add.days]) byDate.set(d.date, { ...byDate.get(d.date), ...d });
  const workouts = dedupe([...(old?.workouts ?? []), ...add.workouts]);
  return {
    read: add.read || old?.read || "",
    days: [...byDate.values()].filter((d) => d.date >= from && d.date <= today).sort((a, b) => a.date.localeCompare(b.date)),
    workouts: workouts.filter((w) => dateOf(w) >= from && dateOf(w) <= today),
  };
}

function dedupe(ws: HealthWorkout[]): HealthWorkout[] {
  const out: HealthWorkout[] = [];
  for (const w of [...ws].sort((a, b) => b.minutes - a.minutes)) {
    const s = startMs(w), e = s + w.minutes * 60_000;
    const same = out.some((o) => {
      const os = startMs(o), oe = os + o.minutes * 60_000;
      const overlap = Math.min(e, oe) - Math.max(s, os);
      return overlap > (Math.min(w.minutes, o.minutes) * 60_000) / 2;
    });
    if (!same) out.push(w);
  }
  return out.sort((a, b) => a.start.localeCompare(b.start));
}

/** A planned workout the data marked done, and on which day the session was (another day: moved). */
export interface AutoMark extends Mark {
  auto: true;
  on: IsoDate;
}

export interface AutoResult {
  marks: Record<string, AutoMark>;
  /** The sessions no planned workout took: they stand beside the day as done outside the plan. */
  unplanned: HealthWorkout[];
}

/**
 * The hall screen's rule 2, week by week (match_week in the author's tools/traning_status.py): first the same
 * day, the longest fitting session (not the warm-up); then for a past day's workout without one, a session of the
 * same sport on another past day of the week, at least 20 minutes and not taken: the nearest day, at a tie the
 * later (Saturday's long run on Sunday). Never a race on another day. Days after `today` take nothing.
 */
export function autoMarks(stored: StoredPlan, health: HealthData | null, today: IsoDate): AutoResult {
  const marks: Record<string, AutoMark> = {};
  if (!health || !health.workouts.length) return { marks, unplanned: [] };
  const taken = new Set<HealthWorkout>();
  const mondays = [...new Set(health.workouts.map((w) => mondayOf(dateOf(w))))].sort();
  for (const monday of mondays) {
    const days = [...Array(7)].map((_, i) => addDays(monday, i));
    const plan = days.map((d) => workoutsOn(stored, d));
    const done = days.map((d) => (d <= today ? health.workouts.filter((w) => dateOf(w) === d) : null));
    const ok = new Set<StoredWorkout>();
    for (let d = 0; d < 7; d++) {
      if (!done[d]) continue;
      for (const w of plan[d]) {
        const best = done[d]!.filter((x) => !taken.has(x) && fits(w.sport, x.sport))
          .sort((a, b) => b.minutes - a.minutes)[0];
        if (best) {
          taken.add(best);
          ok.add(w);
          marks[w.key] = { status: "done", auto: true, on: days[d] };
        }
      }
    }
    const past = days.map((_, d) => d).filter((d) => done[d]);
    const last = past[past.length - 1];
    for (let d = 0; d < 7; d++) {
      if (!past.length || d >= last) continue;
      for (const w of plan[d]) {
        if (ok.has(w) || w.goal) continue;
        let best: { dist: number; e: number; x: HealthWorkout } | null = null;
        for (const e of past) {
          if (e === d) continue;
          for (const x of done[e]!) {
            if (taken.has(x) || x.sport !== w.sport || x.minutes < MOVED_MIN) continue;
            const dist = Math.abs(e - d);
            if (!best || dist < best.dist || (dist === best.dist && e > best.e)) best = { dist, e, x };
          }
        }
        if (best) {
          taken.add(best.x);
          ok.add(w);
          marks[w.key] = { status: "done", auto: true, on: days[best.e] };
        }
      }
    }
  }
  return { marks, unplanned: health.workouts.filter((w) => !taken.has(w)) };
}

/** The log the views show: the athlete's marks over the ones the data gave. */
export function withAuto(log: TrainingLog, auto: AutoResult): TrainingLog {
  return { ...log, marks: { ...auto.marks, ...log.marks } };
}

/** The log to keep: without the marks the data gave (they are worked out again each time). */
export function withoutAuto(log: TrainingLog): TrainingLog {
  return { ...log, marks: Object.fromEntries(Object.entries(log.marks).filter(([, m]) => !(m as Partial<AutoMark>).auto)) };
}

export const unplannedOn = (auto: AutoResult, day: IsoDate) => auto.unplanned.filter((w) => dateOf(w) === day);

export type Signal = "hrv" | "rhr" | "sleep";

export interface FormReason {
  signal: Signal;
  strong: boolean;
  value: number;
  /** HRV and resting heart rate: the athlete's own mean. */
  mean?: number;
}

export interface DayForm {
  tired: boolean;
  /** The strong first. */
  reasons: FormReason[];
  /** The signals the data had nothing for today (HRV is often not in Apple Health from a Garmin watch). */
  missing: Signal[];
}

function mean(days: HealthDay[], key: "hrv" | "rhr", today: IsoDate): number | null {
  const from = addDays(today, -28);
  const v = days.filter((d) => d.date >= from && d.date < today && d[key] != null).map((d) => d[key]!);
  return v.length >= 14 ? v.reduce((s, x) => s + x, 0) / v.length : null;
}

/** Today's form from the data (rule 3 on the hall screen, with resting heart rate for Garmin's Training Readiness,
 * which is not in Health); null when the data has nothing for today. */
export function dayForm(health: HealthData | null, today: IsoDate): DayForm | null {
  const day = health?.days.find((d) => d.date === today);
  if (!health || !day) return null;
  const reasons: FormReason[] = [];
  const missing: Signal[] = [];
  const hrvMean = mean(health.days, "hrv", today);
  if (day.hrv != null && hrvMean) {
    if (day.hrv < 0.9 * hrvMean) reasons.push({ signal: "hrv", strong: day.hrv < 0.8 * hrvMean, value: day.hrv, mean: hrvMean });
  } else missing.push("hrv");
  const rhrMean = mean(health.days, "rhr", today);
  if (day.rhr != null && rhrMean) {
    if (day.rhr >= rhrMean + 5) reasons.push({ signal: "rhr", strong: day.rhr >= rhrMean + 8, value: day.rhr, mean: rhrMean });
  } else missing.push("rhr");
  if (day.sleep_h != null) {
    if (day.sleep_h < 6) reasons.push({ signal: "sleep", strong: day.sleep_h < 4.5, value: day.sleep_h });
  } else missing.push("sleep");
  reasons.sort((a, b) => Number(b.strong) - Number(a.strong));
  const tired = reasons.some((r) => r.strong) || reasons.length >= 2;
  return { tired, reasons, missing };
}
