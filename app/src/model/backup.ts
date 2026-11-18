/**
 * The athlete's own copy (#790): the app has no cloud, so a copy is what moves the plan to a new phone or brings it
 * back after the browser emptied its storage. The copy is the payload the coach gets (#781, share.ts): the stored
 * plan with its edits, own values and times, what is ticked off, the sessions outside the plan and the times still
 * waiting under "Mina tider". The same file or link reads back into the app with *Öppna en fil* or *Klistra in en
 * länk*, and Tröskel reads it as before. The health data (the nights, the watch's workouts) is not in it: the watch
 * gives it again.
 *
 * `storageState` says whether the browser may empty the storage (WebKit's storage policy): a home screen web app or a
 * page the browser made persistent (`navigator.storage.persist`) keeps it, a Safari tab loses it after seven days of
 * Safari use without a visit. The app store builds keep it in Capacitor Preferences.
 */
import { Capacitor } from "@capacitor/core";
import { strFromU8, unzlibSync } from "fflate";
import { addDays } from "./dates";
import { checkPlan, fromBase64Url, PlanError } from "./link";
import { checkLog, type TrainingLog } from "./log";
import { ATHLETE_KIND, ATHLETE_FORMAT, type AthletePayload } from "./share";
import type { PendingTimes } from "./storage";
import type { IsoDate, StoredPlan } from "./types";

/** A copy read back: the plan, the log (the watch's marks become the athlete's) and the waiting times. */
export interface Restored {
  plan: StoredPlan;
  log: TrainingLog;
  times: PendingTimes | null;
  exported: IsoDate | null;
}

/** The `a=` part of a link, or null. */
export function copyData(link: string): string | null {
  const hash = link.includes("#") ? link.slice(link.indexOf("#") + 1) : link;
  for (const part of hash.split("&")) if (part.startsWith("a=")) return part.slice(2).trim();
  return null;
}

function restored(v: unknown): Restored {
  const p = v as Partial<AthletePayload>;
  if (p.format !== ATHLETE_FORMAT) throw new PlanError(typeof p.format === "number" && p.format > ATHLETE_FORMAT ? "too_new" : "broken");
  const plan = checkPlan(p.plan);
  const log = checkLog({ marks: p.marks, extras: p.extras });
  const times = p.times && typeof p.times === "object" && typeof p.times.saved === "string" ? p.times : null;
  return { plan, log, times, exported: typeof p.exported === "string" ? p.exported : null };
}

/** A copy in a text (the `.troskel` file of share.athleteFile, or a text holding share.athleteLink); null when the
 * text is something else (a plan from the coach), so that the plan's own reader takes it. */
export function readCopyText(text: string): Restored | null {
  const s = text.trim().replace(/^﻿/, "");
  if (s.startsWith("{")) {
    let json: unknown;
    try {
      json = JSON.parse(s);
    } catch {
      return null;
    }
    return (json as { kind?: unknown })?.kind === ATHLETE_KIND ? restored(json) : null;
  }
  const data = copyData(s);
  if (!data) return null;
  let json: unknown;
  try {
    json = JSON.parse(strFromU8(unzlibSync(fromBase64Url(data))));
  } catch {
    throw new PlanError("broken");
  }
  if ((json as { kind?: unknown })?.kind !== ATHLETE_KIND) throw new PlanError("not_a_plan");
  return restored(json);
}

/** The log after a copy is read in: the copy's marks over the ones the phone kept for the same plan, and the sessions
 * outside the plan from both, once each. */
export function mergeLog(kept: TrainingLog, copy: TrainingLog): TrainingLog {
  const ids = new Set(copy.extras.map((x) => x.id));
  return { marks: { ...kept.marks, ...copy.marks }, extras: [...copy.extras, ...kept.extras.filter((x) => !ids.has(x.id))] };
}

/** The copy's file name: troskel-kopia-<day>.troskel. */
export const copyFileName = (today: IsoDate) => `troskel-kopia-${today}.troskel`;

/** How long a copy counts as fresh before *I dag* reminds of a new one, when the storage may be emptied. */
export const COPY_DAYS = 14;

/** "native": the app store build (Preferences). "kept": the browser keeps the storage (persistent, or a home screen
 * web app that is). "home": a home screen web app the browser has not made persistent; it may still be emptied when the
 * phone runs out of space. "tab": a tab in the browser; Safari empties it after seven days of use without a visit. */
export type StorageState = "native" | "kept" | "home" | "tab";

/** Whether the copy reminder shows on *I dag*: a plan of the athlete's, storage that may go, no fresh copy. */
export function remindCopy(state: StorageState | null, copied: IsoDate | undefined, today: IsoDate): boolean {
  if (!state || state === "native" || state === "kept") return false;
  return !copied || addDays(copied, COPY_DAYS) <= today;
}

interface Env {
  native: boolean;
  standalone: boolean;
  storage?: { persisted?: () => Promise<boolean>; persist?: () => Promise<boolean> };
}

/** The browser's side of `StorageState`: asks for persistent storage in a home screen web app (Safari grants it to
 * one, Chrome to an installed app, and neither asks the user); a tab is not asked, since Firefox would show a prompt. */
export async function storageState(env: Env = browserEnv()): Promise<StorageState> {
  if (env.native) return "native";
  try {
    if (await env.storage?.persisted?.()) return "kept";
    if (env.standalone && (await env.storage?.persist?.())) return "kept";
  } catch {
    /* no Storage API: as if not persistent */
  }
  return env.standalone ? "home" : "tab";
}

function browserEnv(): Env {
  const nav = globalThis.navigator as (Navigator & { standalone?: boolean }) | undefined;
  const standalone = nav?.standalone === true || !!globalThis.matchMedia?.("(display-mode: standalone)").matches;
  return { native: Capacitor.isNativePlatform(), standalone, storage: nav?.storage };
}
