// @vitest-environment happy-dom
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { App } from "../src/App";
import { stub } from "../src/engine";
import { setLang } from "../src/i18n";
import { addDays } from "../src/model/dates";
import {
  autoMarks, dayForm, decodeHealth, HealthError, healthData, mergeHealth, sportOf, withAuto, withoutAuto,
  type HealthData, type HealthDay, type HealthWorkout,
} from "../src/model/health";
import { emptyLog, markOf, setMark, weekTotals } from "../src/model/log";
import { workoutMinutes } from "../src/model/plan";
import { memoryStore } from "../src/model/storage";
import { demoPlan } from "./helpers";

// The demo's second week: Monday recovery, Tuesday easy, Thursday hills, Saturday easy, Sunday the long run.
const MON = "2026-11-23";
const day = (n: number) => addDays(MON, n);
const run = (n: number, minutes: number, at = "07:00", sport = "running"): HealthWorkout =>
  ({ sport: sportOf(sport), start: `${day(n)}T${at}:00+01:00`, minutes });
const data = (workouts: HealthWorkout[], days: HealthDay[] = []): HealthData => ({ read: "", workouts, days });
const week = () => demoPlan().plan.weeks[1].workouts;
const key = (n: number) => week().find((w) => w.date === day(n))!.key;

afterEach(() => setLang("sv"));

describe("the health link", () => {
  const json = { kind: "troskel-health", format: 1, read: "2026-11-25T07:10:00+01:00",
    workouts: [{ sport: "HKWorkoutActivityTypeRunning", start: `${day(1)}T18:02:00+01:00`, minutes: "48,4", hr: 142 }],
    days: [{ date: day(2), sleep_h: 4.5, rhr: 48 }, { date: `${day(2)}T07:02:00+01:00`, sleep_h: "2" }, { date: "yesterday" }] };
  const text = JSON.stringify(json);
  const bytes = new TextEncoder().encode(text);
  const b64 = btoa(String.fromCharCode(...bytes));

  it("reads base64url, a Shortcut's base64 and URL-encoded JSON alike", () => {
    const url = b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    for (const d of [url, b64, encodeURIComponent(text)]) {
      const h = decodeHealth(d);
      expect(h.workouts).toEqual([{ sport: "run", start: `${day(1)}T18:02:00+01:00`, minutes: 48, hr: 142 }]);
      expect(h.days).toEqual([{ date: day(2), sleep_h: 6.5, rhr: 48 }]);
    }
    expect(healthData(`https://x/app/#h=${url}`)).toBe(url);
    expect(healthData("https://x/app/#p=abc")).toBeNull();
  });

  it("puts a Shortcut's samples of one day together: the sleep added up, HRV the mean", () => {
    const h = decodeHealth(encodeURIComponent(JSON.stringify({ ...json, days: [
      { date: day(2), hrv: 50 }, { date: day(2), hrv: 61 }, { date: day(2), sleep_h: 1.5 }, { date: day(2), sleep_h: 5 }] })));
    expect(h.days).toEqual([{ date: day(2), sleep_h: 6.5, hrv: 55.5 }]);
  });

  it("refuses what is not health data, a newer format and broken data", () => {
    const code = (d: string) => { try { decodeHealth(d); } catch (e) { return (e as HealthError).code; } return null; };
    expect(code(encodeURIComponent(JSON.stringify({ ...json, kind: "troskel" })))).toBe("not_health");
    expect(code(encodeURIComponent(JSON.stringify({ ...json, format: 2 })))).toBe("too_new");
    expect(code("@@@")).toBe("broken");
  });

  it("knows the sports of Health, Health Connect and Strava", () => {
    expect(["Running", "EXERCISE_TYPE_RUNNING_TREADMILL", "VirtualRun"].map(sportOf)).toEqual(["run", "run", "run"]);
    expect(["cycling", "EXERCISE_TYPE_BIKING_STATIONARY", "VirtualRide"].map(sportOf)).toEqual(["bike", "bike", "bike"]);
    expect(["crossCountrySkiing", "NordicSki", "swimming", "EXERCISE_TYPE_SWIMMING_POOL"].map(sportOf))
      .toEqual(["ski", "ski", "swim", "swim"]);
    expect(["traditionalStrengthTraining", "WeightTraining", "yoga"].map(sportOf)).toEqual(["strength", "strength", "other"]);
  });

  it("keeps six weeks, a new day over the kept, and one of two sessions that overlap", () => {
    const old = data([run(0, 45, "07:00")], [{ date: day(0), sleep_h: 7 }, { date: addDays(day(0), -50), sleep_h: 7 }]);
    const h = mergeHealth(old, data([run(0, 50, "07:05"), run(0, 20, "18:00")], [{ date: day(0), sleep_h: 5, rhr: 50 }]), day(2));
    expect(h.workouts.map((w) => w.minutes)).toEqual([50, 20]);
    expect(h.days).toEqual([{ date: day(0), sleep_h: 5, rhr: 50 }]);
    expect(mergeHealth(h, data([], [{ date: day(0), hrv: 60 }]), day(2)).days).toEqual([{ date: day(0), sleep_h: 5, rhr: 50, hrv: 60 }]);
  });
});

describe("the workouts the data ticks off", () => {
  it("takes the longest session the same day, not the warm-up", () => {
    const a = autoMarks(demoPlan(), data([run(0, 6, "06:50"), run(0, 35, "07:00")]), day(2));
    expect(a.marks[key(0)]).toEqual({ status: "done", auto: true, on: day(0) });
    expect(a.unplanned.map((w) => w.minutes)).toEqual([6]);
  });

  it("moves a session from another day of the week: the nearest, at a tie the later, at least 20 minutes", () => {
    // Thursday's hills: a run on Wednesday and one on Friday, as near; Friday wins and Wednesday's stays outside.
    const a = autoMarks(demoPlan(), data([run(0, 30), run(1, 30), run(2, 40), run(4, 40)]), day(5));
    expect(a.marks[key(3)].on).toBe(day(4));
    expect(a.marks[key(1)].on).toBe(day(1));
    expect(a.unplanned.map((w) => w.start.slice(0, 10))).toEqual([day(2)]);
    // Tuesday's easy run, with only 10 minutes on Monday besides Monday's own: not ticked off.
    const short = autoMarks(demoPlan(), data([run(0, 30), run(0, 10, "18:00")]), day(2));
    expect(short.marks[key(1)]).toBeUndefined();
  });

  it("does not tick off a workout whose day has not been, nor one in another sport", () => {
    expect(autoMarks(demoPlan(), data([run(2, 40)]), day(2)).marks[key(3)]).toBeUndefined();
    expect(autoMarks(demoPlan(), data([run(0, 40, "07:00", "cycling")]), day(2)).marks[key(0)]).toBeUndefined();
  });

  it("lets the athlete's mark win, counts the week, and keeps only the athlete's marks", () => {
    const stored = demoPlan();
    const auto = autoMarks(stored, data([run(0, 35), run(1, 40)]), day(2));
    const own = setMark(emptyLog(), key(1), { status: "missed" });
    const shown = withAuto(own, auto);
    const [mon, tue] = week();
    expect(markOf(shown, mon)!.status).toBe("done");
    expect(markOf(shown, tue)!.status).toBe("missed");
    expect(weekTotals(stored, shown, MON).done).toBe(workoutMinutes(mon));
    expect(withoutAuto(shown).marks).toEqual(own.marks);
  });
});

describe("today's form", () => {
  const history = (n: number, f: (i: number) => HealthDay): HealthDay[] => [...Array(n)].map((_, i) => f(i));
  const today = day(2);
  const past = (i: number) => addDays(today, -1 - i);

  it("is tired at one strong signal or two weak ones", () => {
    expect(dayForm(data([], [{ date: today, sleep_h: 4 }]), today)!.tired).toBe(true);
    const weak = dayForm(data([], [{ date: today, sleep_h: 5.5 }]), today)!;
    expect(weak.tired).toBe(false);
    expect(weak.reasons).toEqual([{ signal: "sleep", strong: false, value: 5.5 }]);
    const rhr = history(20, (i) => ({ date: past(i), rhr: 48 }));
    const two = dayForm(data([], [...rhr, { date: today, sleep_h: 5.5, rhr: 53 }]), today)!;
    expect(two.tired).toBe(true);
    expect(two.reasons.map((r) => r.signal)).toEqual(["rhr", "sleep"]);
  });

  it("needs 14 nights for a mean, and says what it did not have", () => {
    const hrv = (n: number) => history(n, (i) => ({ date: past(i), hrv: 60 }));
    expect(dayForm(data([], [...hrv(13), { date: today, hrv: 40, sleep_h: 7 }]), today)!.missing).toEqual(["hrv", "rhr"]);
    const low = dayForm(data([], [...hrv(14), { date: today, hrv: 45, sleep_h: 7 }]), today)!;
    expect(low.reasons).toEqual([{ signal: "hrv", strong: true, value: 45, mean: 60 }]);
    expect(low.tired).toBe(true);
    expect(dayForm(data([], [{ date: past(0), sleep_h: 4 }]), today)).toBeNull();
  });
});

describe("the health data in the app", () => {
  it("reads the link the app was opened with, shows today's form and ticks off Monday", async () => {
    const store = memoryStore();
    store.setPlan(demoPlan());
    store.setSettings({ lang: "sv" });
    const json = { kind: "troskel-health", format: 1, read: "", workouts: [run(0, 35)], days: [{ date: day(2), sleep_h: 4.2 }] };
    let read = 0;
    const root = document.createElement("div");
    await act(async () => {
      render(<App store={store} engine={stub} now={() => new Date("2026-11-25T08:00:00Z")} timeZone="Europe/Stockholm"
        loadDemo={async () => ""} incoming={`https://x/app/#h=${encodeURIComponent(JSON.stringify(json))}`}
        onIncomingRead={() => read++} />, root);
    });
    expect(read).toBe(1);
    expect(root.querySelector(".form.tired h3")?.textContent).toBe("Ta det lugnt i dag");
    expect(store.health()!.workouts).toHaveLength(1);
    expect(store.log().marks).toEqual({});
    store.clear();
    expect(store.health()).toBeNull();
    render(null, root);
  });
});
