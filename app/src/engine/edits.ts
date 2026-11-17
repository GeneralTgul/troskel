/**
 * The athlete's changes of single workouts (#777, step 3): a port of lopanalys/plan/edits.py, checked against it with
 * the fixtures of app/scripts/make_fixtures.py (test/fixtures/edits.json.gz, test/edits.test.ts).
 *
 * The changes are kept as a list on the plan (`plan.edits`: the workout's key, the new day, the new length) and laid
 * on the weeks again after everything that makes workouts anew (`rebuild`, `rollForward`, a new plan from the coach
 * with the same id), so that a change does not disappear when the athlete saves new times or a new test comes (#776).
 * - An edit is laid on the workout with its key in a detailed week; a key that is gone, a race and a test drop it.
 * - `date` moves the workout within its week (a day outside it is left alone), with the clock time of the new day's
 *   slot (the first that holds its sport, else the day's first) or none. A swap is two moves. The key stays.
 * - `minutes` shortens the workout (`shorten`); one as short or shorter is left as it is.
 * - Laying the edits again on a plan that has them changes nothing.
 */
import type { IsoDate, StoredEdit, StoredPart, StoredPlanBody, StoredRequest, StoredWorkout } from "../model/types";
import { addDays, round, sortedBy, weekday } from "./py";

export const MIN_PART_S = 5 * 60;
export const KEEP_WARM_S = 10 * 60;
const FIXED = ["warmup", "cooldown", "work"];

const total = (p: StoredPart) => p.repeat * p.duration_s + (p.repeat - 1) * p.rest_s;

/** A workout the athlete may move and shorten: not a race, not the retest. */
export const editable = (w: StoredWorkout) => w.goal == null && !w.parts.some((p) => p.kind === "test" || p.kind === "race");

/** The parts cut to at most `minutes` (edits.shorten): the steady parts (the longest first) down to five minutes, the
 * cool-down and the warm-up down to ten, whole repeats (the last set first, down to one), the ends down to five; when a
 * repeat took more than needed, the warm-up and then the cool-down get the rest back. */
export function shorten(parts: StoredPart[], minutes: number): StoredPart[] {
  const out = parts.map((p) => ({ ...p }));
  let over = out.reduce((s, p) => s + total(p), 0) - minutes * 60;
  if (over <= 0) return parts;
  const cut = (i: number, floor: number) => {
    const room = out[i].duration_s - floor;
    if (over > 0 && room > 0) {
      const c = Math.min(room, over);
      out[i].duration_s -= c;
      over -= c;
    }
  };
  const steady = sortedBy(out.map((p, i) => [p, i] as const).filter(([p]) => !FIXED.includes(p.kind) && p.repeat === 1),
    ([p, i]) => [-p.duration_s, i]).map(([, i]) => i);
  for (const i of steady) cut(i, MIN_PART_S);
  const ends: number[] = [];
  for (const kind of ["cooldown", "warmup"]) for (let i = out.length - 1; i >= 0; i--) if (out[i].kind === kind) ends.push(i);
  for (const i of ends) cut(i, KEEP_WARM_S);
  for (let i = out.length - 1; i >= 0; i--) {
    while (over > 0 && out[i].repeat > 1) {
      out[i].repeat -= 1;
      over -= out[i].duration_s + out[i].rest_s;
    }
  }
  for (const i of ends) cut(i, MIN_PART_S);
  for (const i of [...ends].reverse()) {
    if (over < 0) {
      const back = Math.min(-over, parts[i].duration_s - out[i].duration_s);
      out[i].duration_s += back;
      over += back;
    }
  }
  return out;
}

/** The fewest minutes `shorten` can cut the workout to. */
export const shortest = (w: StoredWorkout) => round(shorten(w.parts, 0).reduce((s, p) => s + total(p), 0) / 60);

/** The clock time of a workout moved to `day` (edits.start_on). */
export function startOn(request: StoredRequest, day: IsoDate, sport: string): string | null {
  const slots = request.availability.filter((s) => s.weekday === weekday(day));
  const fit = slots.filter((s) => !s.sports.length || s.sports.includes(sport));
  return (fit[0] ?? slots[0])?.start ?? null;
}

/** The plan with its edits laid on the weeks, and the edits whose workout is gone dropped (edits.apply_edits). The
 * same plan when it has none. */
export function applyEdits(plan: StoredPlanBody, request: StoredRequest): StoredPlanBody {
  if (!plan.edits?.length) return plan;
  const weeks = plan.weeks.map((wk) => ({ ...wk, workouts: [...wk.workouts] }));
  const kept: StoredEdit[] = [];
  for (const e of plan.edits) {
    let i = -1;
    let j = -1;
    for (let k = 0; k < weeks.length && j < 0; k++) {
      if (weeks[k].sketch) continue;
      j = weeks[k].workouts.findIndex((w) => w.key === e.key);
      i = k;
    }
    if (j < 0) continue;
    const wk = weeks[i];
    let w = wk.workouts[j];
    if (!editable(w)) continue;
    kept.push(e);
    if (e.minutes != null) w = { ...w, parts: shorten(w.parts, e.minutes) };
    if (e.date != null && e.date !== w.date && wk.monday <= e.date && e.date <= addDays(wk.monday, 6))
      w = { ...w, date: e.date, start: startOn(request, e.date, w.sport) };
    wk.workouts[j] = w;
    weeks[i] = { ...wk, workouts: sortedBy(wk.workouts, (x) => [x.date, x.key]) };
  }
  const { edits: _, ...rest } = plan;
  return { ...rest, weeks, ...(kept.length ? { edits: kept } : {}) };
}

/** The edits of a new plan with the same id, and the old plan's for the workouts the new one has none for. */
export function mergedEdits(fresh: StoredEdit[] = [], old: StoredEdit[] = []): StoredEdit[] {
  const have = new Set(fresh.map((e) => e.key));
  return [...fresh, ...old.filter((e) => !have.has(e.key))];
}
