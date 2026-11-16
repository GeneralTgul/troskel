// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from "vitest";
import { cached, localBackend, memoryBackend, openStore, Store, type AsyncBackend, type Backend } from "../src/model/storage";
import { demoPlan } from "./helpers";

/** A stand-in for Capacitor Preferences: a Map behind promises, with the writes it saw. */
function fakePreferences(start: Record<string, string> = {}) {
  const m = new Map(Object.entries(start));
  const writes: string[] = [];
  const prefs: AsyncBackend & { m: Map<string, string>; writes: string[] } = {
    m,
    writes,
    get: async (k) => m.get(k) ?? null,
    set: async (k, v) => {
      await new Promise((r) => setTimeout(r, 1));
      writes.push(`set ${k}`);
      m.set(k, v);
    },
    remove: async (k) => {
      writes.push(`remove ${k}`);
      m.delete(k);
    },
  };
  return prefs;
}

const troskelKeys = (b: Backend) => ["plan", "demo", "times", "settings"].filter((k) => b.get(`troskel.${k}`) != null);

afterEach(() => localStorage.clear());

describe("the browser keeps everything in localStorage", () => {
  it("saves, reads back and deletes", () => {
    const s = new Store(localBackend());
    s.setPlan(demoPlan());
    s.setSettings({ lang: "en" });
    expect(localStorage.getItem("troskel.plan")).not.toBeNull();
    expect(new Store(localBackend()).plan()).toEqual(demoPlan());
    expect(new Store(localBackend()).settings()).toEqual({ lang: "en" });
    s.clear();
    expect(troskelKeys(localBackend())).toEqual([]);
  });

  it("openStore uses localStorage off the native platform and never asks for Preferences", async () => {
    let asked = false;
    const s = await openStore(false, async () => {
      asked = true;
      return fakePreferences();
    });
    s.setSettings({ lang: "sv" });
    expect(asked).toBe(false);
    expect(localStorage.getItem("troskel.settings")).toBe('{"lang":"sv"}');
  });
});

describe("the app store builds keep everything in Preferences", () => {
  it("reads everything at start and writes through, in order", async () => {
    const prefs = fakePreferences({ "troskel.settings": '{"lang":"en"}' });
    const b = await cached(prefs, memoryBackend());
    const s = new Store(b);
    expect(s.settings()).toEqual({ lang: "en" });
    s.setPlan(demoPlan(), true);
    s.setTimes({ saved: "2026-11-16", slots: [] } as never);
    // The views read from memory at once, before Preferences has the writes.
    expect(s.plan()).toEqual(demoPlan());
    expect(s.isDemo()).toBe(true);
    await b.flush();
    expect(prefs.writes).toEqual(["set troskel.plan", "set troskel.demo", "remove troskel.times", "set troskel.times"]);
    const again = new Store(await cached(prefs));
    expect(again.plan()).toEqual(demoPlan());
    expect(again.times()).toEqual({ saved: "2026-11-16", slots: [] });
    expect(localStorage.length).toBe(0);
  });

  it("openStore on the native platform keeps nothing in localStorage", async () => {
    const prefs = fakePreferences();
    const s = await openStore(true, async () => prefs);
    s.setSettings({ lang: "en" });
    await new Promise((r) => setTimeout(r, 20));
    expect(prefs.m.get("troskel.settings")).toBe('{"lang":"en"}');
    expect(localStorage.getItem("troskel.settings")).toBeNull();
  });

  it("falls back to localStorage when Preferences cannot be read", async () => {
    const s = await openStore(true, async () => {
      throw new Error("no plugin");
    });
    s.setSettings({ lang: "en" });
    expect(localStorage.getItem("troskel.settings")).toBe('{"lang":"en"}');
  });

  it("keeps the value for this visit when a write fails", async () => {
    const prefs = fakePreferences();
    prefs.set = async () => {
      throw new Error("full");
    };
    const b = await cached(prefs);
    const s = new Store(b);
    s.setSettings({ lang: "en" });
    await b.flush();
    expect(s.settings()).toEqual({ lang: "en" });
  });
});

describe("an installed app moves its old localStorage data to Preferences once", () => {
  it("moves every key and empties localStorage", async () => {
    new Store(localBackend()).setPlan(demoPlan());
    new Store(localBackend()).setSettings({ lang: "en" });
    const prefs = fakePreferences();
    const s = new Store(await cached(prefs, localBackend()));
    expect(s.plan()).toEqual(demoPlan());
    expect(s.settings()).toEqual({ lang: "en" });
    expect([...prefs.m.keys()].sort()).toEqual(["troskel.demo", "troskel.plan", "troskel.settings"]);
    expect(troskelKeys(localBackend())).toEqual([]);
  });

  it("does not move anything when Preferences already has data", async () => {
    localStorage.setItem("troskel.settings", '{"lang":"en"}');
    const prefs = fakePreferences({ "troskel.settings": '{"lang":"sv"}' });
    const s = new Store(await cached(prefs, localBackend()));
    expect(s.settings()).toEqual({ lang: "sv" });
    expect(prefs.writes).toEqual([]);
  });

  it("keeps localStorage when the move fails, so nothing is lost", async () => {
    localStorage.setItem("troskel.settings", '{"lang":"en"}');
    const prefs = fakePreferences();
    prefs.set = async () => {
      throw new Error("full");
    };
    const s = await openStore(true, async () => prefs);
    expect(s.settings()).toEqual({ lang: "en" });
    expect(localStorage.getItem("troskel.settings")).toBe('{"lang":"en"}');
  });
});

describe("Ta bort all data på enheten", () => {
  it("empties both Preferences and localStorage", async () => {
    const prefs = fakePreferences({ "troskel.plan": JSON.stringify(demoPlan()), "troskel.demo": "false" });
    localStorage.setItem("troskel.settings", '{"lang":"en"}'); // left over, for example from a failed move
    const b = await cached(prefs, localBackend());
    const s = new Store(b);
    s.setSettings({ lang: "sv" });
    s.clear();
    expect(s.plan()).toBeNull();
    expect(s.settings()).toEqual({});
    await b.flush();
    expect([...prefs.m.keys()]).toEqual([]);
    expect(troskelKeys(localBackend())).toEqual([]);
    expect(new Store(await cached(prefs, localBackend())).plan()).toBeNull();
  });
});
