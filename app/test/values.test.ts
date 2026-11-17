/**
 * The athlete's own values (#777) against Tröskel. test/fixtures/zones.json.gz is written by
 * app/scripts/make_fixtures.py (`zones_fixtures`): capacities and the targets resolve.py gives them
 * (`link.zone_targets`), the targets per sport of requests (`link._targets`), typed-in values and the capacity they
 * give (`values_capacity`), and the demo plan laid on own values (`plan_with_values`). The app's port
 * (engine/zones.ts, model/values.ts) must give the same, to the last watt and the last tenth of a second.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { roundTo } from "../src/engine/py";
import { NotInApp, planTargets, zoneTargets } from "../src/engine/zones";
import type { Sport, StoredCapacity, StoredPlan, StoredRequest, ZoneTargets } from "../src/model/types";
import {
  ValuesError, keptOwnValues, planWithValues, valuesCapacity, valuesOf, type OwnValues, type SportValues,
} from "../src/model/values";
import { demoPlan } from "./helpers";

interface Fx {
  targets: { name: string; capacity: StoredCapacity; sport: Sport | null; targets: ZoneTargets }[];
  plans: { name: string; request: StoredRequest; targets: Record<Sport, ZoneTargets> }[];
  values: { sport: Sport; values: SportValues; capacity?: StoredCapacity | null; error?: string }[];
  laid: { name: string; values: OwnValues; stored: StoredPlan; from?: string }[];
  values_of: { capacity: StoredCapacity; values: SportValues }[];
}

const fx: Fx = JSON.parse(gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/zones.json.gz"))).toString("utf-8"));

describe("the zones against resolve.py", () => {
  it.each(fx.targets.map((c) => [c.name, c] as const))("%s", (_, c) => {
    expect(zoneTargets(c.capacity, c.sport)).toEqual(c.targets);
  });

  it.each(fx.plans.map((c) => [c.name, c] as const))("the plan's targets: %s", (_, c) => {
    expect(planTargets(c.request)).toEqual(c.targets);
  });

  it("rounds as Python does", () => {
    expect([roundTo(0.25, 1), roundTo(0.35, 1), roundTo(112.25, 1), roundTo(2.675, 2), roundTo(-0.25, 1)])
      .toEqual([0.2, 0.3, 112.2, 2.67, -0.2]);
  });

  it("leaves a zones test's pace from heart rate to Tröskel", () => {
    const zonesTest: StoredCapacity = { sport: "run", tested: "2026-11-01", source: "Zontest",
      anchors: { max_hr: 190, resting_hr: null, lt1_hr: null, lt2_hr: null, lt1_speed_kmh: null, lt2_speed_kmh: null },
      speed_line: { slope: 8, intercept: 60, speed_min: 8, speed_max: 14, ramp_top_kmh: null, extrapolate_kmh: 2 } };
    expect(() => zoneTargets(zonesTest)).toThrow(NotInApp);
    const sent = { run: { Z1: { hr_low: 95, hr_high: 114, speed_low_kmh: null, speed_high_kmh: 8.2, estimated: true,
      speed_low_approx: false, speed_high_approx: false, speed_from_hr: true } } };
    const r = { ...demoPlan().request, capacity: [zonesTest] };
    expect(planTargets(r, sent).run).toBe(sent.run);
  });
});

describe("the typed-in values against Python", () => {
  it.each(fx.values.map((c, i) => [`${i} ${c.sport} ${c.error ?? "ok"}`, c] as const))("%s", (_, c) => {
    if (c.error) {
      let err: unknown;
      try {
        valuesCapacity(c.sport, c.values);
      } catch (e) {
        err = e;
      }
      expect(err).toBeInstanceOf(ValuesError);
      expect((err as ValuesError).key).toBe(c.error);
    } else expect(valuesCapacity(c.sport, c.values)).toEqual(c.capacity);
  });

  it.each(fx.values_of.map((c, i) => [i, c] as const))("the fields of capacity %s", (_, c) => {
    expect(valuesOf(c.capacity)).toEqual(c.values);
  });
});

describe("a plan on own values", () => {
  it.each(fx.laid.map((c) => [c.name, c] as const))("%s: as Tröskel lays it", (_, c) => {
    const from = c.from ? fx.laid.find((x) => x.name === c.from)!.stored : demoPlan();
    expect(planWithValues(from, c.values)).toEqual(c.stored);
  });

  it("marks own values and keeps the weeks", () => {
    const own = fx.laid[0].stored;
    const run = own.request.capacity.find((c) => c.sport === "run")!;
    expect(run.source).toBe("self");
    expect(run.tested).toBeNull();
    expect(own.plan.based_on.run).toBeNull();
    expect(own.plan.weeks).toEqual(demoPlan().plan.weeks);
    expect(own.targets.run.Z4!.hr_high).toBe(176);
  });

  it("keeps own values when the coach's plan has no test in the sport, and gives way to a test", () => {
    const demo = demoPlan();
    const bike: OwnValues = { bike: { ...valuesOf({ sport: "bike", anchors: null, tested: null, source: "" }), max_hr: "180", lt2: "250" } };
    const roles = [{ sport: "run", role: "main", share: 0 }, { sport: "bike", role: "support", share: 0 }];
    const both = { ...demo, request: { ...demo.request, roles } };
    const mine = planWithValues(both, bike);
    expect(mine.targets.bike.sweetspot!.power_low_w).toBe(220);
    // The coach's new link: the same plan id, a test in running, nothing on the bike.
    const kept = keptOwnValues(mine, both);
    expect(kept.request.capacity.map((c) => [c.sport, c.source])).toEqual([["run", "Laktattrappa"], ["bike", "self"]]);
    expect(kept.targets.bike).toEqual(mine.targets.bike);
    expect(kept.targets.run).toBe(both.targets.run);
    // A test on the bike from Tröskel takes the place of the own values.
    const tested: StoredCapacity = { ...mine.request.capacity[1], source: "Laktattrappa", tested: "2026-12-01" };
    const withTest = { ...both, request: { ...both.request, capacity: [...both.request.capacity, tested] } };
    expect(keptOwnValues(mine, withTest)).toBe(withTest);
    // Another plan: nothing is kept.
    const other = { ...both, plan: { ...both.plan, id: "p999" } };
    expect(keptOwnValues(mine, other)).toBe(other);
  });
});
