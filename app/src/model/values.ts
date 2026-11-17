/**
 * The athlete's own values (#777): max HR, resting HR, the heart rate and the pace or watt at the two thresholds
 * (LT1, LT2; on the bike LT2 is the FTP) and the CSS pace in swimming, typed in by the athlete when no test was made
 * in Tröskel. They are stored as the plan's capacity in the sport, as a test is, with `source: "self"` and no test day
 * (plan.models.SELF), and the app works their targets out itself (engine/zones.ts).
 *
 * The rules:
 * - Own values never replace a measured test unasked: a sport with a test from Tröskel shows the test, and the
 *   athlete must choose *Skriv in egna värden i stället* (the form then gets a row for the sport, `values[sport]`).
 * - A test in Tröskel replaces own values in its sport (resolve.retest): a plan from the coach with the same id and a
 *   test in the sport takes its place, and own values in the other sports are kept (`keptOwnValues`).
 * - Tröskel never compares a test with own values: the report's *Mot schemat* (plan.expected) takes two tests.
 *
 * app/scripts/make_fixtures.py has the same rules in Python (`values_capacity`, `with_values`), and
 * test/values.test.ts checks the app against them (test/fixtures/zones.json.gz).
 */
import { capacityNotes, basedOn, isCapacityNote } from "../engine/engine";
import { BORROWS, normRequest, ownCapacity, planRoles } from "../engine/models";
import { SELF, SWIM, measuredIn, ownValues, planTargets } from "../engine/zones";
import type { Sport, StoredCapacity, StoredPlan, StoredRequest } from "./types";

/** One sport's fields as typed: digits, a pace as "m:ss" (per km running, per 100 m swimming) or watt. */
export interface SportValues {
  max_hr: string;
  resting_hr: string;
  lt1_hr: string;
  lt2_hr: string;
  /** Running: the pace at LT1 ("5:20" per km); the bike and the SkiErg: the watt. */
  lt1: string;
  /** Running: the pace at LT2; the bike: the FTP; the SkiErg: the watt at LT2. */
  lt2: string;
  /** Swimming: the CSS pace per 100 m ("1:55"). */
  css: string;
}

export type OwnValues = Partial<Record<Sport, SportValues>>;

/** The sports that take own values, and what they ask for: running a pace, the bike and the SkiErg watt, skis the
 * heart rates only (skis have no load of their own; resolve.targets), swimming the CSS pace. */
export const VALUE_SPORTS: Sport[] = ["run", "bike", "skierg", "ski", "swim"];
export const loadOf = (sport: Sport): "pace" | "watt" | "none" | "css" =>
  sport === "run" ? "pace" : sport === "bike" || sport === "skierg" ? "watt" : sport === SWIM ? "css" : "none";

/** The limits a value must lie within (the same in Python's mirror, make_fixtures.values_capacity). */
export const LIMITS = {
  max_hr: [100, 240], resting_hr: [25, 110], hr: [80, 235], pace_s: [120, 720], watt: [30, 900], css_s: [50, 240],
} as const;

export const emptyValues = (): SportValues => ({ max_hr: "", resting_hr: "", lt1_hr: "", lt2_hr: "", lt1: "", lt2: "", css: "" });

/** A field that cannot be read: the text key (ui.values.err.*) and the sport. */
export class ValuesError extends Error {
  constructor(readonly key: string, readonly args: Record<string, string | number> = {}) {
    super(key);
    this.name = "ValuesError";
  }
}

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function int(v: string, sport: Sport, field: string, [lo, hi]: readonly [number, number]): number | null {
  const s = v.trim();
  if (!s) return null;
  if (!/^\d+$/.test(s) || Number(s) < lo || Number(s) > hi) throw new ValuesError(`ui.values.err.${field}`, { sport, lo, hi });
  return Number(s);
}

function seconds(v: string, sport: Sport, field: string, [lo, hi]: readonly [number, number]): number | null {
  const s = v.trim();
  if (!s) return null;
  const m = /^(\d{1,2})[:.](\d{2})$/.exec(s);
  const n = m && Number(m[2]) < 60 ? Number(m[1]) * 60 + Number(m[2]) : NaN;
  if (!(n >= lo && n <= hi)) throw new ValuesError(`ui.values.err.${field}`, { sport, lo: mmss(lo), hi: mmss(hi) });
  return n;
}

/** The capacity of one sport's own values; null when every field is empty. Throws ValuesError. */
export function valuesCapacity(sport: Sport, v: SportValues): StoredCapacity | null {
  if (loadOf(sport) === "css") {
    const css = seconds(v.css, sport, "css", LIMITS.css_s);
    return css == null ? null : { sport, anchors: null, tested: null, source: SELF, css_s100: css };
  }
  const max = int(v.max_hr, sport, "max_hr", LIMITS.max_hr);
  const rest = int(v.resting_hr, sport, "resting_hr", LIMITS.resting_hr);
  const lt1 = int(v.lt1_hr, sport, "hr", LIMITS.hr);
  const lt2 = int(v.lt2_hr, sport, "hr", LIMITS.hr);
  if (rest != null && max != null && rest >= max) throw new ValuesError("ui.values.err.order", { sport });
  if ((lt1 != null && lt2 != null && lt1 >= lt2) || (lt2 != null && max != null && lt2 > max)
    || (lt1 != null && max != null && lt1 >= max)) throw new ValuesError("ui.values.err.order", { sport });
  let s1: number | null = null, s2: number | null = null;
  const load = loadOf(sport);
  if (load === "pace") {
    const p1 = seconds(v.lt1, sport, "pace", LIMITS.pace_s);
    const p2 = seconds(v.lt2, sport, "pace", LIMITS.pace_s);
    if (p1 != null && p2 != null && p1 <= p2) throw new ValuesError("ui.values.err.load_order", { sport });
    s1 = p1 == null ? null : 3600 / p1;
    s2 = p2 == null ? null : 3600 / p2;
  } else if (load === "watt") {
    s1 = int(v.lt1, sport, "watt", LIMITS.watt);
    s2 = int(v.lt2, sport, "watt", LIMITS.watt);
    if (s1 != null && s2 != null && s1 >= s2) throw new ValuesError("ui.values.err.load_order", { sport });
  }
  if ([max, rest, lt1, lt2, s1, s2].every((x) => x == null)) return null;
  return { sport, anchors: { max_hr: max, resting_hr: rest, lt1_hr: lt1, lt2_hr: lt2, lt1_speed_kmh: s1,
    lt2_speed_kmh: s2, sport }, tested: null, source: SELF };
}

const str = (v: number | null | undefined) => (v == null ? "" : String(Math.round(v)));
const pace = (kmh: number | null | undefined) => (kmh ? mmss(Math.round(3600 / kmh)) : "");

/** The fields of a capacity (own values or a test), to fill the form in with. */
export function valuesOf(c: StoredCapacity): SportValues {
  const a = c.anchors;
  const load = loadOf(c.sport);
  return {
    ...emptyValues(), max_hr: str(a?.max_hr), resting_hr: str(a?.resting_hr), lt1_hr: str(a?.lt1_hr), lt2_hr: str(a?.lt2_hr),
    lt1: load === "pace" ? pace(a?.lt1_speed_kmh) : load === "watt" ? str(a?.lt1_speed_kmh) : "",
    lt2: load === "pace" ? pace(a?.lt2_speed_kmh) : load === "watt" ? str(a?.lt2_speed_kmh) : "",
    css: c.css_s100 ? mmss(Math.round(c.css_s100)) : "",
  };
}

/** The own values a plan holds, as fields. */
export function ownValuesOf(r: StoredRequest): OwnValues {
  const out: OwnValues = {};
  for (const c of r.capacity) if (ownValues(c)) out[c.sport] = valuesOf(c);
  return out;
}

/**
 * The capacity with the own values laid on it: a sport with a row in `values` gets its own values in place of what
 * it had (a test too: the row is the athlete's choice), or nothing when the row is empty; the other sports keep
 * theirs. Only the sports in `sports` take own values. Throws ValuesError.
 */
export function withValues(capacity: StoredCapacity[], values: OwnValues, sports: Sport[]): StoredCapacity[] {
  const own: StoredCapacity[] = [];
  for (const sp of VALUE_SPORTS) {
    const v = values[sp];
    if (!v || !sports.includes(sp)) continue;
    const c = valuesCapacity(sp, v);
    if (c) own.push(c);
  }
  const replaced = (sp: Sport) => !!values[sp] && sports.includes(sp);
  return [...capacity.filter((c) => !replaced(c.sport)), ...own];
}

/**
 * The stored plan on new own values (*Mina värden*): the same weeks and workouts, the request's capacity with the
 * values laid on it, the targets of the sports with own values worked out here (the tests' stay as Tröskel sent them),
 * and the notes and `based_on` that build_plan gives for that capacity. The whole plan follows the new values.
 */
export function planWithValues(stored: StoredPlan, values: OwnValues): StoredPlan {
  const r = stored.request;
  const request = normRequest({ ...r, capacity: withValues(r.capacity, values, planRoles(r).map((x) => x.sport)) });
  return withCapacity(stored, request);
}

/** The plan's targets per sport (link._targets): those of own values worked out here, a test's as Tröskel sent them
 * in `sent` (the app does not have every zones test's rules), else worked out here too. */
export function targetsFor(request: StoredRequest, sent: StoredPlan["targets"] = {}): StoredPlan["targets"] {
  const targets: StoredPlan["targets"] = {};
  for (const [sport, tg] of Object.entries(planTargets(request, sent))) {
    const own = ownCapacity(request, sport) ?? (sport in BORROWS ? ownCapacity(request, BORROWS[sport]) : undefined);
    targets[sport] = own && !ownValues(own) && sent[sport] ? sent[sport] : tg;
  }
  return targets;
}

function withCapacity(stored: StoredPlan, request: StoredRequest): StoredPlan {
  const targets = targetsFor(request, stored.targets);
  const notes = [...stored.plan.notes.filter((n) => !isCapacityNote(n.key)), ...capacityNotes(request)];
  return { ...stored, request, targets, plan: { ...stored.plan, notes, based_on: basedOn(request) } };
}

/**
 * The own values to keep when a plan from the coach replaces `old` (#777): the same plan id (a new test, or the
 * coach's plan after the phone's was read in), and own values in a sport the new plan has no capacity of its own in.
 * A test in the sport, or a new test in the sport skis borrow, takes their place. Another plan: none are kept.
 */
export function keptOwnValues(old: StoredPlan | null, incoming: StoredPlan): StoredPlan {
  if (!old || old.plan.id !== incoming.plan.id) return incoming;
  const r = incoming.request;
  const sports = planRoles(r).map((x) => x.sport);
  const same = (a: StoredCapacity | undefined, b: StoredCapacity | undefined) => JSON.stringify(a) === JSON.stringify(b);
  const keep = old.request.capacity.filter((c) => ownValues(c) && sports.includes(c.sport) && !ownCapacity(r, c.sport)
    && !(c.sport in BORROWS && measuredIn(r, BORROWS[c.sport])
      && !same(measuredIn(r, BORROWS[c.sport]), measuredIn(old.request, BORROWS[c.sport]))));
  if (!keep.length) return incoming;
  return withCapacity(incoming, normRequest({ ...r, capacity: [...r.capacity, ...keep] }));
}

export { measuredIn, ownValues };
