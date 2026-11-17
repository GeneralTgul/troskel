// @vitest-environment happy-dom
/** Ticking off, the sessions outside the plan, the week in numbers, the box for the day, the season strip and the
 * wall mode, as the athlete sees them. */
import { render } from "preact";
import { act } from "preact/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App, type Tab } from "../src/App";
import { stub } from "../src/engine";
import { setLang } from "../src/i18n";
import { memoryStore, type Store } from "../src/model/storage";
import type { StoredPlan } from "../src/model/types";
import { WALL_IDLE_MS } from "../src/model/wall";
import { demoPlan, demoText } from "./helpers";

const TZ = "Europe/Stockholm";
const THURSDAY = new Date("2026-11-26T08:00:00Z"); // the hills in the demo's second week
let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
  vi.useRealTimers();
});

async function mount(o: { plan?: StoredPlan; tab?: Tab; now?: Date; store?: Store; lang?: "sv" | "en";
  keepAwake?: () => () => void; canKeepAwake?: boolean } = {}) {
  const store = o.store ?? memoryStore();
  if (!o.store) {
    store.setPlan(o.plan ?? demoPlan());
    store.setSettings({ lang: o.lang ?? "sv" });
  }
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={stub} now={() => o.now ?? THURSDAY} timeZone={TZ} tab={o.tab}
      loadDemo={async () => demoText()} keepAwake={o.keepAwake} canKeepAwake={o.canKeepAwake} />, root);
  });
  return store;
}

const click = async (el: Element | null | undefined) => {
  expect(el).toBeTruthy();
  await act(async () => { (el as HTMLElement).click(); });
};
const byText = (sel: string, text: string, from: ParentNode = root) =>
  [...from.querySelectorAll(sel)].find((e) => e.textContent?.includes(text)) ?? null;
const type = async (el: Element | null, value: string, event = "input") => {
  await act(async () => {
    (el as HTMLInputElement).value = value;
    el!.dispatchEvent(new Event(event, { bubbles: true }));
  });
};

describe("ticking off today's workout", () => {
  it("marks it done, keeps it on the device and counts it in the week's numbers", async () => {
    const store = await mount();
    const row = () => root.querySelector(".numbers .sum-row[data-sport=run]")!;
    expect(row().textContent).toMatch(/^Löpning0:\d\d av \d:\d\d$/);
    const before = row().textContent;
    await click(byText(".workout.big .status button", "Gjort"));
    expect(root.querySelector(".status button.on")!.textContent).toBe("Gjort");
    expect(Object.values(store.log().marks)).toEqual([{ status: "done" }]);
    expect(row().textContent).not.toBe(before);
    await click(byText(".workout.big .status button", "Gjort"));          // a second tap takes it away
    expect(store.log().marks).toEqual({});
  });

  it("asks for the minutes of a partly done workout", async () => {
    const store = await mount();
    await click(byText(".workout.big .status button", "Delvis"));
    const input = root.querySelector<HTMLInputElement>(".status-minutes input")!;
    expect(Number(input.value) % 5).toBe(0);
    await type(input, "25", "change");
    expect(Object.values(store.log().marks)).toEqual([{ status: "partial", minutes: 25 }]);
  });

  it("adds a session outside the plan as \"+ name\", counted in its sport's row (#774)", async () => {
    const store = await mount();
    await click(byText("button", "+ Lägg till ett pass"));
    await type(root.querySelector(".extra-form input[type=text]"), "Yoga");
    // Any whole minute: step 5 from min 1 refused 60 ("the nearest valid values are 56 and 61", #774).
    expect(root.querySelector(".extra-form input[type=number]")!.getAttribute("step")).toBe("1");
    await type(root.querySelector(".extra-form input[type=number]"), "40");
    await click(root.querySelector(".extra-form button[type=submit]"));
    expect(store.log().extras).toMatchObject([{ date: "2026-11-26", name: "Yoga", minutes: 40, sport: "run" }]);
    expect(byText(".extra-list li", "+ Yoga")).not.toBeNull();
    expect(root.querySelector(".numbers .sum-row.extra")).toBeNull();
    expect(root.querySelector(".numbers .sum-row[data-sport=run] .sum-val")!.textContent).toMatch(/^0:40 av /);
    await click(root.querySelector(".extra-list button"));
    expect(store.log().extras).toEqual([]);
  });

  it("turns the bar green when the week's plan is reached", async () => {
    const p = demoPlan();
    const store = memoryStore();
    store.setPlan(p);
    store.setSettings({ lang: "sv" });
    store.setLog({ marks: Object.fromEntries(p.plan.weeks[1].workouts.map((w) => [w.key, { status: "done" as const }])), extras: [] });
    await mount({ store, now: new Date("2026-11-29T08:00:00Z") });
    expect(root.querySelector(".numbers .sum-row.reached")).not.toBeNull();
  });
});

describe("the box for the day", () => {
  it("has the purpose, the feel and the tips", async () => {
    await mount();
    const guide = root.querySelector(".workout.big .guide")!;
    expect(guide.textContent).toContain("Varför.");
    expect(guide.querySelector(".feel")!.textContent).toBe("Känsla. Z5, ansträngning 8 av 10");
    expect(guide.querySelectorAll(".tips li").length).toBeGreaterThan(0);
  });

  it("has ten drills on a technique swim, in English too", async () => {
    const fx = JSON.parse(gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/engine.json.gz"))).toString("utf-8"));
    const p: StoredPlan = fx.plans["triathlon-css"];
    const day = p.plan.weeks.flatMap((wk) => wk.workouts).find((w) => w.template_id === "swim_technique")!.date;
    await mount({ plan: p, now: new Date(`${day}T08:00:00Z`), lang: "en" });
    const card = [...root.querySelectorAll(".today .col:first-child .workout.big")]
      .find((c) => c.querySelector(".drills"))!;
    expect(card.querySelectorAll(".drills li")).toHaveLength(10);
    expect(card.querySelector(".guide h4:last-of-type")!.textContent).toBe("Drills to choose from");
  });
});

describe("the week board", () => {
  it("opens a day with its box, marks a past workout and shows the mark on the board", async () => {
    const store = await mount({ tab: "week" });
    const monday = root.querySelectorAll(".board .day")[0];
    await click(monday);
    const sheet = root.querySelector(".sheet[role=dialog]")!;
    expect(sheet.querySelector(".guide")).not.toBeNull();
    await click(byText("button", "Missat", sheet));
    expect(Object.values(store.log().marks)).toEqual([{ status: "missed" }]);
    await click(sheet.querySelector(".sheet-head button"));
    expect(root.querySelector(".sheet")).toBeNull();
    expect(root.querySelectorAll(".board .day")[0].querySelector(".st-mark.st-missed")!.textContent).toBe("Missat");
  });

  it("does not tick off a day that has not come", async () => {
    await mount({ tab: "week" });
    await click(root.querySelectorAll(".board .day")[6]);
    expect(root.querySelector(".sheet .guide")).not.toBeNull();
    expect(root.querySelector(".sheet .status")).toBeNull();
  });

  it("is a row per day with the workouts as pills, the state at the right, and the next race beside", async () => {
    await mount({ tab: "week" });
    const days = root.querySelectorAll(".board .day");
    expect(days[0].querySelector(".pill .sport-icon")).not.toBeNull();
    expect(root.querySelector(".board .day.is-today > .st-mark.st-today")!.textContent).toBe("I dag");
    expect(root.querySelector(".week .races .race-days")!.textContent).toMatch(/^\d+ dagar$/);
    expect(root.querySelector(".week .week-season .strip")).not.toBeNull();
  });

  it("closes the day with Escape and shows the week's numbers", async () => {
    await mount({ tab: "week" });
    expect(root.querySelector(".week .numbers .sum-row")).not.toBeNull();
    await click(root.querySelectorAll(".board .day")[1]);
    await act(async () => { globalThis.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
    expect(root.querySelector(".sheet")).toBeNull();
  });
});

describe("the season strip", () => {
  it("stands on today with a cell per week up to the goal, the easy weeks green and a line at today", async () => {
    await mount();
    const strip = root.querySelector(".today .season .strip")!;
    expect(strip.querySelectorAll(".strip-weeks i")).toHaveLength(12);
    expect(strip.querySelectorAll(".strip-weeks i.easy")).toHaveLength(2);
    expect([...strip.querySelectorAll(".strip-name")].map((e) => e.textContent)).toEqual(["Grund", "Uppb.", "Topp", "Nedtr."]);
    expect((strip.querySelector(".strip-now") as HTMLElement).style.left).toMatch(/^12\.\d+%$/);
    expect(root.querySelector(".today .season")!.textContent).toContain("Fram till Stadsvarvet, 6 feb");
  });
});

describe("the wall mode", () => {
  it("is switched on under About, keeps the screen on, and goes back to today and this week after 3 minutes", async () => {
    vi.useFakeTimers();
    const release = vi.fn();
    const keepAwake = vi.fn(() => release);
    const store = await mount({ tab: "about", keepAwake });
    await click(byText("label.switch", "väggläget")!.querySelector("input"));
    expect(store.settings().wall).toBe(true);
    expect(keepAwake).toHaveBeenCalledTimes(1);

    await click(byText(".tabs button", "Veckan"));
    await click(root.querySelector(".week-head button:last-child"));        // next week
    expect(root.querySelector(".week-title h2")!.textContent).toBe("Vecka 49");
    await act(async () => { vi.advanceTimersByTime(WALL_IDLE_MS - 1000); });
    await act(async () => { globalThis.dispatchEvent(new Event("pointerdown")); });   // a touch: the time starts again
    await act(async () => { vi.advanceTimersByTime(WALL_IDLE_MS - 1000); });
    expect(root.querySelector(".week")).not.toBeNull();
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(root.querySelector(".today")).not.toBeNull();
    await click(byText(".tabs button", "Veckan"));
    expect(root.querySelector(".week-title h2")!.textContent).toBe("Vecka 48");

    await click(byText(".tabs button", "Om appen"));
    await click(byText("label.switch", "väggläget")!.querySelector("input"));
    expect(release).toHaveBeenCalled();
    expect(store.settings().wall).toBe(false);
  });

  it("says so when the device cannot keep the screen on", async () => {
    const store = memoryStore();
    store.setPlan(demoPlan());
    store.setSettings({ lang: "sv", wall: true });
    await mount({ store, tab: "about", keepAwake: () => () => {}, canKeepAwake: false });
    expect(root.querySelector(".about .notice")!.textContent).toContain("kan inte hålla skärmen tänd");
  });
});
