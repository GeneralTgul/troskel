import { strFromU8, unzlibSync } from "fflate";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { decodeData, decodeLink, encodeData, linkData, PlanError, readPlanText } from "../src/model/link";
import type { StoredPlan } from "../src/model/types";
import { demoPlan, fixture } from "./helpers";

describe("the link from Tröskel", () => {
  const link = fixture("demo.link.txt").trim();

  it("decodes to the same plan as the file Python wrote", () => {
    expect(link.startsWith("https://generaltgul.github.io/troskel/app/#p=")).toBe(true);
    expect(decodeLink(link)).toEqual(demoPlan());
  });

  it("holds the plan, the name and the targets", () => {
    const p = decodeLink(link);
    expect(p.kind).toBe("troskel.plan");
    expect(p.athlete).toBe("Exempel Löpare");
    expect(p.plan.weeks).toHaveLength(12);
    expect(p.targets.run.Z4?.hr_low).toBe(170);
  });

  it("is read from a pasted text, a bare fragment and a file", () => {
    const want = demoPlan();
    expect(readPlanText(`  ${link}\n`)).toEqual(want);
    expect(readPlanText(link.slice(link.indexOf("#")))).toEqual(want);
    expect(readPlanText(JSON.stringify(want))).toEqual(want);
  });

  it("survives the app's own encoding", () => {
    const p = demoPlan();
    expect(decodeLink(`#p=${encodeData(p)}`)).toEqual(p);
  });

  it("finds p= among other fragment parts", () => {
    expect(linkData("https://x.example/#a=1&p=abc")).toBe("abc");
    expect(linkData("https://x.example/")).toBeNull();
  });

  it("says why something is not a plan", () => {
    const code = (f: () => unknown) => { try { f(); } catch (e) { return (e as PlanError).code; } return null; };
    expect(code(() => readPlanText("https://x.example/"))).toBe("not_a_plan");
    expect(code(() => readPlanText("#p=!!!notbase64"))).toBe("broken");
    expect(code(() => readPlanText('{"kind":"other"}'))).toBe("not_a_plan");
    expect(code(() => readPlanText('{"kind":"troskel.plan","format":99}'))).toBe("too_new");
    expect(code(() => readPlanText('{"kind":"troskel.plan","format":1}'))).toBe("broken");
    expect(code(() => readPlanText("{oops"))).toBe("broken");
  });
});

describe("the short link (format 2): the app builds the plan with its engine", () => {
  const fx = JSON.parse(gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/engine.json.gz"))).toString("utf-8"));
  const want = (name: string): Partial<StoredPlan> => {
    const [kind, key] = name.split(":");
    if (kind === "plan") return fx.plans[key];
    if (kind === "build") {
      const c = fx.build.find((b: { name: string }) => b.name === key);
      return { request: c.request, plan: c.plan };
    }
    const c = fx.roll.cases.find((b: { name: string }) => b.name === key);
    return { request: fx.roll.request, plan: c.plan };
  };

  it("has a link for every fixture but the plan with statuses", () => {
    expect(Object.keys(fx.links).length).toBeGreaterThan(80);
    expect(fx.links["plan:demo-done"]).toBeUndefined();
    expect(fx.links["plan:four-sports"] ?? fx.links["build:four-sports"]).toBeDefined();
  });

  it.each(Object.keys(fx.links))("gives the plan Tröskel saved: %s", (name) => {
    const got = decodeData(fx.links[name]);
    const w = want(name);
    if (name.startsWith("plan:")) expect(got).toEqual(w);
    else {
      expect(got.plan).toEqual(w.plan);
      expect(got.request).toEqual(w.request);
    }
  });

  it("is what the demo link holds, and the whole plan's link gives the same", () => {
    const short = fixture("demo.link.txt").trim();
    const whole = fixture("demo.full.link.txt").trim();
    expect(short.length).toBeLessThan(whole.length / 4);
    const raw = JSON.parse(strFromU8(unzlibSync(base64(linkData(short)!))));
    expect([raw.kind, raw.format, raw.plan]).toEqual(["troskel.plan", 2, undefined]);
    expect(decodeLink(short)).toEqual(decodeLink(whole));
  });

  it("asks for an update when another engine made it, rather than build another plan", () => {
    const raw = JSON.parse(strFromU8(unzlibSync(base64(linkData(fixture("demo.link.txt"))!))));
    const code = (f: () => unknown) => { try { f(); } catch (e) { return (e as PlanError).code; } return null; };
    expect(code(() => readPlanText(JSON.stringify({ ...raw, v: "7" })))).toBe("other_engine");
    expect(code(() => readPlanText(JSON.stringify({ ...raw, r: { ...raw.r, a: [] } })))).toBe("broken");
    // Format 3 (the coach's choices) is read like 2; a newer one asks for an update.
    expect(code(() => readPlanText(JSON.stringify({ ...raw, format: 3 })))).toBeNull();
    expect(code(() => readPlanText(JSON.stringify({ ...raw, format: 4 })))).toBe("too_new");
  });
});

function base64(data: string): Uint8Array {
  return new Uint8Array(Buffer.from(data.replace(/-/g, "+").replace(/_/g, "/"), "base64"));
}
