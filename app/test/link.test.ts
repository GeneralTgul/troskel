import { describe, expect, it } from "vitest";
import { decodeLink, encodeData, linkData, PlanError, readPlanText } from "../src/model/link";
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
