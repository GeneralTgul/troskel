/**
 * The short payload (format 2, or 3 with the coach's choices, `lopanalys.plan.link.short_plan`): the plan's request
 * instead of its workouts.
 * The app builds the plan with its own engine, the same rules as Tröskel's, so the plan is the one Tröskel saved
 * (test/link.test.ts checks every fixture). Keys are short and a default is left out; `x` holds the fields of the
 * plan the engine does not give back as they were saved (notes, the log of earlier tests, a status, the athlete's
 * changes of single workouts).
 * A short payload from another engine version is not built: the app asks for an update instead.
 */
import { applyEdits } from "../engine/edits";
import { ENGINE_VERSION, buildPlan, sketchOf } from "../engine/engine";
import { CODEC_FORMAT, normRequest } from "../engine/models";
import { FORMAT, KIND, type StoredCapacity, type StoredGoal, type StoredPlan, type StoredPlanBody, type StoredRequest,
  type ZoneTarget, type ZoneTargets } from "./types";

export const SHORT_FORMAT = 2;
/** Format 2 with the coach's choices in the request (`link.CHOICES_FORMAT`): an app from before them turns it down
 * as too new instead of dropping the keys it does not know. */
export const CHOICES_FORMAT = 3;
export const SHORT_FORMATS: readonly number[] = [SHORT_FORMAT, CHOICES_FORMAT];

type Row = (string | number | null | string[])[];

export interface ShortPayload {
  kind: string;
  format: number;
  /** The engine version that built the plan. */
  v: string;
  id: string;
  /** The athlete's name ("" when missing). */
  n?: string;
  /** The horizon of a rolling plan. */
  h?: number;
  /** `created`, when it is not midnight (UTC) of the start. */
  c?: string;
  r: ShortRequest;
  x?: Partial<StoredPlanBody>;
  t: Record<string, Record<string, Row>>;
  tl?: [string, string, Record<string, Row>][];
}

interface ShortCapacity { s: string; a?: (number | string | null)[]; t?: string; o?: string; l?: StoredCapacity["speed_line"]; c?: number }

interface ShortRequest {
  s: string;
  sp?: string[];
  a: Row[];
  c?: ShortCapacity[];
  g?: Row[];
  ro?: Row[];
  w?: number;
  l?: string;
  m?: number;
  st?: number;
  /** The fixed hard days: [weekday], [weekday, sport] or [weekday, sport, minutes]. */
  hd?: (number | string)[][];
  /** The coach's choices (link.pack_request): quality sessions, the fixed easy days (as `hd`), the second long
   * workout (1), the easy weeks (ISO dates) and the most minutes a week. */
  q?: number;
  ed?: (number | string)[][];
  sl?: number;
  ew?: string[];
  mx?: number;
}

const ANCHORS = ["max_hr", "resting_hr", "lt1_hr", "lt2_hr", "lt1_speed_kmh", "lt2_speed_kmh"] as const;
const FLAGS = ["estimated", "speed_low_approx", "speed_high_approx", "speed_from_hr"] as const;

const at = <T>(row: readonly unknown[], i: number): T | null => (i < row.length ? (row[i] as T) : null);

function capacity(d: ShortCapacity): StoredCapacity {
  const out: StoredCapacity = { sport: d.s, anchors: null, tested: d.t ?? null, source: d.o ?? "" };
  if (d.a) {
    const a: Record<string, unknown> = {};
    ANCHORS.forEach((k, i) => { a[k] = at(d.a!, i); });
    a.sport = at<string>(d.a, 6) ?? d.s;
    out.anchors = a as unknown as StoredCapacity["anchors"];
  }
  if (d.l) out.speed_line = d.l;
  if (d.c != null) out.css_s100 = d.c;
  return out;
}

const goal = (g: Row): StoredGoal => ({ name: g[0] as string, date: g[1] as string, sport: at<string>(g, 2) ?? "run",
  distance_km: at<number>(g, 3), priority: at<string>(g, 4) ?? "A", target: at<string>(g, 5) ?? "" });

/** `link.unpack_request`: the request as `codec.request_to_dict` writes it. */
export function unpackRequest(d: ShortRequest): StoredRequest {
  const sports = d.sp ?? ["run"];
  const start = (v: string | null) => (v && v.length === 5 ? `${v}:00` : v);
  const out: StoredRequest = {
    format: CODEC_FORMAT, start: d.s, sports: [...sports],
    availability: d.a.map((x) => ({ weekday: x[0] as number, max_minutes: x[1] as number,
      sports: [...(at<string[]>(x, 3) ?? sports)], start: start(at<string>(x, 2)), role: at<string>(x, 4) ?? "core" })),
    capacity: (d.c ?? []).map(capacity), goals: (d.g ?? []).map(goal),
    duration_weeks: d.w ?? null, level: d.l ?? "recreational", recent_weekly_minutes: d.m ?? null,
    roles: (d.ro ?? []).map((x) => ({ sport: x[0] as string, role: at<string>(x, 1) ?? "main", share: at<number>(x, 2) ?? 0 })),
  };
  if (d.st) out.strength_sessions = d.st;
  /** `link._unpack_day`: the sport and the minutes only when set. */
  const day = (h: (number | string)[]) => ({ weekday: h[0] as number, ...(h.length > 1 && h[1] ? { sport: h[1] as string } : {}),
    ...(h.length > 2 && h[2] ? { minutes: h[2] as number } : {}) });
  if (d.hd?.length) out.hard_days = d.hd.map(day);
  if (d.q) out.quality_sessions = d.q;
  if (d.ed?.length) out.easy_days = d.ed.map(day);
  if (d.sl) out.second_long = true;
  if (d.ew?.length) out.easy_weeks = [...d.ew];
  if (d.mx) out.max_weekly_minutes = d.mx;
  return out;
}

/** `link.unpack_targets`: [hr low, hr high, speed low, speed high, flags as bits, watt low, watt high, swim low, swim high]. */
export function unpackTargets(d: Record<string, Row>): ZoneTargets {
  const out: ZoneTargets = {};
  for (const [zone, row] of Object.entries(d)) {
    const n = (i: number) => at<number>(row, i);
    const flags = n(4) ?? 0;
    const tg: Record<string, number | boolean | null> = { hr_low: n(0), hr_high: n(1), speed_low_kmh: n(2), speed_high_kmh: n(3) };
    FLAGS.forEach((k, i) => { tg[k] = ((flags >> i) & 1) === 1; });
    if (n(5) || n(6)) Object.assign(tg, { power_low_w: n(5), power_high_w: n(6) });
    if (n(7) || n(8)) Object.assign(tg, { swim_low_s: n(7), swim_high_s: n(8) });
    out[zone] = tg as unknown as ZoneTarget;
  }
  return out;
}

export class OtherEngine extends Error {
  constructor(public readonly version: string) {
    super("other engine");
  }
}

/** The whole payload (format 1) that Tröskel saved, built with the engine. Throws OtherEngine for another version. */
export function expandShort(p: ShortPayload): StoredPlan {
  if (p.v !== ENGINE_VERSION) throw new OtherEngine(p.v);
  const request = unpackRequest(p.r);
  const built = buildPlan(normRequest(request), { planId: p.id, created: p.c });
  const h = p.h ?? null;
  const weeks = h == null ? built.weeks : built.weeks.map((w) => (w.index <= h ? w : sketchOf(w)));
  const plan = { ...built, weeks, horizon: h, ...(p.x ?? {}) };
  return {
    kind: KIND, format: FORMAT, athlete: p.n ?? "", request,
    // The athlete's changes of single workouts (`x.edits`), laid on the workouts the engine built (link.expand).
    plan: plan.edits?.length ? applyEdits(plan, normRequest(request)) : plan,
    targets: Object.fromEntries(Object.entries(p.t).map(([s, ts]) => [s, unpackTargets(ts)])),
    targets_log: (p.tl ?? []).map(([until, sport, ts]) => ({ until, sport, targets: unpackTargets(ts) })),
  };
}
