// @vitest-environment happy-dom
// The dates that do not work (#846): once "Mina tider" rebuilt the plan without them, they are no longer waiting times
// but store.unavailable. They follow the plan in what the phone gives the coach (share.ts) and in the athlete's own
// copy (backup.ts), so that the next rebuild, on a new phone or in Tröskel, drops their workouts again.
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { engine } from "../src/engine";
import { setLang } from "../src/i18n";
import { readCopyText } from "../src/model/backup";
import { athleteFile, athleteLink, athletePayload, readAthleteLink, type AthletePayload } from "../src/model/share";
import { memoryStore, type Store } from "../src/model/storage";
import { demoPlan, demoText } from "./helpers";

const NOW = new Date("2026-11-25T08:00:00Z");
const TODAY = "2026-11-25";
let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
  vi.unstubAllGlobals();
});

async function mount(store: Store, props: { tab?: "today" | "about"; download?: (n: string, t: string, ty: string) => void;
  incoming?: string } = {}) {
  store.setSettings({ ...store.settings(), lang: "sv" });
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={engine} now={() => NOW} timeZone="Europe/Stockholm" tab={props.tab}
      loadDemo={async () => demoText()} download={props.download} incoming={props.incoming} />, root);
  });
}
const click = async (el: Element | null | undefined) => {
  expect(el).toBeTruthy();
  await act(async () => { (el as HTMLElement).click(); });
};

/** The demo plan rebuilt from tomorrow without a day that has a workout, as "Mina tider" saves it. */
function rebuilt() {
  const p = demoPlan();
  const day = p.plan.weeks[2].workouts.find((w) => w.goal == null)!.date;
  const plan = engine.rebuild(p, { slots: p.request.availability, unavailable: [day], from: "2026-11-26" });
  expect(plan.plan.weeks.flatMap((w) => w.workouts).some((w) => w.date === day)).toBe(false);
  return { plan, day };
}

describe("the dates that do not work after a rebuild", () => {
  it("go in the payload, the coming ones only, and read back from the file and the link", () => {
    const { plan, day } = rebuilt();
    const out = athletePayload(plan, { marks: {}, extras: [] }, null, null, TODAY, [day, "2026-11-20"]);
    expect(out.off).toEqual([day]);
    expect("times" in out).toBe(false);
    expect("off" in athletePayload(plan, { marks: {}, extras: [] }, null, null, TODAY)).toBe(false);
    for (const text of [athleteFile(out), athleteLink(out)]) expect(readCopyText(text)!.off).toEqual([day]);
    // a copy from an app before #846 has none
    const { off: _, ...old } = out;
    expect(readCopyText(athleteFile(old as AthletePayload))!.off).toEqual([]);
  });

  it("follow the plan to the coach and into the athlete's copy from the app", async () => {
    const { plan, day } = rebuilt();
    const store = memoryStore();
    store.setPlan(plan, false);
    store.setUnavailable([day]);
    const download = vi.fn();
    const writeText = vi.fn(async (_: string) => {});
    vi.stubGlobal("navigator", { ...globalThis.navigator, clipboard: { writeText } });
    await mount(store, { tab: "about", download });
    await click([...root.querySelectorAll(".backup button")].find((b) => b.textContent?.includes("Spara en kopia")));
    const copy = JSON.parse(download.mock.calls[0][1]) as AthletePayload;
    expect(copy.off).toEqual([day]);
    const box = root.querySelector(".share")!;
    await click(box.querySelector("input[type=checkbox]"));
    await click([...box.querySelectorAll("button")][1]);
    expect(readAthleteLink(writeText.mock.calls[0][0]).off).toEqual([day]);
  });

  it("come back with the copy on a new phone, so that Mina tider drops them again", async () => {
    const { plan, day } = rebuilt();
    const copy = athletePayload(plan, { marks: {}, extras: [] }, null, null, TODAY, [day]);
    const store = memoryStore();
    await mount(store, { incoming: athleteLink(copy) });
    expect(store.plan()).toEqual(plan);
    expect(store.unavailable()).toEqual([day]);
    expect(store.times()).toBeNull();
  });
});
