// @vitest-environment happy-dom
// A plan written outside the engine (plan.source, e.g. a schedule week by week): its workouts keep their own titles,
// and "Mina tider" never rebuilds it with the engine.
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../src/App";
import { engine } from "../src/engine";
import { setLang } from "../src/i18n";
import { readPlanText } from "../src/model/link";
import { fromEngine, planTitle } from "../src/model/plan";
import { memoryStore } from "../src/model/storage";
import type { StoredPlan } from "../src/model/types";
import { demoPlan, demoText } from "./helpers";

const NOW = new Date("2026-11-25T08:00:00Z");
let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
});

/** The example plan as a schedule would write it: a source, and the first workout with its own title. */
function schedulePlan(): StoredPlan {
  const p = demoPlan();
  const weeks = p.plan.weeks.map((wk, i) =>
    i === 0 ? { ...wk, workouts: wk.workouts.map((w, j) => (j === 0 ? { ...w, name: "Löp 5x1000 m (4:00)" } : w)) } : wk);
  return { ...p, plan: { ...p.plan, source: "schedule", weeks } };
}

async function mount(plan: StoredPlan) {
  const store = memoryStore();
  store.setPlan(plan);
  store.setSettings({ lang: "sv" });
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={engine} now={() => NOW} timeZone="Europe/Stockholm" tab="times"
      loadDemo={async () => demoText()} />, root);
  });
  return store;
}

describe("a plan written outside the engine", () => {
  it("reads from a file with its source and titles", () => {
    const p = readPlanText(JSON.stringify(schedulePlan()));
    expect(fromEngine(p)).toBe(false);
    expect(fromEngine(demoPlan())).toBe(true);
    expect(planTitle(p, p.plan.weeks[0].workouts[0])).toBe("Löp 5x1000 m (4:00)");
    expect(planTitle(p, p.plan.weeks[0].workouts[1])).toBe(planTitle(demoPlan(), demoPlan().plan.weeks[0].workouts[1]));
  });

  it("shows my times without a form, and the plan stays as it is", async () => {
    const plan = schedulePlan();
    const store = await mount(plan);
    expect(root.querySelector(".times form")).toBeNull();
    expect(root.querySelector(".times [role=status]")!.textContent).toContain("vecka för vecka");
    expect(root.querySelectorAll(".times .time-rows li")).toHaveLength(7);
    expect(store.plan()).toEqual(plan);
  });

  it("is never rebuilt by the engine", () => {
    expect(() => engine.rebuild(schedulePlan(), { slots: [], unavailable: [], from: "2026-11-26" }))
      .toThrow("outside the engine");
  });
});
