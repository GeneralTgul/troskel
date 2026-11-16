/**
 * The athlete's own log, kept on the device beside the plan: each workout done, partly done or missed, and sessions
 * outside the plan ("+ yoga"). A mark is keyed by the workout's key ("<plan>-<yy><ww>-<day>"), which the engine keeps
 * when it rebuilds the plan with new times (same plan id, same dates), so a mark stays with its workout. A status that
 * came with the plan from Tröskel ("done", "missed") counts until the athlete marks the workout.
 */
import { planSports } from "../engine/models";
import { addDays } from "./dates";
import { pyRound, workoutMinutes, workoutsOn } from "./plan";
import type { IsoDate, StoredPlan, StoredWorkout } from "./types";

const STRENGTH = "strength";

export type Status = "done" | "partial" | "missed";
export const STATUSES: Status[] = ["done", "partial", "missed"];

export interface Mark {
  status: Status;
  /** A partly done workout: the minutes that were done. */
  minutes?: number;
}

export interface Extra {
  id: string;
  date: IsoDate;
  /** A plan sport ("run", "bike", ...), "strength" or "other". */
  sport: string;
  minutes: number;
  name: string;
}

export interface TrainingLog {
  marks: Record<string, Mark>;
  extras: Extra[];
}

export const emptyLog = (): TrainingLog => ({ marks: {}, extras: [] });

/** A status the plan carries from Tröskel (StoredWorkout.status) as a mark; "planned" and "moved" are none. */
function fromPlan(status: string): Mark | null {
  if (status === "done") return { status: "done" };
  if (status === "missed" || status === "skipped") return { status: "missed" };
  return null;
}

export function markOf(log: TrainingLog, w: StoredWorkout): Mark | null {
  return log.marks[w.key] ?? fromPlan(w.status);
}

/** The log with `w` marked (null: the mark taken away). */
export function setMark(log: TrainingLog, key: string, mark: Mark | null): TrainingLog {
  const marks = { ...log.marks };
  if (mark) marks[key] = mark;
  else delete marks[key];
  return { ...log, marks };
}

/** What a partly done workout counts as until the athlete says: half of it, in whole five minutes (at least 5). */
export const halfOf = (w: StoredWorkout) => Math.max(5, pyRound(workoutMinutes(w) / 10) * 5);

/** The minutes a workout counts with in the week's numbers. */
export function doneMinutes(w: StoredWorkout, mark: Mark | null): number {
  if (!mark || mark.status === "missed") return 0;
  if (mark.status === "partial") return Math.max(0, Math.min(mark.minutes ?? halfOf(w), workoutMinutes(w)));
  return workoutMinutes(w);
}

export function addExtra(log: TrainingLog, e: Omit<Extra, "id">): TrainingLog {
  let n = 1;
  while (log.extras.some((x) => x.id === `${e.date}-${n}`)) n++;
  const name = e.name.trim();
  return { ...log, extras: [...log.extras, { ...e, name, minutes: Math.max(0, Math.round(e.minutes)), id: `${e.date}-${n}` }] };
}

export function removeExtra(log: TrainingLog, id: string): TrainingLog {
  return { ...log, extras: log.extras.filter((x) => x.id !== id) };
}

export const extrasOn = (log: TrainingLog, day: IsoDate) => log.extras.filter((x) => x.date === day);

/** The log for a new plan: the marks of this plan's workouts stay (a renewed plan has the same id and keys), the
 * others go. The sessions outside the plan stay: they are the athlete's, not the plan's. */
export function keepFor(log: TrainingLog, stored: StoredPlan): TrainingLog {
  const prefix = `${stored.plan.id}-`;
  return { ...log, marks: Object.fromEntries(Object.entries(log.marks).filter(([k]) => k.startsWith(prefix))) };
}

/** A log read from storage, with anything broken left out. */
export function checkLog(v: unknown): TrainingLog {
  const out = emptyLog();
  if (!v || typeof v !== "object") return out;
  const { marks, extras } = v as Partial<TrainingLog>;
  if (marks && typeof marks === "object")
    for (const [k, m] of Object.entries(marks))
      if (m && STATUSES.includes(m.status)) out.marks[k] = m.minutes != null ? { status: m.status, minutes: +m.minutes } : { status: m.status };
  if (Array.isArray(extras))
    out.extras = extras.filter((x) => x && typeof x.id === "string" && typeof x.date === "string"
      && typeof x.sport === "string" && Number.isFinite(x.minutes));
  return out;
}

export interface SportRow {
  sport: string;
  planned: number;
  done: number;
  /** The plan's minutes are reached (a sport with planned minutes only). */
  reached: boolean;
}

export interface WeekTotals {
  rows: SportRow[];
  extras: Extra[];
  planned: number;
  done: number;
}

/** The week from `monday` in numbers: done against planned per sport, in the plan's order (the main sport first,
 * strength last), and the sessions outside the plan, which count on their own as "extra". A race has no planned
 * minutes and makes no row of its own. */
export function weekTotals(stored: StoredPlan, log: TrainingLog, monday: IsoDate): WeekTotals {
  const by = new Map<string, SportRow>();
  const order = [...planSports(stored.request), STRENGTH];
  for (let i = 0; i < 7; i++) {
    for (const w of workoutsOn(stored, addDays(monday, i))) {
      const planned = workoutMinutes(w);
      if (!planned) continue;
      const row = by.get(w.sport) ?? { sport: w.sport, planned: 0, done: 0, reached: false };
      row.planned += planned;
      row.done += doneMinutes(w, markOf(log, w));
      by.set(w.sport, row);
    }
  }
  const rank = (s: string) => (order.includes(s) ? order.indexOf(s) : order.length);
  const rows = [...by.values()].sort((a, b) => rank(a.sport) - rank(b.sport));
  for (const r of rows) r.reached = r.done >= r.planned;
  const sunday = addDays(monday, 6);
  const extras = log.extras.filter((x) => x.date >= monday && x.date <= sunday)
    .sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id));
  return {
    rows, extras,
    planned: rows.reduce((s, r) => s + r.planned, 0),
    done: rows.reduce((s, r) => s + r.done, 0),
  };
}
