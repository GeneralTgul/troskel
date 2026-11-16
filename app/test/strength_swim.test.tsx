// @vitest-environment happy-dom
/** The workout card for strength (no zones) and swimming (effort, or the pace per 100 m of a CSS test), #666 step 3. */
import { render } from "preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../src/i18n";
import { planTitle, weekMinutes, workoutMinutes } from "../src/model/plan";
import type { StoredPlan } from "../src/model/types";
import { WorkoutCard } from "../src/views/parts";

const fx: { plans: Record<string, StoredPlan> } = JSON.parse(
  gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/engine.json.gz"))).toString("utf-8"));
const all = (p: StoredPlan) => p.plan.weeks.flatMap((wk) => wk.workouts);
let root: HTMLElement | null = null;

function card(stored: StoredPlan, key: string): HTMLElement {
  root = document.createElement("div");
  document.body.appendChild(root);
  const w = all(stored).find((x) => x.key === key)!;
  render(<WorkoutCard stored={stored} w={w} big />, root);
  return root;
}

afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

describe("strength and swimming in the app", () => {
  it("shows strength without zones or targets, with its sport first", () => {
    setLang("sv");
    const p = fx.plans["run-strength"];
    const w = all(p).find((x) => x.sport === "strength")!;
    const el = card(p, w.key);
    expect(el.querySelector("h3")!.textContent).toMatch(/^Styrka: /);
    expect(el.querySelector(".targets")).toBeNull();
    expect(el.textContent).not.toMatch(/Z[1-5]/);
    expect(el.textContent).toContain("ansträngning");
    const run = all(p).find((x) => x.template_id === "easy")!;
    expect(planTitle(p, run)).toBe("Löpning: lugnt pass");
  });

  it("counts strength on top of the week's time", () => {
    const wk = fx.plans["run-strength"].plan.weeks[0];
    const endurance = wk.workouts.filter((w) => w.sport !== "strength").reduce((s, w) => s + workoutMinutes(w), 0);
    expect(weekMinutes(wk)).toBe(endurance);
  });

  it("shows swimming with the pace per 100 m on a CSS test, and on effort without one", () => {
    setLang("sv");
    const tri = fx.plans["triathlon-css"];
    const swim = all(tri).find((x) => x.template_id === "swim_intervals")!;
    expect(card(tri, swim.key).querySelector(".targets")!.textContent).toMatch(/\d:\d\d–\d:\d\d \/100 m/);
    render(null, root!);
    const plain = fx.plans["swim-without-test"];
    const easy = all(plain).find((x) => x.sport === "swim")!;
    const el = card(plain, easy.key);
    expect(el.querySelector(".targets")).toBeNull();
    expect(el.textContent).toContain("Inga mål i puls och tempo");
  });
});
