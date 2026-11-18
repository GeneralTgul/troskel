/**
 * The athlete's own plan (#777): the form *Gör ett schema själv* and the stored plan the app's engine builds from it,
 * without a coach and without a test. The form holds the same fields as the coach's form in Tröskel
 * (views/plan.py, `collect`), and `requestFromForm` follows its rules, so that the request is the one Tröskel would
 * make from the same answers (`codec.request_to_dict`): the slots of the days that are on, the roles only with more
 * than one sport or another sport than running, the first goal the A goal and the others milestones before it, a
 * length in weeks without a goal. app/scripts/make_fixtures.py writes the same forms in Python
 * (test/fixtures/request.json.gz), and test/request.test.ts checks that both give the same stored plan.
 *
 * Without a test the capacity is empty: the workouts get zones and effort, no heart rate or pace (as in Tröskel
 * without a test). A form filled in from a plan the phone already has (`formFromPlan`) keeps that plan's tests and
 * their targets, since the targets depend on the tests and not on the answers. The athlete's own values (#777,
 * model/values.ts: max HR, thresholds, FTP, CSS) are `values`, one row per sport, and give the targets of their sport;
 * a row in a sport with a test is the athlete's choice to use own values in its place.
 *
 * The plan rolls as a coach's plan in Tröskel (#789): it is built on the day it is made (`today`), with the weeks up to
 * four weeks ahead detailed and the rest sketches, and `renewOwn` details the next weeks when the app starts and
 * `due` says it is time (`rollForward`, Tröskel's *Förnya*). The workouts of the new weeks follow the request as it
 * is then (new own values, a new test), and the athlete's edits (`plan.edits`) are laid on them again.
 *
 * Not in the form (yet): a second time on a day, extra slots, fixed hard days and the shares of the sports; a form
 * filled in from a plan with them leaves them out.
 */
import { buildPlan, due, rollForward } from "../engine";
import { BORROWS, LEVELS, RUNNING, normRequest, planRoles } from "../engine/models";
import { daysFromSlots } from "./times";
import { FORMAT, KIND, type IsoDate, type Sport, type StoredCapacity, type StoredPlan, type StoredRequest,
  type StoredSlot, type ZoneTargets } from "./types";
import { ownValues, ownValuesOf, targetsFor, withValues, type OwnValues } from "./values";

/** plan.models.PLAN_SPORTS, in the order the form shows them. */
export const PLAN_SPORTS: Sport[] = ["run", "bike", "skierg", "ski", "swim"];
export const FORM_ROLES = ["main", "support", "maintenance"] as const;
/** views/plan.py: MIN_SLOT, MAX_SLOT and MAX_WEEKS. */
export const MIN_SLOT = 20;
export const MAX_SLOT = 480;
export const MAX_WEEKS = 104;
export const MAX_STRENGTH = 3;
export const MILESTONE_PRIORITIES = ["B", "C"] as const;
/** The prefix of a plan id made in the app; Tröskel's own are `p<athlete>`. */
export const APP_ID_PREFIX = "app-";

export interface FormDay {
  on: boolean;
  minutes: number;
  start: string; // "HH:MM", "" for no set time
  /** The sports that day; [] = all of the plan's sports. */
  sports: Sport[];
}

export interface FormSport {
  sport: Sport;
  role: string; // main, support, maintenance
  share: number; // percent of the weekly time, 0: the role's
}

export interface FormGoal {
  name: string;
  date: IsoDate | "";
  sport: Sport | ""; // "": the plan's first sport
  distance_km: number | null;
  priority: string; // the first goal is always A; the others B or C
  target: string;
}

export interface RequestForm {
  athlete: string;
  start: IsoDate;
  sports: FormSport[];
  level: string; // "" until chosen
  recent_weekly_minutes: number | null;
  days: FormDay[]; // 7, Monday first
  mode: "goal" | "weeks";
  goals: FormGoal[];
  weeks: number | null;
  strength_sessions: number;
  /** The tests of the plan the form was filled in from (`formFromPlan`); [] for a plan without a test. */
  capacity: StoredCapacity[];
  /** The athlete's own values per sport (model/values.ts); missing or {}: none. */
  values?: OwnValues;
}

/** A form that cannot give a request: the text key (ui.request.err.* or plan.error.*) and its values (`day`: the
 * weekday, 0 = Monday). */
export class FormError extends Error {
  constructor(readonly key: string, readonly args: Record<string, string | number> = {}) {
    super(key);
    this.name = "FormError";
  }
}

const DEFAULT_DAYS: [number, number, string][] = [[1, 45, ""], [3, 45, ""], [6, 75, ""]];

/** An empty form: running, three days a week, an eight-week plan from `today`. */
export function emptyForm(today: IsoDate): RequestForm {
  const days = Array.from({ length: 7 }, (): FormDay => ({ on: false, minutes: 45, start: "", sports: [] }));
  for (const [wd, m, s] of DEFAULT_DAYS) days[wd] = { on: true, minutes: m, start: s, sports: [] };
  return {
    athlete: "", start: today, sports: [{ sport: RUNNING, role: "main", share: 0 }], level: "",
    recent_weekly_minutes: null, days, mode: "weeks", goals: [newGoal()], weeks: 8, strength_sessions: 0, capacity: [],
    values: {},
  };
}

export const newGoal = (priority = "A"): FormGoal => ({ name: "", date: "", sport: "", distance_km: null, priority, target: "" });

/** The form filled in from a plan the phone has: its answers, its tests, and a start on `today`. */
export function formFromPlan(stored: StoredPlan, today: IsoDate): RequestForm {
  const r = stored.request;
  const sports = planRoles(r).map((x) => ({ sport: x.sport, role: x.role, share: x.share }));
  const all = sports.map((x) => x.sport);
  const rows = daysFromSlots(r.availability);
  const days = rows.map((d, wd): FormDay => {
    const slot = r.availability.find((x) => x.weekday === wd && x.role !== "extra");
    const own = slot ? slot.sports.filter((s) => all.includes(s)) : [];
    return { ...d, sports: own.length && own.length < all.length ? own : [] };
  });
  const goals = r.goals.filter((g) => g.date >= today).map((g): FormGoal => ({
    name: g.name, date: g.date, sport: g.sport, distance_km: g.distance_km, priority: g.priority, target: g.target,
  }));
  const a = goals.findIndex((g) => g.priority === "A");
  if (a > 0) goals.unshift(...goals.splice(a, 1));
  if (goals.length) goals[0] = { ...goals[0], priority: "A" };
  return {
    athlete: stored.athlete ?? "", start: today, sports, level: (LEVELS as readonly string[]).includes(r.level) ? r.level : "",
    recent_weekly_minutes: r.recent_weekly_minutes, days, mode: goals.length ? "goal" : "weeks",
    goals: goals.length ? goals : [newGoal()], weeks: r.duration_weeks ?? 8, strength_sessions: r.strength_sessions ?? 0,
    capacity: (r.capacity ?? []).filter((c) => !ownValues(c)), values: ownValuesOf(r),
  };
}

const isDate = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v);

/** The request of the form, by the rules of the coach's form; throws a FormError when something is wrong or missing. */
export function requestFromForm(f: RequestForm): StoredRequest {
  if (!(LEVELS as readonly string[]).includes(f.level)) throw new FormError("ui.request.err.level");
  const sports: Sport[] = [];
  for (const s of f.sports) if (PLAN_SPORTS.includes(s.sport) && !sports.includes(s.sport)) sports.push(s.sport);
  if (!sports.length) throw new FormError("ui.request.err.sport");
  const slots: StoredSlot[] = [];
  f.days.forEach((d, wd) => {
    if (!d.on) return;
    if (!Number.isFinite(d.minutes) || d.minutes < MIN_SLOT || d.minutes > MAX_SLOT)
      throw new FormError("ui.request.err.minutes", { day: wd, lo: MIN_SLOT, hi: MAX_SLOT });
    if (d.start && !/^\d{2}:\d{2}$/.test(d.start)) throw new FormError("ui.request.err.clock", { day: wd });
    const own = d.sports.filter((s) => sports.includes(s));
    slots.push({ weekday: wd, max_minutes: Math.round(d.minutes), sports: own.length ? own : [...sports],
      start: d.start ? `${d.start}:00` : null, role: "core" });
  });
  if (!slots.length) throw new FormError("plan.error.no_slots");
  if (!isDate(f.start)) throw new FormError("ui.request.err.start");
  const goals: StoredRequest["goals"] = [];
  let weeks: number | null = null;
  if (f.mode === "goal") {
    f.goals.forEach((g, i) => {
      const name = g.name.trim();
      if (!name) throw new FormError("ui.request.err.goal_name");
      if (!isDate(g.date)) throw new FormError("ui.request.err.goal_date", { name });
      const priority = i === 0 ? "A" : (MILESTONE_PRIORITIES as readonly string[]).includes(g.priority) ? g.priority : "B";
      if (i > 0 && g.date >= goals[0].date) throw new FormError("ui.request.err.milestone_late", { name });
      goals.push({ name, date: g.date, sport: g.sport && sports.includes(g.sport) ? g.sport : sports[0],
        distance_km: g.distance_km && g.distance_km > 0 ? g.distance_km : null, priority,
        target: i === 0 ? g.target.trim() : "" });
    });
    if (!goals.length) throw new FormError("ui.request.err.goal_name");
  } else {
    if (f.weeks == null || !Number.isInteger(f.weeks) || f.weeks < 1 || f.weeks > MAX_WEEKS)
      throw new FormError("ui.request.err.weeks", { hi: MAX_WEEKS });
    weeks = f.weeks;
  }
  const roles = f.sports.filter((s, i) => sports.includes(s.sport) && f.sports.findIndex((x) => x.sport === s.sport) === i)
    .map((s) => ({ sport: s.sport, role: (FORM_ROLES as readonly string[]).includes(s.role) ? s.role : "support",
      share: Math.max(0, Math.round(s.share || 0)) }));
  // The coach's form: a test for each sport of the plan, and skis on the SkiErg's when the SkiErg is not in it.
  const tests = f.capacity.filter((c) => !ownValues(c) && (sports.includes(c.sport)
    || Object.entries(BORROWS).some(([to, from]) => from === c.sport && sports.includes(to) && !sports.includes(from))));
  // Own values (#777) in place of what a sport had: throws a ValuesError for a field that cannot be read.
  const capacity = withValues(tests, f.values ?? {}, sports);
  const recent = f.recent_weekly_minutes && f.recent_weekly_minutes > 0 ? Math.round(f.recent_weekly_minutes) : null;
  const strength = Math.max(0, Math.min(MAX_STRENGTH, Math.round(f.strength_sessions || 0)));
  return normRequest({
    format: 2, start: f.start, sports, availability: slots, capacity, goals, duration_weeks: weeks, level: f.level,
    recent_weekly_minutes: recent, roles: roles.length > 1 || roles[0].sport !== RUNNING ? roles : [],
    ...(strength ? { strength_sessions: strength } : {}),
  });
}

/** A new plan id for a plan made in the app. */
export function newPlanId(random: () => number = Math.random): string {
  return APP_ID_PREFIX + Array.from({ length: 8 }, () => Math.floor(random() * 36).toString(36)).join("");
}

export interface OwnPlanOptions {
  id: string;
  /** The targets of the plan the form was filled in from (its tests', as Tröskel sent them); none without a test. */
  targets?: Record<Sport, ZoneTargets>;
  /** The day the plan is made: the weeks after four weeks from it are sketches, detailed by `renewOwn` later. */
  today: IsoDate;
}

/** The stored plan of the form, as Tröskel would store it (link.stored_plan); throws FormError or PlanError. */
export function planFromForm(f: RequestForm, o: OwnPlanOptions): StoredPlan {
  const request = requestFromForm(f);
  // A rolling plan, as Tröskel builds one: the app renews it itself (`renewOwn`), as the coach's *Förnya*.
  const plan = buildPlan(request, { planId: o.id, today: o.today });
  return {
    kind: KIND, format: FORMAT, athlete: f.athlete.trim(), request, plan,
    targets: targetsFor(request, o.targets ?? {}), targets_log: [],
  };
}

/** A plan the athlete made in the app (not one from a coach). */
export const ownPlan = (s: StoredPlan | null) => !!s && s.plan.id.startsWith(APP_ID_PREFIX);

/** The athlete's own plan renewed on `today` (`rollForward` when `due`), or null when there is nothing to renew: not
 * an own plan, every week detailed already (a plan made before #789), or not yet time. A coach's plan is renewed by
 * the coach (*Förnya* in Tröskel) and comes with a new link. */
export function renewOwn(s: StoredPlan | null, today: IsoDate): StoredPlan | null {
  if (!s || !ownPlan(s) || s.plan.source || due(s.plan, today) === 0) return null;
  return { ...s, plan: rollForward(s.plan, s.request, today) };
}
