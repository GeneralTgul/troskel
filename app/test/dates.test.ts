import { describe, expect, it } from "vitest";
import { setLang } from "../src/i18n";
import { addDays, dayMonth, isoWeek, mondayOf, today, weekday, weekDays } from "../src/model/dates";
import { liveDemo } from "../src/model/demo";
import { demoPlan } from "./helpers";

const SE = "Europe/Stockholm";

describe("the week", () => {
  it("starts on Monday", () => {
    expect(weekday("2026-11-16")).toBe(0); // a Monday
    expect(weekday("2026-11-22")).toBe(6); // a Sunday
    expect(mondayOf("2026-11-22")).toBe("2026-11-16");
    expect(mondayOf("2026-11-16")).toBe("2026-11-16");
    expect(weekDays("2026-11-16")).toEqual(["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20",
      "2026-11-21", "2026-11-22"]);
  });

  it("crosses months, years and summer time without losing a day", () => {
    expect(addDays("2026-12-28", 7)).toBe("2027-01-04");
    expect(addDays("2027-03-28", 1)).toBe("2027-03-29"); // summer time begins in Sweden
    expect(addDays("2026-10-25", -1)).toBe("2026-10-24"); // and ends
    expect(mondayOf("2027-01-01")).toBe("2026-12-28");
  });

  it("has the ISO week number", () => {
    expect(isoWeek("2026-11-16")).toBe(47);
    expect(isoWeek("2026-12-31")).toBe(53);
    expect(isoWeek("2027-01-03")).toBe(53);
    expect(isoWeek("2027-01-04")).toBe(1);
  });
});

describe("today in Swedish time", () => {
  it("is Monday from midnight in Sweden, while it is still Sunday in UTC", () => {
    expect(today(new Date("2026-11-15T23:30:00Z"), SE)).toBe("2026-11-16"); // winter: UTC+1
    expect(today(new Date("2026-11-15T22:59:00Z"), SE)).toBe("2026-11-15");
    expect(today(new Date("2027-06-13T22:30:00Z"), SE)).toBe("2027-06-14"); // summer: UTC+2
    expect(today(new Date("2027-06-13T21:30:00Z"), SE)).toBe("2027-06-13");
  });
});

describe("dates in words", () => {
  it("uses the plan's month names", () => {
    setLang("sv");
    expect(dayMonth("2026-11-16")).toBe("16 nov");
    setLang("en");
    expect(dayMonth("2027-03-02")).toBe("2 Mar");
    setLang("sv");
  });
});

describe("the example plan", () => {
  it("is moved by whole weeks so that its second week is this week", () => {
    const p = liveDemo(demoPlan(), "2027-05-12");
    expect(p.plan.weeks[1].monday).toBe("2027-05-10");
    expect(weekday(p.plan.weeks[0].workouts[0].date)).toBe(weekday(demoPlan().plan.weeks[0].workouts[0].date));
    const goal = p.request.goals.find((g) => g.priority === "A")!;
    expect(weekday(goal.date)).toBe(5);
  });
});
