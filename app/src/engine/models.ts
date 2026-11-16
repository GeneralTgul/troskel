/**
 * The engine's view of the stored plan: what models.py computes (minutes, zone time, the sketch of a week)
 * and what codec.py does when it reads a stored plan back (defaults, old sport names). The engine works on
 * the stored shapes of model/types.ts directly, so what it builds is the stored plan as Python writes it.
 */
import type {
  StoredCapacity, StoredGoal, StoredNote, StoredPart, StoredPlanBody, StoredRequest, StoredSketch,
  StoredRole, StoredSlot, StoredWeek, StoredWorkout,
} from "../model/types";
import { round, round2 } from "./py";

export const RUNNING = "run";
/** A sport without its own test borrows the zones of another: skis take the SkiErg's. */
export const BORROWS: Record<string, string> = { ski: "skierg" };
export const ROLES = ["main", "support", "maintenance"];
export const ROLE_SHARE: Record<string, number> = { main: 60, support: 30, maintenance: 10 };
export const LEVELS = ["beginner", "recreational", "trained"] as const;
export const WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export const EASY_ZONES = ["Z1", "Z2"];
export const DEFAULT_SYSTEM = "garmin";
export const CODEC_FORMAT = 2;

/** A plan the engine cannot make, with the text key of the reason (plan.error.*) and its values. */
export class PlanError extends Error {
  constructor(readonly key: string, readonly args: Record<string, string | number> = {}) {
    super(key);
    this.name = "PlanError";
  }
}

/** Note.of: the values sorted by name. */
export function note(key: string, args: Record<string, string | number | null> = {}): StoredNote {
  return { key, args: Object.keys(args).sort().map((k) => [k, args[k]]) };
}

// ---- the numbers of models.py -----------------------------------------------------------------------

const partWork = (p: StoredPart) => p.repeat * p.duration_s;
export const partTotal = (p: StoredPart) => partWork(p) + (p.repeat - 1) * p.rest_s;
export const seconds = (w: StoredWorkout) => w.parts.reduce((s, p) => s + partTotal(p), 0);
export const minutes = (w: StoredWorkout) => round(seconds(w) / 60);

/** Seconds per zone, in the order the zones first appear (Workout.zone_seconds). */
export function zoneSeconds(w: StoredWorkout): Map<string, number> {
  const out = new Map<string, number>();
  for (const p of w.parts) {
    if (p.zone == null) continue;
    out.set(p.zone, (out.get(p.zone) ?? 0) + partWork(p));
    if (p.repeat > 1 && p.rest_s) out.set(p.rest_zone, (out.get(p.rest_zone) ?? 0) + (p.repeat - 1) * p.rest_s);
  }
  return out;
}

export const weekMinutes = (wk: StoredWeek) =>
  wk.sketch ? wk.sketch.minutes : wk.workouts.reduce((s, w) => s + minutes(w), 0);

export function weekZoneMinutes(wk: StoredWeek): Map<string, number> {
  if (wk.sketch) return new Map(wk.sketch.zones.map(([z, m]) => [z, m]));
  const out = new Map<string, number>();
  for (const w of wk.workouts) for (const [z, s] of zoneSeconds(w)) out.set(z, (out.get(z) ?? 0) + s / 60);
  return out;
}

/** Week.sketch from a detailed week: its sums, and only its races (engine.sketch_of). */
export function sketchOf(wk: StoredWeek): StoredWeek {
  if (wk.sketch) return wk;
  const zm = weekZoneMinutes(wk);
  const plain = wk.workouts.filter((w) => w.goal == null);
  const sketch: StoredSketch = {
    minutes: weekMinutes(wk), sessions: plain.length, quality: plain.filter((w) => w.quality).length,
    longest: plain.reduce((m, w) => Math.max(m, minutes(w)), 0),
    zones: [...zm.keys()].sort().map((z) => [z, round2(zm.get(z)!)]),
  };
  return { ...wk, workouts: wk.workouts.filter((w) => w.goal != null), sketch };
}

// ---- codec.py: a stored plan read back and written again --------------------------------------------

const OLD_SPORT: Record<string, string> = { running: "run" };
const sport = (v: string) => OLD_SPORT[v] ?? v;
const isoTime = (v: string | null | undefined) => (v ? (v.length === 5 ? `${v}:00` : v) : null);
const ANCHORS = ["max_hr", "resting_hr", "lt1_hr", "lt2_hr", "lt1_speed_kmh", "lt2_speed_kmh", "sport"] as const;

export function normCapacity(c: StoredCapacity): StoredCapacity {
  const a = c.anchors;
  const anchors = a == null ? null
    : Object.fromEntries(ANCHORS.map((k) => [k, k === "sport" ? sport((a.sport as string) ?? "run") : a[k] ?? null]));
  const out: StoredCapacity = { sport: sport(c.sport), anchors: anchors as StoredCapacity["anchors"],
    tested: c.tested || null, source: c.source ?? "" };
  const ln = c.speed_line;
  if (ln) out.speed_line = { slope: ln.slope, intercept: ln.intercept, speed_min: ln.speed_min, speed_max: ln.speed_max,
    ramp_top_kmh: ln.ramp_top_kmh ?? null, extrapolate_kmh: ln.extrapolate_kmh ?? 2.0 };
  return out;
}

export const normGoal = (g: StoredGoal): StoredGoal => ({ name: g.name, date: g.date, sport: sport(g.sport),
  distance_km: g.distance_km ?? null, priority: g.priority ?? "A", target: g.target ?? "" });

export const normSlot = (s: StoredSlot): StoredSlot => ({ weekday: s.weekday, max_minutes: s.max_minutes,
  sports: s.sports.map(sport), start: isoTime(s.start), role: s.role ?? "core" });

export function normRequest(r: StoredRequest): StoredRequest {
  return {
    format: CODEC_FORMAT, start: r.start, sports: r.sports.map(sport), availability: r.availability.map(normSlot),
    capacity: (r.capacity ?? []).map(normCapacity), goals: (r.goals ?? []).map(normGoal),
    duration_weeks: r.duration_weeks ?? null, level: r.level ?? "recreational",
    recent_weekly_minutes: r.recent_weekly_minutes ?? null,
    roles: (r.roles ?? []).map((x) => ({ sport: sport(x.sport), role: x.role ?? "main", share: x.share ?? 0 })),
  };
}

/** SportRole.weight: the share, or the role's default share. */
export const roleWeight = (r: StoredRole) => (r.share > 0 ? r.share : ROLE_SHARE[r.role] ?? ROLE_SHARE.support);

/** PlanRequest.plan_roles: the plan's sports with their roles, the main sport first and each sport once. */
export function planRoles(r: StoredRequest): StoredRole[] {
  const given: StoredRole[] = r.roles?.length ? r.roles : r.sports.map((s) => ({ sport: s, role: "support", share: 0 }));
  const out: StoredRole[] = [];
  for (const x of given)
    if (!out.some((o) => o.sport === x.sport))
      out.push(ROLES.includes(x.role) ? x : { sport: x.sport, role: "support", share: x.share });
  if (!out.length) return [];
  let main = out.find((x) => x.role === "main");
  if (main === undefined) {
    const a = r.goals.filter((g) => g.priority === "A").map((g, i) => [g, i] as const)
      .sort(([g, i], [h, j]) => (g.date < h.date ? -1 : g.date > h.date ? 1 : i - j)).map(([g]) => g);
    main = out.find((x) => a.length > 0 && x.sport === a[a.length - 1].sport) ?? out[0];
  }
  const rest = out.filter((x) => x !== main).map((x) => (x.role === "main" ? { sport: x.sport, role: "support", share: x.share } : x));
  return [{ sport: main.sport, role: "main", share: main.share }, ...rest];
}

export const planSports = (r: StoredRequest) => planRoles(r).map((x) => x.sport);
export const mainSport = (r: StoredRequest) => planRoles(r)[0]?.sport ?? RUNNING;

const normNote = (n: StoredNote): StoredNote => ({ key: n.key, args: (n.args ?? []).map(([k, v]) => [k, v]) });

function normWorkout(w: StoredWorkout): StoredWorkout {
  return {
    key: w.key, date: w.date, sport: sport(w.sport), template_id: w.template_id, family: w.family, quality: w.quality,
    start: isoTime(w.start), status: w.status ?? "planned", goal: w.goal ? normGoal(w.goal) : null,
    parts: w.parts.map((p) => ({ kind: p.kind, duration_s: p.duration_s, zone: p.zone ?? null, rpe: p.rpe,
      repeat: p.repeat ?? 1, rest_s: p.rest_s ?? 0, rest_zone: p.rest_zone ?? "Z1" })),
  };
}

export function normWeek(w: StoredWeek): StoredWeek {
  const out: StoredWeek = { index: w.index, monday: w.monday, block: w.block, kind: w.kind,
    target_minutes: w.target_minutes, notes: (w.notes ?? []).map(normNote), workouts: w.workouts.map(normWorkout) };
  if (w.sketch) out.sketch = { minutes: w.sketch.minutes, sessions: w.sketch.sessions, quality: w.sketch.quality,
    longest: w.sketch.longest, zones: (w.sketch.zones ?? []).map(([z, m]) => [z, m]) };
  return out;
}

export function normPlan(p: StoredPlanBody): StoredPlanBody {
  return {
    format: CODEC_FORMAT, id: p.id, created: p.created, engine_version: p.engine_version ?? "",
    zone_system: p.zone_system ?? DEFAULT_SYSTEM, status: p.status ?? "draft",
    based_on: Object.fromEntries(Object.entries(p.based_on ?? {}).map(([k, v]) => [sport(k), v || null])),
    notes: (p.notes ?? []).map(normNote),
    capacity_log: (p.capacity_log ?? []).map(([until, c]) => [until, normCapacity(c)]),
    blocks: p.blocks.map((b) => ({ kind: b.kind, first_week: b.first_week, weeks: b.weeks, start: b.start,
      reason: normNote(b.reason) })),
    weeks: p.weeks.map(normWeek), horizon: p.horizon ?? null,
  };
}

export const ownCapacity = (r: StoredRequest, sp: string) => r.capacity.find((c) => c.sport === sp);

/** PlanRequest.capacity_for: the sport's capacity, a borrowed one (BORROWS), or one where nothing is known. */
export function capacityFor(r: StoredRequest, sp: string): StoredCapacity {
  return ownCapacity(r, sp) ?? (sp in BORROWS ? ownCapacity(r, BORROWS[sp]) : undefined)
    ?? { sport: sp, anchors: null, tested: null, source: "" };
}

export function hasThresholds(c: StoredCapacity): boolean {
  const a = c.anchors;
  return !!(a && a.lt1_hr && a.lt2_hr && a.lt1_hr < a.lt2_hr);
}

export const estimated = (c: StoredCapacity) => c.anchors == null || !hasThresholds(c);
export const speedFromHr = (c: StoredCapacity) => c.speed_line != null && estimated(c);
