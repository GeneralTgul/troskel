/**
 * Health Connect in the Android app: the app reads the workouts and the night's values itself, with
 * `@capgo/capacitor-health` (read only, nothing is written), and makes of them the same `HealthData` as the
 * `#h=` link (health.ts). The data stays on the device.
 *
 * - Workouts: Health Connect's exercise sessions, the sport by `sportOf`, the mean and highest heart rate from the
 *   heart rate samples of the session's time when there are any.
 * - Sleep: a session belongs to the day it ended (the day the athlete woke up); the asleep stages count, or the
 *   whole session when the writer gave no stages.
 * - Resting heart rate and HRV: one entry per sample on its local day; `checkHealth` takes the mean of a day.
 *   A Garmin watch writes no HRV to Health Connect, so with Garmin the day form counts without it.
 *
 * `fromHealthConnect` is pure and tested (app/test/healthconnect.test.ts); `readHealthConnect` calls the plugin,
 * which only exists in the Android build.
 */
import { checkHealth, HEALTH_FORMAT, HEALTH_KIND, type HealthData } from "./health";

/** The data types the app asks to read, and nothing else (AndroidManifest.xml removes the plugin's other ones). */
export const READ_TYPES = ["workouts", "heartRate", "sleep", "restingHeartRate", "heartRateVariability"] as const;
/** 28 days for the means and the week; Health Connect gives at most 30 days before the first grant. */
export const READ_DAYS = 35;

export interface HcWorkout {
  workoutType: string;
  /** Seconds. */
  duration: number;
  startDate: string;
  endDate: string;
}

export interface HcStage {
  stage: string;
  durationMinutes: number;
}

export interface HcSample {
  value: number;
  startDate: string;
  endDate: string;
  stages?: HcStage[];
  hasStageData?: boolean;
}

export interface HcRead {
  workouts: HcWorkout[];
  heartRate: HcSample[];
  sleep: HcSample[];
  restingHeartRate: HcSample[];
  heartRateVariability: HcSample[];
}

/** The part of the plugin the app uses (HealthPlugin in @capgo/capacitor-health). */
export interface HealthPluginLike {
  isAvailable(): Promise<{ available: boolean; reason?: string }>;
  requestAuthorization(o: { read: string[] }): Promise<{ readAuthorized: string[] }>;
  checkAuthorization(o: { read: string[] }): Promise<{ readAuthorized: string[] }>;
  readSamples(o: { dataType: string; startDate: string; endDate: string; limit: number; ascending: boolean }):
    Promise<{ samples: HcSample[] }>;
  queryWorkouts(o: { startDate: string; endDate: string; limit: number; ascending: boolean; anchor?: string }):
    Promise<{ workouts: HcWorkout[]; anchor?: string }>;
  openHealthConnectSettings(): Promise<void>;
}

const ASLEEP = new Set(["asleep", "light", "deep", "rem"]);

/** An instant ("2026-11-16T17:02:00Z") as local time with the offset ("2026-11-16T18:02:00+01:00"). */
export function localIso(instant: string, timeZone?: string): string {
  const ms = Date.parse(instant);
  if (!Number.isFinite(ms)) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(ms));
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  const wall = `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}:${get("second")}`;
  const offset = Math.round((Date.parse(`${wall}Z`) - Math.floor(ms / 1000) * 1000) / 60_000);
  const abs = Math.abs(offset);
  const sign = offset < 0 ? "-" : "+";
  return `${wall}${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

/** What Health Connect gave, as the health data of the `#h=` link (checked by `checkHealth`). */
export function fromHealthConnect(r: HcRead, read: string, timeZone?: string): HealthData {
  const workouts = r.workouts.map((w) => {
    const s = Date.parse(w.startDate), e = Date.parse(w.endDate);
    const hr = r.heartRate.filter((x) => {
      const t = Date.parse(x.startDate);
      return t >= s && t <= e && x.value > 0;
    }).map((x) => x.value);
    return {
      sport: w.workoutType,
      start: localIso(w.startDate, timeZone),
      minutes: (w.duration > 0 ? w.duration : (e - s) / 1000) / 60,
      ...(hr.length ? { hr: hr.reduce((a, b) => a + b, 0) / hr.length, hr_max: Math.max(...hr) } : {}),
    };
  });
  const day = (instant: string) => localIso(instant, timeZone).slice(0, 10);
  const days = [
    ...r.sleep.map((x) => {
      const minutes = x.hasStageData && x.stages?.length
        ? x.stages.filter((st) => ASLEEP.has(st.stage)).reduce((a, st) => a + st.durationMinutes, 0)
        : x.value;
      return { date: day(x.endDate), sleep_h: minutes / 60 };
    }),
    ...r.restingHeartRate.map((x) => ({ date: day(x.startDate), rhr: x.value })),
    ...r.heartRateVariability.map((x) => ({ date: day(x.startDate), hrv: x.value })),
  ];
  return checkHealth({ kind: HEALTH_KIND, format: HEALTH_FORMAT, read, workouts, days });
}

export type HcResult =
  | { ok: true; data: HealthData }
  | { ok: false; why: "unavailable" | "denied" | "failed" };

/**
 * Reads the last READ_DAYS days from Health Connect. `ask`: show Health Connect's permission sheet (the button
 * in "About"); without it the app only reads when the athlete has already allowed it (at the start).
 */
export async function readHealthConnect(plugin: HealthPluginLike, ask: boolean, now = new Date()): Promise<HcResult> {
  try {
    if (!(await plugin.isAvailable()).available) return { ok: false, why: "unavailable" };
    const read = [...READ_TYPES];
    const auth = ask ? await plugin.requestAuthorization({ read }) : await plugin.checkAuthorization({ read });
    const allowed = new Set(auth.readAuthorized);
    if (!allowed.has("workouts") && !allowed.has("sleep") && !allowed.has("restingHeartRate")) {
      return { ok: false, why: "denied" };
    }
    const endDate = now.toISOString();
    const startDate = new Date(now.getTime() - READ_DAYS * 86_400_000).toISOString();
    const samples = async (dataType: string) =>
      allowed.has(dataType)
        ? (await plugin.readSamples({ dataType, startDate, endDate, limit: 20_000, ascending: true })).samples
        : [];
    const workouts: HcWorkout[] = [];
    if (allowed.has("workouts")) {
      let anchor: string | undefined;
      for (let page = 0; page < 10; page++) {
        const res = await plugin.queryWorkouts({ startDate, endDate, limit: 200, ascending: true, anchor });
        workouts.push(...res.workouts);
        anchor = res.anchor ?? undefined;
        if (!anchor) break;
      }
    }
    // The heart rate only for the sessions' time: a watch writes it all day, tens of thousands of samples a month.
    const heartRate: HcSample[] = [];
    if (allowed.has("heartRate")) {
      for (const w of workouts) {
        const res = await plugin.readSamples({
          dataType: "heartRate", startDate: w.startDate, endDate: w.endDate, limit: 10_000, ascending: true,
        });
        heartRate.push(...res.samples);
      }
    }
    const data = fromHealthConnect({
      workouts,
      heartRate,
      sleep: await samples("sleep"),
      restingHeartRate: await samples("restingHeartRate"),
      heartRateVariability: await samples("heartRateVariability"),
    }, localIso(endDate));
    return { ok: true, data };
  } catch {
    return { ok: false, why: "failed" };
  }
}

/**
 * A plain object around a Capacitor plugin. The plugin itself is a Proxy that answers every property, `then` too
 * ("Health.then() is not implemented"), so a promise resolved with it never settles and the app never starts (#757).
 */
export function wrapPlugin(p: HealthPluginLike): HealthPluginLike {
  return {
    isAvailable: () => p.isAvailable(),
    requestAuthorization: (o) => p.requestAuthorization(o),
    checkAuthorization: (o) => p.checkAuthorization(o),
    readSamples: (o) => p.readSamples(o),
    queryWorkouts: (o) => p.queryWorkouts(o),
    openHealthConnectSettings: () => p.openHealthConnectSettings(),
  };
}

/** The plugin in the Android build; null in the browser and on iOS (HealthKit waits for the developer account). */
export async function healthConnectPlugin(
  platform?: string,
  // The module, never the plugin: an async function or an await must not hand the plugin itself on.
  load: () => Promise<{ Health: unknown }> = () => import("@capgo/capacitor-health"),
): Promise<HealthPluginLike | null> {
  const p = platform ?? (await import("@capacitor/core")).Capacitor.getPlatform();
  if (p !== "android") return null;
  const mod = await load();
  return wrapPlugin(mod.Health as HealthPluginLike);
}
