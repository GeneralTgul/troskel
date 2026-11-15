/**
 * Everything the app keeps, on the device only: the plan, the athlete's new times and the settings.
 * The browser keeps it in localStorage; the app store builds can swap `backend` for Capacitor Preferences.
 */
import type { Lang } from "../i18n";
import type { NewAvailability } from "../engine";
import { checkPlan } from "./link";
import type { StoredPlan } from "./types";

export interface Backend {
  get(key: string): string | null;
  set(key: string, value: string): void;
  remove(key: string): void;
}

const PREFIX = "troskel.";

function memory(): Backend {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v), remove: (k) => void m.delete(k) };
}

function local(): Backend {
  try {
    const ls = globalThis.localStorage;
    const probe = PREFIX + "probe";
    ls.setItem(probe, "1");
    ls.removeItem(probe);
    return { get: (k) => ls.getItem(k), set: (k, v) => ls.setItem(k, v), remove: (k) => ls.removeItem(k) };
  } catch {
    return memory();
  }
}

export interface Settings {
  lang?: Lang;
}

export interface PendingTimes extends Omit<NewAvailability, "from"> {
  saved: string; // ISO date the athlete saved them
}

export class Store {
  constructor(private readonly backend: Backend = local()) {}

  private read<T>(key: string): T | null {
    try {
      const v = this.backend.get(PREFIX + key);
      return v == null ? null : (JSON.parse(v) as T);
    } catch {
      return null;
    }
  }

  private write(key: string, value: unknown): void {
    try {
      this.backend.set(PREFIX + key, JSON.stringify(value));
    } catch {
      /* full or blocked: the app still works for this visit */
    }
  }

  plan(): StoredPlan | null {
    const p = this.read<unknown>("plan");
    try {
      return p ? checkPlan(p) : null;
    } catch {
      return null;
    }
  }

  setPlan(p: StoredPlan, demo = false): void {
    this.write("plan", p);
    this.write("demo", demo);
    this.backend.remove(PREFIX + "times");
  }

  isDemo(): boolean {
    return this.read<boolean>("demo") === true;
  }

  times(): PendingTimes | null {
    return this.read<PendingTimes>("times");
  }

  setTimes(t: PendingTimes): void {
    this.write("times", t);
  }

  settings(): Settings {
    return this.read<Settings>("settings") ?? {};
  }

  setSettings(s: Settings): void {
    this.write("settings", s);
  }

  clear(): void {
    for (const k of ["plan", "demo", "times", "settings"]) this.backend.remove(PREFIX + k);
  }
}

export const memoryStore = () => new Store(memory());
