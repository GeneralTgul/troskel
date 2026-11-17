// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { stub } from "../src/engine";
import {
  fromHealthConnect, localIso, READ_TYPES, readHealthConnect, type HcRead, type HcSample, type HealthPluginLike,
} from "../src/model/healthconnect";
import { memoryStore } from "../src/model/storage";
import { demoPlan } from "./helpers";

const TZ = "Europe/Stockholm";
const empty: HcRead = { workouts: [], heartRate: [], sleep: [], restingHeartRate: [], heartRateVariability: [] };
const sample = (startDate: string, value: number, extra: Partial<HcSample> = {}): HcSample =>
  ({ startDate, endDate: startDate, value, ...extra });

describe("Health Connect's data as the app's health data", () => {
  it("writes an instant as local time with the offset, also over midnight and in summer", () => {
    expect(localIso("2026-11-16T17:02:00Z", TZ)).toBe("2026-11-16T18:02:00+01:00");
    expect(localIso("2026-11-16T23:30:00.000Z", TZ)).toBe("2026-11-17T00:30:00+01:00");
    expect(localIso("2026-07-01T10:00:00Z", TZ)).toBe("2026-07-01T12:00:00+02:00");
    expect(localIso("not a date", TZ)).toBe("");
  });

  it("takes the workouts with their sport, local start, minutes and the heart rate of the session's time", () => {
    const h = fromHealthConnect({
      ...empty,
      workouts: [
        { workoutType: "running", duration: 2880, startDate: "2026-11-24T17:02:00Z", endDate: "2026-11-24T17:50:00Z" },
        { workoutType: "bikingStationary", duration: 0, startDate: "2026-11-25T05:00:00Z", endDate: "2026-11-25T06:00:00Z" },
        { workoutType: "yoga", duration: 1800, startDate: "2026-11-25T16:00:00Z", endDate: "2026-11-25T16:30:00Z" },
      ],
      heartRate: [sample("2026-11-24T17:10:00Z", 140), sample("2026-11-24T17:40:00Z", 160),
        sample("2026-11-24T19:00:00Z", 60)],
    }, "2026-11-25T08:00:00+01:00", TZ);
    expect(h.read).toBe("2026-11-25T08:00:00+01:00");
    expect(h.workouts).toEqual([
      { sport: "run", start: "2026-11-24T18:02:00+01:00", minutes: 48, hr: 150, hr_max: 160 },
      { sport: "bike", start: "2026-11-25T06:00:00+01:00", minutes: 60 },
      { sport: "other", start: "2026-11-25T17:00:00+01:00", minutes: 30 },
    ]);
  });

  it("gives the night's sleep to the day it ended, counting the asleep stages or the whole session", () => {
    const h = fromHealthConnect({
      ...empty,
      sleep: [
        { startDate: "2026-11-24T22:00:00Z", endDate: "2026-11-25T05:30:00Z", value: 450, hasStageData: true,
          stages: [{ stage: "light", durationMinutes: 240 }, { stage: "awake", durationMinutes: 30 },
            { stage: "deep", durationMinutes: 90 }, { stage: "rem", durationMinutes: 60 }, { stage: "inBed", durationMinutes: 30 }] },
        { startDate: "2026-11-25T22:30:00Z", endDate: "2026-11-26T04:30:00Z", value: 360 },
      ],
      restingHeartRate: [sample("2026-11-25T04:00:00Z", 48), sample("2026-11-25T23:30:00Z", 52)],
      heartRateVariability: [sample("2026-11-25T03:00:00Z", 60), sample("2026-11-25T04:00:00Z", 64)],
    }, "", TZ);
    expect(h.days).toEqual([
      { date: "2026-11-25", sleep_h: 6.5, rhr: 48, hrv: 62 },
      { date: "2026-11-26", sleep_h: 6, rhr: 52 },
    ]);
  });
});

/** A stand-in for the plugin: what it was asked, and the answers. */
function fakePlugin(o: { available?: boolean; allowed?: string[]; data?: Partial<HcRead>; throws?: boolean } = {}) {
  const calls: string[] = [];
  const data = { ...empty, ...o.data };
  const auth = async (x: { read: string[] }) => {
    calls.push(`auth:${x.read.join(",")}`);
    return { readAuthorized: o.allowed ?? x.read };
  };
  const plugin: HealthPluginLike = {
    isAvailable: async () => ({ available: o.available ?? true }),
    requestAuthorization: async (x) => { calls.push("ask"); return auth(x); },
    checkAuthorization: async (x) => { calls.push("check"); return auth(x); },
    readSamples: async (x) => {
      if (o.throws) throw new Error("boom");
      calls.push(`samples:${x.dataType}`);
      const s = data[x.dataType as keyof HcRead] as HcSample[];
      return { samples: x.dataType === "heartRate"
        ? s.filter((v) => v.startDate >= x.startDate && v.startDate <= x.endDate) : s };
    },
    queryWorkouts: async () => { calls.push("workouts"); return { workouts: data.workouts }; },
    openHealthConnectSettings: async () => { calls.push("settings"); },
  };
  return { plugin, calls };
}

describe("reading Health Connect", () => {
  const now = new Date("2026-11-25T07:00:00Z");

  it("asks only for reading, and only the five types", async () => {
    const { plugin, calls } = fakePlugin();
    const r = await readHealthConnect(plugin, true, now);
    expect(r.ok).toBe(true);
    expect(calls[0]).toBe("ask");
    expect(calls[1]).toBe(`auth:${READ_TYPES.join(",")}`);
    expect([...READ_TYPES]).toEqual(["workouts", "heartRate", "sleep", "restingHeartRate", "heartRateVariability"]);
  });

  it("without the button it checks the permission and never shows the sheet", async () => {
    const { plugin, calls } = fakePlugin();
    await readHealthConnect(plugin, false, now);
    expect(calls).toContain("check");
    expect(calls).not.toContain("ask");
  });

  it("reads the heart rate only for the sessions, and nothing it was not allowed", async () => {
    const { plugin, calls } = fakePlugin({
      allowed: ["workouts", "heartRate", "sleep"],
      data: { workouts: [{ workoutType: "running", duration: 1800, startDate: "2026-11-24T17:00:00Z", endDate: "2026-11-24T17:30:00Z" }],
        heartRate: [sample("2026-11-24T17:10:00Z", 150), sample("2026-11-24T12:00:00Z", 70)] },
    });
    const r = await readHealthConnect(plugin, false, now);
    expect(r.ok && r.data.workouts[0].hr).toBe(150);
    expect(calls.filter((c) => c.startsWith("samples:"))).toEqual(["samples:heartRate", "samples:sleep"]);
  });

  it("says why when Health Connect is missing, the permission was not given or the reading failed", async () => {
    expect(await readHealthConnect(fakePlugin({ available: false }).plugin, true, now)).toEqual({ ok: false, why: "unavailable" });
    expect(await readHealthConnect(fakePlugin({ allowed: ["heartRate"] }).plugin, true, now)).toEqual({ ok: false, why: "denied" });
    expect(await readHealthConnect(fakePlugin({ throws: true }).plugin, true, now)).toEqual({ ok: false, why: "failed" });
  });
});

describe("Health Connect in the app", () => {
  const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

  it("shows the section only with Health Connect, reads after the button and again at the next start", async () => {
    const store = memoryStore();
    store.setPlan(demoPlan());
    store.setSettings({ lang: "sv" });
    const { plugin, calls } = fakePlugin({ data: { sleep: [
      { startDate: "2026-11-24T22:00:00Z", endDate: "2026-11-25T03:00:00Z", value: 250 }] } });
    const root = document.createElement("div");
    const app = (hc?: HealthPluginLike) => (
      <App store={store} engine={stub} now={() => new Date("2026-11-25T08:00:00Z")} timeZone={TZ} tab="about"
        loadDemo={async () => ""} healthConnect={hc} />);

    await act(async () => { render(app(), root); });
    expect(root.textContent).not.toContain("Health Connect");
    render(null, root);

    await act(async () => { render(app(plugin), root); });
    await flush();
    expect(calls).toEqual([]);
    const button = [...root.querySelectorAll("button")].find((b) => b.textContent === "Läs från Health Connect")!;
    expect(button).toBeTruthy();
    await act(async () => { button.click(); });
    await flush();
    expect(calls[0]).toBe("ask");
    expect(store.settings().healthConnect).toBe(true);
    expect(store.health()!.days).toEqual([{ date: "2026-11-25", sleep_h: 4.17 }]);
    expect(root.textContent).toContain("Läs Health Connect nu");
    render(null, root);

    calls.length = 0;
    await act(async () => { render(app(plugin), root); });
    await flush();
    expect(calls[0]).toBe("check");
    expect(calls).not.toContain("ask");
    render(null, root);
  });
});
