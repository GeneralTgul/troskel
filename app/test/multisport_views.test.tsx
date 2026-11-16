// @vitest-environment happy-dom
/** The views with a plan of several sports: the zones of each sport, the week numbers of a long plan, a race's
 * distance in the language's own form, and the texts the screenshots are checked against (store/screenshots.mjs). */
import { render } from "preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../src/i18n";
import type { StoredPlan } from "../src/model/types";
import { labelStep, Overview } from "../src/views/Overview";
import { distanceText, WorkoutCard, ZONE_COLORS } from "../src/views/parts";
import { dayColor, Week } from "../src/views/Week";
import { demoPlan } from "./helpers";
// @ts-expect-error a plain script without types
import { NAMES_EN, renamed } from "../store/screenshots.mjs";

const fx: { plans: Record<string, StoredPlan> } = JSON.parse(
  gunzipSync(readFileSync(resolve(process.cwd(), "test/fixtures/engine.json.gz"))).toString("utf-8"));
const all = (p: StoredPlan) => p.plan.weeks.flatMap((wk) => wk.workouts);
let root: HTMLElement | null = null;

function show(node: preact.ComponentChild): HTMLElement {
  root = document.createElement("div");
  document.body.appendChild(root);
  render(node, root);
  return root;
}

afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  setLang("sv");
});

describe("a plan with several sports in the views", () => {
  it("lists the zones of every sport that has targets, the main sport first", () => {
    setLang("sv");
    const el = show(<Overview stored={fx.plans["triathlon-css"]} today="2026-11-18" />);
    const sports = [...el.querySelectorAll<HTMLElement>(".zone-sport")];
    expect(sports.map((s) => s.dataset.sport)).toEqual(["run", "bike", "swim"]);
    expect(sports.map((s) => s.querySelector("h4")!.textContent)).toEqual(["Löpning", "Cykel", "Simning"]);
    expect(sports[0].textContent).toMatch(/min\/km/);
    expect(sports[1].textContent).toMatch(/180–210 W/);
    expect(sports[1].textContent).not.toMatch(/min\/km/);
    expect(sports[2].textContent).toMatch(/\d:\d\d–\d:\d\d \/100 m/);
    expect(sports[2].textContent).toContain("CSS-test");
  });

  it("leaves out a sport without targets, and has no sport heading in a plan with one sport", () => {
    const plain = show(<Overview stored={fx.plans["swim-without-test"]} today="2026-11-18" />);
    expect([...plain.querySelectorAll<HTMLElement>(".zone-sport")].map((s) => s.dataset.sport)).toEqual(["run"]);
    render(null, root!);
    const one = show(<Overview stored={demoPlan()} today="2026-11-18" />);
    expect(one.querySelectorAll(".zone-sport")).toHaveLength(1);
    expect(one.querySelector(".zone-sport h4")).toBeNull();
  });

  it("numbers every week of a short plan and fewer of a long one, with a bar for each", () => {
    expect([5, 12, 13, 24, 25, 52].map(labelStep)).toEqual([1, 1, 2, 2, 4, 4]);
    const labels = (p: StoredPlan) =>
      [...show(<Overview stored={p} today="2026-11-18" />).querySelectorAll(".bar-label")].map((l) => l.textContent!.trim());
    expect(labels(demoPlan())).toEqual(["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]);
    render(null, root!);
    const long = labels(fx.plans["rolling"]);
    expect(long).toHaveLength(20);
    expect(long.filter(Boolean)).toEqual(["1", "3", "5", "7", "9", "11", "13", "15", "17", "19"]);
  });

  it("writes a race's distance with the language's decimal sign", () => {
    setLang("sv");
    expect(distanceText(21.1)).toBe("21,1 km");
    expect(distanceText(10)).toBe("10 km");
    expect(show(<Overview stored={demoPlan()} today="2026-11-18" />).querySelector(".list")!.textContent).toContain("21,1 km");
    setLang("en");
    expect(distanceText(21.1)).toBe("21.1 km");
  });

  it("calls the pace of a swim time per 100 m and the SkiErg's time per 500 m", () => {
    setLang("sv");
    const tri = fx.plans["triathlon-css"];
    const swim = all(tri).find((w) => w.template_id === "swim_intervals")!;
    expect(show(<WorkoutCard stored={tri} w={swim} big />).querySelector(".targets dt")!.textContent).toBe("Tid per 100 m");
    render(null, root!);
    const erg = fx.plans["bike-skierg-tested"];
    const ski = all(erg).find((w) => w.sport === "skierg")!;
    const dts = [...show(<WorkoutCard stored={erg} w={ski} big />).querySelectorAll(".targets dt")].map((d) => d.textContent);
    expect(dts).toEqual(["Puls", "Watt", "Tid per 500 m"]);
  });

  it("shows a day with an endurance workout and strength as two workouts, strength without a zone", () => {
    setLang("sv");
    const p = fx.plans["run-strength"];
    const el = show(<Week stored={p} today="2026-11-16" />);
    const monday = el.querySelector(".day")!;
    const ws = [...monday.querySelectorAll(".day-w")];
    const title = (w: Element) => w.querySelector(".day-title")!.textContent;
    expect(ws.map(title).sort()).toEqual(["Löpning: återhämtningspass", "Styrka: ben och bål"]);
    const strength = ws.find((w) => title(w)!.startsWith("Styrka"))!;
    const run = ws.find((w) => title(w)!.startsWith("Löpning"))!;
    expect(strength.querySelector(".chip")).toBeNull();
    expect(run.querySelector(".chip")!.textContent).toContain("Z2");
    // The card has the run's zone colour whichever of the two comes first.
    expect((monday as HTMLElement).style.getPropertyValue("--zc")).toBe(ZONE_COLORS.Z2);
    expect(dayColor(all(p).filter((w) => w.sport === "strength").slice(0, 1))).toBe("var(--rest)");
    expect(el.querySelector(".week-of")!.textContent).toBe("Vecka 1 av 12 i schemat");
  });
});

describe("a test day", () => {
  it("says which sport a test day tests when the plan has several sports", () => {
    const days = (name: string) =>
      [...show(<Overview stored={fx.plans[name]} today="2026-11-18" />).querySelectorAll("li[data-sport]")].map((l) => l.textContent!);
    setLang("sv");
    const tri = days("triathlon-css");
    expect(tri.length).toBeGreaterThan(0);
    for (const line of tri) expect(line).toMatch(/ · Löpning/);
    render(null, root!);
    setLang("en");
    expect(days("triathlon-css")[0]).toMatch(/ · Running/);
    render(null, root!);
    // One sport, with or without strength: only the date.
    for (const name of ["demo", "run-strength"]) {
      const one = days(name);
      expect(one.length, name).toBeGreaterThan(0);
      for (const line of one) expect(line, name).not.toMatch(/ · /);
      render(null, root!);
    }
  });

  it("has no zone and no target of its own, in the card and in the week", () => {
    setLang("sv");
    for (const name of ["demo", "ski-main", "swim-alone-cycle"]) {
      const p = fx.plans[name];
      const test = all(p).find((w) => w.family === "test")!;
      const el = show(<WorkoutCard stored={p} w={test} big />);
      expect(el.querySelector(".stats .chip"), name).toBeNull();
      expect(el.querySelector(".targets"), name).toBeNull();
      expect((el.querySelector(".workout") as HTMLElement).style.getPropertyValue("--zc"), name).toBe("var(--accent)");
      render(null, root!);
      const wk = show(<Week stored={p} today={test.date} />);
      const day = wk.querySelector<HTMLElement>(".day.is-today")!;
      expect(day.querySelector(".chip"), name).toBeNull();
      expect(day.style.getPropertyValue("--zc"), name).toBe("var(--accent)");
      render(null, root!);
    }
  });
});

describe("the English names of the example plan in the store screenshots", () => {
  it("replace every Swedish name of the example, and nothing else", () => {
    const sv = demoPlan();
    const en: StoredPlan = renamed(sv, NAMES_EN);
    const names = (p: StoredPlan) => [p.athlete, ...p.request.goals.map((g) => g.name), ...p.request.capacity.map((c) => c.source)];
    expect(names(sv).filter((n) => n && !(n in NAMES_EN))).toEqual([]);
    expect(en.athlete).toBe("Example Runner");
    expect(JSON.stringify(en)).not.toMatch(/Exempel Löpare|Seedningslopp|Laktattrappa|Stadsvarvet/);
    expect(all(en).map((w) => [w.key, w.date, w.template_id])).toEqual(all(sv).map((w) => [w.key, w.date, w.template_id]));
    expect(sv.athlete).toBe("Exempel Löpare");
  });
});
