import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../src/i18n";
import { fold, toIcs } from "../src/model/ics";
import { demoPlan, fixture } from "./helpers";

const withoutProdid = (s: string) => s.split("\r\n").filter((l) => !l.startsWith("PRODID:"));

afterEach(() => setLang("sv"));

describe("the calendar file", () => {
  it.each(["sv", "en"] as const)("is the one Python writes, in %s", (lang) => {
    setLang(lang);
    expect(withoutProdid(toIcs(demoPlan()))).toEqual(withoutProdid(fixture(`demo.${lang}.ics`)));
  });

  it("holds only the workouts from a given day", () => {
    const p = demoPlan();
    const all = (toIcs(p).match(/BEGIN:VEVENT/g) ?? []).length;
    const from = p.plan.weeks[6].monday;
    const later = p.plan.weeks.slice(6).reduce((n, w) => n + w.workouts.length, 0);
    expect(all).toBe(p.plan.weeks.reduce((n, w) => n + w.workouts.length, 0));
    expect((toIcs(p, { from }).match(/BEGIN:VEVENT/g) ?? []).length).toBe(later);
  });

  it("folds long lines at 75 octets without splitting a character", () => {
    const line = "DESCRIPTION:" + "å".repeat(80);
    const parts = fold(line).split("\r\n ");
    for (const [i, p] of parts.entries()) expect(new TextEncoder().encode(p).length).toBeLessThanOrEqual(i ? 74 : 75);
    expect(parts.join("")).toBe(line);
  });

  it("ends every line with CRLF", () => {
    const s = toIcs(demoPlan());
    expect(s.endsWith("END:VCALENDAR\r\n")).toBe(true);
    expect(s.replace(/\r\n/g, "")).not.toMatch(/[\r\n]/);
  });
});
