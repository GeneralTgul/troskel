// @vitest-environment happy-dom
// A new test updates the plan on the phone, and nothing else does (#776): the coach's new link replaces the plan and
// keeps what the athlete ticked off and added; the athlete's own times wait under "Mina tider" as a choice; and the
// app never changes the workouts by itself.
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { App, TABS } from "../src/App";
import { engine } from "../src/engine";
import { setLang } from "../src/i18n";
import { encodeData } from "../src/model/link";
import { memoryStore, type Store } from "../src/model/storage";
import { keptTimes } from "../src/model/times";
import type { StoredPlan } from "../src/model/types";
import { demoPlan, demoText } from "./helpers";

const NOW = new Date("2026-11-25T08:00:00Z"); // a Wednesday in the demo's second week
let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
});

/** The demo plan after a new running test, as Tröskel sends it (resolve.retest): the same id and workouts, the new
 * targets, and the old ones standing until the test day. */
function retested(p: StoredPlan = demoPlan(), day = "2026-11-26"): StoredPlan {
  const bump = (n: number | null) => (n == null ? n : n + 3);
  const run = Object.fromEntries(Object.entries(p.targets.run ?? {}).map(([z, tg]) =>
    [z, tg && { ...tg, hr_low: bump(tg.hr_low), hr_high: bump(tg.hr_high) }]));
  return { ...p, targets: { ...p.targets, run }, targets_log: [...p.targets_log, { until: day, sport: "run", targets: p.targets.run ?? {} }] };
}

async function mount(store: Store, props: { tab?: (typeof TABS)[number]; incoming?: string; now?: Date } = {}) {
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={engine} now={() => props.now ?? NOW} timeZone="Europe/Stockholm" tab={props.tab ?? "times"}
      loadDemo={async () => demoText()} incoming={props.incoming} />, root);
  });
}
const link = (p: StoredPlan) => `https://example.com/app/#p=${encodeData(p)}`;
const click = async (el: Element | null) => {
  expect(el).not.toBeNull();
  await act(async () => { (el as HTMLElement).click(); });
};
const stored = () => {
  const s = memoryStore();
  s.setPlan(demoPlan());
  s.setSettings({ lang: "sv" });
  return s;
};

describe("a new test from the coach", () => {
  it("replaces the plan and keeps the marks and the sessions outside the plan", async () => {
    const store = stored();
    const first = demoPlan().plan.weeks[0].workouts[0];
    store.setLog({ marks: { [first.key]: { status: "done" } },
      extras: [{ id: "x1", date: first.date, sport: "other", name: "Yoga", minutes: 30 }] });
    const next = retested();
    await mount(store, { incoming: link(next), tab: "today" });
    expect(store.plan()).toEqual(next);
    expect(store.log().marks[first.key]).toEqual({ status: "done" });
    expect(store.log().extras.map((x) => x.name)).toEqual(["Yoga"]);
    expect(store.times()).toBeNull(); // the athlete had not changed the times: nothing to keep
  });

  it("keeps the athlete's own times as a choice, and Spara lays the new plan on them", async () => {
    const store = stored();
    await mount(store);
    await click(root.querySelectorAll<HTMLInputElement>(".time-rows input[type=checkbox]")[0]); // no Mondays
    await click(root.querySelector(".times button[type=submit]"));
    const own = store.plan()!.request.availability;
    expect(own.map((s) => s.weekday)).toEqual([1, 3, 5, 6]);
    render(null, root);

    const next = retested(); // the coach's plan has the times the coach has, Mondays included
    await mount(store, { incoming: link(next) });
    expect(store.plan()).toEqual(next);
    expect(store.times()).toMatchObject({ slots: own, kept: true });
    expect(root.querySelector(".times .notice")!.textContent).toContain("Dina egna tider står kvar");
    await click(root.querySelector(".times button[type=submit]"));
    const after = store.plan()!;
    expect(after.request.availability.map((s) => s.weekday)).toEqual([1, 3, 5, 6]);
    expect(after.targets).toEqual(next.targets); // the new test's values stay
    expect(after.targets_log).toEqual(next.targets_log);
    expect(store.times()).toBeNull();
  });

  it("keeps nothing for another plan, the same times or a plan written outside the engine", () => {
    const p = demoPlan();
    const moved = { ...p, request: { ...p.request, availability: p.request.availability.slice(1) } };
    expect(keptTimes(moved, p, "2026-11-25")).toMatchObject({ kept: true });
    expect(keptTimes(p, retested(p), "2026-11-25")).toBeNull();
    expect(keptTimes(moved, { ...p, plan: { ...p.plan, id: "other" } }, "2026-11-25")).toBeNull();
    expect(keptTimes({ ...moved, plan: { ...moved.plan, source: "schedule" } }, p, "2026-11-25")).toBeNull();
    expect(keptTimes(null, p, "2026-11-25")).toBeNull();
  });
});

describe("without a new test", () => {
  it("the app changes no workout by itself, however much later it is opened", async () => {
    const store = stored();
    const before = demoPlan();
    for (const tab of TABS) {
      await mount(store, { tab, now: new Date("2027-02-20T08:00:00Z") });
      render(null, root);
    }
    expect(store.plan()).toEqual(before);
  });
});
