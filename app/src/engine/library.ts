/**
 * The workout library, as data: a port of lopanalys/plan/library.py. Running; the bike, the SkiErg and
 * swimming in the places of the running templates; and strength, which has no zones. Every workout in a plan comes from a template here. Quality templates
 * carry their progression as `steps`; the engine picks the step and never invents a workout.
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
  /** The running template this one takes the place of in another sport ("" for running's own). */
  base: string;
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
  return { id, family, quality, top_zone, steps: o.steps ?? [], min_minutes: o.min ?? 20, max_minutes: o.max ?? 240,
    base: "" };
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

// ---- the other sports (library.py, #666 step 2): the same family and steps, each step as long as running's.
const partTotal = (p: StoredPart) => p.repeat * p.duration_s + (p.repeat - 1) * p.rest_s;

type SetSpec = [number, number, number, number, ZoneCode, number, ZoneCode?];

/** library._fit: an interval set as long as the running step `like`; the cool-down takes what the set leaves. */
function fit(like: StoredPart[], [warm, reps, work_s, rest_s, zone, rpe, rest_zone = "Z1"]: SetSpec): StoredPart[] {
  const total = like.reduce((s, p) => s + partTotal(p), 0);
  const cool = total - warm * MIN - reps * work_s - (reps - 1) * rest_s;
  if (cool < 5 * MIN) throw new Error("the cool-down would be shorter than five minutes");
  return [part("warmup", warm * MIN, "Z2", 3), part("work", work_s, zone, rpe, reps, rest_s, rest_zone),
    part("cooldown", cool, "Z2", 2)];
}

function steps(runId: string, ...sets: SetSpec[]): StoredPart[][] {
  const run = TEMPLATES[runId].steps;
  if (sets.length !== run.length) throw new Error(`${runId}: ${run.length} steps`);
  return run.map((like, i) => fit(like, sets[i]));
}

/** library._band: the steps with the work parts in a band of their zone (resolve.BANDS). */
const band = (own: StoredPart[][], name: string): StoredPart[][] =>
  own.map((st) => st.map((p) => (p.kind === "work" ? { ...p, band: name } : p)));

/** library._test: a sport's own test in the place of running's, the whole session as long. */
function test(id: string, warm: number, testMin: number, cool: number): Template {
  const parts = [part("warmup", warm * MIN, "Z2", 3), part("test", testMin * MIN, null, 10),
    part("cooldown", cool * MIN, "Z2", 2)];
  const run = TEMPLATES.test;
  if (parts.reduce((s, p) => s + partTotal(p), 0) !== run.steps[0].reduce((s, p) => s + partTotal(p), 0))
    throw new Error(`${id}: as long as running's test`);
  return { ...run, id, steps: [parts], base: "test" };
}

function like(runId: string, id: string, own?: StoredPart[][]): Template {
  const t = TEMPLATES[runId];
  const top = own === undefined ? t.top_zone
    : own.flat().map((p) => p.zone).filter((z): z is ZoneCode => z !== null).sort().pop()!;
  return { ...t, id, top_zone: top, steps: own ?? t.steps, base: runId };
}

const BIKE: Template[] = [
  like("easy", "bike_easy"), like("recovery", "bike_recovery"), like("long", "bike_long"),
  like("long_progressive", "bike_long_progressive"),
  // Sweet spot: the blocks carry the band "sweetspot", 88–94 % of the threshold power, and count as Z4.
  like("fartlek", "bike_sweetspot", band(steps("fartlek", [10, 2, 10 * MIN, 3 * MIN, "Z4", 6, "Z2"],
    [10, 2, 12 * MIN, 3 * MIN, "Z4", 6, "Z2"], [10, 3, 10 * MIN, 2 * MIN, "Z4", 6, "Z2"],
    [10, 2, 15 * MIN, 3 * MIN, "Z4", 6, "Z2"], [10, 3, 12 * MIN, 3 * MIN, "Z4", 6, "Z2"]), "sweetspot")),
  like("threshold", "bike_threshold", steps("threshold", [12, 3, 8 * MIN, 3 * MIN, "Z4", 7],
    [10, 3, 10 * MIN, 3 * MIN, "Z4", 7], [12, 3, 12 * MIN, 3 * MIN, "Z4", 7], [12, 2, 20 * MIN, 5 * MIN, "Z4", 7],
    [10, 4, 10 * MIN, 3 * MIN, "Z4", 7], [12, 3, 15 * MIN, 4 * MIN, "Z4", 7])),
  like("vo2max", "bike_vo2max", steps("vo2max", [15, 4, 3 * MIN, 3 * MIN, "Z5", 9],
    [15, 5, 3 * MIN, 3 * MIN, "Z5", 9], [15, 4, 4 * MIN, 4 * MIN, "Z5", 9], [15, 5, 4 * MIN, 3 * MIN, "Z5", 9],
    [15, 5, 4 * MIN, 4 * MIN, "Z5", 9], [15, 6, 4 * MIN, 4 * MIN, "Z5", 9])),
  like("hills", "bike_vo2_short", steps("hills", [15, 8, MIN, MIN, "Z5", 8], [15, 10, MIN, MIN, "Z5", 8],
    [15, 12, MIN, MIN, "Z5", 8], [15, 14, MIN, MIN, "Z5", 8], [15, 16, MIN, MIN, "Z5", 8])),
  like("opener", "bike_opener", steps("opener", [10, 3, 2 * MIN, 2 * MIN, "Z4", 6])),
  test("bike_test", 10, 40, 10),   // the lactate step test on the trainer: 6–8 steps of 5 minutes
];
const SKIERG: Template[] = [...["easy", "recovery", "long", "long_progressive", "threshold", "vo2max", "fartlek",
  "hills", "opener"].map((id) => like(id, `skierg_${id}`)),
  test("skierg_test", 10, 35, 15)];   // the lactate step test on the SkiErg: 6–8 steps of 4 minutes
/** Skis have no test of their own: the athlete tests on the SkiErg, whose zones the skis borrow. */
const SKI: Template[] = [test("ski_test", 10, 35, 15)];
/** Swimming (#666 step 3): on effort (RPE), or with the pace per 100 m of a CSS test. */
const SWIM: Template[] = [
  like("easy", "swim_easy"), like("recovery", "swim_technique"), like("long", "swim_long"),
  like("long_progressive", "swim_long_progressive"),
  like("threshold", "swim_intervals", steps("threshold", [10, 8, 3 * MIN, 30, "Z4", 7], [10, 10, 3 * MIN, 30, "Z4", 7],
    [10, 9, 4 * MIN, 30, "Z4", 7], [10, 6, 6 * MIN, 45, "Z4", 7], [10, 5, 8 * MIN, MIN, "Z4", 7],
    [10, 6, 8 * MIN, MIN, "Z4", 7])),
  like("vo2max", "swim_hard", steps("vo2max", [15, 10, MIN, MIN, "Z5", 9], [15, 12, MIN, MIN, "Z5", 9],
    [15, 8, 2 * MIN, MIN, "Z5", 9], [15, 10, 2 * MIN, MIN, "Z5", 9], [15, 8, 3 * MIN, MIN, "Z5", 9],
    [15, 10, 3 * MIN, MIN, "Z5", 9])),
  like("fartlek", "swim_mixed", steps("fartlek", [10, 4, 4 * MIN, MIN, "Z3", 6, "Z2"], [10, 5, 4 * MIN, MIN, "Z3", 6, "Z2"],
    [10, 6, 4 * MIN, MIN, "Z3", 6, "Z2"], [10, 4, 6 * MIN, MIN, "Z3", 6, "Z2"], [10, 5, 6 * MIN, MIN, "Z3", 6, "Z2"])),
  like("hills", "swim_sprints", steps("hills", [15, 8, 30, 30, "Z5", 8], [15, 12, 30, 30, "Z5", 8],
    [15, 16, 30, 30, "Z5", 8], [15, 20, 30, 30, "Z5", 8], [15, 24, 30, 30, "Z5", 8])),
  like("opener", "swim_opener", steps("opener", [10, 4, MIN, MIN, "Z4", 6])),
  test("swim_test", 20, 25, 15),   // the CSS test: 400 m and, after a full rest, 200 m
];
for (const t of [...BIKE, ...SKIERG, ...SKI, ...SWIM]) TEMPLATES[t.id] = t;

/** Sport -> running template -> the sport's template (library.SPORT_TEMPLATES). */
export const SPORT_TEMPLATES: Record<string, Record<string, string>> = {
  bike: Object.fromEntries(BIKE.map((t) => [t.base, t.id])),
  skierg: Object.fromEntries(SKIERG.map((t) => [t.base, t.id])),
  swim: Object.fromEntries(SWIM.map((t) => [t.base, t.id])),
};

// ---- strength (library.py, #666 step 3): no zones, a fixed length, effort only.
const strength = (id: string, warm: number, main: number, rpe: number, mobility: number): Template =>
  tpl(id, "strength", false, null, { steps: [[part("warmup", warm * MIN, null, 3), part("strength", main * MIN, null, rpe),
    part("mobility", mobility * MIN, null, 2)]] });
for (const t of [strength("strength_full", 10, 30, 7, 5), strength("strength_legs", 10, 25, 7, 5),
  strength("strength_upper", 8, 20, 6, 7), strength("strength_maintenance", 8, 15, 5, 7)]) TEMPLATES[t.id] = t;
/** The week's strength sessions by their number (library.STRENGTH_WEEK); the first takes the roomiest day. */
export const STRENGTH_WEEK: Record<number, string[]> = { 1: ["strength_full"], 2: ["strength_legs", "strength_upper"],
  3: ["strength_legs", "strength_full", "strength_upper"] };
export const STRENGTH_TAPER = "strength_maintenance";
SPORT_TEMPLATES.ski = { ...SPORT_TEMPLATES.skierg, ...Object.fromEntries(SKI.map((t) => [t.base, t.id])) };

export const forSport = (id: string, sport: string): string => SPORT_TEMPLATES[sport]?.[id] ?? id;

const sameParts = (a: StoredPart[], b: StoredPart[]) => a.length === b.length && a.every((p, i) =>
  p.kind === b[i].kind && p.duration_s === b[i].duration_s && p.zone === b[i].zone && p.rpe === b[i].rpe
  && p.repeat === b[i].repeat && p.rest_s === b[i].rest_s && p.rest_zone === b[i].rest_zone
  && (p.band ?? "") === (b[i].band ?? ""));

/** library.in_sport: a running workout's template and parts moved to `sport`, just as long. */
export function inSport(parts: StoredPart[], id: string, sport: string): [string, StoredPart[]] {
  const to = forSport(id, sport);
  if (to === id) return [id, parts];
  const run = get(id);
  if (run.steps.length) return [to, build(to, { step: run.steps.findIndex((st) => sameParts(st, parts)) })];
  return [to, build(to, { minutes: Math.floor(parts.reduce((s, p) => s + partTotal(p), 0) / MIN) })];
}

export function get(id: string): Template {
  const t = TEMPLATES[id];
  if (!t) throw new Error(`no template ${id}`);
  return t;
}

export const lastStep = (id: string) => Math.max(0, get(id).steps.length - 1);

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
  const kind = t.base || id;
  if (kind === "long_progressive")
    return [part("steady", (o.minutes - 20) * MIN, "Z2", 4), part("steady", 20 * MIN, "Z3", 6)];
  if (id === "swim_technique") return [part("technique", o.minutes * MIN, "Z2", 3)];
  const rpe = ({ recovery: 2, long: 4 } as Record<string, number>)[kind] ?? 3;
  return [part("steady", o.minutes * MIN, "Z2", rpe)];
}
