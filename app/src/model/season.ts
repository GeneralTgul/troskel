/**
 * The season strip: the plan's blocks up to the goal, a cell per week, the easy weeks marked, and where today is.
 */
import { addDays, daysBetween } from "./dates";
import type { IsoDate, StoredGoal, StoredPlan } from "./types";

export interface SeasonWeek {
  index: number;
  monday: IsoDate;
  kind: string;
  /** An easy week, or the test week an easy week became (periodize): lighter, green in the strip. */
  easy: boolean;
}

export interface SeasonBlock {
  kind: string;
  weeks: SeasonWeek[];
}

export interface Season {
  blocks: SeasonBlock[];
  goal: StoredGoal | null;
  /** Where today is along the strip, 0 at the first Monday and 1 after the last Sunday; null outside it. */
  marker: number | null;
}

/** The goal the strip leads up to: the next A race from today, otherwise the next race, otherwise the last one. */
export function seasonGoal(stored: StoredPlan, today: IsoDate): StoredGoal | null {
  const goals = [...stored.request.goals].sort((a, b) => a.date.localeCompare(b.date));
  const ahead = goals.filter((g) => g.date >= today);
  return ahead.find((g) => g.priority === "A") ?? ahead[0] ?? goals[goals.length - 1] ?? null;
}

export function season(stored: StoredPlan, today: IsoDate): Season {
  const goal = seasonGoal(stored, today);
  const all = stored.plan.weeks;
  // Up to the week of the goal; the whole plan without one (or a goal past the plan's end).
  const last = goal ? all.findIndex((w) => goal.date >= w.monday && goal.date <= addDays(w.monday, 6)) : -1;
  const weeks = last >= 0 ? all.slice(0, last + 1) : all;
  const blocks: SeasonBlock[] = [];
  for (const w of weeks) {
    const kind = stored.plan.blocks.find((b) => w.index >= b.first_week && w.index < b.first_week + b.weeks)?.kind ?? w.block;
    const cell = { index: w.index, monday: w.monday, kind: w.kind, easy: w.kind === "easy" || w.kind === "test" };
    const prev = blocks[blocks.length - 1];
    if (prev && prev.kind === kind) prev.weeks.push(cell);
    else blocks.push({ kind, weeks: [cell] });
  }
  let marker: number | null = null;
  if (weeks.length) {
    const days = daysBetween(weeks[0].monday, today);
    const span = weeks.length * 7;
    if (days >= 0 && days < span) marker = (days + 0.5) / span;
  }
  return { blocks, goal, marker };
}
