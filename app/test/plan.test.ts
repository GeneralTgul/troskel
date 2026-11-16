import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { keys, setLang, t } from "../src/i18n";
import {
  duration, hours, hrText, paceStr, pyRound, splitStr, targetText, weekMinutes, workoutLines, workoutTitle, planTitle, plain,
} from "../src/model/plan";
import type { StoredPlan } from "../src/model/types";
import { daysFromSlots, slotsFromDays } from "../src/model/times";
import { demoPlan } from "./helpers";

describe("numbers as in the printout", () => {
  it("rounds halves to even, like Python", () => {
    expect([pyRound(0.5), pyRound(1.5), pyRound(2.5), pyRound(2.4), pyRound(2.6)]).toEqual([0, 2, 2, 2, 3]);
  });
  it("writes durations and weekly hours", () => {
    setLang("sv");
    expect([duration(45), duration(90), duration(120), duration(360), duration(3600), duration(5400)])
      .toEqual(["45 s", "90 s", "2 min", "6 min", "1 h", "1 h 30 min"]);
    expect(hours(275)).toBe("4:35");
  });
  it("gives the pace in min/km", () => {
    expect(paceStr(12)).toBe("5:00");
    expect(paceStr(14.4)).toBe("4:10");
    expect(paceStr(null)).toBe("");
  });
});

describe("a workout in another sport", () => {
  it("has the sport's name in its title, as Workout.title in Python", () => {
    setLang("sv");
    const w = demoPlan().plan.weeks[0].workouts.find((x) => x.template_id === "easy")!;
    expect(workoutTitle(w)).toBe("Lugn löpning");
    expect(workoutTitle({ ...w, sport: "bike" })).toBe("Cykel: lugnt pass");
    expect(workoutTitle({ ...w, sport: "swim", template_id: "threshold" })).toBe("Simning: tröskelintervaller");
    expect(workoutTitle(w, true)).toBe("Löpning: lugnt pass");
    setLang("en");
    expect(workoutTitle({ ...w, sport: "bike" })).toBe("Cycling: easy session");
    setLang("sv");
  });
});

describe("the demo plan's texts", () => {
  it("gives the zone's heart rate before its pace", () => {
    setLang("sv");
    expect(targetText(demoPlan().targets.run.Z4)).toBe("170–174 slag/min · 4:21–4:10 min/km");
  });
  it("writes a part as one line", () => {
    setLang("sv");
    const p = demoPlan();
    const w = p.plan.weeks[0].workouts.find((x) => x.template_id === "fartlek")!;
    expect(plain(workoutLines(p, w)[1])).toMatch(/^Intervaller: 6 × 2 min i Z4 \(170–174 slag\/min · .+\), lugn jogg 2 min emellan$/);
  });
  it("sums the week", () => {
    expect(weekMinutes(demoPlan().plan.weeks[0])).toBeGreaterThan(100);
  });
});

describe("the texts", () => {
  it("are the same keys in Swedish and English", () => {
    expect(keys("en").sort()).toEqual(keys("sv").sort());
  });
  it("fill in values", () => {
    setLang("en");
    expect(t("ui.week.title", { n: 47 })).toBe("Week 47");
    setLang("sv");
  });
});

describe("my times", () => {
  it("go from the slots to one row per weekday and back", () => {
    const slots = demoPlan().request.availability;
    const days = daysFromSlots(slots);
    expect(days.map((d) => d.on)).toEqual([true, true, false, true, false, true, true]);
    expect(days[0]).toEqual({ on: true, minutes: 45, start: "18:00" });
    expect(slotsFromDays(days, slots, ["run"])).toEqual(slots);
  });
  it("turn a day off and another on", () => {
    const slots = demoPlan().request.availability;
    const days = daysFromSlots(slots);
    days[0] = { ...days[0], on: false };
    days[2] = { on: true, minutes: 30, start: "" };
    const out = slotsFromDays(days, slots, ["run"]);
    expect(out.map((s) => s.weekday)).toEqual([1, 2, 3, 5, 6]);
    expect(out[1]).toEqual({ weekday: 2, max_minutes: 30, sports: ["run"], start: null, role: "core" });
  });
});

describe("the bike's and the SkiErg's targets (#666, step 2)", () => {
  interface TextCase { plan: string; key: string; lang: "sv" | "en"; title: string; lines: string[] }
  const fx: { plans: Record<string, StoredPlan>; texts: TextCase[] } = JSON.parse(
    gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/engine.json.gz"))).toString("utf-8"));

  it.each(fx.texts.map((c) => [`${c.plan} ${c.key} ${c.lang}`, c] as const))("shows %s as Python", (_, c) => {
    setLang(c.lang);
    const stored = fx.plans[c.plan];
    const w = stored.plan.weeks.flatMap((wk) => wk.workouts).find((x) => x.key === c.key)!;
    expect(planTitle(stored, w)).toBe(c.title);
    expect(workoutLines(stored, w).map(plain)).toEqual(c.lines);
  });

  it("gives watt on the bike, watt and time per 500 m on the SkiErg, and heart rate only on skis", () => {
    setLang("sv");
    const plan = fx.plans["bike-skierg-tested"];
    expect(targetText(plan.targets.bike.Z4, "bike")).toMatch(/^\d+–\d+ slag\/min · \d+–\d+ W$/);
    expect(targetText(plan.targets.skierg.Z4, "skierg")).toMatch(/^\d+–\d+ slag\/min · \d+–\d+ W · \d:\d\d–\d:\d\d \/500 m$/);
    const ski = fx.plans["ski-main"].targets.ski.Z4!;
    expect(ski.power_low_w).toBeUndefined();
    expect(targetText(ski, "ski")).toMatch(/^\d+–\d+ slag\/min$/);
  });

  it("reads a target without the watt fields, as sent before", () => {
    const tg = { ...demoPlan().targets.run.Z4! };
    expect(targetText(tg, "bike")).toBe(hrText(tg));           // no watt in it, and no pace in km/h on the bike
    expect(splitStr(null)).toBe("");
    expect(splitStr(200)).toBe("2:01");                          // core.sport: 500 * (2.80 / 200) ** (1/3) s
  });
});
