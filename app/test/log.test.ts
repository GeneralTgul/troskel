import { describe, expect, it, vi } from "vitest";
import { engine } from "../src/engine";
import { setLang } from "../src/i18n";
import {
  addExtra, checkLog, doneMinutes, emptyLog, halfOf, keepFor, markOf, removeExtra, setMark, weekTotals,
} from "../src/model/log";
import { workoutMinutes } from "../src/model/plan";
import { season, seasonGoal } from "../src/model/season";
import { memoryStore } from "../src/model/storage";
import type { StoredPlan } from "../src/model/types";
import { idleTimer, keepAwake, WALL_IDLE_MS } from "../src/model/wall";
import { demoPlan } from "./helpers";

const MONDAY = "2026-11-23"; // the demo's second week: recovery, easy, hills, easy and the long run
const week2 = (p: StoredPlan) => p.plan.weeks[1].workouts;

describe("ticking off a workout", () => {
  it("marks done, partly and missed, and a second mark replaces the first", () => {
    const [mon, tue] = week2(demoPlan());
    let log = setMark(emptyLog(), mon.key, { status: "done" });
    log = setMark(log, tue.key, { status: "partial", minutes: 20 });
    expect(markOf(log, mon)).toEqual({ status: "done" });
    expect(markOf(log, tue)).toEqual({ status: "partial", minutes: 20 });
    log = setMark(log, tue.key, { status: "missed" });
    expect(markOf(log, tue)!.status).toBe("missed");
    expect(markOf(setMark(log, tue.key, null), tue)).toBeNull();
  });

  it("reads a status that came with the plan from Tröskel, until the athlete marks it", () => {
    const w = { ...week2(demoPlan())[0], status: "done" };
    expect(markOf(emptyLog(), w)).toEqual({ status: "done" });
    expect(markOf(emptyLog(), { ...w, status: "skipped" })).toEqual({ status: "missed" });
    expect(markOf(emptyLog(), { ...w, status: "moved" })).toBeNull();
    expect(markOf(setMark(emptyLog(), w.key, { status: "missed" }), w)!.status).toBe("missed");
  });

  it("counts a partly done workout as half of it until the minutes are given, never more than the whole", () => {
    const long = week2(demoPlan()).find((w) => w.template_id === "long")!;
    const all = workoutMinutes(long);
    expect(halfOf(long) % 5).toBe(0);
    expect(doneMinutes(long, { status: "partial" })).toBe(halfOf(long));
    expect(doneMinutes(long, { status: "partial", minutes: 999 })).toBe(all);
    expect(doneMinutes(long, { status: "done" })).toBe(all);
    expect(doneMinutes(long, { status: "missed" })).toBe(0);
    expect(doneMinutes(long, null)).toBe(0);
  });

  it("keeps the marks when the engine rebuilds the plan with new times from tomorrow", () => {
    const p = demoPlan();
    const [mon, tue] = week2(p);
    let log = setMark(emptyLog(), mon.key, { status: "done" });
    log = setMark(log, tue.key, { status: "partial", minutes: 25 });
    const slots = p.request.availability.map((s) => ({ ...s, max_minutes: s.max_minutes + 15 }));
    const rebuilt = engine.rebuild(p, { slots, unavailable: [], from: "2026-11-26" });
    const kept = keepFor(log, rebuilt);
    const again = week2(rebuilt);
    expect(markOf(kept, again.find((w) => w.key === mon.key)!)).toEqual({ status: "done" });
    expect(markOf(kept, again.find((w) => w.key === tue.key)!)).toEqual({ status: "partial", minutes: 25 });
    expect(weekTotals(rebuilt, kept, MONDAY).rows[0].done).toBe(workoutMinutes(mon) + 25);
  });

  it("drops the marks of another plan and keeps the sessions outside the plan", () => {
    const p = demoPlan();
    let log = setMark(emptyLog(), "other-2648-mon", { status: "done" });
    log = setMark(log, week2(p)[0].key, { status: "done" });
    log = addExtra(log, { date: MONDAY, sport: "other", minutes: 30, name: "Yoga" });
    const kept = keepFor(log, p);
    expect(Object.keys(kept.marks)).toEqual([week2(p)[0].key]);
    expect(kept.extras).toHaveLength(1);
  });
});

describe("sessions outside the plan", () => {
  it("are added with an id of their own and taken away again", () => {
    let log = addExtra(emptyLog(), { date: MONDAY, sport: "other", minutes: 30, name: " Yoga " });
    log = addExtra(log, { date: MONDAY, sport: "bike", minutes: 45.4, name: "Pendling" });
    expect(log.extras.map((x) => [x.id, x.name, x.minutes])).toEqual([[`${MONDAY}-1`, "Yoga", 30], [`${MONDAY}-2`, "Pendling", 45]]);
    log = removeExtra(log, `${MONDAY}-1`);
    log = addExtra(log, { date: MONDAY, sport: "run", minutes: 20, name: "Jogg" });
    expect(log.extras.map((x) => x.id)).toEqual([`${MONDAY}-2`, `${MONDAY}-1`]);
  });
});

describe("the week in numbers", () => {
  it("sums done against planned per sport, and the bar is reached when the plan is", () => {
    const p = demoPlan();
    const ws = week2(p);
    const planned = ws.reduce((s, w) => s + workoutMinutes(w), 0);
    let log = emptyLog();
    let sum = weekTotals(p, log, MONDAY);
    expect(sum.rows).toEqual([{ sport: "run", planned, done: 0, reached: false }]);
    for (const w of ws.slice(0, -1)) log = setMark(log, w.key, { status: "done" });
    sum = weekTotals(p, log, MONDAY);
    expect(sum.rows[0].done).toBe(planned - workoutMinutes(ws[ws.length - 1]));
    expect(sum.rows[0].reached).toBe(false);
    log = setMark(log, ws[ws.length - 1].key, { status: "done" });
    expect(weekTotals(p, log, MONDAY).rows[0].reached).toBe(true);
  });

  it("lists the sessions outside the plan as extra, apart from the plan's minutes", () => {
    const p = demoPlan();
    let log = addExtra(emptyLog(), { date: "2026-11-25", sport: "other", minutes: 40, name: "Yoga" });
    log = addExtra(log, { date: "2026-11-30", sport: "other", minutes: 40, name: "Next week" });
    const sum = weekTotals(p, log, MONDAY);
    expect(sum.extras.map((x) => x.name)).toEqual(["Yoga"]);
    expect(sum.done).toBe(0);
  });

  it("puts the main sport first and strength last, and leaves a race out", async () => {
    const { readFileSync } = await import("node:fs");
    const { gunzipSync } = await import("node:zlib");
    const fx = JSON.parse(gunzipSync(readFileSync("test/fixtures/engine.json.gz")).toString("utf-8"));
    const p: StoredPlan = fx.plans["run-strength"];
    const wk = p.plan.weeks.find((w) => w.workouts.some((x) => x.sport === "strength"))!;
    expect(weekTotals(p, emptyLog(), wk.monday).rows.map((r) => r.sport)).toEqual(["run", "strength"]);
    const demo = demoPlan();
    const race = demo.plan.weeks[11];
    expect(weekTotals(demo, emptyLog(), race.monday).rows.every((r) => r.planned > 0)).toBe(true);
  });
});

describe("the log on the device", () => {
  it("is kept beside the plan, outlives a new plan and goes with *Delete all*", () => {
    const store = memoryStore();
    store.setPlan(demoPlan());
    const log = setMark(emptyLog(), "demo-2648-mon", { status: "done" });
    store.setLog(log);
    store.setPlan(demoPlan());
    expect(store.log()).toEqual(log);
    store.clear();
    expect(store.log()).toEqual(emptyLog());
  });
  it("leaves broken entries out", () => {
    expect(checkLog({ marks: { a: { status: "done" }, b: { status: "nope" }, c: null }, extras: [{ id: 1 }] }))
      .toEqual({ marks: { a: { status: "done" } }, extras: [] });
    expect(checkLog("garbage")).toEqual(emptyLog());
  });
});

describe("the season strip", () => {
  it("has the blocks up to the A race, with the easy and test weeks green and today marked", () => {
    const p = demoPlan();
    const s = season(p, "2026-11-25");
    expect(s.goal!.name).toBe("Stadsvarvet");
    expect(s.blocks.map((b) => [b.kind, b.weeks.length])).toEqual([["base", 5], ["build", 4], ["peak", 2], ["taper", 1]]);
    expect(s.blocks.flatMap((b) => b.weeks).filter((w) => w.easy).map((w) => w.index)).toEqual([4, 8]);
    expect(s.marker).toBeCloseTo((9 + 0.5) / 84);
  });
  it("has no marker before the start or after the goal week", () => {
    const p = demoPlan();
    expect(season(p, "2026-11-15").marker).toBeNull();
    expect(season(p, "2027-02-08").marker).toBeNull();
    expect(season(p, "2026-11-16").marker).toBeCloseTo(0.5 / 84);
  });
  it("leads up to the next A race, or the last race when all are past, or the whole plan without one", () => {
    const p = demoPlan();
    expect(seasonGoal(p, "2026-11-25")!.name).toBe("Stadsvarvet");
    expect(seasonGoal(p, "2027-03-01")!.name).toBe("Stadsvarvet");
    const none: StoredPlan = { ...p, request: { ...p.request, goals: [] } };
    const s = season(none, "2026-11-25");
    expect([s.goal, s.blocks.reduce((n, b) => n + b.weeks.length, 0)]).toEqual([null, 12]);
  });
});

describe("the wall mode", () => {
  it("goes home three minutes after the last touch, and every touch starts the time again", () => {
    vi.useFakeTimers();
    try {
      const onIdle = vi.fn();
      const timer = idleTimer(WALL_IDLE_MS, onIdle);
      timer.poke();
      vi.advanceTimersByTime(WALL_IDLE_MS - 1000);
      timer.poke();
      vi.advanceTimersByTime(WALL_IDLE_MS - 1000);
      expect(onIdle).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1000);
      expect(onIdle).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(WALL_IDLE_MS * 3);
      expect(onIdle).toHaveBeenCalledTimes(1);
      timer.poke();
      timer.stop();
      vi.advanceTimersByTime(WALL_IDLE_MS);
      expect(onIdle).toHaveBeenCalledTimes(1);
      expect(WALL_IDLE_MS).toBe(180_000);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the screen on, takes the lock again when the page comes back, and lets it go", async () => {
    const release = vi.fn(async () => {});
    const request = vi.fn(async () => ({ release }));
    const listeners: Record<string, () => void> = {};
    const doc = {
      visibilityState: "visible" as DocumentVisibilityState,
      addEventListener: (k: string, f: () => void) => { listeners[k] = f; },
      removeEventListener: (k: string) => { delete listeners[k]; },
    };
    const stop = keepAwake({ request }, doc as never);
    await Promise.resolve();
    expect(request).toHaveBeenCalledWith("screen");
    doc.visibilityState = "hidden";
    listeners.visibilitychange();
    expect(request).toHaveBeenCalledTimes(1);
    doc.visibilityState = "visible";
    listeners.visibilitychange();
    expect(request).toHaveBeenCalledTimes(2);
    await Promise.resolve();
    stop();
    expect(release).toHaveBeenCalled();
    expect(listeners.visibilitychange).toBeUndefined();
  });

  it("does nothing without the API, and survives a refusal", async () => {
    expect(() => keepAwake(undefined, undefined)()).not.toThrow();
    const stop = keepAwake({ request: () => Promise.reject(new Error("battery")) }, undefined);
    await Promise.resolve();
    stop();
  });
});

setLang("sv");
