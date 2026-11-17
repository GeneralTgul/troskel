/**
 * The zones' targets in the app (#777): a port of lopanalys/plan/resolve.py (`targets`, `band_targets`,
 * `swim_targets`) and of the default zone system of assessments/zone_systems.py (`convert` for "garmin": the watch's
 * five zones from LT1, LT2 and max HR, else from max HR alone), checked against Python with the fixtures that
 * app/scripts/make_fixtures.py writes (test/fixtures/zones.json.gz, test/zones.test.ts).
 *
 * The app used to show only the targets Tröskel sent with the plan. With the athlete's own values (thresholds, max
 * HR, FTP and CSS typed into the app, `source: "self"`) it works them out itself, as Tröskel does for a test, and
 * `planTargets` gives the plan's targets per sport the way link._targets does.
 *
 * Not ported: the pace from heart rate of a zones test without lactate (hr_zones.zone_speeds, a capacity with a
 * `speed_line` whose zones are estimated) and the lactate curve of the other systems. `zoneTargets` throws
 * `NotInApp` for such a capacity, and `planTargets` keeps the targets Tröskel sent for it.
 */
import type { Sport, StoredAnchors, StoredCapacity, StoredRequest, ZoneTarget, ZoneTargets } from "../model/types";
import { BORROWS, RUNNING, capacityFor, ownCapacity, planRoles, speedFromHr } from "./models";
import { round, roundTo } from "./py";

export const SWIM = "swim";
/** resolve.POWER_SPORTS: the test's load is watt. */
const POWER_SPORTS = ["bike", "skierg"];
/** Capacity.source of the athlete's own values (plan.models.SELF). */
export const SELF = "self";

type Ref = readonly [kind: "max" | "lt1" | "lt2" | "mid", factor: number] | null;

/** zone_systems.SYSTEMS["garmin"]: the borders relative to the anchors, and the nominal % of max HR. */
const CODES = ["Z1", "Z2", "Z3", "Z4", "Z5"] as const;
const ANCHORED: Ref[] = [["max", 0.5], ["max", 0.6], ["lt1", 1.0], ["mid", 0.5], ["lt2", 1.0], ["max", 1.0]];
const PCT_MAX = [50, 60, 70, 80, 90, 100];

/** resolve.SWIM_ZONES: seconds per 100 m added to the CSS pace, (slower end, faster end); null: open. */
const SWIM_ZONES: Record<string, [number | null, number | null]> = {
  Z1: [null, 15.0], Z2: [15.0, 7.0], Z3: [7.0, 3.0], Z4: [3.0, -2.0], Z5: [-2.0, null] };

/** resolve.BANDS: band -> (sport, its zone, low and high share of the threshold power). */
const BANDS: Record<string, [Sport, string, number, number]> = { sweetspot: ["bike", "Z4", 0.88, 0.94] };

export class NotInApp extends Error {
  constructor() {
    super("the targets of this test are worked out in Tröskel");
    this.name = "NotInApp";
  }
}

interface Zone {
  code: string;
  hr_low: number | null;
  hr_high: number | null;
  speed_low_kmh: number | null;
  speed_high_kmh: number | null;
  estimated: boolean;
}

const r1 = (v: number | null) => (v == null ? null : roundTo(v, 1));
const validMax = (mx: number | null | undefined) => (mx && mx >= 100 && mx <= 240 ? Math.trunc(mx) : null);
const hasThresholds = (a: StoredAnchors) => !!(a.lt1_hr && a.lt2_hr && a.lt1_hr < a.lt2_hr);

function resolveRef(ref: Ref, a: StoredAnchors, mx: number | null): number | null {
  if (ref == null) return null;
  const [kind, f] = ref;
  if (kind === "max") return mx ? f * mx : null;
  if (kind === "lt1") return a.lt1_hr ? f * a.lt1_hr : null;
  if (kind === "lt2") return a.lt2_hr ? f * a.lt2_hr : null;
  return a.lt1_hr && a.lt2_hr ? a.lt1_hr + f * (a.lt2_hr - a.lt1_hr) : null;
}

/** zone_systems._speed: only at the thresholds and between them. */
function speedAt(ref: Ref, a: StoredAnchors): number | null {
  if (ref == null || !a.lt1_speed_kmh || !a.lt2_speed_kmh) return null;
  const [kind, f] = ref;
  if (kind === "lt1" && f === 1.0) return a.lt1_speed_kmh;
  if (kind === "lt2" && f === 1.0) return a.lt2_speed_kmh;
  if (kind === "mid") return a.lt1_speed_kmh + f * (a.lt2_speed_kmh - a.lt1_speed_kmh);
  return null;
}

function anchoredZones(a: StoredAnchors, mx: number | null): Zone[] {
  const vals = ANCHORED.map((r) => resolveRef(r, a, mx));
  if (vals.slice(1, -1).some((v) => v == null)) return [];
  const speeds = ANCHORED.map((r) => speedAt(r, a));
  for (let i = vals.length - 2; i >= 0; i--) {
    const lo = vals[i], hi = vals[i + 1];
    if (lo != null && hi != null && lo > hi) {
      vals[i] = hi;
      speeds[i] = null;
    }
  }
  const out: Zone[] = [];
  CODES.forEach((code, i) => {
    const lo = vals[i], hi = vals[i + 1];
    if (lo != null && hi != null && round(hi) <= round(lo)) return;
    out.push({ code, hr_low: lo ? round(lo) : null, hr_high: hi ? round(hi) : null, speed_low_kmh: r1(speeds[i]),
      speed_high_kmh: r1(speeds[i + 1]), estimated: false });
  });
  return out;
}

function nominalZones(mx: number): Zone[] {
  return CODES.map((code, i) => ({ code, hr_low: round(PCT_MAX[i] / 100 * mx), hr_high: round(PCT_MAX[i + 1] / 100 * mx),
    speed_low_kmh: null, speed_high_kmh: null, estimated: true }));
}

/** zone_systems.convert(anchors, "garmin"): the watch's zones at LT1 and LT2, else from max HR; [] without either. */
function convert(a: StoredAnchors): Zone[] {
  const mx = validMax(a.max_hr);
  if (hasThresholds(a) && a.lt2_hr) {
    const zs = anchoredZones(a, mx);
    if (zs.length) return zs;
  }
  return mx == null ? [] : nominalZones(mx);
}

const watt = (v: number | null) => (v ? round(v) : null);

function target(z: Partial<ZoneTarget>): ZoneTarget {
  return { hr_low: null, hr_high: null, speed_low_kmh: null, speed_high_kmh: null, estimated: false,
    speed_low_approx: false, speed_high_approx: false, speed_from_hr: false, ...z };
}

/** resolve.targets: every zone with its targets for workouts in `sport` (null: the test's); {} when nothing is known. */
export function targets(c: StoredCapacity | null | undefined, sport: Sport | null = null): ZoneTargets {
  if (c && c.anchors == null && c.css_s100 && (sport || c.sport) === SWIM) return swimTargets(c.css_s100);
  if (!c || c.anchors == null) return {};
  if (speedFromHr(c)) throw new NotInApp();
  const sp = sport || c.sport;
  const load = sp === c.sport && (sp === RUNNING || POWER_SPORTS.includes(sp));
  const out: ZoneTargets = {};
  for (const z of convert(c.anchors)) {
    const base = { hr_low: z.hr_low, hr_high: z.hr_high, estimated: z.estimated };
    if (!load) out[z.code] = target(base);
    else if (sp === RUNNING) out[z.code] = target({ ...base, speed_low_kmh: z.speed_low_kmh, speed_high_kmh: z.speed_high_kmh });
    else out[z.code] = target({ ...base, power_low_w: watt(z.speed_low_kmh), power_high_w: watt(z.speed_high_kmh) });
  }
  return out;
}

/** resolve.band_targets: the bike's sweet spot, 88–94 % of the threshold power, with the heart rates of its zone. */
export function bandTargets(c: StoredCapacity | null | undefined, sport: Sport | null = null): ZoneTargets {
  if (!c || c.anchors == null) return {};
  const sp = sport || c.sport;
  const threshold = c.anchors.lt2_speed_kmh;
  if (!threshold || sp !== c.sport) return {};
  const out: ZoneTargets = {};
  let zones: ZoneTargets | null = null;
  for (const [band, [bandSport, zone, low, high]] of Object.entries(BANDS)) {
    if (bandSport !== sp) continue;
    zones = zones ?? targets(c, sp);
    const z = zones[zone];
    if (z) out[band] = { ...z, power_low_w: watt(low * threshold), power_high_w: watt(high * threshold),
      speed_low_approx: false, speed_high_approx: false, speed_from_hr: false };
  }
  return out;
}

/** resolve.swim_targets: every zone's pace per 100 m around the CSS pace, to tenths of a second. */
export function swimTargets(css: number): ZoneTargets {
  const out: ZoneTargets = {};
  for (const [code, [slow, fast]] of Object.entries(SWIM_ZONES))
    out[code] = target({ swim_low_s: slow == null ? null : roundTo(css + slow, 1),
      swim_high_s: fast == null ? null : roundTo(css + fast, 1) });
  return out;
}

/** link._target_dict: the watt and the swimming pace only in a target that has them. */
function stored(tg: ZoneTarget): ZoneTarget {
  const { power_low_w, power_high_w, swim_low_s, swim_high_s, ...rest } = tg;
  return { ...rest, ...(power_low_w || power_high_w ? { power_low_w: power_low_w ?? null, power_high_w: power_high_w ?? null } : {}),
    ...(swim_low_s || swim_high_s ? { swim_low_s: swim_low_s ?? null, swim_high_s: swim_high_s ?? null } : {}) };
}

/** link.zone_targets: the zones and the bands as the plan stores them. Throws NotInApp (see the top). */
export function zoneTargets(c: StoredCapacity | null | undefined, sport: Sport | null = null): ZoneTargets {
  const both = { ...targets(c, sport), ...bandTargets(c, sport) };
  return Object.fromEntries(Object.entries(both).map(([z, tg]) => [z, stored(tg!)]));
}

const known = (c: StoredCapacity) => c.anchors != null || c.css_s100 != null;

/**
 * link._targets: each capacity's targets, and a sport without its own that borrows a known one (skis: the SkiErg's
 * heart rates, without its watt). A capacity the app cannot work out (NotInApp) keeps what `sent` has for the sport.
 */
export function planTargets(r: StoredRequest, sent: Record<Sport, ZoneTargets> = {}): Record<Sport, ZoneTargets> {
  const out: Record<Sport, ZoneTargets> = {};
  const put = (sport: Sport, c: StoredCapacity, forSport: Sport | null) => {
    try {
      out[sport] = zoneTargets(c, forSport);
    } catch (e) {
      if (!(e instanceof NotInApp)) throw e;
      out[sport] = sent[sport] ?? {};
    }
  };
  for (const c of r.capacity) put(c.sport, c, null);
  for (const { sport } of planRoles(r)) {
    const c = capacityFor(r, sport);
    if (!(sport in out) && known(c)) put(sport, c, sport);
  }
  return out;
}

/** The athlete's own values (`source: "self"`), not a measured test. */
export const ownValues = (c: StoredCapacity | null | undefined) => !!c && c.source === SELF;

/** A measured test the plan has in the sport (its own, not a borrowed one): own values never replace it unasked. */
export function measuredIn(r: StoredRequest, sport: Sport): StoredCapacity | undefined {
  const c = ownCapacity(r, sport);
  return c && !ownValues(c) && known(c) ? c : undefined;
}

export { BORROWS };
