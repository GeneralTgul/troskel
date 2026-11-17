// @vitest-environment happy-dom
// The athlete's changes of single workouts (#777, step 3): the app's rules (engine/edits.ts) against Python's
// (lopanalys.plan.edits) with test/fixtures/edits.json.gz from app/scripts/make_fixtures.py, the changes in the day
// sheet (model/adjust.ts, views/Adjust.tsx), and that they stay through new times and a new link from the coach.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../src/App";
import { applyEdits, engine, rebuild, rollForward, shorten, shortest } from "../src/engine";
import { normRequest } from "../src/engine/models";
import { setLang } from "../src/i18n";
import {
  editOf, keptEdits, moveDays, moveWorkout, original, shortenTo, shortenWorkout, swapWith, swapWorkouts, undoEdit,
} from "../src/model/adjust";
import { encodeData, readPlanText } from "../src/model/link";
import { workoutMinutes } from "../src/model/plan";
import { expandShort, type ShortPayload } from "../src/model/short";
import { memoryStore } from "../src/model/storage";
import type { StoredPart, StoredPlan, StoredPlanBody, StoredSlot, StoredWorkout } from "../src/model/types";
import { demoPlan } from "./helpers";

interface Fixtures {
  shorten: { template: string; step: number; minutes: number; parts: StoredPart[]; out: StoredPart[]; shortest?: number }[];
  apply: { name: string; before: StoredPlan; after: StoredPlan }[];
  rebuild: { name: string; plan: string; slots: StoredSlot[]; from: string; result: StoredPlan }[];
  roll: { today: string; plan: StoredPlanBody }[];
  links: Record<string, ShortPayload>;
  coach: { name: string; old: string; incoming: StoredPlan; result: StoredPlan }[];
  stored: Record<string, StoredPlan>;
}

const fx: Fixtures = JSON.parse(
  gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/edits.json.gz"))).toString("utf-8"));

const all = (p: StoredPlan) => p.plan.weeks.flatMap((wk) => wk.workouts);
const byKey = (p: StoredPlan, key: string) => all(p).find((w) => w.key === key)!;

describe("the edits against Python", () => {
  it("shortens every template as Python does", () => {
    for (const c of fx.shorten) {
      expect(shorten(c.parts, c.minutes), `${c.template} ${c.step} ${c.minutes}`).toEqual(c.out);
      if (c.shortest != null) expect(shortest({ parts: c.parts } as StoredWorkout), c.template).toBe(c.shortest);
    }
  });

  it("lays moves, swaps and shortenings on the plans as Python does", () => {
    for (const c of fx.apply) {
      const out = applyEdits(c.before.plan, normRequest(c.before.request));
      expect(out, c.name).toEqual(c.after.plan);
      expect(applyEdits(out, normRequest(c.before.request)), c.name).toEqual(out);   // again: no change
    }
  });

  it("keeps the edits through new times, Förnya, the short link and the coach's new plan", () => {
    for (const c of fx.rebuild) {
      const out = rebuild(fx.stored[c.plan], { slots: c.slots, unavailable: [], from: c.from });
      expect(out, c.name).toEqual(c.result);
      expect(out.plan.edits?.length, c.name).toBeGreaterThan(0);
    }
    const rolling = fx.stored.rolling;
    for (const c of fx.roll) expect(rollForward(rolling.plan, rolling.request, c.today), c.today).toEqual(c.plan);
    for (const [name, payload] of Object.entries(fx.links)) {
      expect(payload.format, name).toBe(2);
      expect(expandShort(payload), name).toEqual(fx.stored[name]);
    }
    for (const c of fx.coach) expect(keptEdits(fx.stored[c.old], c.incoming), c.name).toEqual(c.result);
  });
});

describe("changing a workout in the app", () => {
  const plan = () => demoPlan();
  const week2 = (p: StoredPlan) => p.plan.weeks[1].workouts.filter((w) => !w.goal);

  it("moves, swaps, shortens and undoes, and the original comes back", () => {
    const p = plan();
    const [a, b, c] = week2(p);
    const today = p.plan.weeks[1].monday;
    expect(moveDays(p, a, today)).not.toContain(a.date);
    expect(swapWith(p, a, today).map((x) => x.key)).toContain(b.key);
    const moved = moveWorkout(p, a.key, moveDays(p, a, today)[2]);
    expect(byKey(moved, a.key).date).toBe(moveDays(p, a, today)[2]);
    expect(editOf(moved, a.key)).toEqual({ key: a.key, date: moveDays(p, a, today)[2] });
    const swapped = swapWorkouts(p, a, b);
    expect([byKey(swapped, a.key).date, byKey(swapped, b.key).date]).toEqual([b.date, a.date]);
    const lengths = shortenTo(p, c);
    expect(lengths[0]).toBeLessThan(workoutMinutes(c));
    const short = shortenWorkout(swapped, c.key, lengths[lengths.length - 1]);
    expect(workoutMinutes(byKey(short, c.key))).toBeLessThanOrEqual(lengths[lengths.length - 1]);
    // A longer length again comes from the original, and undo gives the engine's workout back.
    const longer = shortenWorkout(short, c.key, lengths[0]);
    expect(workoutMinutes(byKey(longer, c.key))).toBe(lengths[0]);
    expect(byKey(undoEdit(longer, c.key), c.key)).toEqual(c);
    expect(original(longer, c.key)).toEqual(c);
    const back = undoEdit(undoEdit(undoEdit(longer, a.key), b.key), c.key);
    expect(back.plan.edits).toBeUndefined();
    expect(back.plan).toEqual(p.plan);
    // Moving it back to its own day is no edit.
    expect(moveWorkout(moved, a.key, a.date).plan).toEqual(p.plan);
  });

  it("keeps the edits when the athlete saves new times", () => {
    const p = plan();
    const [a, b] = week2(p).slice(-2);
    const swapped = swapWorkouts(p, a, b);
    const slots = p.request.availability.map((s) => ({ ...s, max_minutes: s.max_minutes + 10 }));
    const out = engine.rebuild(swapped, { slots, unavailable: [], from: p.plan.weeks[0].monday });
    expect([byKey(out, a.key).date, byKey(out, b.key).date]).toEqual([b.date, a.date]);
  });
});

describe("the day sheet", () => {
  let root: HTMLElement;
  afterEach(() => {
    if (root) render(null, root);
    document.body.innerHTML = "";
    setLang("sv");
  });
  const NOW = new Date("2026-11-23T08:00:00Z");     // the Monday of the demo's second week

  async function mount(store = memoryStore(), incoming?: string) {
    root = document.createElement("div");
    document.body.append(root);
    await act(async () => {
      render(<App store={store} engine={engine} now={() => NOW} timeZone="Europe/Stockholm" tab="week"
        loadDemo={async () => ""} incoming={incoming} />, root);
    });
    return store;
  }
  const click = async (el: Element | null | undefined) => {
    expect(el).toBeTruthy();
    await act(async () => { (el as HTMLElement).click(); });
  };
  const choose = async (sel: HTMLSelectElement, value: string) => {
    await act(async () => {
      sel.value = value;
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    });
  };
  const button = (text: string) => [...root.querySelectorAll("button")].find((b) => b.textContent?.trim() === text);

  it("moves a workout to another day and keeps it when the coach sends the plan again", async () => {
    const store = memoryStore();
    store.setPlan(demoPlan());
    store.setSettings({ lang: "sv" });
    await mount(store);
    const p = demoPlan();
    const w = p.plan.weeks[1].workouts.find((x) => x.date > "2026-11-23" && !x.goal)!;
    const day = [...root.querySelectorAll<HTMLElement>("li.day")].find((li) => li.getAttribute("aria-label")?.includes(
      String(Number(w.date.slice(8)))))!;
    await click(day);
    await click(button("Ändra passet"));
    const target = moveDays(p, w, "2026-11-23")[0];
    await choose(root.querySelector(".adjust-box select") as HTMLSelectElement, target);
    await click(button("Flytta"));
    const saved = store.plan()!;
    expect(byKey(saved, w.key).date).toBe(target);
    expect(editOf(saved, w.key)?.date).toBe(target);
    expect(root.textContent).toContain("Passet är ändrat.");

    // The coach's new link with the same id (a new test) lays the edit on the new plan.
    render(null, root);
    await mount(store, `https://example.com/app/#p=${encodeData(demoPlan())}`);
    expect(byKey(store.plan()!, w.key).date).toBe(target);
  });

  it("offers no changes in the example plan", async () => {
    const store = memoryStore();
    store.setPlan(readPlanText(JSON.stringify(demoPlan())), true);
    store.setSettings({ lang: "sv" });
    await mount(store);
    await click(root.querySelector("li.day"));
    expect(button("Ändra passet")).toBeUndefined();
  });
});
