/** The example plan (public/demo.troskel), moved by whole weeks so that it is under way today. */
import { addDays, daysBetween, mondayOf } from "./dates";
import type { IsoDate, StoredPlan } from "./types";

const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** A copy of the plan with every date `days` later (whole weeks keep the weekdays). */
export function shiftPlan(stored: StoredPlan, days: number): StoredPlan {
  const walk = (v: unknown): unknown => {
    if (typeof v === "string") return ISO.test(v) ? addDays(v, days) : v;
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x)]));
    return v;
  };
  const out = walk(stored) as StoredPlan;
  return out;
}

/** The example plan with its second week as this week. */
export function liveDemo(stored: StoredPlan, todayIso: IsoDate): StoredPlan {
  const first = stored.plan.weeks[0]?.monday;
  if (!first) return stored;
  return shiftPlan(stored, daysBetween(first, addDays(mondayOf(todayIso), -7)));
}
