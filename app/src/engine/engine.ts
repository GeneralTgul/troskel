/**
 * Fills the skeleton with workouts: which slot gets which template, and how long. A port of
 * lopanalys/plan/engine.py, rule for rule and in the same order (the rules are in its docstring and in
 * docs/TRANINGSPLAN.md). Python's dicts keep their order, so they are Maps here.
 */
import type {
  IsoDate, StoredGoal, StoredNote, StoredPlanBody, StoredRequest, StoredRole, StoredSlot, StoredWeek, StoredWorkout,
} from "../model/types";
import * as library from "./library";
import {
  BORROWS, DEFAULT_SYSTEM, CODEC_FORMAT, EASY_ZONES, type FixedDay, RUNNING, STRENGTH, WEEKDAY_KEYS, capacityFor, choices,
  estimated, mainSport, minutes, note, ownCapacity, planRoles, qualityAsked, roleWeight, sketchOf, speedFromHr,
  strengthCount, weekMinutes, zoneSeconds,
} from "./models";
import {
  LEVEL, type Level, MAX_WEEKLY_INCREASE, type Skeleton, type WeekSpec, coreSlots, longCap, longSlot, periodize, recoveryDays,
  round5, secondSlots,
} from "./periodize";
import { addDays, cmp, combinations, daysBetween, floordiv, isoYearWeek, maxBy, minBy, mod, sortedBy, sum, weekday } from "./py";

export const ENGINE_VERSION = "9";
/** No strength in the days before an A or B race (engine.STRENGTH_CLEAR_DAYS). */
export const STRENGTH_CLEAR_DAYS = 3;
/** The key suffix of a day's second workout, and its weight when the easy minutes are shared (engine.SECOND). */
export const SECOND = "-2";
export const SECOND_WEIGHT = 0.7;
/** A supporting sport's smaller long workout: at least this long, at most this share of the long run. */
export const SUPPORT_LONG_MIN = 60;
export const SUPPORT_LONG_OF_MAIN = 0.75;
/** The longest workout in the first and the second half of the recovery after a race (engine.RECOVERY_MAX). */
export const RECOVERY_MAX = [30, 45];
/** A fixed easy day with a length when the easy minutes are shared, the key offset of a quality session's fill in
 * the sharing, and the base block's rotation (engine.FIXED_WEIGHT, FILL, QUALITY_BASE). */
export const FIXED_WEIGHT = 2.0;
export const FILL = 14;
export const QUALITY_BASE = ["fartlek", "hills", "threshold"];
export const MAX_HARD_SHARE = 0.25;
export const MIN_SESSION = 30;
export const LONG_SHARE = 0.33;
export const LONG_MIN = 30;
export const LONG_STEP = 15;
export const QUALITY_MIN_SLOT = 40;
export const PRE_RACE_MAX = [30, 40];
export const RACE_WEEK_MAX = 45;
export const BEGINNER_EARLY_WEEKS = 4;
export const PROGRESSIVE_FROM_KM = 20.0;
export const STEP_BACK = 2;
export const DETAIL_WEEKS = 4;
/** The part of the other sports' share the main sport takes near the goal (engine.SHARE_SHIFT). */
export const SHARE_SHIFT: Record<string, number> = { peak: 0.5, taper: 0.75 };

export const floor5 = (m: number) => floordiv(m, 5) * 5;

interface State {
  steps: Map<string, number>;
  last_long: number | null;
  max_minutes: number | null;
  base_i: number;
  build_i: number;
  sunday_hard: boolean;
  load_target: number;
  load_actual: number;
  max_run: number;
  support_i: number;
  /** Last week ended with the second long workout; last week's long run when it was on the Sunday (second_long). */
  sunday_pair: boolean;
  sunday_long: number;
  /** The second long workout of the last load week that had one, minutes. */
  last_pair: number;
}

/** Smallest distance in days between any two of the weekdays, around the week. */
function gap(days: number[]): number {
  if (days.length < 2) return 7;
  const d = [...days].sort((a, b) => a - b);
  const diffs = d.slice(1).map((b, i) => b - d[i]);
  return Math.min(...diffs, 7 - d[d.length - 1] + d[0]);
}

/** n of the slots, as evenly spread over the week as possible (ties: the earlier weekday). `seeds`: slots that are
 * always among them (the long run's and the fixed hard days'), the first ones when n is smaller. */
function spread(slots: StoredSlot[], n: number, seeds: StoredSlot[]): StoredSlot[] {
  const chosen = seeds.length ? seeds.slice(0, n) : slots.slice(0, 1);
  while (chosen.length < n) {
    const rest = slots.filter((s) => !chosen.includes(s));
    chosen.push(maxBy(rest, (s) => [gap([...chosen.map((c) => c.weekday), s.weekday]), -s.weekday]));
  }
  return sortedBy(chosen, (s) => s.weekday);
}

type Item = [number, number, number, number]; // weekday, weight, min, max

/** Split minutes over easy sessions (engine._share). Never hands out more than `total`. */
function share(total: number, items0: Item[]): Map<number, number> {
  const items = [...items0];
  while (items.length && total < items.reduce((s, it) => s + it[2], 0))
    items.splice(items.indexOf(minBy(items, (it) => [it[1], -it[0]])), 1);
  const out = new Map<number, number>();
  let rem = total;
  const pool = [...items];
  while (pool.length) {
    const sw = sum(pool.map((it) => it[1]));
    const full = pool.filter((it) => rem * it[1] / sw >= it[3]);
    if (!full.length) break;
    for (const it of full) {
      out.set(it[0], floor5(it[3]));
      rem -= out.get(it[0])!;
      pool.splice(pool.indexOf(it), 1);
    }
  }
  const sw = sum(pool.map((it) => it[1]));
  for (const [wd, w, lo] of pool) out.set(wd, Math.max(lo, floor5(rem * w / sw)));
  const lows = new Map(items.map((it) => [it[0], it[2]]));
  const highs = new Map(items.map((it) => [it[0], floor5(it[3])]));
  const weights = new Map(items.map((it) => [it[0], it[1]]));
  const total_ = () => [...out.values()].reduce((a, b) => a + b, 0);
  while (total - total_() >= 5) {
    const room = [...out.keys()].filter((wd) => out.get(wd)! + 5 <= highs.get(wd)!);
    if (!room.length) break;
    const k = minBy(room, (k) => [out.get(k)! / weights.get(k)!, k]);
    out.set(k, out.get(k)! + 5);
  }
  while (total_() > total) {
    const wd = maxBy([...out.keys()], (k) => [out.get(k)! - lows.get(k)!, k]);
    if (out.get(wd)! - 5 >= lows.get(wd)!) out.set(wd, out.get(wd)! - 5);
    else out.delete(wd);
  }
  return out;
}

const isAB = (g: StoredGoal) => g.priority === "A" || g.priority === "B";

/** engine._quality_count. `asked`: the coach's number for a load week. */
function qualityCount(spec: WeekSpec, n: number, final: StoredGoal | null, asked = 0): number {
  if (n < 2) return 0;
  let q: number;
  if (spec.kind === "easy") q = n >= 3 ? 1 : 0;
  else if (spec.kind === "test" || spec.kind === "taper" || spec.kind === "race") q = 1;
  else if (asked) q = Math.min(asked, Math.max(1, floordiv(n, 2)));
  else q = spec.block === "base" || n < 4 ? 1 : 2;
  const races = spec.goals.filter((g) => g !== final);
  if (races.some(isAB)) q = Math.min(q, 1);
  return Math.max(0, q - races.filter((g) => g.priority === "C").length);
}

function families(spec: WeekSpec, q: number, st: State, goal: StoredGoal | null, earlyBeginner: boolean): string[] {
  if (q <= 0) return [];
  if (["test", "race", "taper", "easy"].includes(spec.kind))
    return [({ test: "test", race: "opener", taper: "threshold", easy: "fartlek" } as Record<string, string>)[spec.kind]];
  let fams: string[];
  if (spec.block === "base") fams = Array.from({ length: q }, (_, k) => QUALITY_BASE[(st.base_i + k) % 3]);
  else if (spec.block === "build")
    fams = q >= 2 ? ["vo2max", "threshold", "hills"].slice(0, q) : [["threshold", "vo2max"][st.build_i % 2]];
  else if (goal !== null && (goal.distance_km || 0) >= PROGRESSIVE_FROM_KM) fams = ["threshold", "fartlek", "hills"].slice(0, q);
  else fams = ["vo2max", "threshold", "fartlek"].slice(0, q);
  if (earlyBeginner) {
    fams = fams.map((f) => (library.get(f).top_zone === "Z5" ? "fartlek" : f));
    if (fams.length === 2 && fams[0] === fams[1]) fams[1] = fams[0] === "fartlek" ? "threshold" : "fartlek";
    fams = [...new Set(fams)]; // a family once (three asked for: two)
  }
  return fams;
}

/** engine.shares: each sport's share of a week in a block, shifted towards the main sport (the first) near the goal. */
export function shares(roles: StoredRole[], block: string): Map<string, number> {
  const total = sum(roles.map(roleWeight));
  const k = SHARE_SHIFT[block] ?? 0.0;
  const out = new Map<string, number>();
  roles.forEach((r, i) => {
    const b = roleWeight(r) / total;
    out.set(r.sport, i === 0 ? b + k * (1 - b) : b * (1 - k));
  });
  return out;
}

/** Planned minutes per sport, races not counted (Week.sport_minutes). */
function sportMinutes(wk: StoredWeek): Map<string, number> {
  const out = new Map<string, number>();
  for (const w of wk.workouts) if (w.goal == null) out.set(w.sport, (out.get(w.sport) ?? 0) + minutes(w));
  return out;
}

function hardShare(workouts: StoredWorkout[]): number {
  const zs = new Map<string, number>();
  for (const w of workouts) for (const [z, s] of zoneSeconds(w)) zs.set(z, (zs.get(z) ?? 0) + s);
  const total = [...zs.values()].reduce((a, b) => a + b, 0);
  return total ? 1.0 - EASY_ZONES.reduce((a, z) => a + (zs.get(z) ?? 0), 0) / total : 0.0;
}

const byDateKey = (ws: StoredWorkout[]) => sortedBy(ws, (w) => [w.date, w.key]);

type Assembled = [StoredWorkout[], Map<string, number>, number | null];

class Filler {
  readonly level: Level;
  readonly roles: StoredRole[];
  readonly main: string;
  readonly slots: StoredSlot[];
  readonly longSlot: StoredSlot;
  readonly longCap: number;
  readonly seconds: StoredSlot[];
  /** Fixed hard days: weekday -> the sport of its quality session ("": any), and the order they were given in. */
  readonly hard = new Map<number, string>();
  readonly hardRank = new Map<number, number>();
  /** The coach's choices: the group session's length on a fixed hard day, the fixed easy days, the first supporting
   * sport and the weekday of the second long workout (the day after the long run; null: none). */
  readonly hardMinutes = new Map<number, number>();
  readonly easy = new Map<number, FixedDay>();
  readonly support: string | null;
  readonly pairWd: number | null;
  /** Strength: the days of the slots that allow it, else every day with an endurance slot (slot null). */
  readonly strengthSlots = new Map<number, StoredSlot | null>();
  readonly goal: StoredGoal | null;
  readonly state: State = {
    steps: new Map(), last_long: null, max_minutes: null, base_i: 0, build_i: 0, sunday_hard: false,
    load_target: 0, load_actual: 0, max_run: 0, support_i: 0, sunday_pair: false, sunday_long: 0, last_pair: 0,
  };
  readonly blocked = new Set<IsoDate>();
  readonly capped = new Map<IsoDate, number>();
  readonly raceDates: Set<IsoDate>;
  /** Recovery after a race that is not the plan's goal: the race and its last day of recovery. */
  readonly recovery: [StoredGoal, IsoDate][] = [];
  readonly recovering = new Set<IsoDate>();

  constructor(readonly request: StoredRequest, skeleton: Skeleton, readonly planId: string) {
    this.goal = skeleton.goal;
    this.level = LEVEL[request.level];
    this.roles = planRoles(request);
    this.main = mainSport(request);
    this.slots = coreSlots(request);
    this.longSlot = longSlot(this.slots, this.main);
    this.longCap = longCap(request);
    this.seconds = secondSlots(request, this.slots);
    const chosen = choices(request);
    for (const h of chosen.hard_days ?? [])
      if (h.weekday >= 0 && h.weekday <= 6 && !this.hard.has(h.weekday)) {
        this.hard.set(h.weekday, h.sport ?? "");
        if ((h.minutes ?? 0) > 0) this.hardMinutes.set(h.weekday, h.minutes!);
      }
    [...this.hard.keys()].forEach((wd, i) => this.hardRank.set(wd, i));
    for (const h of chosen.easy_days ?? [])
      if (h.weekday >= 0 && h.weekday <= 6 && !this.hard.has(h.weekday) && !this.easy.has(h.weekday)) this.easy.set(h.weekday, h);
    this.support = this.roles.slice(1).find((r) => r.role === "support")?.sport ?? null;
    const pairWd = chosen.second_long && this.support ? mod(this.longSlot.weekday + 1, 7) : null;
    this.pairWd = pairWd !== null && this.hard.has(pairWd) ? null : pairWd; // a fixed hard day there wins
    const own = new Map<number, StoredSlot>();
    for (const s of request.availability)
      if (s.role === "core" && s.sports.includes(STRENGTH) && s.max_minutes > 0 && s.weekday >= 0 && s.weekday <= 6)
        if (!own.has(s.weekday) || s.max_minutes > own.get(s.weekday)!.max_minutes) own.set(s.weekday, s);
    if (own.size) for (const wd of [...own.keys()].sort((a, b) => a - b)) this.strengthSlots.set(wd, own.get(wd)!);
    else for (const s of this.slots) this.strengthSlots.set(s.weekday, null);
    const first = skeleton.weeks[0].monday;
    const last = addDays(skeleton.weeks[skeleton.weeks.length - 1].monday, 6);
    this.raceDates = new Set(request.goals.map((g) => g.date));
    for (const g of request.goals) {
      if (!(first <= g.date && g.date <= last)) continue;
      const major = isAB(g);
      for (let d = 1; d < (major ? 3 : 2); d++) {
        this.blocked.add(addDays(g.date, -d));
        this.blocked.add(addDays(g.date, d));
      }
      if (major) PRE_RACE_MAX.forEach((m, i) => this.capped.set(addDays(g.date, -(i + 1)), m));
    }
    for (const g of sortedBy(request.goals, (g) => g.date)) {
      const days = recoveryDays(g);
      if (g === this.goal || days === 0 || g.date > last || addDays(g.date, days) < first) continue;
      this.recovery.push([g, addDays(g.date, days)]);
      for (let k = 1; k <= days; k++) {
        const d = addDays(g.date, k);
        const cap = k <= floordiv(days + 1, 2) ? RECOVERY_MAX[0] : RECOVERY_MAX[1];
        this.recovering.add(d);
        this.blocked.add(d);
        this.capped.set(d, Math.min(this.capped.get(d) ?? cap, cap));
      }
    }
  }

  key(day: IsoDate, suffix = ""): string {
    const [y, w] = isoYearWeek(day);
    return `${this.planId}-${String(y % 100).padStart(2, "0")}${String(w).padStart(2, "0")}-${WEEKDAY_KEYS[weekday(day)]}${suffix}`;
  }

  workout(day: IsoDate, templateId: string, slot: StoredSlot | null,
    o: { minutes?: number | null; step?: number; goal?: StoredGoal | null; suffix?: string } = {}): StoredWorkout {
    const t = library.get(templateId);
    const goal = o.goal ?? null;
    return {
      key: this.key(day, goal ? "-race" : o.suffix ?? ""), date: day, sport: goal ? goal.sport : this.main, template_id: templateId, family: t.family,
      quality: t.quality, start: slot ? slot.start : null, status: "planned", goal: goal ? { ...goal } : null,
      parts: library.build(templateId, { minutes: o.minutes, step: o.step ?? 0 }),
    };
  }

  place(cands: StoredSlot[], q: number, fixedHard: number[]): StoredSlot[] | null {
    let best: StoredSlot[] | null = null;
    let bestScore: number[] | null = null;
    for (const combo of combinations(cands, q)) {
      const days = combo.map((s) => s.weekday);
      if (gap(days) < 2) continue;
      const fixed = days.filter((d) => this.hardRank.has(d)).map((d) => this.hardRank.get(d)!);
      const score = [fixed.length, -sum(fixed), gap([...days, ...fixedHard])];
      if (bestScore === null || cmp(score, bestScore) > 0) [best, bestScore] = [combo, score];
    }
    return q ? best : [];
  }

  fill(spec: WeekSpec): StoredWeek {
    const st = this.state;
    const level = this.level;
    const final = this.goal !== null && spec.goals.includes(this.goal) && spec.kind === "race" ? this.goal : null;
    const raceDays = new Set(spec.goals.map((g) => weekday(g.date)));
    const avail = this.slots.filter((s) => !raceDays.has(s.weekday) && (final === null || s.weekday < weekday(final.date)));
    let ceiling = spec.target_minutes;
    if (st.max_minutes !== null) ceiling = Math.min(ceiling, floor5((1 + MAX_WEEKLY_INCREASE) * st.max_minutes));
    if (spec.kind !== "load" && st.load_target)
      ceiling = Math.min(ceiling, round5(spec.target_minutes * st.load_actual / st.load_target));
    const availDays = new Set(avail.map((s) => s.weekday));
    const seconds = spec.kind !== "taper" && spec.kind !== "race" ? this.seconds.filter((s) => availDays.has(s.weekday)) : [];
    const n = Math.min(avail.length + seconds.length, level.max_sessions, Math.max(1, floordiv(ceiling, MIN_SESSION)));
    const majorRace = spec.goals.some(isAB);
    const longDay = addDays(spec.monday, this.longSlot.weekday);
    const wantLong = n >= 2 && avail.includes(this.longSlot) && !majorRace && !this.raceDates.has(addDays(longDay, -1))
      && !this.recovering.has(longDay);
    // The second long workout this week: the day after this week's long run, or after last week's on a Sunday.
    const pair = this.pairWd !== null && ["load", "easy", "test"].includes(spec.kind) && !majorRace
      && (this.pairWd ? wantLong : st.sunday_long > 0);
    const seeds = [...(wantLong ? [this.longSlot] : []),
      ...avail.filter((s) => this.hard.has(s.weekday) && !(wantLong && s === this.longSlot))];
    seeds.push(...avail.filter((s) => !seeds.includes(s) && ((pair && s.weekday === this.pairWd) || this.easy.has(s.weekday))));
    const chosen = avail.length ? spread(avail, Math.min(n, avail.length), seeds) : [];
    const doubles = n - chosen.length; // second workouts of a day: only once every day has one
    const day = new Map(chosen.map((s) => [s.weekday, addDays(spec.monday, s.weekday)]));
    const longWd = this.longSlot.weekday;
    const early = this.request.level === "beginner" && spec.index <= BEGINNER_EARLY_WEEKS;
    const cands = chosen.filter((s) => !(wantLong && s === this.longSlot) && !this.blocked.has(day.get(s.weekday)!)
      && s.max_minutes >= QUALITY_MIN_SLOT && mod(longWd - s.weekday, 7) !== 1 && !(s.weekday === 0 && st.sunday_hard)
      && !this.easy.has(s.weekday) && !(pair && s.weekday === this.pairWd));
    const fixedHard = [...new Set([...raceDays, ...(wantLong ? [longWd] : [])])].sort((a, b) => a - b);
    const supportLong = wantLong && spec.kind === "load" && (spec.block === "base" || spec.block === "build")
      && !spec.goals.length && st.support_i % 2 === 0 && !choices(this.request).second_long ? this.support : null;

    let result: Assembled | null = null;
    const asked = qualityAsked(this.request);
    for (let q = Math.min(qualityCount(spec, n, final, asked), cands.length); q >= 0; q--) {
      const qSlots = this.place(cands, q, fixedHard);
      if (qSlots === null) continue;
      const fams = families(spec, q, st, this.goal, early);
      const downs = fams.length ? Math.max(...fams.map((f) => library.get(f).steps.length)) : 1;
      for (let down = 0; down < downs; down++) {
        result = this.assemble(spec, chosen, day, qSlots, fams, down, ceiling, wantLong, seconds, doubles, supportLong, pair);
        if (result !== null) break;
      }
      if (result !== null) break;
    }
    const [ws, usedSteps, longMinutes] = result ?? [[], new Map<string, number>(), null];
    let workouts = ws;
    for (const g of spec.goals) workouts.push(this.workout(g.date, "race", null, { goal: g }));
    workouts.push(...this.strength(spec, workouts));
    workouts = byDateKey(workouts);

    const notes: StoredNote[] = [note(`plan.week.focus.${spec.kind === "load" ? spec.block : spec.kind}`)];
    for (const g of spec.goals)
      if (g !== final) notes.push(note(`plan.week.race_${g.priority.toLowerCase()}`, { name: g.name }));
    for (const [g, until] of this.recovery)
      if (addDays(g.date, 1) <= addDays(spec.monday, 6) && until >= spec.monday)
        notes.push(note("plan.week.recovery", { name: g.name, until }));
    const week: StoredWeek = { index: spec.index, monday: spec.monday, block: spec.block, kind: spec.kind,
      target_minutes: spec.target_minutes, notes, workouts };
    // ---- what the next week needs to know
    if (spec.kind === "load") {
      for (const [fam, step] of usedSteps) st.steps.set(fam, Math.min(step + 1, library.lastStep(fam)));
      if (longMinutes) st.last_long = longMinutes;
      st.load_target = spec.target_minutes;
      st.load_actual = weekMinutes(week);
      st.base_i += spec.block === "base" ? 1 : 0;
      st.build_i += spec.block === "build" ? 1 : 0;
      st.support_i += (spec.block === "base" || spec.block === "build") && !spec.goals.length ? 1 : 0;
    }
    st.max_minutes = Math.max(st.max_minutes ?? 0, weekMinutes(week));
    st.max_run = Math.max(st.max_run, sportMinutes(week).get(RUNNING) ?? 0);
    st.sunday_hard = workouts.some((w) => w.quality && weekday(w.date) === 6);
    if (this.pairWd !== null) {
      st.sunday_long = this.longSlot.weekday === 6 ? longMinutes || 0 : 0;
      st.sunday_pair = this.pairWd === 6 && workouts.some((w) => w.family === "long" && weekday(w.date) === 6 && w.goal == null);
      const second = workouts.find((w) => w.family === "long" && w.goal == null && weekday(w.date) === this.pairWd);
      if (spec.kind === "load" && second) st.last_pair = minutes(second);
    }
    return week;
  }

  /** engine._Filler._strength: the week's strength sessions on the days easiest for the legs. */
  strength(spec: WeekSpec, workouts: StoredWorkout[]): StoredWorkout[] {
    let n = strengthCount(this.request);
    if (spec.kind === "race") n = Math.min(n, 1);
    if (n === 0) return [];
    const days = new Map([...this.strengthSlots.keys()].map((wd) => [wd, addDays(spec.monday, wd)]));
    const closed = new Set<IsoDate>();
    for (const g of this.request.goals) {
      closed.add(g.date);
      closed.add(addDays(g.date, -1));
      if (isAB(g)) for (let k = 1; k <= STRENGTH_CLEAR_DAYS; k++) closed.add(addDays(g.date, -k));
      if (g === this.goal) for (let k = 1; k < 7; k++) closed.add(addDays(g.date, k));
    }
    for (const [g, until] of this.recovery) // the first half of a recovery
      for (let k = 1; k <= floordiv(daysBetween(g.date, until) + 1, 2); k++) closed.add(addDays(g.date, k));
    const cands = [...days.keys()].sort((a, b) => a - b).filter((wd) => !closed.has(days.get(wd)!));
    if (!cands.length) return [];
    const on = new Map<IsoDate, StoredWorkout[]>();
    for (const w of workouts) if (w.goal == null) on.set(w.date, [...(on.get(w.date) ?? []), w]);
    const heavy = new Set(workouts.filter((w) => w.quality || w.family === "long" || w.goal != null).map((w) => w.date));
    const beforeHard = new Map(cands.map((wd) => [wd, heavy.has(addDays(days.get(wd)!, 1)) ? 1 : 0]));
    const own = (wd: number) => { // two workouts in a day count as a hard day
      const ws = on.get(days.get(wd)!) ?? [];
      return !ws.length ? 0 : ws.length > 1 || ws.some((w) => w.quality || w.family === "long") ? 2 : 1;
    };
    let best: number[] | null = null;
    let bestKey: number[] | null = null;
    for (const combo of combinations(cands, Math.min(n, cands.length))) {
      const key = [sum(combo.map((wd) => beforeHard.get(wd)!)), combo.length > 1 ? -Math.min(gap(combo), 2) : 0,
        sum(combo.map(own))];
      if (bestKey === null || cmp(key, bestKey) < 0) [best, bestKey] = [combo, key];
    }
    const chosen = best!;
    const ids = spec.kind === "taper" || spec.kind === "race" ? chosen.map(() => library.STRENGTH_TAPER)
      : library.STRENGTH_WEEK[chosen.length];
    const roomiest = minBy(chosen, (wd) => [beforeHard.get(wd)!, own(wd), wd]);
    const order = [roomiest, ...chosen.filter((wd) => wd !== roomiest)];
    return order.map((wd, i) => {
      const slot = this.strengthSlots.get(wd) ?? null;
      const t = library.get(ids[i]);
      const day = days.get(wd)!;
      return { key: this.key(day, "-strength"), date: day, sport: STRENGTH, template_id: t.id, family: t.family,
        quality: t.quality, start: slot ? slot.start : null, status: "planned", goal: null, parts: library.build(t.id) };
    });
  }

  /** engine._Filler._sports: every workout of the week (no races) gets its sport, in place, and then the
   * sport's template in the place of the running one (library.inSport: the same minutes). */
  sports(spec: WeekSpec, workouts: StoredWorkout[], slotOf: Map<number, StoredSlot>, secondOf: Map<number, StoredSlot>,
    pinned: Map<string, string>): void {
    if (this.roles.length >= 2) this.assign(spec, workouts, slotOf, secondOf, pinned);
    for (const w of workouts) [w.template_id, w.parts] = library.inSport(w.parts, w.template_id, w.sport);
  }

  assign(spec: WeekSpec, workouts: StoredWorkout[], slotOf: Map<number, StoredSlot>, secondOf: Map<number, StoredSlot>,
    pinned: Map<string, string>): void {
    const share = shares(this.roles, spec.block);
    const total = sum(workouts.map(minutes));
    const done = new Map(this.roles.map((r) => [r.sport, 0]));
    const count = new Map(this.roles.map((r) => [r.sport, 0]));
    const runCap = this.state.max_run ? floor5((1 + MAX_WEEKLY_INCREASE) * this.state.max_run) : null;
    const mainFirst = (w: StoredWorkout) => (w.family === "long" && !pinned.has(w.key)) || w.template_id === "test";
    const order = sortedBy(workouts, (w) => [mainFirst(w) || pinned.has(w.key) ? 0 : w.quality ? 1 : 2,
      mainFirst(w) || w.quality ? 0 : -minutes(w), w.date]);
    const or = <T>(xs: T[], els: T[]) => (xs.length ? xs : els);
    const dayOf = new Map<IsoDate, StoredWorkout[]>();
    for (const w of workouts) dayOf.set(w.date, [...(dayOf.get(w.date) ?? []), w]);
    const assigned = new Set<string>();
    const allowedOf = (w: StoredWorkout) => {
      const slot = (w.key.endsWith(SECOND) ? secondOf : slotOf).get(weekday(w.date))!;
      return this.roles.filter((r) => slot.sports.includes(r.sport));
    };
    for (const w of order) {
      let allowed = allowedOf(w);
      const others = dayOf.get(w.date)!.filter((o) => o !== w);
      if (others.length) { // not the sport of the day's other workout, and leave that one a sport of its own
        let free = allowed.filter((r) => others.every((o) => !assigned.has(o.key) || o.sport !== r.sport));
        free = or(free.filter((r) => others.every((o) => assigned.has(o.key) || allowedOf(o).some((x) => x.sport !== r.sport))), free);
        allowed = or(free, allowed);
      }
      let cands = or(w.quality ? allowed.filter((r) => r.role !== "maintenance") : allowed, allowed);
      cands = or(cands.filter((r) => r.role !== "maintenance" || count.get(r.sport) === 0), cands);
      cands = or(cands.filter((r) => r.sport !== RUNNING || runCap === null || done.get(RUNNING)! + minutes(w) <= runCap), cands);
      const pick = pinned.has(w.key) ? pinned.get(w.key)! : mainFirst(w) && cands.some((r) => r.sport === this.main) ? this.main
        : maxBy(cands, (r) => [share.get(r.sport)! * total - done.get(r.sport)!, -this.roles.indexOf(r)]).sport;
      w.sport = pick;
      done.set(pick, done.get(pick)! + minutes(w));
      count.set(pick, count.get(pick)! + 1);
      assigned.add(w.key);
    }
    for (const w of workouts.filter((x) => x.key.endsWith(SECOND)))
      if (dayOf.get(w.date)!.some((o) => o !== w && o.sport === w.sport)) workouts.splice(workouts.indexOf(w), 1);
  }

  assemble(spec: WeekSpec, chosen: StoredSlot[], day: Map<number, IsoDate>, qSlots: StoredSlot[], fams: string[],
    down: number, ceiling: number, wantLong: boolean, seconds: StoredSlot[], doubles: number,
    supportLong: string | null = null, pair = false): Assembled | null {
    const st = this.state;
    const level = this.level;
    const workouts: StoredWorkout[] = [];
    const used = new Map<string, number>();
    const pinned = new Map<string, string>(); // workouts whose sport is set already, by key (sports)
    const back = spec.kind === "easy" || spec.kind === "taper" ? STEP_BACK : 0;
    for (let i = 0; i < Math.min(qSlots.length, fams.length); i++) {
      const slot = qSlots[i];
      const fam = fams[i];
      let step = Math.max(0, Math.min(st.steps.get(fam) ?? level.first_step, library.lastStep(fam)) - back - down);
      while (step > 0 && library.minutesOf(fam, step) > slot.max_minutes) step -= 1;
      if (library.minutesOf(fam, step) > slot.max_minutes) return null;
      used.set(fam, step);
      const w = this.workout(day.get(slot.weekday)!, fam, slot, { step });
      const sp = this.hard.get(slot.weekday) ?? "";
      if (sp && slot.sports.includes(sp) && this.roles.some((r) => r.sport === sp) && fam !== "test")
        pinned.set(w.key, sp); // the coach's sport on a fixed hard day
      workouts.push(w);
    }
    let left = ceiling - workouts.reduce((s, w) => s + library.minutesOf(w.template_id, used.get(w.family)!), 0);
    if (left < 0) return null;

    let longMinutes: number | null = null;
    if (wantLong) {
      let want = Math.min(this.longSlot.max_minutes, this.longCap, Math.max(45, round5(LONG_SHARE * ceiling)));
      if (spec.kind === "load" && st.last_long) want = Math.min(want, st.last_long + LONG_STEP);
      else if (spec.kind === "easy" || spec.kind === "test") want = round5(0.75 * (st.last_long || want));
      else if (spec.kind === "taper") want = round5(0.60 * (st.last_long || want));
      want = Math.min(floor5(want), floor5(left));
      if (want >= LONG_MIN) {
        longMinutes = want;
        const progressive = spec.block === "peak" && spec.kind === "load" && this.goal !== null
          && (this.goal.distance_km || 0) >= PROGRESSIVE_FROM_KM
          && want >= library.get("long_progressive").min_minutes && this.request.level !== "beginner";
        workouts.push(this.workout(day.get(this.longSlot.weekday)!, progressive ? "long_progressive" : "long",
          this.longSlot, { minutes: want }));
        left -= want;
      }
    }

    // ---- the second long workout, in the supporting sport the day after the long run (second_long)
    const after = this.pairWd ? longMinutes : st.sunday_long;
    if (pair && after && this.pairWd !== null && day.has(this.pairWd)) {
      const d = day.get(this.pairWd)!;
      const slot = chosen.find((s) => s.weekday === this.pairWd)!;
      if (!workouts.some((w) => w.date === d) && slot.sports.includes(this.support!) && !this.blocked.has(d)
        && !this.capped.has(d)) {
        const part = shares(this.roles, spec.block).get(this.support!)! * ceiling;
        let want = floor5(Math.min(slot.max_minutes, level.easy_max, SUPPORT_LONG_OF_MAIN * after, part, left));
        // An easy or a test week: three quarters, as the long run.
        if (spec.kind !== "load" && st.last_pair) want = Math.min(want, round5(0.75 * st.last_pair));
        if (want >= SUPPORT_LONG_MIN) {
          const w = this.workout(d, "long", slot, { minutes: want });
          workouts.push(w);
          pinned.set(w.key, this.support!);
          left -= want;
        }
      }
    }

    // ---- a smaller long workout in the supporting sport, away from the long run (engine.py)
    if (supportLong && longMinutes) {
      const near = new Set([-1, 0, 1].map((k) => mod(this.longSlot.weekday + k, 7)));
      const busy = new Set(workouts.map((w) => w.date));
      const hard = new Set(workouts.filter((w) => w.quality).map((w) => w.date));
      if (st.sunday_hard) hard.add(addDays(spec.monday, -1));
      const free = chosen.filter((s) => !near.has(s.weekday) && !busy.has(day.get(s.weekday)!)
        && !hard.has(addDays(day.get(s.weekday)!, -1)) && s.sports.includes(supportLong)
        && !this.blocked.has(day.get(s.weekday)!) && !this.capped.has(day.get(s.weekday)!));
      if (free.length) {
        const slot = maxBy(free, (s) => [s.max_minutes, s.weekday]);
        const part = shares(this.roles, spec.block).get(supportLong)! * ceiling;
        const want = floor5(Math.min(slot.max_minutes, this.longCap, SUPPORT_LONG_OF_MAIN * longMinutes, part, left));
        if (want >= SUPPORT_LONG_MIN) {
          const w = this.workout(day.get(slot.weekday)!, "long", slot, { minutes: want });
          workouts.push(w);
          pinned.set(w.key, supportLong);
          left -= want;
        }
      }
    }

    // ---- the rest of the week is easy running, shared over the remaining slots
    const taken = new Set(workouts.map((w) => w.date));
    const heavy = new Set([...workouts.map((w) => w.date), ...spec.goals.map((g) => g.date)]);
    if (st.sunday_hard || this.longSlot.weekday === 6 || st.sunday_pair) heavy.add(addDays(spec.monday, -1));
    const easySlots = chosen.filter((s) => !taken.has(day.get(s.weekday)!));
    const items: Item[] = [];
    const kinds = new Map<number, string>();
    for (const s of easySlots) {
      const d = day.get(s.weekday)!;
      const fixed = this.easy.get(s.weekday); // the coach's easy day: never a recovery workout, its own length
      const own = fixed && (fixed.minutes ?? 0) > 0 ? fixed.minutes! : 0;
      const recovery = !fixed && chosen.length >= 4 && easySlots.length >= 2 && heavy.has(addDays(d, -1));
      const t = library.get(recovery ? "recovery" : "easy");
      const top = recovery ? level.recovery_max : own || level.easy_max;
      const hi = Math.min(s.max_minutes, top, this.capped.get(d) ?? 10_000, spec.kind === "race" ? RACE_WEEK_MAX : 10_000);
      if (hi >= t.min_minutes) {
        items.push([s.weekday, recovery ? 0.7 : own ? FIXED_WEIGHT : 1.0, t.min_minutes, hi]);
        kinds.set(s.weekday, t.id);
      }
    }
    // The fill of a group session (keyed weekday + FILL): easy time around the quality session of a fixed hard day
    // with a length, as far as the week's time allows. Not a test, not in the race week.
    if (spec.kind !== "race")
      for (let i = 0; i < Math.min(qSlots.length, fams.length); i++) {
        const slot = qSlots[i];
        const w = workouts[i];
        const room = floor5(Math.min(this.hardMinutes.get(slot.weekday) ?? 0, slot.max_minutes)
          - library.minutesOf(w.template_id, used.get(w.family)!));
        if (room >= 5 && w.family !== "test") items.push([slot.weekday + FILL, 1.0, 0, room]);
      }
    // A day's second workout (keyed weekday + 7): easy, a recovery workout on a hard day, on the days without a hard
    // session or a long run first, never on the long run's day or around a race.
    // Only on the days the week uses (a week with fewer sessions than days has none: `doubles` is 0).
    const extra = sortedBy(seconds.filter((s) => day.has(s.weekday) && !this.blocked.has(day.get(s.weekday)!)
      && !this.capped.has(day.get(s.weekday)!)
      && !(longMinutes && s.weekday === this.longSlot.weekday)),
    (s) => [taken.has(day.get(s.weekday)!) ? 1 : 0, -s.max_minutes, s.weekday]).slice(0, Math.max(0, doubles));
    for (const s of extra) {
      const t = library.get(taken.has(day.get(s.weekday)!) ? "recovery" : "easy");
      const hi = Math.min(s.max_minutes, t.id === "recovery" ? level.recovery_max : level.easy_max);
      if (hi >= t.min_minutes) {
        items.push([s.weekday + 7, SECOND_WEIGHT, t.min_minutes, hi]);
        kinds.set(s.weekday + 7, t.id);
      }
    }
    const byWd = new Map(chosen.map((s) => [s.weekday, s]));
    const secondOf = new Map(extra.map((s) => [s.weekday, s]));
    const split = share(Math.max(0, left), items);
    const fills = new Map<string, number>(); // the quality session's key -> the minutes of its fill
    for (const [k, m] of split) {
      if (k >= FILL) fills.set(this.key(day.get(k - FILL)!), m);
      else if (k < 7) {
        const w = this.workout(day.get(k)!, kinds.get(k)!, byWd.get(k)!, { minutes: m });
        workouts.push(w);
        const fixed = this.easy.get(k);
        if (fixed?.sport && byWd.get(k)!.sports.includes(fixed.sport) && this.roles.some((r) => r.sport === fixed.sport))
          pinned.set(w.key, fixed.sport); // the coach's sport on a fixed easy day
      } else workouts.push(this.workout(day.get(k - 7)!, kinds.get(k)!, secondOf.get(k - 7)!, { minutes: m, suffix: SECOND }));
    }
    // With few slots the easy runs fill up: what is left goes to the long run, within its own limits.
    const spare = floor5(left - [...split.values()].reduce((a, b) => a + b, 0));
    if (longMinutes && spec.kind === "load" && st.last_long && spare >= 5) {
      const room = Math.min(this.longSlot.max_minutes, this.longCap, st.last_long + LONG_STEP) - longMinutes;
      if (room >= 5) {
        longMinutes += Math.min(spare, floor5(room));
        const i = workouts.findIndex((w) => w.family === "long");
        workouts[i] = this.workout(workouts[i].date, workouts[i].template_id, this.longSlot, { minutes: longMinutes });
      }
    }
    // Every workout in its sport, before the checks: another sport's template may hold more hard minutes.
    this.sports(spec, workouts, byWd, secondOf, pinned);
    for (const w of workouts) // the fills, once every quality session has its sport's template
      if (fills.get(w.key)) w.parts = library.fillOut(w.parts, fills.get(w.key)!);
    if (workouts.reduce((s, w) => s + minutes(w), 0) > ceiling || hardShare(workouts) > MAX_HARD_SHARE) return null;
    return [workouts, used, longMinutes];
  }
}

/** The plan week (1-based) a day falls in; 0 or less before the plan's first Monday. */
export const weekOf = (start: IsoDate, day: IsoDate) => floordiv(daysBetween(start, day), 7) + 1;

/** The last detailed week on `today`: the week of the day and the three after it, within the plan. */
export function horizonFor(start: IsoDate, nWeeks: number, today: IsoDate): number {
  return Math.max(1, Math.min(nWeeks, Math.max(1, weekOf(start, today)) + DETAIL_WEEKS - 1));
}

/** Every week of the skeleton with its workouts, in order: each week's filling depends on the ones before. */
export function fillWeeks(request: StoredRequest, skeleton: Skeleton, planId: string): StoredWeek[] {
  const filler = new Filler(request, skeleton, planId);
  return skeleton.weeks.map((spec) => filler.fill(spec));
}

export interface BuildOptions {
  planId?: string;
  /** ISO datetime; default midnight (UTC) of the request's start, as Python. */
  created?: string;
  /** The day the plan is built: the weeks after the horizon become sketches. Null: every week is detailed. */
  today?: IsoDate | null;
}

/** The plan for a (normalised) request, as `codec.plan_to_dict(build_plan(...))`. Throws PlanError. */
export function buildPlan(request: StoredRequest, o: BuildOptions = {}): StoredPlanBody {
  const planId = o.planId ?? "plan";
  const skeleton = periodize(request);
  let weeks = fillWeeks(request, skeleton, planId);
  let horizon: number | null = null;
  if (o.today != null) {
    const h = horizonFor(weeks[0].monday, weeks.length, o.today);
    horizon = h;
    weeks = weeks.map((wk) => (wk.index <= h ? wk : sketchOf(wk)));
  }
  const notes = [...skeleton.notes, ...capacityNotes(request)];
  return {
    format: CODEC_FORMAT, id: planId, created: o.created ?? `${request.start}T00:00:00+00:00`,
    engine_version: ENGINE_VERSION, zone_system: DEFAULT_SYSTEM, status: "draft", based_on: basedOn(request), notes,
    capacity_log: [], blocks: skeleton.blocks, weeks, horizon,
  };
}

/** The notes build_plan adds for what the plan's capacity is (no test, estimated zones, borrowed or missing tests). */
export function capacityNotes(request: StoredRequest): StoredNote[] {
  const notes: StoredNote[] = [];
  const cap = capacityFor(request, mainSport(request));
  if (cap.anchors == null && cap.css_s100 != null) { /* swimming as the main sport on a CSS test */ }
  else if (cap.anchors == null) notes.push(note("plan.note.no_test"));
  else if (speedFromHr(cap)) notes.push(note("plan.note.speed_from_hr"));
  else if (estimated(cap)) notes.push(note("plan.note.estimated"));
  planRoles(request).forEach((r, i) => {
    if (ownCapacity(request, r.sport) !== undefined) return;
    if (r.sport in BORROWS && ownCapacity(request, BORROWS[r.sport]) !== undefined) notes.push(note(`plan.note.borrowed.${r.sport}`));
    else if (i > 0) notes.push(note(`plan.note.no_test_sport.${r.sport}`));
  });
  return notes;
}

/** The keys of the notes `capacityNotes` gives. */
export const isCapacityNote = (key: string) =>
  /^plan\.note\.(no_test|speed_from_hr|estimated|borrowed\.[a-z]+|no_test_sport\.[a-z]+)$/.test(key);

/** Plan.based_on: sport -> the day of its test (null for one without, as the athlete's own values). */
export function basedOn(request: StoredRequest): Record<string, IsoDate | null> {
  const out: Record<string, IsoDate | null> = {};
  for (const c of request.capacity) out[c.sport] = c.tested || null;
  return out;
}

export { sketchOf };
