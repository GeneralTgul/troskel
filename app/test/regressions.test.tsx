// @vitest-environment happy-dom
// Bugs found by reading the app (#844), each with the test that keeps it from coming back.
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { engine, stub, type Engine } from "../src/engine";
import { setLang } from "../src/i18n";
import { mergeLog, readCopyText } from "../src/model/backup";
import { escape, toIcs } from "../src/model/ics";
import { decodeLink, encodeData } from "../src/model/link";
import { checkLog } from "../src/model/log";
import { qrDetector, qrScanner } from "../src/model/qr";
import { emptyForm, planFromForm, renewOwn } from "../src/model/request";
import { athleteFile, athleteLink, athletePayload } from "../src/model/share";
import { memoryStore, type PendingTimes } from "../src/model/storage";
import { browserSave, nativeSave, safeName, saveFile, type NativeIo } from "../src/model/save";
import { checkTimes, MAX_MINUTES, slotsFromDays } from "../src/model/times";
import type { StoredPlan } from "../src/model/types";
import { demoPlan, demoText } from "./helpers";

const TZ = "Europe/Stockholm";
let now = new Date("2026-11-25T08:00:00Z"); // a Wednesday in the demo's second week
let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
  now = new Date("2026-11-25T08:00:00Z");
  vi.useRealTimers();
});

async function mount(props: Partial<Parameters<typeof App>[0]> & { plan?: StoredPlan | null } = {}) {
  const store = props.store ?? memoryStore();
  if (props.plan) store.setPlan(props.plan);
  store.setSettings({ ...store.settings(), lang: "sv" });
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={props.engine ?? stub} now={() => now} timeZone={TZ} tab={props.tab}
      loadDemo={async () => demoText()} incoming={props.incoming} download={props.download}
      keepAwake={props.keepAwake} />, root);
  });
  return store;
}

const click = async (el: Element | null | undefined) => {
  expect(el).toBeTruthy();
  await act(async () => { (el as HTMLElement).click(); });
};
/** Lets what waits on a promise (a file being saved) finish and draw. */
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const byText = (sel: string, text: string) =>
  [...root.querySelectorAll(sel)].find((e) => e.textContent?.includes(text)) ?? null;
const input = async (el: HTMLInputElement, value: string) => {
  await act(async () => {
    el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
};

describe("the calendar file", () => {
  it("escapes a semicolon, as RFC 5545 asks and as Python's file does", () => {
    expect(escape("Sub 3; negative split, ok\\")).toBe("Sub 3\\; negative split\\, ok\\\\");
    const p = demoPlan();
    const race = p.plan.weeks.flatMap((w) => w.workouts).find((w) => w.goal)!;
    race.goal = { ...race.goal!, name: "Lopp; final" };
    expect(toIcs(p)).toContain("SUMMARY:Lopp\\; final");
  });
});

describe("opening a plan on a phone", () => {
  it("lets the file picker show every file: Android has no type for .troskel and would grey the plan's file out", async () => {
    await mount();
    const file = root.querySelector<HTMLInputElement>("input[type=file]")!;
    expect(file.getAttribute("accept")).toBeNull();
  });

  it("reads a pasted message with the link in it, also when the message goes on after the link", async () => {
    const link = `https://generaltgul.github.io/troskel/app/#p=${encodeData(demoPlan())}`;
    expect(decodeLink(`Här är ditt schema: ${link} Lycka till!`).plan.id).toBe(demoPlan().plan.id);
    expect(decodeLink(`${link.slice(0, 90)}\n${link.slice(90)}`).plan.id).toBe(demoPlan().plan.id); // wrapped by a mail program
    const copy = athleteLink(athletePayload(demoPlan(), { marks: {}, extras: [] }, null, null, "2026-11-25"));
    expect(readCopyText(`Min kopia: ${copy} (från i dag)`)!.plan.plan.id).toBe(demoPlan().plan.id);

    const store = await mount();
    // The browser checks a form's fields before it sends it, and a message is not a URL: the form must not ask it to.
    expect(root.querySelector<HTMLFormElement>("form.paste")!.noValidate).toBe(true);
    await input(root.querySelector<HTMLInputElement>("form.paste input")!, `Här är ditt schema: ${link} Lycka till!`);
    await act(async () => {
      root.querySelector("form.paste")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(store.plan()?.plan.id).toBe(demoPlan().plan.id);
  });
});

describe("the day changes while the app is open", () => {
  it("shows the new day when the app comes back to the front, without the wall mode", async () => {
    await mount({ plan: demoPlan(), tab: "today" });
    expect(root.querySelector(".today h2 .date")!.textContent).toContain("25 nov");
    now = new Date("2026-11-26T05:00:00Z");
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(root.querySelector(".today h2 .date")!.textContent).toContain("26 nov");
  });

  it("shows the new day by itself within a minute", async () => {
    vi.useFakeTimers();
    await mount({ plan: demoPlan(), tab: "today" });
    now = new Date("2026-11-25T23:00:30Z"); // half past midnight in Stockholm
    await act(async () => { vi.advanceTimersByTime(60_000); });
    expect(root.querySelector(".today h2 .date")!.textContent).toContain("26 nov");
  });

  it("moves the week view on to the new week when it stood on this week, and leaves another week alone", async () => {
    await mount({ plan: demoPlan(), tab: "week" });
    expect(root.querySelector(".week-title h2")!.textContent).toBe("Vecka 48");
    now = new Date("2026-11-30T05:00:00Z"); // Monday of week 49
    await act(async () => { globalThis.dispatchEvent(new Event("focus")); });
    expect(root.querySelector(".week-title h2")!.textContent).toBe("Vecka 49");
    await click(root.querySelector(".week-head button:last-child")); // the athlete looks at week 50
    now = new Date("2026-12-07T05:00:00Z");
    await act(async () => { globalThis.dispatchEvent(new Event("focus")); });
    expect(root.querySelector(".week-title h2")!.textContent).toBe("Vecka 50");
  });
});

describe("my times", () => {
  const save = () => act(async () => {
    root.querySelector(".times form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  const addDate = async (day: string) => {
    await input(root.querySelector<HTMLInputElement>(".add-date input")!, day);
    await click(root.querySelector(".add-date button"));
  };
  const workoutsOn = (p: StoredPlan, day: string) => p.plan.weeks.flatMap((w) => w.workouts).filter((w) => w.date === day);
  /** A day after tomorrow with a workout that is no race. */
  const busyDay = (p: StoredPlan) =>
    p.plan.weeks.flatMap((w) => w.workouts).find((w) => w.date > "2026-11-27" && !w.goal)!.date;

  it("keep a date that does not work through the next save, instead of bringing its workout back", async () => {
    const plan = demoPlan();
    const day = busyDay(plan);
    const store = await mount({ plan, tab: "times", engine });
    await addDate(day);
    await save();
    expect(workoutsOn(store.plan()!, day)).toEqual([]);
    expect(store.unavailable()).toEqual([day]);

    // Another visit: the date still stands in the list, and a change of a weekday keeps its workout away.
    render(null, root);
    await mount({ store, tab: "times", engine });
    expect(root.querySelector(".times .list")!.textContent).toContain(String(Number(day.slice(8))));
    await input(root.querySelector<HTMLInputElement>(".time-fields input[type=number]")!, "50");
    await save();
    expect(root.textContent).toContain("omräknat från i morgon");
    expect(workoutsOn(store.plan()!, day)).toEqual([]);

    // Taken off the list, the workout comes back.
    await click(root.querySelector(".times .list button"));
    await save();
    expect(workoutsOn(store.plan()!, day).length).toBeGreaterThan(0);
    expect(store.unavailable()).toEqual([]);
  });

  it("let the dates wait with the times when the coach sends the same plan again, and drop them for another plan", async () => {
    const plan = demoPlan();
    const day = busyDay(plan);
    const store = await mount({ plan, tab: "times", engine });
    await addDate(day);
    await save();
    render(null, root);
    await mount({ store, tab: "times", engine, incoming: `https://example.com/#p=${encodeData(plan)}` });
    expect(workoutsOn(store.plan()!, day).length).toBeGreaterThan(0); // the coach's plan has the workout again
    expect(store.times()).toMatchObject({ unavailable: [day], kept: true });
    expect(root.querySelector(".times .notice")!.textContent).toContain("tryck Spara");
    await save();
    expect(workoutsOn(store.plan()!, day)).toEqual([]);

    const other: StoredPlan = { ...plan, plan: { ...plan.plan, id: "another" } };
    render(null, root);
    await mount({ store, tab: "times", engine, incoming: `https://example.com/#p=${encodeData(other)}` });
    expect(store.unavailable()).toEqual([]);
    expect(store.times()).toBeNull();
  });

  it("drop the workouts of such a date when the athlete's own plan renews itself", () => {
    const f = { ...emptyForm("2026-11-23"), level: "recreational", weeks: 12 };
    const own = planFromForm(f, { id: "app-test0001", today: "2026-11-23" });
    const later = "2026-12-14";
    const plain = renewOwn(own, later)!;
    const fresh = plain.plan.weeks.filter((w, i) => !w.sketch && own.plan.weeks[i].sketch);
    const day = fresh.flatMap((w) => w.workouts).find((w) => !w.goal)!.date;
    const renewed = renewOwn(own, later, [day])!;
    expect(renewed.plan.weeks.flatMap((w) => w.workouts).filter((w) => w.date === day)).toEqual([]);
    expect(renewed.plan.weeks.flatMap((w) => w.workouts).length)
      .toBe(plain.plan.weeks.flatMap((w) => w.workouts).length - plain.plan.weeks.flatMap((w) => w.workouts).filter((w) => w.date === day).length);
  });

  it("save a change when the coach gave a day more minutes than the field's hint, or minutes between its steps", async () => {
    const plan = demoPlan();
    plan.request.availability[0] = { ...plan.request.availability[0], max_minutes: 362 };
    const calls: PendingTimes["slots"][] = [];
    const fake: Engine = { available: true, version: "fake", rebuild: (s, a) => { calls.push(a.slots); return s; } };
    await mount({ plan, tab: "times", engine: fake });
    const form = root.querySelector<HTMLFormElement>(".times form")!;
    expect(form.noValidate).toBe(true); // the browser would refuse to send the form for 362 with max 300 and step 5
    await save();
    expect(calls[0][0].max_minutes).toBe(362);
  });

  it("keep the minutes of a day within what a day holds", () => {
    const day = (minutes: number) => [{ on: true, minutes, start: "" }, ...Array(6).fill({ on: false, minutes: 45, start: "" })];
    expect(slotsFromDays(day(9999), [], ["run"])[0].max_minutes).toBe(MAX_MINUTES);
    expect(slotsFromDays(day(Number.NaN), [], ["run"])[0].max_minutes).toBe(5);
  });

  it("open with broken times in the storage or in a copy, without them", async () => {
    expect(checkTimes({ saved: "2026-11-20" })).toBeNull();
    expect(checkTimes({ saved: "2026-11-20", slots: [{ weekday: 1 }] })).toBeNull();
    expect(checkTimes({ saved: "2026-11-20", slots: [], unavailable: ["2026-12-01", 7, "soon"] }))
      .toEqual({ saved: "2026-11-20", slots: [], unavailable: ["2026-12-01"] });
    const p = athletePayload(demoPlan(), { marks: {}, extras: [] }, null, null, "2026-11-25");
    const copy = readCopyText(athleteFile({ ...p, times: { saved: "2026-11-20" } as unknown as PendingTimes }))!;
    expect(copy.times).toBeNull();

    const store = memoryStore();
    store.setTimes({ saved: "2026-11-20" } as unknown as PendingTimes);
    await mount({ store, plan: demoPlan(), tab: "times" });
    expect(root.querySelector(".times .time-rows")).not.toBeNull();
  });
});

describe("the log", () => {
  it("keeps both sessions when a copy and the phone gave the same id to different sessions", () => {
    const yoga = { id: "2026-11-20-1", date: "2026-11-20", sport: "other", minutes: 30, name: "Yoga" };
    const swim = { id: "2026-11-20-1", date: "2026-11-20", sport: "swim", minutes: 45, name: "Sim" };
    const merged = mergeLog({ marks: {}, extras: [swim] }, { marks: {}, extras: [yoga] });
    expect(merged.extras).toEqual([yoga, { ...swim, id: "2026-11-20-2" }]);
    expect(mergeLog({ marks: {}, extras: [yoga] }, { marks: {}, extras: [yoga] }).extras).toEqual([yoga]); // the same: once
  });

  it("reads a session without a name and a mark with broken minutes", () => {
    const log = checkLog({
      marks: { a: { status: "partial", minutes: "x" }, b: { status: "partial", minutes: 20 }, c: { status: "gone" } },
      extras: [{ id: "1", date: "2026-11-20", sport: "other", minutes: 30 }],
    });
    expect(log.marks).toEqual({ a: { status: "partial" }, b: { status: "partial", minutes: 20 } });
    expect(log.extras[0].name).toBe("");
  });
});

describe("Ta bort all data på enheten", () => {
  it("forgets the day of the last copy and the dates that do not work", async () => {
    const store = memoryStore();
    store.setPlan(demoPlan());
    store.setSettings({ copied: "2026-11-24" });
    store.setUnavailable(["2026-12-24"]);
    vi.stubGlobal("confirm", () => true);
    await mount({ store, tab: "about" });
    expect(root.querySelector(".backup")!.textContent).toContain("24 nov");
    await click(byText("button.danger", "Ta bort"));
    expect(store.unavailable()).toEqual([]);
    expect(store.settings().copied).toBeUndefined();
    vi.unstubAllGlobals();
  });
});

describe("saving a file in the app store builds", () => {
  // A web view has no downloads: the button did nothing there, and the app said the file was saved.
  it("writes the file in the app's cache and hands it to the phone's share sheet", async () => {
    const seen: string[] = [];
    const io: NativeIo = {
      write: async (name, text) => { seen.push(`write ${name} ${text.length}`); return `file:///cache/${name}`; },
      share: async (name, uri) => { seen.push(`share ${name} ${uri}`); },
    };
    expect(await nativeSave(io)("troskel-kopia-2026-11-25.troskel", "{}", "application/json")).toBe(true);
    expect(seen).toEqual(["write troskel-kopia-2026-11-25.troskel 2", "share troskel-kopia-2026-11-25.troskel file:///cache/troskel-kopia-2026-11-25.troskel"]);
    expect(safeName("../../etc/x y.ics")).toBe("etc-x-y.ics");
    expect(saveFile(false)).toBe(browserSave);
    expect(saveFile(true)).not.toBe(browserSave);
  });

  it("says so when the share sheet was closed, and does not count a copy that was never saved", async () => {
    const io: NativeIo = { write: async () => "file:///cache/x", share: async () => { throw new Error("Share canceled"); } };
    const store = memoryStore();
    store.setPlan(demoPlan());
    await mount({ store, tab: "about", download: nativeSave(io) });
    await click(byText(".backup button", "Spara en kopia"));
    await settle();
    expect(root.querySelector(".backup [role=status]")!.textContent).toContain("sparades inte");
    expect(store.settings().copied).toBeUndefined();

    await click(root.querySelector(".share input[type=checkbox]"));
    await click(byText(".share button", "Spara som fil"));
    await settle();
    expect(root.querySelector(".share [role=status]")!.textContent).toContain("sparades inte");
  });

  it("remembers the day of the copy once the file is saved", async () => {
    const io: NativeIo = { write: async () => "file:///cache/x", share: async () => {} };
    const store = memoryStore();
    store.setPlan(demoPlan());
    await mount({ store, tab: "about", download: nativeSave(io) });
    await click(byText(".backup button", "Spara en kopia"));
    await settle();
    expect(root.querySelector(".backup [role=status]")!.textContent).toContain("Kopian är sparad");
    expect(store.settings().copied).toBe("2026-11-25");
  });
});

describe("reading a QR code", () => {
  const frame = { source: {} as CanvasImageSource, pixels: () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 }) };
  const jsQr = vi.fn(() => ({ data: "from jsQR" }));
  class Works {
    static getSupportedFormats = async () => ["qr_code", "ean_13"];
    detect = async () => [{ rawValue: "from the browser" }];
  }
  class NoQr extends Works { static getSupportedFormats = async () => ["ean_13"]; }
  class Broken extends Works { detect = async (): Promise<{ rawValue: string }[]> => { throw new Error("NotSupportedError"); }; }

  it("uses the browser's detector only when it reads QR codes", async () => {
    expect(await qrDetector(undefined)).toBeNull();
    expect(await qrDetector(NoQr)).toBeNull(); // Android without the Play services module
    expect(await qrDetector(Works)).toBeInstanceOf(Works);
    expect(await qrScanner(await qrDetector(Works), async () => jsQr)(frame)).toBe("from the browser");
    expect(await qrScanner(null, async () => jsQr)(frame)).toBe("from jsQR");
  });

  it("hands over to jsQR when the browser's detector fails, instead of never finding the code", async () => {
    const load = vi.fn(async () => jsQr);
    const scan = qrScanner(new Broken(), load);
    expect(await scan(frame)).toBe("from jsQR");
    expect(await scan(frame)).toBe("from jsQR");
    expect(load).toHaveBeenCalledTimes(1);
  });
});

describe("the wall mode", () => {
  it("leaves a form that was left open and goes back to today", async () => {
    vi.useFakeTimers();
    const store = memoryStore();
    store.setPlan(demoPlan());
    store.setSettings({ wall: true });
    await mount({ store, tab: "about", keepAwake: () => () => {} });
    await click(byText(".about button", "Gör ett schema själv"));
    expect(root.querySelector(".request")).not.toBeNull();
    await act(async () => { vi.advanceTimersByTime(3 * 60_000); });
    expect(root.querySelector(".request")).toBeNull();
    expect(root.querySelector(".today")).not.toBeNull();
  });
});

describe("the times that wait under Mina tider", () => {
  const waiting = (p: StoredPlan): PendingTimes =>
    ({ slots: p.request.availability.slice(1), unavailable: ["2026-12-24"], saved: "2026-11-20", kept: true });

  it("stay when the athlete's own plan renews itself", async () => {
    const f = { ...emptyForm("2026-10-26"), level: "recreational", weeks: 12 };
    const own = planFromForm(f, { id: "app-test0002", today: "2026-10-26" });
    expect(renewOwn(own, "2026-11-25")).not.toBeNull(); // due on the day of the test
    const store = memoryStore();
    store.setPlan(own);
    store.setTimes(waiting(own));
    await mount({ store, tab: "today" });
    expect(store.plan()!.plan.weeks.filter((w) => !w.sketch).length).toBeGreaterThan(own.plan.weeks.filter((w) => !w.sketch).length);
    expect(store.times()).toEqual(waiting(own));
  });

  it("stay when the athlete changes own values, and the example stays the example", async () => {
    const plan = demoPlan();
    const store = memoryStore();
    store.setPlan(plan);
    store.setTimes(waiting(plan));
    await mount({ store, tab: "plan" });
    await click(byText(".overview button", "Ändra mina värden"));
    await act(async () => {
      root.querySelector(".values form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(root.querySelector(".values")).toBeNull(); // saved
    expect(store.times()).toEqual(waiting(plan));

    const demo = memoryStore();
    demo.setPlan(plan, true);
    demo.updatePlan(plan);
    expect(demo.isDemo()).toBe(true);
  });
});
