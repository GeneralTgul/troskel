// @vitest-environment happy-dom
// The plan to the coach (#781): what the phone gives (share.ts), the box under Om that gives it only after the
// athlete's consent, and the way back: after the coach read the plan in and laid a new test on it, the app takes the
// new link without keeping any times waiting.
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { engine } from "../src/engine";
import { setLang } from "../src/i18n";
import { memoryStore, type PendingTimes, type Store } from "../src/model/storage";
import { athleteFile, athleteFileName, athleteLink, athletePayload, readAthleteLink, type AthletePayload,
  ATHLETE_KIND } from "../src/model/share";
import { keptTimes } from "../src/model/times";
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

async function mount(store: Store, props: { incoming?: string; download?: (n: string, t: string, ty: string) => void } = {}) {
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={engine} now={() => NOW} timeZone="Europe/Stockholm" tab="about"
      loadDemo={async () => demoText()} incoming={props.incoming} download={props.download} />, root);
  });
}
const click = async (el: Element | null) => {
  expect(el).not.toBeNull();
  await act(async () => { (el as HTMLElement).click(); });
};

describe("what the phone gives the coach", () => {
  it("is the plan as the phone has it, the marks, the sessions outside the plan and the waiting times", () => {
    const p = demoPlan();
    const [a, b, c] = p.plan.weeks[0].workouts;
    const log = { marks: { [a.key]: { status: "done" as const }, [b.key]: { status: "partial" as const, minutes: 20 } },
      extras: [{ id: "2026-11-23-1", date: "2026-11-23", sport: "other", minutes: 30, name: "Yoga" }] };
    const auto = { marks: { [a.key]: { status: "missed" as const }, [c.key]: { status: "done" as const } }, unplanned: [] };
    const out = athletePayload(p, log, auto as never, null, "2026-11-25");
    expect(out.kind).toBe(ATHLETE_KIND);
    expect(out.plan).toBe(p);
    // the athlete's own mark wins over the watch's, and the watch's are marked as such
    expect(out.marks).toEqual({ [a.key]: { status: "done" }, [b.key]: { status: "partial", minutes: 20 },
      [c.key]: { status: "done", auto: true } });
    expect(out.extras).toEqual(log.extras);
    expect("times" in out).toBe(false);
    const pending: PendingTimes = { slots: p.request.availability.slice(1), unavailable: ["2026-12-03"], saved: "2026-11-25" };
    expect(athletePayload(p, log, null, pending, "2026-11-25").times).toEqual(pending);
  });

  it("goes as a file and as a link that give the same, and Tröskel's link reads in the app's way", () => {
    const p = FIXTURE();
    expect(JSON.parse(athleteFile(p))).toEqual(p);
    expect(readAthleteLink(athleteLink(p))).toEqual(p);
    expect(athleteLink(p)).toMatch(/^https:\/\/generaltgul\.github\.io\/troskel\/app\/#a=[A-Za-z0-9_-]+$/);
    expect(readAthleteLink(fixture("athlete.link.txt"))).toEqual(p); // made in Python (plan.phone.athlete_link)
    expect(athleteFileName(p)).toBe("troskel-exempel-lopare-2026-11-25.troskel");
    expect(athleteFileName({ ...p, plan: { ...p.plan, athlete: "" } })).toBe("troskel-2026-11-25.troskel");
  });

  it("has the plan the app's engine built from the athlete's times, as the fixture Tröskel reads", () => {
    const p = demoPlan();
    const slots = p.request.availability.filter((s) => s.weekday !== 0);
    expect(engine.rebuild(p, { slots, unavailable: [], from: "2026-11-26" })).toEqual(FIXTURE().plan);
  });
});

describe("the box under Om", () => {
  const own = () => {
    const s = memoryStore();
    s.setPlan(demoPlan());
    s.setSettings({ lang: "sv" });
    const first = demoPlan().plan.weeks[0].workouts[0];
    s.setLog({ marks: { [first.key]: { status: "done" } }, extras: [] });
    return s;
  };

  it("gives the file and the link only after the athlete's consent", async () => {
    const download = vi.fn();
    const writeText = vi.fn(async (_: string) => {});
    vi.stubGlobal("navigator", { ...globalThis.navigator, clipboard: { writeText } });
    await mount(own(), { download });
    const box = root.querySelector(".share")!;
    expect(box.querySelector("h3")!.textContent).toBe("Skicka schemat till din coach");
    expect(box.textContent).toContain("Nätterna (sömn, vilopuls, HRV)");
    const buttons = [...box.querySelectorAll("button")];
    expect(buttons.map((b) => b.textContent)).toEqual(["Spara som fil", "Kopiera länken"]);
    expect(buttons.every((b) => b.disabled)).toBe(true);
    await click(buttons[0]);
    expect(download).not.toHaveBeenCalled();

    await click(box.querySelector("input[type=checkbox]"));
    expect(buttons.every((b) => !b.disabled)).toBe(true);
    await click(buttons[0]);
    const [name, text, type] = download.mock.calls[0];
    expect(name).toBe("troskel-exempel-lopare-2026-11-25.troskel");
    expect(type).toBe("application/json");
    const sent = JSON.parse(text) as AthletePayload;
    expect(sent.kind).toBe(ATHLETE_KIND);
    expect(sent.plan).toEqual(demoPlan());
    expect(Object.values(sent.marks)).toEqual([{ status: "done" }]);
    expect(box.querySelector(".notice")!.textContent).toContain("Filen är sparad");

    await click(buttons[1]);
    expect(readAthleteLink(writeText.mock.calls[0][0])).toEqual(sent);
    expect(box.querySelector(".notice")!.textContent).toContain("Länken är kopierad");
  });

  it("says so when the link cannot be copied", async () => {
    vi.stubGlobal("navigator", { ...globalThis.navigator, clipboard: { writeText: async () => { throw new Error("no"); } } });
    await mount(own());
    const box = root.querySelector(".share")!;
    expect(box.querySelectorAll("button")).toHaveLength(1); // no file without a way to save one
    await click(box.querySelector("input[type=checkbox]"));
    await click(box.querySelector("button"));
    expect(box.querySelector(".notice")!.textContent).toContain("gick inte att kopiera");
  });

  it("is not there for the example plan or without a plan", async () => {
    const demo = memoryStore();
    demo.setPlan(demoPlan(), true);
    demo.setSettings({ lang: "sv" });
    await mount(demo);
    expect(root.querySelector(".share")).toBeNull();
    render(null, root);
    const none = memoryStore();
    none.setSettings({ lang: "sv" });
    await mount(none);
    expect(root.querySelector(".share")).toBeNull();
  });
});

describe("the way back after a new test", () => {
  it("the new link replaces the plan and keeps the marks, with no times waiting", async () => {
    const p = FIXTURE();
    const store = memoryStore();
    store.setPlan(p.plan);
    store.setSettings({ lang: "sv" });
    store.setLog({ marks: Object.fromEntries(Object.entries(p.marks).filter(([, m]) => !m.auto)), extras: p.extras });
    await mount(store, { incoming: fixture("athlete.back.link.txt").trim() });
    const now = store.plan()!;
    expect(now.plan.id).toBe(p.plan.plan.id);
    expect(now.request.availability).toEqual(p.plan.request.availability);
    expect(now.targets_log.at(-1)!.until).toBe("2026-11-26"); // the new test's targets from its day
    expect(store.times()).toBeNull();
    expect(Object.keys(store.log().marks)).toHaveLength(2);
    expect(store.log().extras).toEqual(p.extras);
  });

  it("times that waited on the phone are the athlete's own, also when they came back from the coach", () => {
    const p = demoPlan();
    const mine = p.request.availability.filter((s) => s.weekday !== 0);
    const pending: PendingTimes = { slots: mine, unavailable: ["2026-11-20", "2026-12-03"], saved: "2026-11-25", kept: true };
    const back = { ...p, request: { ...p.request, availability: mine } };
    expect(keptTimes(p, back, "2026-11-25", pending)).toBeNull(); // the coach laid them on the plan
    expect(keptTimes(p, p, "2026-11-25", pending)).toEqual({ slots: mine, unavailable: ["2026-12-03"],
      saved: "2026-11-25", kept: true }); // a new test without them: still waiting, not the coach's
  });
});
