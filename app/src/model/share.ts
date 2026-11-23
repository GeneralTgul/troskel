/**
 * The athlete's plan from the phone to the coach (#781): what the phone has, so that Tröskel can read it in on the
 * athlete before a new test and lay the test on the athlete's own plan rather than on the one it sent (#776).
 *
 * The payload is `troskel.athlete`, format 1: the whole stored plan as the phone has it (format 1, with the athlete's
 * times and what the app's engine rebuilt), what is ticked off (the athlete's own marks and the ones the health data
 * gave, marked `auto`), the sessions outside the plan, the times still waiting under "Mina tider", the dates that do not
 * work that the plan was last rebuilt with (`off`, store.unavailable, #846), and the day of the export. The nights (sleep, resting heart rate, HRV) and the watch's workouts stay on the phone.
 *
 * It goes as a `.troskel` file (the JSON as text) or as a link `<app>#a=<data>`, coded as the plan's link
 * (deflate + base64url). Nothing is sent by the app: the athlete chooses whom to give the file or the link to.
 * Tröskel reads it with `lopanalys.plan.phone`.
 */
import { strFromU8, strToU8, unzlibSync, zlibSync } from "fflate";
import type { AutoResult } from "./health";
import { fromBase64Url, toBase64Url } from "./link";
import type { Extra, Mark, TrainingLog } from "./log";
import type { PendingTimes } from "./storage";
import type { IsoDate, StoredPlan } from "./types";

export const ATHLETE_KIND = "troskel.athlete";
export const ATHLETE_FORMAT = 1;
export const APP_URL = "https://generaltgul.github.io/troskel/app/";

export interface SharedMark extends Mark {
  /** Ticked off by the health data (the watch), not by the athlete. */
  auto?: true;
}

export interface AthletePayload {
  kind: typeof ATHLETE_KIND;
  format: number;
  exported: IsoDate;
  plan: StoredPlan;
  marks: Record<string, SharedMark>;
  extras: Extra[];
  /** The times the athlete saved under "Mina tider" that the plan is not built on yet (PendingTimes). */
  times?: PendingTimes;
  /** The dates that do not work that the stored plan was last rebuilt with under "Mina tider" (store.unavailable): their
   * workouts are dropped from `plan` already, and the next rebuild (on the phone or in Tröskel) must drop them again
   * (#846). Missing when there are none; an older app or Tröskel reads the payload as before without them. */
  off?: IsoDate[];
}

/** What the phone gives the coach. `log` is the athlete's own log (store.log()), `auto` what the health data ticked
 * off (health.autoMarks); a mark the athlete set wins, as in the views. `off`: the dates the plan was rebuilt without
 * (store.unavailable); the ones that have passed are left out. */
export function athletePayload(stored: StoredPlan, log: TrainingLog, auto: AutoResult | null, pending: PendingTimes | null,
  today: IsoDate, off: IsoDate[] = []): AthletePayload {
  const marks: Record<string, SharedMark> = {};
  const plain = (m: Mark): Mark => (m.minutes != null ? { status: m.status, minutes: m.minutes } : { status: m.status });
  for (const [k, m] of Object.entries(auto?.marks ?? {})) marks[k] = { ...plain(m), auto: true };
  for (const [k, m] of Object.entries(log.marks)) marks[k] = plain(m);
  const out: AthletePayload = {
    kind: ATHLETE_KIND, format: ATHLETE_FORMAT, exported: today, plan: stored, marks, extras: [...log.extras],
  };
  if (pending) out.times = pending;
  const later = off.filter((d) => d > today).sort();
  if (later.length) out.off = later;
  return out;
}

/** The payload as a `.troskel` file: readable JSON. */
export const athleteFile = (p: AthletePayload) => JSON.stringify(p, null, 1) + "\n";

/** The payload as a link, `<app>#a=<data>`: the app does not open it, Tröskel reads it when it is pasted. */
export const athleteLink = (p: AthletePayload, base = APP_URL) =>
  `${base}#a=${toBase64Url(zlibSync(strToU8(JSON.stringify(p)), { level: 9 }))}`;

/** `athleteFile`'s name: troskel-<name>-<day>.troskel, with the name as letters and digits only. */
export function athleteFileName(p: AthletePayload): string {
  const name = (p.plan.athlete || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase();
  return `troskel-${name ? name + "-" : ""}${p.exported}.troskel`;
}

/** The payload of a link from `athleteLink` (for the tests: Tröskel reads it in Python). */
export function readAthleteLink(link: string): AthletePayload {
  const hash = link.slice(link.indexOf("#") + 1);
  const part = hash.split("&").find((x) => x.startsWith("a="));
  if (!part) throw new Error("no athlete payload");
  return JSON.parse(strFromU8(unzlibSync(fromBase64Url(part.slice(2).trim()))));
}
