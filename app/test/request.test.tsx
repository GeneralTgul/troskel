// @vitest-environment happy-dom
/**
 * The athlete's own plan (#777): the form *Gör ett schema själv* against Tröskel. test/fixtures/request.json.gz is
 * written by app/scripts/make_fixtures.py (`request_fixtures`): each form, and the plan Tröskel builds from the same
 * answers by the rules of its own form (`form_request`, `build_plan`, `link.stored_plan`). The app must give the same
 * request and the same stored plan, so that a plan made in the app reads into Tröskel as Tröskel would have made it.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { PlanError, engine } from "../src/engine";
import { setLang } from "../src/i18n";
import {
  FormError, emptyForm, formFromPlan, newPlanId, ownPlan, planFromForm, requestFromForm, type RequestForm,
} from "../src/model/request";
import { memoryStore } from "../src/model/storage";
import type { StoredPlan } from "../src/model/types";
import { demoPlan, demoText } from "./helpers";

interface Case { name: string; form: RequestForm; plan_id: string; stored: StoredPlan; from?: string; today?: string }

const fx: { cases: Case[] } = JSON.parse(
  gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/request.json.gz"))).toString("utf-8"));

const NOW = new Date("2026-11-25T08:00:00Z");
let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
  vi.unstubAllGlobals();
});

function thrown(f: () => unknown): FormError | PlanError {
  try {
    f();
  } catch (e) {
    if (e instanceof FormError || e instanceof PlanError) return e;
    throw e;
  }
  throw new Error("no error");
}

describe("the form against Tröskel", () => {
  it("has every kind of case", () => {
    expect(fx.cases.map((c) => c.name)).toEqual(
      ["run-weeks", "run-goal-milestones", "beginner-weeks", "multi-sport", "high-ski", "own-values", "from-demo"]);
  });

  it.each(fx.cases.map((c) => [c.name, c] as const))("%s: the same request and the same plan", (_, c) => {
    expect(requestFromForm(c.form)).toEqual(c.stored.request);
    const targets = c.from === "demo" ? demoPlan().targets : undefined;
    expect(planFromForm(c.form, { id: c.plan_id, targets })).toEqual(c.stored);
  });

  it("fills the form in from the demo plan as Tröskel does", () => {
    const c = fx.cases.find((x) => x.name === "from-demo")!;
    expect(formFromPlan(demoPlan(), c.today!)).toEqual(c.form);
  });

  it("gives a plan without a test zones and no targets", () => {
    const c = fx.cases.find((x) => x.name === "run-weeks")!;
    const p = planFromForm(c.form, { id: "app-x" });
    expect(p.request.capacity).toEqual([]);
    expect(p.targets).toEqual({});
    expect(p.plan.notes.map((n) => n.key)).toContain("plan.note.no_test");
    expect(p.plan.horizon).toBeNull();
    expect(p.plan.weeks.every((w) => !w.sketch)).toBe(true);
  });
});

describe("what the form stops", () => {
  const ok = (): RequestForm => ({ ...emptyForm("2026-11-25"), level: "recreational" });
  const cases: [string, (f: RequestForm) => RequestForm, string, Record<string, unknown>][] = [
    ["no level", (f) => ({ ...f, level: "" }), "ui.request.err.level", {}],
    ["no sport", (f) => ({ ...f, sports: [] }), "ui.request.err.sport", {}],
    ["too few minutes", (f) => ({ ...f, days: f.days.map((d, i) => (i === 1 ? { ...d, minutes: 10 } : d)) }),
      "ui.request.err.minutes", { day: 1, lo: 20, hi: 480 }],
    ["no day", (f) => ({ ...f, days: f.days.map((d) => ({ ...d, on: false })) }), "plan.error.no_slots", {}],
    ["no start", (f) => ({ ...f, start: "" }), "ui.request.err.start", {}],
    ["a goal without a name", (f) => ({ ...f, mode: "goal" }), "ui.request.err.goal_name", {}],
    ["a goal without a day", (f) => ({ ...f, mode: "goal", goals: [{ ...f.goals[0], name: "Loppet" }] }),
      "ui.request.err.goal_date", { name: "Loppet" }],
    ["a milestone after the race", (f) => ({ ...f, mode: "goal", goals: [
      { ...f.goals[0], name: "Loppet", date: "2027-03-01" }, { ...f.goals[0], name: "Sen", date: "2027-04-01", priority: "B" }] }),
      "ui.request.err.milestone_late", { name: "Sen" }],
    ["no weeks", (f) => ({ ...f, weeks: null }), "ui.request.err.weeks", { hi: 104 }],
    ["too many weeks", (f) => ({ ...f, weeks: 105 }), "ui.request.err.weeks", { hi: 104 }],
  ];
  it.each(cases)("%s", (_, change, key, args) => {
    const e = thrown(() => requestFromForm(change(ok())));
    expect(e).toBeInstanceOf(FormError);
    expect([e.key, e.args]).toEqual([key, args]);
  });

  it("leaves the engine's own errors to the engine", () => {
    const f: RequestForm = { ...ok(), mode: "goal", goals: [{ ...ok().goals[0], name: "Snart", date: "2026-11-28" }] };
    const e = thrown(() => planFromForm(f, { id: "app-x" }));
    expect(e).toBeInstanceOf(PlanError);
    expect(e.key).toBe("plan.error.goal_too_close");
    expect(thrown(() => planFromForm({ ...ok(), weeks: 3 }, { id: "app-x" })).key).toBe("plan.error.too_short");
  });

  it("makes ids of its own", () => {
    const id = newPlanId();
    expect(id).toMatch(/^app-[0-9a-z]{8}$/);
    expect(newPlanId()).not.toBe(id);
    expect(ownPlan(planFromForm(ok(), { id }))).toBe(true);
    expect(ownPlan(demoPlan())).toBe(false);
  });
});

async function mount(plan: StoredPlan | null = null, demo = false) {
  const store = memoryStore();
  if (plan) store.setPlan(plan, demo);
  store.setSettings({ lang: "sv" });
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={engine} now={() => NOW} timeZone="Europe/Stockholm" tab={plan ? "about" : undefined}
      loadDemo={async () => demoText()} newPlanId={() => "app-test0001"} />, root);
  });
  return store;
}

const click = async (el: Element | null | undefined) => {
  expect(el).toBeTruthy();
  await act(async () => { (el as HTMLElement).click(); });
};
const byText = (sel: string, text: string) => [...root.querySelectorAll(sel)].find((e) => e.textContent?.includes(text));
const change = async (el: Element | null, value: string, type: "change" | "input" = "change") => {
  expect(el).not.toBeNull();
  await act(async () => {
    (el as HTMLInputElement).value = value;
    el!.dispatchEvent(new Event(type, { bubbles: true }));
  });
};

describe("Gör ett schema själv", () => {
  it("makes a plan without a coach from the welcome page", async () => {
    const store = await mount();
    await click(byText(".welcome button", "Gör ett schema själv"));
    expect(root.querySelector(".request")).not.toBeNull();
    expect(root.querySelector(".tabs")).toBeNull();
    expect(root.querySelector(".about")).toBeNull();
    await click(byText(".request button", "Gör schemat"));
    expect(root.querySelector(".request [role=alert]")!.textContent).toBe("Välj din nivå.");
    await change(root.querySelector(".request select[name=level]"), "recreational");
    await click(byText(".request button", "Gör schemat"));
    expect(root.querySelector(".request")).toBeNull();
    expect(root.querySelector(".tabs")).not.toBeNull();
    expect(root.querySelector(".toast")!.textContent).toBe("Schemat är klart.");
    const p = store.plan()!;
    expect(p.plan.id).toBe("app-test0001");
    expect(p.request.availability.map((s) => s.weekday)).toEqual([1, 3, 6]);
    expect(p.plan.weeks).toHaveLength(8);
    expect(store.isDemo()).toBe(false);
  });

  it("starts from the plan the phone has, with its tests, and asks before it replaces it", async () => {
    const plan = demoPlan();
    const store = await mount(plan);
    await click(byText(".about button", "Gör ett schema själv"));
    expect(root.querySelector(".request .notice")!.textContent).toContain("Testerna från ditt nuvarande schema");
    const on = [...root.querySelectorAll<HTMLInputElement>(".request .time-rows input[type=checkbox]")].map((x) => x.checked);
    expect(on).toEqual([true, true, false, true, false, true, true]);
    vi.stubGlobal("confirm", () => false);
    await click(byText(".request button", "Gör schemat"));
    expect(store.plan()!.plan.id).toBe(plan.plan.id);
    expect(root.querySelector(".request")).not.toBeNull();
    vi.stubGlobal("confirm", () => true);
    await click(byText(".request button", "Gör schemat"));
    const p = store.plan()!;
    expect(p.plan.id).toBe("app-test0001");
    expect(p.request.capacity).toEqual(plan.request.capacity);
    expect(p.targets).toEqual(plan.targets);
  });

  it("does not take the example's test", async () => {
    await mount(demoPlan(), true);
    await click(byText(".about button", "Gör ett schema själv"));
    expect(root.querySelector(".request .notice")!.textContent).toContain("Utan test");
    await click(byText(".request button", "Avbryt"));
    expect(root.querySelector(".request")).toBeNull();
    expect(root.querySelector(".about")).not.toBeNull();
  });
});

describe("Dina värden (#777)", () => {
  const field = (sport: string, k: string) => root.querySelector(`.own-values input[name=${sport}-${k}]`);

  it("gives a plan without a test the athlete's own heart rates and paces", async () => {
    const store = await mount();
    await click(byText(".welcome button", "Gör ett schema själv"));
    await change(root.querySelector(".request select[name=level]"), "recreational");
    await change(field("run", "max_hr"), "191", "input");
    await change(field("run", "lt1_hr"), "176", "input");
    await change(field("run", "lt2_hr"), "158", "input");
    await click(byText(".request button", "Gör schemat"));
    expect(root.querySelector(".request [role=alert]")!.textContent)
      .toBe("Löpning: pulserna ska stiga från vilopuls till LT1, LT2 och maxpuls.");
    await change(field("run", "lt1_hr"), "158", "input");
    await change(field("run", "lt2_hr"), "176", "input");
    await change(field("run", "lt2"), "4:25", "input");
    await click(byText(".request button", "Gör schemat"));
    const p = store.plan()!;
    expect(p.request.capacity).toEqual([{ sport: "run", tested: null, source: "self", anchors: {
      max_hr: 191, resting_hr: null, lt1_hr: 158, lt2_hr: 176, lt1_speed_kmh: null, lt2_speed_kmh: 3600 / 265, sport: "run" } }]);
    expect(p.targets.run.Z4).toMatchObject({ hr_low: 167, hr_high: 176, estimated: false });
    expect(p.plan.notes.map((n) => n.key)).not.toContain("plan.note.no_test");
    await click(byText(".tabs button", "Schema"));
    expect(root.querySelector(".zone-sport")!.textContent).toContain("167–176");
    expect(root.querySelector(".zone-sport")!.textContent).toContain("Egna värden");
  });

  it("shows the coach's test and replaces it only when the athlete asks", async () => {
    const plan = demoPlan();
    const store = await mount(plan);
    await click(byText(".tabs button", "Schema"));
    expect(root.querySelector(".zone-sport")!.textContent).toContain("Uppmätt i test");
    await click(byText(".overview button, button", "Ändra mina värden"));
    expect(root.querySelector(".values")).not.toBeNull();
    expect(field("run", "max_hr")).toBeNull();
    expect(byText(".values-sport", "Uppmätt i test")).toBeTruthy();
    vi.stubGlobal("confirm", () => false);
    await click(byText(".values button", "Skriv in egna värden i stället"));
    expect(field("run", "max_hr")).toBeNull();
    vi.stubGlobal("confirm", () => true);
    await click(byText(".values button", "Skriv in egna värden i stället"));
    await change(field("run", "max_hr"), "195", "input");
    await click(byText(".values button", "Spara värdena"));
    expect(root.querySelector(".toast")!.textContent).toBe("Värdena är sparade.");
    const p = store.plan()!;
    expect(p.plan.id).toBe(plan.plan.id);
    expect(p.plan.weeks).toEqual(plan.plan.weeks);
    expect(p.request.capacity.map((c) => c.source)).toEqual(["self"]);
    expect(p.targets.run.Z5).toMatchObject({ hr_low: 176, hr_high: 195, estimated: true });
    expect(root.querySelector(".zone-sport")!.textContent).toContain("Egna värden");
  });

  it("is not offered for the example plan", async () => {
    await mount(demoPlan(), true);
    await click(byText(".tabs button", "Schema"));
    expect(byText("button", "Ändra mina värden")).toBeUndefined();
  });
});
