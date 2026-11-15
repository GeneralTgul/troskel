/** The athlete's times as one row per weekday, and back to the plan's slots. */
import type { StoredSlot } from "./types";

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
