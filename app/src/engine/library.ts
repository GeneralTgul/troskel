/**
 * The workout library for running, as data: a port of lopanalys/plan/library.py. Every workout in a plan
 * comes from a template here. Quality templates carry their progression as `steps`; the engine picks the
 * step and never invents a workout.
 */
import type { StoredPart, ZoneCode } from "../model/types";

const MIN = 60;

export interface Template {
  id: string;
  family: string;
  quality: boolean;
  top_zone: ZoneCode | null;
  steps: StoredPart[][];
  min_minutes: number;
  max_minutes: number;
}

export function part(kind: string, duration_s: number, zone: ZoneCode | null, rpe: number, repeat = 1, rest_s = 0,
  rest_zone: ZoneCode = "Z1"): StoredPart {
  return { kind, duration_s, zone, rpe, repeat, rest_s, rest_zone };
}

const set = (warm: number, reps: number, work_s: number, rest_s: number, zone: ZoneCode, rpe: number, cool = 10,
  rest_zone: ZoneCode = "Z1"): StoredPart[] => [
  part("warmup", warm * MIN, "Z2", 3),
  part("work", work_s, zone, rpe, reps, rest_s, rest_zone),
  part("cooldown", cool * MIN, "Z2", 2),
];

function tpl(id: string, family: string, quality: boolean, top_zone: ZoneCode | null,
  o: { steps?: StoredPart[][]; min?: number; max?: number } = {}): Template {
  return { id, family, quality, top_zone, steps: o.steps ?? [], min_minutes: o.min ?? 20, max_minutes: o.max ?? 240 };
}

const fart = (reps: number, work: number) => set(10, reps, work * MIN, 120, "Z4", 6, 10, "Z2");

export const TEMPLATES: Record<string, Template> = Object.fromEntries([
  tpl("easy", "easy", false, "Z2", { min: 25, max: 75 }),
  tpl("recovery", "recovery", false, "Z2", { min: 20, max: 45 }),
  tpl("long", "long", false, "Z2", { min: 30 }),
  tpl("long_progressive", "long", false, "Z3", { min: 70 }),
  tpl("threshold", "threshold", true, "Z4", { steps: [
    set(15, 4, 5 * MIN, 60, "Z4", 7), set(15, 4, 6 * MIN, 60, "Z4", 7), set(15, 5, 6 * MIN, 60, "Z4", 7),
    set(15, 4, 8 * MIN, 120, "Z4", 7), set(15, 3, 12 * MIN, 120, "Z4", 7), set(15, 4, 10 * MIN, 120, "Z4", 7)] }),
  tpl("vo2max", "vo2max", true, "Z5", { steps: [
    set(15, 4, 3 * MIN, 120, "Z5", 9), set(15, 5, 3 * MIN, 120, "Z5", 9), set(15, 4, 4 * MIN, 180, "Z5", 9),
    set(15, 6, 3 * MIN, 120, "Z5", 9), set(15, 5, 4 * MIN, 180, "Z5", 9), set(15, 6, 4 * MIN, 180, "Z5", 9)] }),
  tpl("fartlek", "fartlek", true, "Z4", { steps: [fart(5, 2), fart(6, 2), fart(8, 2), fart(6, 3), fart(8, 3)] }),
  tpl("hills", "hills", true, "Z5", { steps: [
    set(15, 6, 45, 90, "Z5", 8), set(15, 8, 45, 90, "Z5", 8), set(15, 8, 60, 120, "Z5", 8),
    set(15, 10, 60, 120, "Z5", 8), set(15, 12, 60, 120, "Z5", 8)] }),
  tpl("opener", "opener", true, "Z4", { steps: [set(10, 4, 1 * MIN, 120, "Z4", 6)] }),
  tpl("test", "test", true, null, { steps: [
    [part("warmup", 15 * MIN, "Z2", 3), part("test", 35 * MIN, null, 10), part("cooldown", 10 * MIN, "Z2", 2)]] }),
  tpl("race", "race", true, null, { steps: [[part("race", 0, null, 10)]] }),
].map((t) => [t.id, t]));

export function get(id: string): Template {
  const t = TEMPLATES[id];
  if (!t) throw new Error(`no template ${id}`);
  return t;
}

export const lastStep = (id: string) => Math.max(0, get(id).steps.length - 1);

const partTotal = (p: StoredPart) => p.repeat * p.duration_s + (p.repeat - 1) * p.rest_s;

/** Total length of a fixed template at a step, in whole minutes (rounded up). */
export function minutesOf(id: string, step = 0): number {
  const parts = get(id).steps[Math.min(step, lastStep(id))];
  return Math.ceil(parts.reduce((s, p) => s + partTotal(p), 0) / 60);
}

/** The parts of a workout: a fixed template at `step`, or an easy or long run of `minutes`. */
export function build(id: string, o: { minutes?: number | null; step?: number } = {}): StoredPart[] {
  const t = get(id);
  const step = o.step ?? 0;
  if (t.steps.length) return t.steps[Math.max(0, Math.min(step, t.steps.length - 1))].map((p) => ({ ...p }));
  if (o.minutes == null) throw new Error(`${id} needs a length`);
  if (id === "long_progressive")
    return [part("steady", (o.minutes - 20) * MIN, "Z2", 4), part("steady", 20 * MIN, "Z3", 6)];
  const rpe = ({ recovery: 2, long: 4 } as Record<string, number>)[id] ?? 3;
  return [part("steady", o.minutes * MIN, "Z2", rpe)];
}
