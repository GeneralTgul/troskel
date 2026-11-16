/**
 * The TypeScript engine against the Python engine: test/fixtures/engine.json.gz is written by
 * app/scripts/make_fixtures.py with lopanalys.plan (build_plan, roll_forward and make_fixtures.rebuild).
 * Every case compares the whole stored result. The Python test test_the_apps_files_from_python_are_up_to_date
 * keeps the file in step with the Python engine, and this test keeps the port in step with the file.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { ENGINE_VERSION, PlanError, buildPlan, engine, rebuild, rollForward } from "../src/engine";
import { normRequest } from "../src/engine/models";
import { cmp, combinations, floordiv, round, round2, sum } from "../src/engine/py";
import type { StoredPlan, StoredPlanBody, StoredRequest, StoredSlot } from "../src/model/types";

interface BuildCase {
  name: string;
  request: StoredRequest;
  plan_id: string;
  today: string | null;
  plan?: StoredPlanBody;
  error?: { key: string; args: Record<string, string | number> };
}

interface RebuildCase {
  name: string;
  plan: string;
  slots: StoredSlot[];
  unavailable: string[];
  from: string;
  result?: StoredPlan;
  error?: { key: string; args: Record<string, string | number> };
}

interface Fixtures {
  engine_version: string;
  build: BuildCase[];
  roll: { plan: StoredPlanBody; request: StoredRequest; cases: { name: string; today: string; plan: StoredPlanBody }[] };
  plans: Record<string, StoredPlan>;
  rebuild: RebuildCase[];
}

const fx: Fixtures = JSON.parse(
  gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/engine.json.gz"))).toString("utf-8"));

function thrown(f: () => unknown): PlanError {
  try {
    f();
  } catch (e) {
    if (e instanceof PlanError) return e;
    throw e;
  }
  throw new Error("no error");
}

describe("the engine against Python", () => {
  it("has the same version", () => {
    expect(ENGINE_VERSION).toBe(fx.engine_version);
    expect(engine.available).toBe(true);
    expect(engine.version).toBe(fx.engine_version);
  });

  it.each(fx.build.map((c) => [c.name, c] as const))("build_plan: %s", (_, c) => {
    const req = normRequest(c.request);
    if (c.error) {
      const e = thrown(() => buildPlan(req, { planId: c.plan_id, today: c.today }));
      expect([e.key, e.args]).toEqual([c.error.key, c.error.args]);
    } else {
      expect(buildPlan(req, { planId: c.plan_id, today: c.today })).toEqual(c.plan);
    }
  });

  it.each(fx.roll.cases.map((c) => [c.name, c] as const))("roll_forward: %s", (_, c) => {
    const before = JSON.stringify(fx.roll.plan);
    expect(rollForward(fx.roll.plan, fx.roll.request, c.today)).toEqual(c.plan);
    expect(JSON.stringify(fx.roll.plan)).toBe(before);
  });

  it.each(fx.rebuild.map((c) => [c.name, c] as const))("rebuild: %s", (_, c) => {
    const stored = fx.plans[c.plan];
    const before = JSON.stringify(stored);
    const change = { slots: c.slots, unavailable: c.unavailable, from: c.from };
    if (c.error) {
      const e = thrown(() => rebuild(stored, change));
      expect([e.key, e.args]).toEqual([c.error.key, c.error.args]);
    } else {
      expect(engine.rebuild(stored, change)).toEqual(c.result);
    }
    expect(JSON.stringify(stored)).toBe(before); // the stored plan is never changed in place
  });

  it("keeps the workouts before the day as they were", () => {
    const c = fx.rebuild.find((x) => x.name === "done-kept")!;
    const out = rebuild(fx.plans[c.plan], c);
    const old = fx.plans[c.plan].plan.weeks.flatMap((w) => w.workouts).filter((w) => w.date < c.from);
    expect(out.plan.weeks.flatMap((w) => w.workouts).filter((w) => w.date < c.from)).toEqual(old);
    expect(old.some((w) => w.status === "done")).toBe(true);
  });
});

describe("Python's arithmetic", () => {
  it("rounds as Python", () => {
    expect([0.5, 1.5, 2.5, -1.5, 2.4999].map(round)).toEqual([0, 2, 2, -2, 2]);
    expect([2.675, 0.125, 0.375, 1.005, 12.345, 0.285, 7.5].map(round2)).toEqual([2.67, 0.12, 0.38, 1.0, 12.35, 0.28, 7.5]);
  });

  it("divides and sums as Python", () => {
    expect([floordiv(7, 2), floordiv(-7, 2), floordiv(7.5, 5), floordiv(-1, 7)]).toEqual([3, -4, 1, -1]);
    expect(sum(Array(10).fill(0.1))).toBe(1.0);
    expect([...combinations([1, 2, 3, 4], 2)]).toEqual([[1, 2], [1, 3], [1, 4], [2, 3], [2, 4], [3, 4]]);
    expect([...combinations([1, 2], 0)]).toEqual([[]]);
    expect(cmp([1, "b"], [1, "a"])).toBeGreaterThan(0);
  });
});
