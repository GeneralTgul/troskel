/**
 * Everything the app keeps, on the device only: the plan, the athlete's new times and the settings.
 * The browser (the PWA) keeps it in localStorage. The app store builds keep it in Capacitor Preferences
 * (UserDefaults on iOS, SharedPreferences on Android), because iOS may empty a web view's localStorage when
 * the phone runs short of space. Preferences is asynchronous: `openStore` reads everything once at start,
 * and the store then reads from memory and writes through, so the views stay synchronous.
 */
import { Capacitor } from "@capacitor/core";
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
const KEYS = ["plan", "demo", "times", "settings"] as const;

/** An asynchronous key-value store, such as Capacitor Preferences. */
export interface AsyncBackend {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
}

/** A Backend over an AsyncBackend, with `flush` to wait for the writes that are still on their way. */
export interface CachedBackend extends Backend {
  flush(): Promise<void>;
}

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

/**
 * Reads the app's keys from `remote` once and returns a synchronous Backend that keeps them in memory and
 * writes every change through to `remote`, in order. When `remote` has none of the keys and `legacy` has
 * some (an installed app that kept them in localStorage before), they are moved over first.
 * `remove` empties `legacy` as well, so deleting everything leaves nothing in either store.
 */
export async function cached(remote: AsyncBackend, legacy?: Backend): Promise<CachedBackend> {
  const m = new Map<string, string>();
  for (const k of KEYS) {
    const v = await remote.get(PREFIX + k);
    if (v != null) m.set(PREFIX + k, v);
  }
  if (m.size === 0 && legacy) {
    const old = KEYS.map((k) => [PREFIX + k, legacy.get(PREFIX + k)] as const).filter(([, v]) => v != null);
    for (const [k, v] of old) await remote.set(k, v!);
    for (const [k, v] of old) {
      m.set(k, v!);
      legacy.remove(k);
    }
  }
  let queue: Promise<void> = Promise.resolve();
  const then = (f: () => Promise<void>) => void (queue = queue.then(f).catch(() => {
    /* the write failed: the value stays in memory for this visit */
  }));
  return {
    get: (k) => m.get(k) ?? null,
    set: (k, v) => {
      m.set(k, v);
      then(() => remote.set(k, v));
    },
    remove: (k) => {
      m.delete(k);
      try {
        legacy?.remove(k);
      } catch {
        /* blocked: nothing to remove */
      }
      then(() => remote.remove(k));
    },
    flush: () => queue,
  };
}

/** Capacitor Preferences as an AsyncBackend. */
export async function preferences(): Promise<AsyncBackend> {
  const { Preferences } = await import("@capacitor/preferences");
  return {
    get: async (key) => (await Preferences.get({ key })).value,
    set: (key, value) => Preferences.set({ key, value }),
    remove: (key) => Preferences.remove({ key }),
  };
}

/**
 * The store the app starts with: localStorage in the browser, Capacitor Preferences in the app store builds.
 * If Preferences cannot be read, the app falls back to localStorage rather than not starting.
 */
export async function openStore(
  native = Capacitor.isNativePlatform(),
  remote: () => Promise<AsyncBackend> = preferences,
  legacy: () => Backend = local,
): Promise<Store> {
  if (!native) return new Store(legacy());
  try {
    return new Store(await cached(await remote(), legacy()));
  } catch {
    return new Store(legacy());
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
    for (const k of KEYS) this.backend.remove(PREFIX + k);
  }
}

export const memoryStore = () => new Store(memory());
export { memory as memoryBackend, local as localBackend };
