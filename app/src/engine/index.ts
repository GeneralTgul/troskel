/**
 * The plan engine in the app: a port of lopanalys/plan (engine, periodize, rolling, library) to TypeScript,
 * checked against the Python engine with the fixtures that app/scripts/make_fixtures.py writes
 * (test/fixtures/engine.json.gz, test/engine.test.ts). The rule of `rebuild` is written in Python there too
 * (`make_fixtures.rebuild`), with the Python engine's own functions.
 *
 * The contract:
 * - `rebuild` takes the stored plan and the athlete's new times and returns a whole new stored plan.
 * - Workouts dated before `availability.from` are kept as they are (keys, dates, status); the rest of the plan is
 *   rebuilt from that day with the new slots. On the dates in `availability.unavailable` the workouts are dropped
 *   (a race stays); the engine does not move them to another day.
 * - The whole plan is built again on the new slots (same id, same `created`), so the blocks and the goal stay and
 *   the weekly time follows what the new slots hold. The horizon of a rolling plan stays: the weeks after it are
 *   sketches of the new plan, so that Tröskel's *Renew* later gives the same workouts.
 * - `request.availability` in the result is `availability.slots`; `targets`, `targets_log` and `athlete` are
 *   carried over unchanged (the targets depend on the tests, not on the times).
 * - It throws a `PlanError` (with the text key plan.error.*) when no plan can be made, e.g. without a core slot
 *   for running; the app then keeps the times until the coach sends a new plan.
 */
import type { IsoDate, StoredPlan, StoredSlot, StoredWeek } from "../model/types";
import { ENGINE_VERSION, buildPlan, sketchOf } from "./engine";
import { PlanError, normPlan, normRequest } from "./models";
import { addDays, sortedBy } from "./py";

export { ENGINE_VERSION, PlanError };
export { buildPlan } from "./engine";
export { rollForward, due, renewSoon } from "./rolling";

export interface NewAvailability {
  /** One or more slots per weekday (0 = Monday), as `request.availability`. A weekday without a slot is a rest day. */
  slots: StoredSlot[];
  /** Single dates that do not work (travel, a party); ISO dates, at or after `from`. */
  unavailable: IsoDate[];
  /** The first day the new times apply: tomorrow, in the app. */
  from: IsoDate;
}

export interface Engine {
  /** False while the engine is a stub: the app then saves the times without rebuilding. */
  readonly available: boolean;
  /** The engine version, as `plan.engine_version` in what it builds. */
  readonly version: string;
  rebuild(stored: StoredPlan, availability: NewAvailability): StoredPlan;
}

export class EngineMissing extends Error {
  constructor() {
    super("not yet");
  }
}

export const stub: Engine = {
  available: false,
  version: "",
  rebuild() {
    throw new EngineMissing();
  },
};

export function rebuild(stored: StoredPlan, { slots, unavailable, from }: NewAvailability): StoredPlan {
  if (stored.plan.source) throw new Error("the plan was written outside the engine");
  const request = normRequest({ ...stored.request, availability: slots });
  const old = normPlan(stored.plan);
  const fresh = buildPlan(request, { planId: old.id, created: old.created });
  if (fresh.weeks.length !== old.weeks.length) throw new Error("the rebuilt plan has another length");
  const off = new Set(unavailable);
  const weeks: StoredWeek[] = old.weeks.map((was, i) => {
    const made = fresh.weeks[i];
    if (addDays(was.monday, 6) < from) return was;
    if (old.horizon != null && made.index > old.horizon) return sketchOf(made);
    const kept = was.workouts.filter((w) => w.date < from);
    const now = made.workouts.filter((w) => w.date >= from && (w.goal != null || !off.has(w.date)));
    return { ...made, workouts: sortedBy([...kept, ...now], (w) => [w.date, w.key]) };
  });
  return {
    ...stored,
    request,
    plan: { ...old, engine_version: ENGINE_VERSION, blocks: fresh.blocks, notes: fresh.notes, weeks },
  };
}

export const engine: Engine = { available: true, version: ENGINE_VERSION, rebuild };
