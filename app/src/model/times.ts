/** The athlete's times as one row per weekday, and back to the plan's slots. */
import type { IsoDate, StoredPlan, StoredSlot } from "./types";

export interface DayTimes {
  on: boolean;
  minutes: number;
  start: string; // "HH:MM", "" for no set time
}

const DEFAULT_MINUTES = 45;

/** One row per weekday from the core slots (the first one of a day; extra slots are kept as they are). */
export function daysFromSlots(slots: StoredSlot[]): DayTimes[] {
  return Array.from({ length: 7 }, (_, wd) => {
    const s = slots.find((x) => x.weekday === wd && x.role !== "extra");
    return s ? { on: true, minutes: s.max_minutes, start: s.start ? s.start.slice(0, 5) : "" }
      : { on: false, minutes: DEFAULT_MINUTES, start: "" };
  });
}

/** The slots for the rows: changed core slots, the rest of the original slots as they were. */
export function slotsFromDays(days: DayTimes[], original: StoredSlot[], sports: string[]): StoredSlot[] {
  const out: StoredSlot[] = [];
  days.forEach((d, wd) => {
    const old = original.filter((x) => x.weekday === wd && x.role !== "extra");
    if (d.on) {
      const base = old[0];
      out.push({
        weekday: wd, max_minutes: Math.max(5, Math.round(d.minutes)), sports: base?.sports ?? [...sports],
        start: d.start ? `${d.start}:00` : null, role: base?.role ?? "core",
      });
      out.push(...old.slice(1));
    }
    out.push(...original.filter((x) => x.weekday === wd && x.role === "extra"));
  });
  return out;
}

/** The athlete's own times to keep when a new plan replaces `old` (#776): a new test gives a new link with the
 * same plan id, built on the times the coach has. When the athlete had changed the times in the app ("Mina tider"
 * rebuilt the plan), the new plan would drop them without a word. They are kept as pending times instead, so the
 * athlete sees them and chooses to lay the new plan on them; the app never rebuilds it by itself. None: nothing to
 * keep (another plan, the same times, or a plan written outside the engine, whose times are not the athlete's). */
export function keptTimes(old: StoredPlan | null, incoming: StoredPlan, today: IsoDate): {
  slots: StoredSlot[]; unavailable: IsoDate[]; saved: IsoDate; kept: true;
} | null {
  if (!old || old.plan.id !== incoming.plan.id || old.plan.source || incoming.plan.source) return null;
  const key = (s: StoredSlot[]) => JSON.stringify(s.map((x) => [x.weekday, x.max_minutes, x.start, x.role, [...x.sports].sort()]));
  if (key(old.request.availability) === key(incoming.request.availability)) return null;
  return { slots: old.request.availability, unavailable: [], saved: today, kept: true };
}
