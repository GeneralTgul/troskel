// @vitest-environment happy-dom
// The athlete's own copy (#790): the payload to the coach (#781) read back into the app, on the same or a new phone,
// and whether the browser may empty the storage (backup.storageState), with the reminder on *I dag*.
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { engine } from "../src/engine";
import { setLang } from "../src/i18n";
import { copyFileName, mergeLog, readCopyText, remindCopy, storageState, type StorageState } from "../src/model/backup";
import { PlanError } from "../src/model/link";
import { athleteFile, athleteLink, type AthletePayload } from "../src/model/share";
import { memoryStore, type Store } from "../src/model/storage";
import { demoPlan, demoText, fixture } from "./helpers";

const NOW = new Date("2026-11-25T08:00:00Z"); // the day of the fixture's export
const FIXTURE = (): AthletePayload => JSON.parse(fixture("athlete.troskel"));
let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
  vi.unstubAllGlobals();
});

async function mount(store: Store, props: {
  tab?: "today" | "about"; download?: (n: string, t: string, ty: string) => void; incoming?: string;
  state?: StorageState;
} = {}) {
  store.setSettings({ ...store.settings(), lang: "sv" });
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={engine} now={() => NOW} timeZone="Europe/Stockholm" tab={props.tab}
      loadDemo={async () => demoText()} download={props.download} incoming={props.incoming}
      storageState={props.state ? async () => props.state! : undefined} />, root);
  });
}
const click = async (el: Element | null | undefined) => {
  expect(el).toBeTruthy();
  await act(async () => { (el as HTMLElement).click(); });
};
const byText = (sel: string, text: string) => [...root.querySelectorAll(sel)].find((e) => e.textContent?.includes(text));
const paste = async (text: string) => {
  const input = root.querySelector<HTMLInputElement>(".welcome .paste input")!;
  await act(async () => {
    input.value = text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => { root.querySelector(".welcome .paste")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
};

describe("the copy read back", () => {
  it("reads the coach's file and link as a copy, and leaves a plan from the coach to the plan's reader", () => {
    const p = FIXTURE();
    for (const text of [athleteFile(p), athleteLink(p), `Min kopia: ${athleteLink(p)}`]) {
      const c = readCopyText(text)!;
      expect(c.plan.plan.id).toBe(p.plan.plan.id);
      expect(Object.keys(c.log.marks).sort()).toEqual(Object.keys(p.marks).sort());
      expect(c.log.extras).toEqual(p.extras);
      expect(c.times).toEqual(p.times ?? null);
      expect(c.exported).toBe(p.exported);
    }
    // the watch's marks come back as the athlete's: the health data is not in the copy
    const c = readCopyText(athleteFile(p))!;
    expect(Object.values(c.log.marks).some((m) => "auto" in m)).toBe(false);
    expect(readCopyText(demoText())).toBeNull();
    expect(readCopyText("https://generaltgul.github.io/troskel/app/#p=abc")).toBeNull();
    expect(() => readCopyText("https://generaltgul.github.io/troskel/app/#a=!!")).toThrow(PlanError);
    expect(() => readCopyText(JSON.stringify({ ...p, format: 9 }))).toThrow(PlanError);
  });

  it("puts the copy's marks over the phone's and keeps each session outside the plan once", () => {
    const x = { id: "a", date: "2026-11-20", sport: "other", minutes: 30, name: "Yoga" };
    const y = { id: "b", date: "2026-11-21", sport: "other", minutes: 20, name: "Rörlighet" };
    expect(mergeLog({ marks: { k1: { status: "missed" }, k2: { status: "done" } }, extras: [x, y] },
      { marks: { k1: { status: "done" } }, extras: [x] }))
      .toEqual({ marks: { k1: { status: "done" }, k2: { status: "done" } }, extras: [x, y] });
    expect(copyFileName("2026-11-25")).toBe("troskel-kopia-2026-11-25.troskel");
  });
});

describe("a new phone", () => {
  it("gets the plan, the marks, the sessions outside the plan and the waiting times from the copy's link", async () => {
    const p = FIXTURE();
    const store = memoryStore();
    await mount(store);
    await paste(athleteLink(p));
    expect(root.querySelector(".toast")!.textContent).toBe("Kopian är inläst.");
    expect(store.plan()).toEqual(p.plan);
    expect(Object.keys(store.log().marks).sort()).toEqual(Object.keys(p.marks).sort());
    expect(store.log().extras).toEqual(p.extras);
    expect(store.times()).toEqual(p.times ?? null);
    expect(store.isDemo()).toBe(false);
  });

  it("opens the copy's link as the app's address", async () => {
    const p = FIXTURE();
    const store = memoryStore();
    await mount(store, { incoming: athleteLink(p) });
    expect(store.plan()!.plan.id).toBe(p.plan.plan.id);
  });

  it("asks before a copy of another plan replaces the phone's", async () => {
    const p = FIXTURE();
    const store = memoryStore();
    const other = demoPlan();
    other.plan.id = "app-other001";
    store.setPlan(other, false);
    vi.stubGlobal("confirm", () => false);
    await mount(store, { incoming: athleteLink(p) });
    expect(store.plan()!.plan.id).toBe("app-other001");
    render(null, root);
    vi.stubGlobal("confirm", () => true);
    await mount(store, { incoming: athleteLink(p) });
    expect(store.plan()!.plan.id).toBe(p.plan.plan.id);
  });
});

describe("Om appen: En kopia av schemat", () => {
  it("saves the copy without the coach's consent and remembers the day", async () => {
    const store = memoryStore();
    store.setPlan(FIXTURE().plan, false);
    const download = vi.fn();
    await mount(store, { tab: "about", download, state: "home" });
    const box = root.querySelector(".backup")!;
    expect(box.textContent).toContain("kan tömma den när telefonen får ont om plats");
    expect(box.textContent).toContain("Ingen kopia sparad än.");
    await click(byText(".backup button", "Spara en kopia"));
    const [name, text] = download.mock.calls[0];
    expect(name).toBe("troskel-kopia-2026-11-25.troskel");
    expect(readCopyText(text)!.plan).toEqual(FIXTURE().plan);
    expect(store.settings().copied).toBe("2026-11-25");
    expect(root.querySelector(".backup")!.textContent).toContain("Senaste kopian: 25 nov.");
  });

  it("is not there for the example", async () => {
    const store = memoryStore();
    store.setPlan(demoPlan(), true);
    await mount(store, { tab: "about" });
    expect(root.querySelector(".backup")).toBeNull();
  });
});

describe("whether the storage may be emptied", () => {
  it("asks for persistent storage only in a home screen web app", async () => {
    const persist = vi.fn(async () => true);
    expect(await storageState({ native: true, standalone: false })).toBe("native");
    expect(await storageState({ native: false, standalone: false, storage: { persisted: async () => true, persist } })).toBe("kept");
    expect(await storageState({ native: false, standalone: false, storage: { persisted: async () => false, persist } })).toBe("tab");
    expect(persist).not.toHaveBeenCalled();
    expect(await storageState({ native: false, standalone: true, storage: { persisted: async () => false, persist } })).toBe("kept");
    expect(await storageState({ native: false, standalone: true, storage: { persisted: async () => false, persist: async () => false } })).toBe("home");
    expect(await storageState({ native: false, standalone: true })).toBe("home");
    expect(await storageState({ native: false, standalone: false, storage: { persisted: async () => { throw new Error("x"); } } })).toBe("tab");
  });

  it("reminds of a copy when the storage may go and the last copy is two weeks old", () => {
    expect(remindCopy("native", undefined, "2026-11-25")).toBe(false);
    expect(remindCopy("kept", undefined, "2026-11-25")).toBe(false);
    expect(remindCopy(null, undefined, "2026-11-25")).toBe(false);
    expect(remindCopy("home", undefined, "2026-11-25")).toBe(true);
    expect(remindCopy("tab", "2026-11-20", "2026-11-25")).toBe(false);
    expect(remindCopy("tab", "2026-11-11", "2026-11-25")).toBe(true);
  });

  it("shows the reminder on I dag, and it leads to the copy", async () => {
    const store = memoryStore();
    store.setPlan(FIXTURE().plan, false);
    await mount(store, { tab: "today", state: "tab" });
    const note = root.querySelector(".copy-remind")!;
    expect(note.textContent).toContain("i Safari efter sju dagar utan besök");
    await click(note.querySelector("button"));
    expect(root.querySelector(".backup")).not.toBeNull();
    render(null, root);
    store.setSettings({ ...store.settings(), copied: "2026-11-24" });
    await mount(store, { tab: "today", state: "tab" });
    expect(root.querySelector(".copy-remind")).toBeNull();
  });
});
