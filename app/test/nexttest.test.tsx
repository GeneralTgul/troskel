// @vitest-environment happy-dom
// The plan's next test (#778): the app's rules (model/nexttest.ts) against Python's (lopanalys.plan.next_test) with
// test/fixtures/nexttest.json.gz from app/scripts/make_fixtures.py, the coach's day through new times, Förnya, the
// athlete's edits, own values, the short link and the athlete's file, and the line under *Schemat*.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { render } from "preact";
import { afterEach, describe, expect, it } from "vitest";
import { buildPlan, rebuild, rollForward } from "../src/engine";
import { normRequest } from "../src/engine/models";
import { setLang } from "../src/i18n";
import { addDays } from "../src/model/dates";
import { anchor, basis, defaultNextTest, nextTest } from "../src/model/nexttest";
import { athletePayload, readAthleteLink } from "../src/model/share";
import { expandShort, type ShortPayload } from "../src/model/short";
import type { IsoDate, StoredPlan, StoredPlanBody, StoredRequest, StoredSlot } from "../src/model/types";
import { planWithValues } from "../src/model/values";
import { Overview } from "../src/views/Overview";

interface Case { name: string; default: IsoDate; anchor: IsoDate; basis: string; tested: IsoDate | null }
interface Fixtures {
  build: (Case & { request: StoredRequest; today: IsoDate | null })[];
  stored: (Case & { stored: StoredPlan })[];
  chosen: Record<string, StoredPlan>;
  next_test: IsoDate;
  rebuilt: { slots: StoredSlot[]; from: IsoDate; result: StoredPlan };
  rolled: StoredPlanBody;
  edited: StoredPlan;
  links: Record<string, ShortPayload>;
  athlete_link: string;
  athlete_read: StoredPlan;
}

const fx: Fixtures = JSON.parse(
  gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/nexttest.json.gz"))).toString("utf-8"));

const storedOf = (request: StoredRequest, plan: StoredPlanBody): StoredPlan =>
  ({ kind: "troskel.plan", format: 1, athlete: "", request, plan, targets: {}, targets_log: [] } as unknown as StoredPlan);

const check = (s: StoredPlan, c: Case) => {
  expect(defaultNextTest(s), c.name).toBe(c.default);
  expect(anchor(s), c.name).toBe(c.anchor);
  expect(basis(s), c.name).toEqual({ kind: c.basis, tested: c.tested });
};

describe("the next test against plan.next_test", () => {
  it("gives the same default day for every build request", () => {
    expect(fx.build.length).toBeGreaterThan(30);
    for (const c of fx.build) {
      const req = normRequest(c.request);
      check(storedOf(req, buildPlan(req, { planId: "demo", today: c.today ?? undefined })), c);
    }
  });

  it("builds on own values without a test day", () => {
    for (const c of fx.stored) check(c.stored, c);
    expect(fx.stored[0].basis).toBe("own");
  });

  it("takes the coach's day before the default", () => {
    const s = fx.chosen.demo;
    expect(s.plan.next_test).toBe(fx.next_test);
    const n = nextTest(s, "2026-11-25");
    expect(n).toMatchObject({ day: fx.next_test, auto: false, passed: false });
    expect(nextTest(s, fx.next_test).passed).toBe(false);          // the test day itself: not yet
    expect(nextTest(s, addDays(fx.next_test, 1)).passed).toBe(true);
    const { next_test: _, ...plain } = s.plan;
    expect(nextTest({ ...s, plan: plain }, "2026-11-25")).toMatchObject({ day: defaultNextTest(s), auto: true });
  });
});

describe("the coach's day stays", () => {
  it("through new times, Förnya and the athlete's edits, as in Tröskel", () => {
    const out = rebuild(fx.chosen.demo, { slots: fx.rebuilt.slots, unavailable: [], from: fx.rebuilt.from });
    expect(out).toEqual(fx.rebuilt.result);
    expect(out.plan.next_test).toBe(fx.next_test);
    const r = fx.chosen.rolling;
    const rolled = rollForward(r.plan, r.request, "2026-12-20");
    expect(rolled).toEqual(fx.rolled);
    expect(rolled.next_test).toBe(fx.next_test);
    expect(fx.edited.plan.edits?.length).toBeGreaterThan(0);
    expect(fx.edited.plan.next_test).toBe(fx.next_test);
  });

  it("through the short link", () => {
    for (const [name, payload] of Object.entries(fx.links)) {
      expect(payload.format, name).toBe(2);
      expect(expandShort(payload), name).toEqual(name === "edited" ? fx.edited : fx.chosen[name]);
    }
  });

  it("through own values and the athlete's file to the coach", () => {
    const own = planWithValues(fx.chosen.demo, { run: { max_hr: "190", resting_hr: "", lt1_hr: "150", lt2_hr: "172",
      lt1: "5:40", lt2: "4:40", css: "" } });
    expect(own.plan.next_test).toBe(fx.next_test);
    expect(nextTest(own, "2026-11-25").basis).toBe("own");
    expect(readAthleteLink(fx.athlete_link).plan.plan.next_test).toBe(fx.next_test);
    expect(fx.athlete_read.plan.next_test).toBe(fx.next_test);       // Tröskel reads it (plan.phone)
    const p = athletePayload(fx.chosen.demo, { marks: {}, extras: [] } as never, null, null, "2026-11-25");
    expect(p.plan.plan.next_test).toBe(fx.next_test);
  });
});

describe("Schemat shows the next test", () => {
  let root: HTMLElement;
  afterEach(() => { render(null, root); setLang("sv"); });
  const show = (s: StoredPlan, today: IsoDate) => {
    root = document.createElement("div");
    render(<Overview stored={s} today={today} />, root);
    return root.querySelector(".next-test")!;
  };

  it("before the day: the day and what the targets build on", () => {
    const el = show(fx.chosen.demo, "2026-11-25");
    expect(el.classList.contains("passed")).toBe(false);
    expect(el.textContent).toContain("Nästa test");
    expect(el.textContent).toContain("Passens mål bygger på testet från");
    expect(el.textContent).toContain("Efter nästa test stämmer de allt sämre.");
  });

  it("after the day: the targets fit worse and worse, and new ones need a new test or own values", () => {
    const el = show(fx.chosen.demo, addDays(fx.next_test, 3));
    expect(el.classList.contains("passed")).toBe(true);
    expect(el.textContent).toContain("och stämmer allt sämre. Nya mål kräver ett nytt test eller nya egna värden.");
    expect(el.textContent).toContain("har varit");
  });

  it("with own values", () => {
    const el = show(fx.stored[0].stored, "2027-06-01");
    expect(el.textContent).toContain("Passens mål bygger på dina egna värden och stämmer allt sämre.");
  });

  it("in English", () => {
    setLang("en");
    const el = show(fx.chosen.demo, "2026-11-25");
    expect(el.textContent).toContain("Next test");
    expect(el.textContent).not.toMatch(/ui\.plan\./);
  });
});
