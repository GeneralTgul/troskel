/**
 * The plan's next test (#778): a port of lopanalys/plan/next_test.py, checked against it with the fixtures of
 * app/scripts/make_fixtures.py (test/fixtures/nexttest.json.gz, test/nexttest.test.tsx).
 *
 * The targets of the workouts come from one test (or the athlete's own values). After the next test they fit worse
 * and worse, and new targets need a new test or new own values; *Schemat* shows the day and says so after it.
 * - `plan.next_test` is the coach's day, chosen in Tröskel; missing gives the default.
 * - The default: the test day of the plan's first test week in the 8th to 12th week after the test of the main
 *   sport (a week that begins 7 to 11 weeks after it), or after the plan's start when there is no test or the test
 *   is older (the workout of the test family, the main sport first; the Monday of a sketched week). Without such a
 *   test week: 10 weeks after.
 */
import { capacityFor, mainSport } from "../engine/models";
import { ownValues } from "../engine/zones";
import { addDays } from "./dates";
import type { IsoDate, StoredPlan } from "./types";

export const WINDOW_WEEKS: [number, number] = [8, 12];
export const FALLBACK_WEEKS = 10;

export type BasisKind = "test" | "own" | "none";

/** What the targets of the main sport build on (next_test.basis). */
export function basis(stored: StoredPlan): { kind: BasisKind; tested: IsoDate | null } {
  const cap = capacityFor(stored.request, mainSport(stored.request));
  if (ownValues(cap)) return { kind: "own", tested: null };
  if (cap.anchors && cap.tested) return { kind: "test", tested: cap.tested };
  return { kind: "none", tested: null };
}

/** The day the default counts from: the test of the main sport, or the plan's start when there is no test or the
 * test is older than the plan (next_test.anchor). */
export function anchor(stored: StoredPlan): IsoDate {
  const tested = basis(stored).tested;
  const start = stored.plan.weeks[0].monday;
  return tested && tested > start ? tested : start;
}

/** The test day of the first test week in the 8th to 12th week after `anchor`, else 10 weeks after it
 * (next_test.default_next_test). */
export function defaultNextTest(stored: StoredPlan): IsoDate {
  const start = anchor(stored);
  const lo = addDays(start, (WINDOW_WEEKS[0] - 1) * 7);
  const hi = addDays(start, (WINDOW_WEEKS[1] - 1) * 7);
  const main = mainSport(stored.request);
  for (const wk of stored.plan.weeks) {
    if (wk.kind !== "test" || wk.monday < lo || wk.monday > hi) continue;
    const tests = wk.workouts.filter((w) => w.family === "test")
      .map((w, i) => ({ w, i }))
      .sort((a, b) => Number(a.w.sport !== main) - Number(b.w.sport !== main) || a.w.date.localeCompare(b.w.date) || a.i - b.i);
    return tests.length ? tests[0].w.date : wk.monday;
  }
  return addDays(start, FALLBACK_WEEKS * 7);
}

export interface NextTest {
  /** The day of the next test. */
  day: IsoDate;
  /** True for the default, false for the coach's day. */
  auto: boolean;
  basis: BasisKind;
  /** The day of the test the targets build on ("test" only). */
  tested: IsoDate | null;
  /** The day has passed: the targets fit worse and worse. */
  passed: boolean;
}

/** The next test as *Schemat* shows it on `today`. */
export function nextTest(stored: StoredPlan, today: IsoDate): NextTest {
  const day = stored.plan.next_test ?? defaultNextTest(stored);
  const b = basis(stored);
  return { day, auto: !stored.plan.next_test, basis: b.kind, tested: b.tested, passed: today > day };
}
