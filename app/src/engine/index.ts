/**
 * The plan engine's interface. The engine itself (a port of lopanalys/plan/engine.py, checked against Python
 * with shared JSON fixtures) comes later; until then `engine` is a stub that says it is not there, and the app
 * keeps the athlete's new times until the coach sends a new plan.
 *
 * The contract for the port:
 * - `rebuild` takes the stored plan and the athlete's new times and returns a whole new stored plan.
 * - Workouts dated before `availability.from` are kept as they are (keys, dates, status); the rest of the plan is
 *   rebuilt from that day with the new slots, skipping the dates in `availability.unavailable`.
 * - `request.availability` in the result is `availability.slots`; `targets`, `targets_log` and `athlete` are
 *   carried over unchanged (the targets depend on the tests, not on the times).
 * - It never throws for a plan it accepts; `EngineMissing` is only for the stub.
 */
import type { IsoDate, StoredPlan, StoredSlot } from "../model/types";

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

export const engine: Engine = stub;
