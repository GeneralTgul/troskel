/**
 * The coach's choices in the request against the Python engine: the number of quality sessions, a group session's
 * length, fixed easy days, two long workouts in a row and the coach's easy weeks (docs/TRANINGSPLAN.md, *Coachens
 * val*). test/fixtures/choices.json.gz is written by tests/plan/choices_fixtures.py with lopanalys.plan, and
 * tests/plan/test_choices.py keeps the file in step with the Python engine.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { ENGINE_VERSION, buildPlan, engine, rollForward } from "../src/engine";
import { choices, normRequest } from "../src/engine/models";
import { coachEasy, fixedMinutes, weekCapacity } from "../src/engine/periodize";
import type { StoredPlan, StoredPlanBody, StoredRequest, StoredSlot } from "../src/model/types";

interface Fixtures {
  engine_version: string;
  build: { name: string; request: StoredRequest; plan_id: string; today: string | null; plan: StoredPlanBody }[];
  roll: { name: string; request: StoredRequest; plan: StoredPlanBody; today: string; result: StoredPlanBody }[];
  plans: Record<string, StoredPlan>;
  rebuild: { name: string; plan: string; slots: StoredSlot[]; unavailable: string[]; from: string; result: StoredPlan }[];
}

const fx: Fixtures = JSON.parse(
  gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/choices.json.gz"))).toString("utf-8"));

describe("the coach's choices against Python", () => {
  it("has the same version and cases with every choice", () => {
    expect(ENGINE_VERSION).toBe(fx.engine_version);
    const all = fx.build.map((c) => choices(c.request));
    expect(all.some((r) => r.quality_sessions)).toBe(true);
    expect(all.some((r) => r.easy_days?.length)).toBe(true);
    expect(all.some((r) => r.second_long)).toBe(true);
    expect(all.some((r) => r.easy_weeks?.length)).toBe(true);
    expect(all.some((r) => r.hard_days?.some((h) => h.minutes))).toBe(true);
  });

  it.each(fx.build.map((c) => [c.name, c] as const))("build_plan: %s", (_, c) => {
    expect(buildPlan(normRequest(c.request), { planId: c.plan_id, today: c.today })).toEqual(c.plan);
  });

  it.each(fx.build.map((c) => [c.name, c] as const))("the request keeps its choices: %s", (_, c) => {
    expect(normRequest(c.request)).toEqual(c.request);
  });

  it.each(fx.roll.map((c) => [c.name, c] as const))("roll_forward: %s", (_, c) => {
    const before = JSON.stringify(c.plan);
    expect(rollForward(c.plan, normRequest(c.request), c.today)).toEqual(c.result);
    expect(JSON.stringify(c.plan)).toBe(before);
  });

  it.each(fx.rebuild.map((c) => [c.name, c] as const))("rebuild: %s", (_, c) => {
    const stored = fx.plans[c.plan];
    const before = JSON.stringify(stored);
    expect(engine.rebuild(stored, { slots: c.slots, unavailable: c.unavailable, from: c.from })).toEqual(c.result);
    expect(JSON.stringify(stored)).toBe(before);
  });
});

describe("the coach's easy weeks", () => {
  const base = "lelllelllellltr".split("").map((k) => ({ l: "load", e: "easy", t: "taper", r: "race" })[k]!);
  it.each([
    [[5], "lellellllellltr"], [[4], "lllelelllellltr"], [[3], "llellelllellltr"], [[8], "lelllelelellltr"],
    [[1], "lelllelllellltr"], [[6], "lelllelllellltr"], [[14], "lelllelllellltr"], [[5, 9], "lellelllelllltr"],
  ] as [number[], string][])("moves the nearest counted easy week: %j", (marked, want) => {
    const kinds = [...base];
    coachEasy(kinds, marked, 3);
    expect(kinds.map((k) => k[0]).join("")).toBe(want);
  });
});

describe("the week's capacity", () => {
  it("holds the group session's length", () => {
    const c = fx.build.find((x) => x.name === "easy-day-on-a-hard-day")!;
    const req = normRequest(c.request);
    expect([...fixedMinutes(req)]).toEqual([[1, 75]]);
    const plain = normRequest({ ...c.request, easy_days: undefined, hard_days: [{ weekday: 1 }] } as StoredRequest);
    expect(weekCapacity(req)).toBe(weekCapacity(plain));   // Tuesday holds 75 either way at this level
  });
});
