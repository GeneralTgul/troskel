/**
 * The stored plan as it travels from Tröskel to the app: the payload of `lopanalys.plan.link.stored_plan`.
 * `request` and `plan` are `codec.request_to_dict` and `codec.plan_to_dict` field by field (see codec.py and
 * models.py). Dates are ISO strings ("2026-11-16"), clock times "18:00:00", weekdays 0 = Monday.
 */

export type ZoneCode = "Z1" | "Z2" | "Z3" | "Z4" | "Z5";
export type Sport = string; // plan.models.PLAN_SPORTS: "run", "bike", "skierg", "ski", "swim"
export type IsoDate = string;
export type IsoTime = string;

export interface StoredNote {
  key: string; // i18n key, e.g. "plan.reason.base"
  args: [string, string | number | null][];
}

export interface StoredAnchors {
  max_hr: number | null;
  resting_hr: number | null;
  lt1_hr: number | null;
  lt2_hr: number | null;
  lt1_speed_kmh: number | null;
  lt2_speed_kmh: number | null;
  sport?: Sport;
  [k: string]: unknown;
}

export interface StoredSpeedLine {
  slope: number;
  intercept: number;
  speed_min: number;
  speed_max: number;
  ramp_top_kmh: number | null;
  extrapolate_kmh: number;
}

export interface StoredCapacity {
  sport: Sport;
  anchors: StoredAnchors | null;
  tested: IsoDate | null;
  source: string;
  speed_line?: StoredSpeedLine;
  /** Swimming: the CSS pace of a manual swim test, seconds per 100 m (only when there is one). */
  css_s100?: number;
}

export interface StoredSlot {
  weekday: number; // 0 = Monday
  max_minutes: number;
  sports: Sport[];
  start: IsoTime | null; // null: any time that day
  role: "core" | "extra" | string;
}

/** One sport of a plan (plan.models.SportRole): its role and its share of the weekly time in percent (0: the role's). */
export interface StoredRole {
  sport: Sport;
  role: "main" | "support" | "maintenance" | string;
  share: number;
}

export interface StoredGoal {
  name: string;
  date: IsoDate;
  sport: Sport;
  distance_km: number | null;
  priority: "A" | "B" | "C" | string;
  target: string;
}

export interface StoredRequest {
  format: number;
  start: IsoDate;
  sports: Sport[];
  availability: StoredSlot[];
  capacity: StoredCapacity[];
  goals: StoredGoal[];
  duration_weeks: number | null;
  level: "beginner" | "recreational" | "trained" | "high" | string;
  recent_weekly_minutes: number | null;
  /** Several sports (#666); missing or [] in a plan saved before them: the sports in `sports`. */
  roles?: StoredRole[];
  /** Strength sessions a week, 0-3 (#666 step 3); only when set. */
  strength_sessions?: number;
  /** Fixed hard days (engine 9): the week's quality sessions go there first; only when set. */
  hard_days?: StoredHardDay[];
}

/** A weekday the coach fixes as a hard day, with the sport of its quality session (none: the engine's choice). */
export interface StoredHardDay {
  weekday: number;
  sport?: Sport;
}

export type PartKind = "warmup" | "steady" | "work" | "cooldown" | "test" | "race" | string;

export interface StoredPart {
  kind: PartKind;
  duration_s: number;
  zone: ZoneCode | null;
  rpe: number;
  repeat: number;
  rest_s: number;
  rest_zone: ZoneCode;
  /** A narrower target inside the zone (resolve.BANDS): "sweetspot" on the bike. Missing on every other part. */
  band?: string;
}

export interface StoredWorkout {
  key: string; // "<plan>-<yy><ww>-<day>", stable
  date: IsoDate;
  sport: Sport;
  template_id: string;
  family: string;
  quality: boolean;
  start: IsoTime | null;
  status: "planned" | "done" | "moved" | "missed" | "skipped" | string;
  goal: StoredGoal | null;
  parts: StoredPart[];
  /** A title written for this workout in place of the template's (a plan written outside the engine); missing:
   * the template's. */
  name?: string;
}

/** A week beyond the rolling plan's horizon: the sums the engine filled it with, without the workouts. */
export interface StoredSketch {
  minutes: number;
  sessions: number; // workouts, races not counted
  quality: number; // of them hard days
  longest: number; // the longest run, minutes
  zones: [ZoneCode | string, number][]; // minutes per zone
  strength?: number; // strength sessions, not among `sessions` (only when there are any)
}

export interface StoredWeek {
  index: number; // 1-based
  monday: IsoDate;
  block: "base" | "build" | "peak" | "taper" | string;
  kind: "load" | "easy" | "test" | "taper" | "race" | string;
  target_minutes: number;
  notes: StoredNote[];
  workouts: StoredWorkout[]; // a sketched week: only its races
  sketch?: StoredSketch; // set beyond the horizon of a rolling plan
}

export interface StoredBlock {
  kind: string;
  first_week: number;
  weeks: number;
  start: IsoDate;
  reason: StoredNote;
}

export interface StoredPlanBody {
  format: number;
  id: string;
  created: string; // ISO datetime with offset
  engine_version: string;
  zone_system: string;
  status: string;
  based_on: Record<Sport, IsoDate | null>;
  notes: StoredNote[];
  capacity_log: [IsoDate, StoredCapacity][];
  blocks: StoredBlock[];
  weeks: StoredWeek[];
  /** The last week with workouts (1-based) in a rolling plan; null or missing: every week has its workouts. */
  horizon?: number | null;
  /** Who wrote the plan (plan.models.Plan.source): missing the engine; anything else (e.g. "schedule") a plan written
   * outside the engine, week by week, which the app never rebuilds (`fromEngine`). */
  source?: string;
  /** The athlete's changes of single workouts (#777, plan.models.Edit), laid on the weeks again by every rebuild
   * (engine/edits.ts); missing when there are none. */
  edits?: StoredEdit[];
  /** The day of the athlete's next test, chosen by the coach in Tröskel (#778, plan.models.Plan.next_test); missing:
   * the default, 8-12 weeks after the test (model/nexttest.ts). */
  next_test?: IsoDate;
}

/** A workout moved within its week (`date`), shortened (`minutes`), or both; `key` is the workout's. A swap is two. */
export interface StoredEdit {
  key: string;
  date?: IsoDate;
  minutes?: number;
}

/** One zone's targets, resolved by Tröskel from the athlete's test (resolve.Target without the name). */
export interface ZoneTarget {
  hr_low: number | null;
  hr_high: number | null;
  speed_low_kmh: number | null; // the slower end
  speed_high_kmh: number | null;
  estimated: boolean;
  speed_low_approx: boolean;
  speed_high_approx: boolean;
  speed_from_hr: boolean;
  /** The bike and the SkiErg: watt from the test, the easier end first (missing in a running target and in
   * plans sent before #666 step 2). The speed_*_approx flags mark them as they mark the pace. */
  power_low_w?: number | null;
  power_high_w?: number | null;
  /** Swimming with a CSS test: seconds per 100 m, the slower end first (only in a swimming target). */
  swim_low_s?: number | null;
  swim_high_s?: number | null;
}

/** The zones' targets, and a band's target under the band's name ("sweetspot": the bike's 88–94 % of the
 * threshold power) when the test gave one. */
export type ZoneTargets = Partial<Record<ZoneCode, ZoneTarget>> & { [band: string]: ZoneTarget | undefined };

export interface StoredPlan {
  kind: "troskel.plan";
  format: number;
  athlete: string;
  request: StoredRequest;
  plan: StoredPlanBody;
  targets: Record<Sport, ZoneTargets>;
  targets_log: { until: IsoDate; sport: Sport; targets: ZoneTargets }[];
}

export const KIND = "troskel.plan";
export const FORMAT = 1;
export const ZONES: ZoneCode[] = ["Z1", "Z2", "Z3", "Z4", "Z5"];
export const WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
