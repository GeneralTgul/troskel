/**
 * *Ändra passet* (#777, step 3): the athlete moves a workout to another day of its week, swaps two, or shortens one.
 * Each change is an edit in `plan.edits` (engine/edits.ts), so that it stays when the plan is rebuilt with new times,
 * renewed or laid on a new test, in the app and in Tröskel (plan/edits.py), and it goes to the coach with the plan
 * (#781).
 *
 * To undo or change an edit, the workout as the engine made it is made again (the weeks are filled from the plan's own sketch, as
 * `rollForward` does, and give the same workouts) and the edits are laid on it.
 */
import { applyEdits, editable, mergedEdits, shortest } from "../engine/edits";
import { fillWeeks } from "../engine/engine";
import { normRequest } from "../engine/models";
import { sortedBy } from "../engine/py";
import { skeletonOf } from "../engine/rolling";
import { addDays } from "./dates";
import { workoutMinutes } from "./plan";
import type { IsoDate, StoredEdit, StoredPlan, StoredWeek, StoredWorkout } from "./types";

export const editOf = (stored: StoredPlan, key: string): StoredEdit | undefined =>
  stored.plan.edits?.find((e) => e.key === key);

/** The workout as the engine made it, before the athlete's edit; undefined when it cannot be made again. */
export function original(stored: StoredPlan, key: string): StoredWorkout | undefined {
  const request = normRequest(stored.request);
  const weeks = fillWeeks(request, skeletonOf(stored.plan, request), stored.plan.id);
  const now = findWorkout(stored, key);
  for (const wk of weeks) for (const w of wk.workouts)
    if (w.key === key && (!now || now.template_id === w.template_id)) return w;
  return undefined;
}

function findWorkout(stored: StoredPlan, key: string): StoredWorkout | undefined {
  for (const wk of stored.plan.weeks) if (!wk.sketch) for (const w of wk.workouts) if (w.key === key) return w;
  return undefined;
}

const weekWith = (stored: StoredPlan, w: StoredWorkout): StoredWeek | undefined =>
  stored.plan.weeks.find((wk) => !wk.sketch && wk.workouts.some((x) => x.key === w.key));

/** True when the athlete may change the workout on `today`: a workout that is not a race or a test, not before
 * today. */
export const adjustable = (_stored: StoredPlan, w: StoredWorkout, today: IsoDate) => editable(w) && w.date >= today;

/** The days of the workout's week it may move to: from today on, not its own day. */
export function moveDays(stored: StoredPlan, w: StoredWorkout, today: IsoDate): IsoDate[] {
  const wk = weekWith(stored, w);
  if (!wk) return [];
  return Array.from({ length: 7 }, (_, i) => addDays(wk.monday, i)).filter((d) => d >= today && d !== w.date);
}

/** The workouts of the same week it may swap with: changeable, on another day. */
export function swapWith(stored: StoredPlan, w: StoredWorkout, today: IsoDate): StoredWorkout[] {
  const wk = weekWith(stored, w);
  return wk ? wk.workouts.filter((x) => x.key !== w.key && x.date !== w.date && adjustable(stored, x, today)) : [];
}

/** The lengths it may be cut to, in five minutes, the longest first: from the original's length less five down to
 * the shortest the parts allow. */
export function shortenTo(stored: StoredPlan, w: StoredWorkout): number[] {
  const base = original(stored, w.key) ?? w;
  const top = Math.ceil(workoutMinutes(base) / 5) * 5 - 5;
  const low = Math.max(5, Math.ceil(shortest(base) / 5) * 5);
  const out: number[] = [];
  for (let m = top; m >= low; m -= 5) out.push(m);
  return out;
}

/**
 * The plan with the edits of `changes` in place of the ones of their workouts: each change is a workout's key with
 * the fields to set (`date`, `minutes`, undefined to drop that field) or null to undo the whole edit. A field equal to
 * the original's (the same day, the full length) is no change and is dropped.
 */
export function withEdits(stored: StoredPlan, changes: Record<string, Partial<Omit<StoredEdit, "key">> | null>): StoredPlan {
  const edits = [...(stored.plan.edits ?? [])];
  const weeks = stored.plan.weeks.map((wk) => ({ ...wk, workouts: [...wk.workouts] }));
  for (const [key, change] of Object.entries(changes)) {
    const was = edits.findIndex((e) => e.key === key);
    const orig = original(stored, key);
    const next: StoredEdit = { ...(was >= 0 ? edits[was] : { key }), ...(change ?? {}) };
    if (change === null) {
      delete next.date;
      delete next.minutes;
    }
    if (next.date === undefined || (orig && next.date === orig.date)) delete next.date;
    if (next.minutes === undefined || (orig && next.minutes >= workoutMinutes(orig))) delete next.minutes;
    if (was >= 0) edits.splice(was, 1);
    if (next.date !== undefined || next.minutes !== undefined) edits.push(next);
    if (orig) {                       // the engine's workout again, with its mark, before the edits are laid on it
      for (const wk of weeks) {
        const j = wk.workouts.findIndex((w) => w.key === key);
        if (j < 0) continue;
        wk.workouts[j] = { ...orig, status: wk.workouts[j].status };
        wk.workouts = sortedBy(wk.workouts, (x) => [x.date, x.key]);
      }
    }
  }
  const plan = { ...stored.plan, weeks };
  delete plan.edits;
  return { ...stored, plan: applyEdits(edits.length ? { ...plan, edits } : plan, normRequest(stored.request)) };
}

export const moveWorkout = (stored: StoredPlan, key: string, date: IsoDate) => withEdits(stored, { [key]: { date } });

export function swapWorkouts(stored: StoredPlan, a: StoredWorkout, b: StoredWorkout): StoredPlan {
  return withEdits(stored, { [a.key]: { date: b.date }, [b.key]: { date: a.date } });
}

export const shortenWorkout = (stored: StoredPlan, key: string, minutes: number) => withEdits(stored, { [key]: { minutes } });

export const undoEdit = (stored: StoredPlan, key: string) => withEdits(stored, { [key]: null });

/**
 * The athlete's edits to keep when a plan from the coach replaces `old` (as `keptTimes` and `keptOwnValues`): the same
 * plan id. The new plan's own edits stay (the coach read the phone's plan in), and the old
 * plan's for the workouts it has none for are laid on it. Another plan: none are kept.
 */
export function keptEdits(old: StoredPlan | null, incoming: StoredPlan): StoredPlan {
  if (!old?.plan.edits?.length || old.plan.id !== incoming.plan.id) return incoming;
  const edits = mergedEdits(incoming.plan.edits, old.plan.edits);
  if (edits.length === (incoming.plan.edits?.length ?? 0)) return incoming;
  return { ...incoming, plan: applyEdits({ ...incoming.plan, edits }, normRequest(incoming.request)) };
}
