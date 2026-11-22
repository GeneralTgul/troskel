import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { PlanError, checkPlan } from "../src/model/link";
import { CHOICES_FORMAT, expandShort, type ShortPayload } from "../src/model/short";
import type { StoredPlan } from "../src/model/types";

const fx: { plans: Record<string, StoredPlan>; short: Record<string, ShortPayload> } = JSON.parse(
  gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/choices.json.gz"))).toString("utf-8"));

describe("the short link with the coach's choices", () => {
  it.each(Object.keys(fx.short))("gives the plan Tröskel saved: %s", (name) => {
    expect(expandShort(fx.short[name])).toEqual(fx.plans[name]);
  });
});

describe("format 3, the short link with the choices", () => {
  it("is what Tröskel writes, and the app reads it like format 2", () => {
    for (const p of Object.values(fx.short)) expect(p.format).toBe(CHOICES_FORMAT);
    const [name] = Object.keys(fx.short);
    expect(checkPlan(fx.short[name])).toEqual(fx.plans[name]);
  });

  it("a newer format is still too new", () => {
    const [name] = Object.keys(fx.short);
    expect(() => checkPlan({ ...fx.short[name], format: CHOICES_FORMAT + 1 })).toThrow(PlanError);
  });
});
