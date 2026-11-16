/**
 * Engine 9 in the app: the high level, the long run after the goal's length and fixed hard days. The fixtures check
 * every plan against Python (engine.test.ts); these tests check the rules themselves, and that a request with the new
 * fields survives the short link and a rebuild with new times.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { ENGINE_VERSION, buildPlan, rebuild } from "../src/engine";
import { LEVELS, normRequest } from "../src/engine/models";
import { LEVEL, longCap } from "../src/engine/periodize";
import { decodeData } from "../src/model/link";
import { unpackRequest } from "../src/model/short";
import type { StoredPlan, StoredRequest, StoredWorkout } from "../src/model/types";

const fx = JSON.parse(gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/engine.json.gz"))).toString("utf-8"));
const request = (name: string): StoredRequest => normRequest(fx.build.find((c: { name: string }) => c.name === name).request);
const weekday = (iso: string) => (new Date(`${iso}T12:00:00Z`).getUTCDay() + 6) % 7;
const hardDays = (ws: StoredWorkout[]) =>
  [...new Set(ws.filter((w) => w.quality && w.goal == null && w.sport !== "strength").map((w) => weekday(w.date)))].sort();

describe("engine 9", () => {
  it("has the high level", () => {
    expect(ENGINE_VERSION).toBe("9");
    expect(LEVELS).toContain("high");
    expect([LEVEL.high.max_sessions, LEVEL.high.easy_max, LEVEL.high.recovery_max]).toEqual([11, 105, 60]);
    expect(["beginner", "recreational", "trained"].map((l) => [LEVEL[l].easy_max, LEVEL[l].recovery_max]))
      .toEqual([[75, 45], [75, 45], [75, 45]]);
  });

  it("plans 11 hours or more a week at the high level", () => {
    const plan = buildPlan(request("high-volume"), { planId: "demo" });
    const most = Math.max(...plan.weeks.map((wk) => wk.workouts.filter((w) => w.sport !== "strength")
      .reduce((s, w) => s + w.parts.reduce((a, p) => a + p.repeat * p.duration_s + (p.repeat - 1) * p.rest_s, 0) / 60, 0)));
    expect(most).toBeGreaterThanOrEqual(11 * 60);
  });

  it("lets a 90 km ski race raise the long run to 4:30, and keeps a 10 km race's ceiling", () => {
    const ski = request("long-ski-race");
    expect(longCap(ski)).toBe(270);
    expect(longCap({ ...ski, level: "recreational" })).toBe(240);
    expect(longCap({ ...ski, goals: [{ ...ski.goals[0], sport: "run", distance_km: 10 }] })).toBe(180);
    expect(longCap({ ...ski, goals: [{ ...ski.goals[0], sport: "run", distance_km: 42.2 }] })).toBe(180);
    expect(longCap({ ...ski, goals: [] })).toBe(180);
  });

  it("puts the quality session on the fixed day, in its sport", () => {
    const plan = buildPlan(request("high-volume"), { planId: "demo" });
    for (const wk of plan.weeks) {
      const q = hardDays(wk.workouts);
      if (q.length === 1 && (wk.kind === "load" || wk.kind === "easy")) expect(q).toEqual([3]);
      if (q.length === 2) expect(q).toEqual([0, 3]);
    }
    const thursday = plan.weeks.flatMap((wk) => wk.workouts).filter((w) => w.quality && weekday(w.date) === 3 && w.family !== "test");
    expect(thursday.length).toBeGreaterThan(5);
    expect(new Set(thursday.map((w) => w.sport))).toEqual(new Set(["ski"]));
  });

  it("never puts two hard days in a row, and leaves a fixed day before the long run easy", () => {
    for (const name of ["hard-days-two", "hard-day-before-long", "hard-day-maintenance", "high-volume"]) {
      const ws = buildPlan(request(name), { planId: "demo" }).weeks.flatMap((wk) => wk.workouts);
      const days = [...new Set(ws.filter((w) => w.quality).map((w) => w.date))].sort();
      for (let i = 1; i < days.length; i++)
        expect(Date.parse(days[i]) - Date.parse(days[i - 1])).toBeGreaterThan(86_400_000);
    }
    const before = buildPlan(request("hard-day-before-long"), { planId: "demo" }).weeks.flatMap((wk) => wk.workouts);
    expect(before.some((w) => w.quality && w.goal == null && weekday(w.date) === 5)).toBe(false);
  });

  it("is the plan before without hard days", () => {
    const req = request("hard-day");
    const without = { ...req };
    delete without.hard_days;
    expect(buildPlan(without, { planId: "demo" })).not.toEqual(buildPlan(req, { planId: "demo" }));
    expect(buildPlan({ ...req, hard_days: [] }, { planId: "demo" })).toEqual(buildPlan(without, { planId: "demo" }));
  });

  it("reads the hard days of a short link", () => {
    expect(unpackRequest({ s: "2026-11-16", a: [[3, 60]], hd: [[3, "ski"], [0]], l: "high" }).hard_days)
      .toEqual([{ weekday: 3, sport: "ski" }, { weekday: 0 }]);
    expect(unpackRequest({ s: "2026-11-16", a: [[3, 60]] }).hard_days).toBeUndefined();
    const got = decodeData(fx.links["plan:high-volume"]);
    expect(got.request.hard_days).toEqual([{ weekday: 3, sport: "ski" }, { weekday: 0, sport: "run" }]);
    expect(got.request.level).toBe("high");
  });

  it("keeps the hard days when the athlete changes the times", () => {
    const stored: StoredPlan = fx.plans["high-volume"];
    const slots = stored.request.availability.filter((s) => s.weekday !== 2);
    const out = rebuild(stored, { slots, unavailable: [], from: "2026-11-30" });
    expect(out.request.hard_days).toEqual(stored.request.hard_days);
  });
});
