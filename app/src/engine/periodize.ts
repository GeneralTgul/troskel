/**
 * Blocks and weeks: the skeleton of a plan, counted backwards from the goal. A port of
 * lopanalys/plan/periodize.py; the rules are in its docstring and in docs/TRANINGSPLAN.md.
 */
import type { IsoDate, StoredBlock, StoredGoal, StoredNote, StoredRequest, StoredSlot } from "../model/types";
import * as library from "./library";
import { LEVELS, PlanError, mainSport, note, planSports } from "./models";
import { addDays, daysBetween, floordiv, round, sortedBy, weekday } from "./py";

export const DEFAULT_WEEKS = 12;
export const MIN_WEEKS = 4;
export const MAX_WEEKLY_INCREASE = 0.10;
export const EASY_FACTOR = 0.65;
export const B_RACE_FACTOR = 0.75;
export const TAPER_FACTORS: Record<number, number[]> = { 1: [0.55], 2: [0.70, 0.50] };
export const LONG_RACE_KM = 25.0;
export const RETEST_MIN_GAP = 8;
export const RETEST_BEFORE_GOAL = 4;
export const CYCLE_MAX = 12;
export const DEFAULT_SHARE = 0.70;
/** Recovery after a race (periodize.RECOVERY_*): one easy day per this many km of the race, by sport. */
export const RECOVERY_KM_PER_DAY: Record<string, number> = { run: 3.0, bike: 15.0, skierg: 10.0, ski: 10.0, swim: 1.0 };
export const RECOVERY_A_DEFAULT = 7;
export const RECOVERY_B_FROM = 7;
export const RECOVERY_MAX_DAYS = 21;

/** periodize.recovery_days: the easy days after an A or B race (0: the two easy days every such race has). */
export function recoveryDays(g: StoredGoal): number {
  if (g.priority !== "A" && g.priority !== "B") return 0;
  let days = g.distance_km ? Math.ceil(g.distance_km / (RECOVERY_KM_PER_DAY[g.sport] ?? RECOVERY_KM_PER_DAY.run))
    : g.priority === "A" ? RECOVERY_A_DEFAULT : 0;
  days = Math.min(days, RECOVERY_MAX_DAYS);
  if (g.priority === "B" && days < RECOVERY_B_FROM) return 0;
  return days > 2 ? days : 0;
}

export interface Level {
  start_minutes: number;
  growth: number;
  load_weeks: number;
  max_sessions: number;
  first_step: number;
  long_cap: number;
}

const lv = (start_minutes: number, growth: number, load_weeks: number, max_sessions: number, first_step: number,
  long_cap: number): Level => ({ start_minutes, growth, load_weeks, max_sessions, first_step, long_cap });

export const LEVEL: Record<string, Level> = {
  beginner: lv(120, 0.05, 2, 3, 0, 90),
  recreational: lv(180, 0.07, 3, 5, 1, 150),
  trained: lv(300, 0.08, 3, 7, 2, 180),
};
if (LEVELS.some((l) => !LEVEL[l])) throw new Error("LEVEL and LEVELS differ");

export interface WeekSpec {
  index: number;
  monday: IsoDate;
  block: string;
  kind: string;
  target_minutes: number;
  goals: StoredGoal[];
}

export interface Skeleton {
  weeks: WeekSpec[];
  blocks: StoredBlock[];
  notes: StoredNote[];
  goal: StoredGoal | null;
}

export const firstMonday = (d: IsoDate) => addDays(d, ((-weekday(d)) % 7 + 7) % 7);
export const round5 = (minutes: number) => round(minutes / 5.0) * 5;

/** The core slots for a sport, or (null) for any of the plan's sports: one per weekday (the longest), in weekday order. */
export function coreSlots(r: StoredRequest, sport: string | null = null): StoredSlot[] {
  const wanted = sport !== null ? [sport] : planSports(r);
  const best = new Map<number, StoredSlot>();
  for (const s of r.availability) {
    if (s.role !== "core" || !s.sports.some((x) => wanted.includes(x)) || s.max_minutes <= 0
      || !(s.weekday >= 0 && s.weekday <= 6))
      continue;
    const b = best.get(s.weekday);
    if (!b || s.max_minutes > b.max_minutes) best.set(s.weekday, s);
  }
  return [...best.keys()].sort((a, b) => a - b).map((d) => best.get(d)!);
}

/** periodize.second_slots (#666, two workouts a day): beside the day's core slot, another core slot at another
 * clock time, when the two allow two different sports of the plan. At most one a day (the longest), in weekday order. */
export function secondSlots(r: StoredRequest, first: StoredSlot[] | null = null): StoredSlot[] {
  const sports = planSports(r);
  if (sports.length < 2) return [];
  const days = new Map((first ?? coreSlots(r)).map((s) => [s.weekday, s]));
  const best = new Map<number, StoredSlot>();
  for (const s of r.availability) {
    const p = days.get(s.weekday);
    if (p === undefined || s === p || s.role !== "core" || s.max_minutes <= 0) continue;
    if (s.start == null || p.start == null || s.start === p.start) continue;
    const mine = s.sports.filter((x) => sports.includes(x));
    const theirs = p.sports.filter((x) => sports.includes(x));
    if (!mine.some((a) => theirs.some((b) => a !== b))) continue;
    const b = best.get(s.weekday);
    if (!b || s.max_minutes > b.max_minutes) best.set(s.weekday, s);
  }
  return [...best.keys()].sort((a, b) => a - b).map((d) => best.get(d)!);
}

/** The long run's slot: the longest slot for the main sport, the later day on a tie (else the longest of all). */
export function longSlot(all: StoredSlot[], main: string): StoredSlot {
  const own = all.filter((s) => s.sports.includes(main));
  const slots = own.length ? own : all;
  let best = slots[0];
  for (const s of slots.slice(1))
    if (s.max_minutes > best.max_minutes || (s.max_minutes === best.max_minutes && s.weekday > best.weekday)) best = s;
  return best;
}

/** The minutes a week can really hold (periodize.week_capacity). */
export function weekCapacity(r: StoredRequest, sport: string | null = null): number {
  const level = LEVEL[r.level];
  const slots = coreSlots(r, sport);
  if (!slots.length) return 0;
  const ls = longSlot(slots, mainSport(r));
  const easyMax = library.get("easy").max_minutes;
  const seconds = sport === null ? secondSlots(r, slots) : [];
  const held = [...slots, ...seconds].map((s) => (s === ls ? Math.min(s.max_minutes, level.long_cap) : Math.min(s.max_minutes, easyMax)))
    .sort((a, b) => b - a);
  return held.slice(0, level.max_sessions).reduce((a, b) => a + b, 0);
}

export function firstWeekMinutes(r: StoredRequest, sport: string | null = null): [number, string] {
  const cap = weekCapacity(r, sport);
  const recent = r.recent_weekly_minutes;
  if (recent) return recent > cap ? [cap, "capped"] : [Math.trunc(recent), "recent"];
  return [Math.min(LEVEL[r.level].start_minutes, round5(DEFAULT_SHARE * cap)), "default"];
}

export function growthRate(start: number, cap: number, increments: number, levelRate: number): number {
  if (start >= cap || increments <= 0 || start <= 0) return 0.0;
  return Math.min(levelRate, MAX_WEEKLY_INCREASE, (cap / start) ** (1 / increments) - 1);
}

function cycles(n: number): number[] {
  let k = Math.max(1, Math.ceil(n / CYCLE_MAX));
  if (k > 1 && n / k < RETEST_MIN_GAP) k -= 1;
  const base = Math.floor(n / k);
  const extra = n % k;
  return Array.from({ length: k }, (_, i) => base + (i < extra ? 1 : 0));
}

function goalBlocks(n: number, goal: StoredGoal): [string, number][] {
  const longRace = (goal.distance_km || 0) >= LONG_RACE_KM;
  const taper = longRace && n >= 8 ? 2 : 1;
  const peak = n < 6 ? 0 : n >= 10 ? 2 : 1;
  const rest = n - taper - peak;
  const build = rest <= 4 ? rest : Math.min(8, Math.max(4, floordiv(rest, 2)));
  return [["base", rest - build], ["build", build], ["peak", peak], ["taper", taper]];
}

export function periodize(r: StoredRequest): Skeleton {
  if (!Object.hasOwn(LEVEL, r.level)) throw new PlanError("plan.error.level", { level: r.level });
  const slots = coreSlots(r);
  if (!slots.length) throw new PlanError("plan.error.no_slots");
  const level = LEVEL[r.level];
  const start = firstMonday(r.start);
  const notes: StoredNote[] = [];

  const aGoals = sortedBy(r.goals.filter((g) => g.priority === "A"), (g) => g.date);
  const goal = aGoals.length ? aGoals[aGoals.length - 1] : null;
  let n: number;
  let layout: [string, number][];
  let cycleEnds: number[] = [];
  if (goal) {
    if (goal.date < addDays(start, 7)) throw new PlanError("plan.error.goal_too_close", { name: goal.name });
    n = floordiv(daysBetween(start, goal.date), 7) + 1;
    layout = goalBlocks(n, goal);
    if (n < 6) notes.push(note("plan.note.short", { weeks: n }));
  } else {
    n = r.duration_weeks || DEFAULT_WEEKS;
    if (n < MIN_WEEKS) throw new PlanError("plan.error.too_short", { weeks: MIN_WEEKS });
    layout = [];
    let w = 0;
    for (const length of cycles(n)) {
      layout.push(["base", Math.ceil(length / 2)], ["build", length - Math.ceil(length / 2)]);
      w += length;
      cycleEnds.push(w);
    }
  }

  // ---- blocks and the block of every week
  const blocks: StoredBlock[] = [];
  const blockOf: string[] = [];
  let first = 1;
  for (const [kind, weeks] of layout) {
    if (weeks <= 0) continue;
    const last = first + weeks - 1;
    let reason: StoredNote;
    if (goal) reason = note(`plan.reason.${kind}`, { weeks, goal: goal.name });
    else {
      const testWeek = cycleEnds.find((e) => e >= last)!;
      reason = note(`plan.reason.${kind}_cycle`, { weeks, test_week: testWeek });
    }
    blocks.push({ kind, first_week: first, weeks, start: addDays(start, 7 * (first - 1)), reason });
    for (let i = 0; i < weeks; i++) blockOf.push(kind);
    first = last + 1;
  }

  // ---- load, easy, test, taper and race weeks
  const period = level.load_weeks + 1;
  const kinds: string[] = Array(n).fill("load");
  if (goal) {
    const taperFirst = blocks.find((b) => b.kind === "taper")!.first_week;
    for (let w = 2; w < taperFirst; w++) if ((taperFirst - w) % period === 0) kinds[w - 1] = "easy";
    for (let w = taperFirst; w <= n; w++) kinds[w - 1] = "taper";
    kinds[n - 1] = "race";
    let lastTest = 0;
    // No test in a week with days of recovery after a race: the next easy week.
    const rest = r.goals.filter((g) => g !== goal).map((g) => [g.date, addDays(g.date, recoveryDays(g))] as const);
    const resting = new Set<number>();
    for (let w = 1; w <= n; w++)
      for (const [a, b] of rest)
        if (a < addDays(start, 7 * (w - 1) + 6) && b >= addDays(start, 7 * (w - 1)) && b > a) resting.add(w);
    for (let w = 1; w < taperFirst; w++)
      if (kinds[w - 1] === "easy" && w - lastTest >= RETEST_MIN_GAP && n - w >= RETEST_BEFORE_GOAL && !resting.has(w)) {
        kinds[w - 1] = "test";
        lastTest = w;
      }
  } else {
    let prevEnd = 0;
    for (const end of cycleEnds) {
      for (let w = prevEnd + 2; w < end; w++) if ((end - w) % period === 0) kinds[w - 1] = "easy";
      kinds[end - 1] = "test";
      prevEnd = end;
    }
  }

  // ---- weekly volume
  const cap = weekCapacity(r);
  const [firstMinutes, source] = firstWeekMinutes(r);
  if (source === "capped") notes.push(note("plan.note.capped", { minutes: cap }));
  const steps = kinds.filter((k, i) => k === "load" && blockOf[i] !== "peak").length - 1;
  const rate = growthRate(firstMinutes, cap, steps, level.growth);
  const byWeek = new Map<number, StoredGoal[]>();
  for (const g of sortedBy(r.goals, (g) => g.date)) {
    const idx = g.date >= start ? floordiv(daysBetween(start, g.date), 7) + 1 : 0;
    if (idx >= 1 && idx <= n) {
      if (!byWeek.has(idx)) byWeek.set(idx, []);
      byWeek.get(idx)!.push(g);
    } else if (g.date < start && addDays(g.date, recoveryDays(g)) >= start) {
      notes.push(note("plan.note.recovery_before", { name: g.name, until: addDays(g.date, recoveryDays(g)) }));
    } else notes.push(note("plan.note.goal_outside", { name: g.name }));
  }

  const weeks: WeekSpec[] = [];
  let load: number | null = null;
  let taperI = 0;
  const taperLen = kinds.filter((k) => k === "taper" || k === "race").length;
  kinds.forEach((kind, i0) => {
    const i = i0 + 1;
    const block = blockOf[i0];
    let minutes: number;
    if (kind === "load") {
      if (load === null) load = firstMinutes;
      else if (block !== "peak") load = Math.min(cap, load * (1 + rate));
      minutes = load;
    } else if (kind === "easy" || kind === "test") {
      minutes = EASY_FACTOR * (load !== null ? load : firstMinutes);
    } else {
      minutes = TAPER_FACTORS[Math.min(taperLen, 2)][Math.min(taperI, 1)] * (load !== null ? load : firstMinutes);
      taperI += 1;
    }
    const goals = byWeek.get(i) ?? [];
    if (kind === "load" && goals.some((g) => g.priority === "A" || g.priority === "B")) minutes *= B_RACE_FACTOR;
    weeks.push({ index: i, monday: addDays(start, 7 * (i - 1)), block, kind, target_minutes: round5(minutes), goals });
  });
  return { weeks, blocks, notes, goal };
}
