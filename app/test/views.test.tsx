// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App, TABS, type Tab } from "../src/App";
import { EngineMissing, engine, stub, type Engine, type NewAvailability } from "../src/engine";
import { weekday } from "../src/model/dates";
import { setLang } from "../src/i18n";
import { liveDemo } from "../src/model/demo";
import { memoryStore } from "../src/model/storage";
import type { StoredPlan } from "../src/model/types";
import { demoPlan, demoText, fixture } from "./helpers";

const NOW = new Date("2026-11-25T08:00:00Z"); // a Wednesday in the demo's second week
const TZ = "Europe/Stockholm";
let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
});

async function mount(props: Partial<Parameters<typeof App>[0]> & { plan?: StoredPlan | null; lang?: "sv" | "en" }) {
  const store = props.store ?? memoryStore();
  if (props.plan) store.setPlan(props.plan);
  store.setSettings({ lang: props.lang ?? "sv" });
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={props.engine ?? stub} now={() => NOW} timeZone={TZ} tab={props.tab}
      loadDemo={async () => demoText()} incoming={props.incoming} download={props.download} />, root);
  });
  return { store, root };
}

const click = async (el: Element | null) => {
  expect(el).not.toBeNull();
  await act(async () => { (el as HTMLElement).click(); });
};
const byText = (sel: string, text: string) =>
  [...root.querySelectorAll(sel)].find((e) => e.textContent?.includes(text)) ?? null;

describe("every view renders the example plan", () => {
  it.each(TABS.flatMap((tab) => (["sv", "en"] as const).map((lang) => [tab, lang] as [Tab, "sv" | "en"])))(
    "%s in %s", async (tab, lang) => {
      const errors = vi.spyOn(console, "error");
      await mount({ plan: demoPlan(), tab, lang });
      expect(root.querySelector("main")!.textContent!.length).toBeGreaterThan(50);
      expect(root.textContent).not.toMatch(/\bplan\.[a-z_]+\.|\bui\.[a-z_]+\./); // no key without a text
      expect(errors).not.toHaveBeenCalled();
      errors.mockRestore();
    });
});

describe("today", () => {
  it("shows today's workout large and tomorrow's", async () => {
    await mount({ plan: demoPlan() }); // 2026-11-25 is a Wednesday: rest in the demo, Thursday has a workout
    expect(root.querySelector(".today h2 .date")!.textContent).toBe("Onsdag 25 nov");
    expect(root.querySelector(".workout.big.rest")).not.toBeNull();
    expect(root.querySelectorAll(".today .col")[1].querySelector(".workout:not(.rest) h3")).not.toBeNull();
  });
  it("shows targets in heart rate and pace on a workout day", async () => {
    const p = demoPlan();
    const day = p.plan.weeks[1].workouts.find((w) => w.template_id !== "recovery" && w.date > "2026-11-25")!.date;
    await mount({ plan: p, now: undefined });
    // Move the clock to that day.
    render(null, root);
    await act(async () => {
      render(<App store={(() => { const s = memoryStore(); s.setPlan(p); s.setSettings({ lang: "sv" }); return s; })()} engine={stub}
        now={() => new Date(`${day}T08:00:00Z`)} timeZone={TZ} loadDemo={async () => ""} />, root);
    });
    const big = root.querySelector(".workout.big")!;
    expect(big.querySelector(".targets")!.textContent).toMatch(/slag\/min.*min\/km/);
  });
});

describe("the week", () => {
  it("has seven days from Monday, today marked, rest days plain", async () => {
    await mount({ plan: demoPlan(), tab: "week" });
    const days = root.querySelectorAll(".board .day");
    expect(days).toHaveLength(7);
    expect(days[0].querySelector(".dow")!.textContent).toBe("Mån");
    expect(days[2].classList.contains("is-today")).toBe(true);
    expect(days[2].classList.contains("is-rest")).toBe(true);
    expect(root.querySelector(".week-title h2")!.textContent).toBe("Vecka 48");
  });
  it("pages forward and back", async () => {
    await mount({ plan: demoPlan(), tab: "week" });
    await click(root.querySelector(".week-head button:last-child"));
    expect(root.querySelector(".week-title h2")!.textContent).toBe("Vecka 49");
    expect(root.querySelector(".is-today")).toBeNull();
    await click(byText("button", "Den här veckan"));
    expect(root.querySelector(".week-title h2")!.textContent).toBe("Vecka 48");
  });
  it("downloads the calendar from today", async () => {
    const download = vi.fn();
    await mount({ plan: demoPlan(), tab: "week", download });
    await click(byText("button", "kalendern"));
    const [name, text] = download.mock.calls[0];
    expect(name).toBe("troskel.ics");
    expect(text).not.toContain("DTSTART:20261124"); // yesterday's workout is left out
    expect(text).toContain("BEGIN:VEVENT");
  });
});

describe("the plan", () => {
  it("has a bar per week and the goals", async () => {
    await mount({ plan: demoPlan(), tab: "plan" });
    expect(root.querySelectorAll(".bars .bar")).toHaveLength(12);
    expect(root.querySelector(".bar.is-now .bar-label")!.textContent).toBe("2");
    expect(root.querySelector(".goal.prio-A")!.textContent).toContain("Stadsvarvet");
  });
});

describe("my times", () => {
  const save = () => click(root.querySelector(".times button[type=submit]"));

  it("are saved for the coach while there is no engine", async () => {
    const { store } = await mount({ plan: demoPlan(), tab: "times" });
    await click(root.querySelectorAll<HTMLInputElement>(".time-rows input[type=checkbox]")[0]);
    await save();
    expect(root.querySelector(".times [role=status]")!.textContent).toContain("när din coach skickar");
    expect(store.times()!.slots.map((s) => s.weekday)).toEqual([1, 3, 5, 6]);
    expect(store.plan()).toEqual(demoPlan()); // the plan is untouched
  });

  it("rebuild the plan from tomorrow with an engine", async () => {
    const calls: NewAvailability[] = [];
    const fake: Engine = {
      available: true, version: "fake",
      rebuild(stored, a) {
        calls.push(a);
        return { ...stored, request: { ...stored.request, availability: a.slots },
          plan: { ...stored.plan, engine_version: "fake" } };
      },
    };
    const { store } = await mount({ plan: demoPlan(), tab: "times", engine: fake });
    await save();
    expect(calls).toHaveLength(1);
    expect(calls[0].from).toBe("2026-11-26");
    expect(store.plan()!.plan.engine_version).toBe("fake");
    expect(store.times()).toBeNull();
    expect(root.textContent).toContain("omräknat från i morgon");
  });

  it("rebuild the plan with the real engine", async () => {
    const before = demoPlan();
    const { store } = await mount({ plan: demoPlan(), tab: "times", engine });
    await click(root.querySelectorAll<HTMLInputElement>(".time-rows input[type=checkbox]")[0]); // no Mondays
    await save();
    const after = store.plan()!;
    const all = (p: StoredPlan) => p.plan.weeks.flatMap((w) => w.workouts);
    const mondays = (p: StoredPlan) => all(p).filter((w) => w.date >= "2026-11-26" && weekday(w.date) === 0 && !w.goal);
    expect(all(after).filter((w) => w.date < "2026-11-26")).toEqual(all(before).filter((w) => w.date < "2026-11-26"));
    expect(mondays(before).length).toBeGreaterThan(0);
    expect(mondays(after)).toEqual([]);
    expect(after.request.availability.map((s) => s.weekday)).toEqual([1, 3, 5, 6]);
    expect(store.times()).toBeNull();
    expect(root.textContent).toContain("omräknat från i morgon");
  });

  it("are kept when the engine fails", async () => {
    const broken: Engine = { available: true, version: "x", rebuild: () => { throw new EngineMissing(); } };
    const { store } = await mount({ plan: demoPlan(), tab: "times", engine: broken });
    await save();
    expect(store.times()).not.toBeNull();
    expect(root.textContent).toContain("gick inte att räkna om");
  });

  it("the stub says not yet", () => {
    expect(stub.available).toBe(false);
    expect(() => stub.rebuild(demoPlan(), { slots: [], unavailable: [], from: "2026-11-26" })).toThrow("not yet");
  });
});

describe("loading", () => {
  it("starts with the welcome page and shows the example", async () => {
    const { store } = await mount({});
    expect(root.querySelector(".welcome")).not.toBeNull();
    await click(byText("button", "Visa ett exempel"));
    await act(async () => {});
    expect(store.isDemo()).toBe(true);
    expect(store.plan()).toEqual(liveDemo(demoPlan(), "2026-11-25"));
    expect(root.querySelector(".banner")).not.toBeNull();
  });

  it("reads the plan from the link the app was opened with", async () => {
    const read = vi.fn();
    const store = memoryStore();
    root = document.createElement("div");
    await act(async () => {
      render(<App store={store} engine={stub} now={() => NOW} timeZone={TZ} loadDemo={async () => ""}
        incoming={fixture("demo.link.txt").trim()} onIncomingRead={read} />, root);
    });
    expect(read).toHaveBeenCalled();
    expect(store.plan()).toEqual(demoPlan());
    expect(store.isDemo()).toBe(false);
  });

  it("deletes everything on the device", async () => {
    const { store } = await mount({ plan: demoPlan(), tab: "about" });
    vi.stubGlobal("confirm", () => true);
    await click(byText("button", "Ta bort all data"));
    vi.unstubAllGlobals();
    expect(store.plan()).toBeNull();
    expect(root.querySelector(".welcome")).not.toBeNull();
  });

  it("switches language", async () => {
    await mount({ plan: demoPlan(), tab: "about" });
    await click(byText("button", "English"));
    expect(root.querySelector(".tabs")!.textContent).toContain("Today");
  });
});
