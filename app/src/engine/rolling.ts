/**
 * The rolling plan: the weeks ahead get their workouts as the time comes. A port of lopanalys/plan/rolling.py.
 * Pure functions of the stored plan and the day: no clock and no randomness. The athlete's changes of single
 * workouts (`plan.edits`) are laid on the new weeks again (edits.ts).
 */
import type { IsoDate, StoredPlanBody, StoredRequest } from "../model/types";
import { applyEdits } from "./edits";
import { ENGINE_VERSION, fillWeeks, horizonFor } from "./engine";
import type { Skeleton, WeekSpec } from "./periodize";
import { addDays, sortedBy } from "./py";

/** The stored plan's sketch as the engine's skeleton: every week's block, kind, time and races. */
export function skeletonOf(plan: StoredPlanBody, request: StoredRequest): Skeleton {
  const goals = sortedBy(request.goals, (g) => g.date);
  const weeks: WeekSpec[] = plan.weeks.map((wk) => ({
    index: wk.index, monday: wk.monday, block: wk.block, kind: wk.kind, target_minutes: wk.target_minutes,
    goals: goals.filter((g) => wk.monday <= g.date && g.date <= addDays(wk.monday, 6)),
  }));
  const a = goals.filter((g) => g.priority === "A");
  return { weeks, blocks: plan.blocks, notes: [], goal: a.length ? a[a.length - 1] : null };
}

/** How many weeks `rollForward` would detail on `today` (0: nothing to do). */
export function due(plan: StoredPlanBody, today: IsoDate): number {
  if (plan.horizon == null || !plan.weeks.length) return 0;
  return Math.max(0, horizonFor(plan.weeks[0].monday, plan.weeks.length, today) - plan.horizon);
}

/** True when the detailed part ends within `days` and there are weeks after it to detail. */
export function renewSoon(plan: StoredPlanBody, today: IsoDate, days = 7): boolean {
  if (plan.horizon == null || plan.horizon >= plan.weeks.length) return false;
  const detailed = plan.weeks.filter((w) => !w.sketch);
  const last = detailed.length ? detailed[detailed.length - 1] : plan.weeks[0];
  const sunday = addDays(last.monday, 6);
  return sunday < addDays(today, days);
}

/** The stored plan with the weeks up to four weeks from `today` detailed; the rest is kept as it was. */
export function rollForward(plan: StoredPlanBody, request: StoredRequest, today: IsoDate): StoredPlanBody {
  if (due(plan, today) === 0) return plan;
  const horizon = plan.horizon!;
  const next = horizonFor(plan.weeks[0].monday, plan.weeks.length, today);
  const filled = fillWeeks(request, skeletonOf(plan, request), plan.id);
  const weeks = plan.weeks.map((old, i) => (horizon < old.index && old.index <= next ? filled[i] : old));
  return applyEdits({ ...plan, weeks, horizon: next, engine_version: ENGINE_VERSION }, request);
}
