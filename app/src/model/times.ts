/** The athlete's times as one row per weekday, and back to the plan's slots. */
import type { PendingTimes } from "./storage";
import type { IsoDate, StoredPlan, StoredSlot } from "./types";

export interface DayTimes {
  on: boolean;
  minutes: number;
  start: string; // "HH:MM", "" for no set time
}

const DEFAULT_MINUTES = 45;
/** The longest slot of a day, as the coach's form in Tröskel (views/plan.py MAX_SLOT, request.ts). */
export const MAX_MINUTES = 480;

const isDay = (d: unknown): d is IsoDate => typeof d === "string" && /^\d{4}-\d\d-\d\d$/.test(d);

/** Times read from storage or from a copy: null for anything the views cannot show (no slots, a slot without its
 * weekday, minutes or sports), so that broken times never stop "Mina tider" from opening. */
export function checkTimes(v: unknown): PendingTimes | null {
  const p = v as Partial<PendingTimes> | null;
  if (!p || typeof p !== "object" || !Array.isArray(p.slots) || typeof p.saved !== "string") return null;
  const ok = p.slots.every((s) => s && typeof s === "object" && Number.isInteger(s.weekday) && s.weekday >= 0
    && s.weekday <= 6 && Number.isFinite(s.max_minutes) && Array.isArray(s.sports));
  if (!ok) return null;
  return { ...p, slots: p.slots, saved: p.saved, unavailable: Array.isArray(p.unavailable) ? p.unavailable.filter(isDay) : [] };
}

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
        weekday: wd, max_minutes: Math.min(MAX_MINUTES, Math.max(5, Math.round(d.minutes) || 0)), sports: base?.sports ?? [...sports],
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
 * rebuilt the plan, or `pending` waits there), the new plan would drop them without a word. They are kept as pending
 * times instead, so the athlete sees them and chooses to lay the new plan on them; the app never rebuilds it by
 * itself. None: nothing to keep (another plan, or the athlete's times already in the new plan). The coach who read the athlete's plan in from the phone (#781)
 * sends a plan that has the athlete's times, also those that were waiting, so it gives none. */
export function keptTimes(old: StoredPlan | null, incoming: StoredPlan, today: IsoDate, pending: PendingTimes | null = null): {
  slots: StoredSlot[]; unavailable: IsoDate[]; saved: IsoDate; kept: true;
} | null {
  if (!old || old.plan.id !== incoming.plan.id) return null;
  const key = (s: StoredSlot[]) => JSON.stringify(s.map((x) => [x.weekday, x.max_minutes, x.start, x.role, [...x.sports].sort()]));
  const own = pending?.slots ?? old.request.availability;
  if (key(own) === key(incoming.request.availability)) return null;
  return { slots: own, unavailable: (pending?.unavailable ?? []).filter((d) => d > today), saved: today, kept: true };
}
