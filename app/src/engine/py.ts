/**
 * What Python does differently from JavaScript, for the port of lopanalys/plan: rounding, floor division,
 * float sums, min and max with a key, combinations and ISO weeks. The engine uses these everywhere Python
 * does, in the same order, so that the TypeScript engine gives the same plan to the last bit.
 */
import { addDays, daysBetween, weekday } from "../model/dates";
import type { IsoDate } from "../model/types";

/** Python's round(x): halves go to the even number. */
export function round(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/** Python's round(x, nd): the exact binary value rounded to `nd` decimals, a true tie to the even digit. */
export function roundTo(x: number, nd: number): number {
  const exact = Math.abs(x).toFixed(100);
  const dot = exact.indexOf(".");
  const rest = exact.slice(dot + 1 + nd);
  if (/^50*$/.test(rest)) {
    const units = Number(exact.slice(0, dot) + exact.slice(dot + 1, dot + 1 + nd));
    const n = units % 2 === 0 ? units : units + 1;
    return (Math.sign(x) || 1) * n / 10 ** nd;
  }
  return Number(x.toFixed(nd));
}

/** Python's round(x, 2). */
export const round2 = (x: number) => roundTo(x, 2);

/** Python's a // b for floats and ints (float_divmod in CPython). */
export function floordiv(a: number, b: number): number {
  let mod = a % b;
  let div = (a - mod) / b;
  if (mod && (b < 0) !== (mod < 0)) {
    mod += b;
    div -= 1;
  }
  if (!div) return 0;
  let fl = Math.floor(div);
  if (div - fl > 0.5) fl += 1;
  return fl;
}

/** Python's a % b (the sign of b). */
export const mod = (a: number, b: number) => ((a % b) + b) % b;

/** Python's sum() of floats: since 3.12 with compensated (Neumaier) summation. */
export function sum(xs: Iterable<number>): number {
  let total = 0;
  let c = 0;
  for (const x of xs) {
    const t = total + x;
    c += Math.abs(total) >= Math.abs(x) ? (total - t) + x : (x - t) + total;
    total = t;
  }
  return c && Number.isFinite(c) ? total + c : total;
}

type Key = number | string | (number | string)[];

/** Python's comparison of keys: numbers, strings and tuples of them. */
export function cmp(a: Key, b: Key): number {
  if (Array.isArray(a) && Array.isArray(b)) {
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      const c = cmp(a[i], b[i]);
      if (c) return c;
    }
    return a.length - b.length;
  }
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Python's max(xs, key=...): the first of the largest. */
export function maxBy<T>(xs: readonly T[], key: (x: T) => Key): T {
  if (!xs.length) throw new Error("max() of nothing");
  let best = xs[0];
  let bk = key(best);
  for (const x of xs.slice(1)) {
    const k = key(x);
    if (cmp(k, bk) > 0) [best, bk] = [x, k];
  }
  return best;
}

/** Python's min(xs, key=...): the first of the smallest. */
export function minBy<T>(xs: readonly T[], key: (x: T) => Key): T {
  if (!xs.length) throw new Error("min() of nothing");
  let best = xs[0];
  let bk = key(best);
  for (const x of xs.slice(1)) {
    const k = key(x);
    if (cmp(k, bk) < 0) [best, bk] = [x, k];
  }
  return best;
}

/** Python's sorted(xs, key=...): stable. */
export const sortedBy = <T>(xs: readonly T[], key: (x: T) => Key): T[] =>
  [...xs].sort((a, b) => cmp(key(a), key(b)));

/** itertools.combinations: in the order Python gives them. */
export function* combinations<T>(xs: readonly T[], k: number): Generator<T[]> {
  const n = xs.length;
  if (k > n) return;
  const idx = Array.from({ length: k }, (_, i) => i);
  yield idx.map((i) => xs[i]);
  for (;;) {
    let i = k - 1;
    while (i >= 0 && idx[i] === i + n - k) i--;
    if (i < 0) return;
    idx[i]++;
    for (let j = i + 1; j < k; j++) idx[j] = idx[j - 1] + 1;
    yield idx.map((x) => xs[x]);
  }
}

/** date.isocalendar(): the ISO year and week. */
export function isoYearWeek(iso: IsoDate): [number, number] {
  const thursday = addDays(iso, 3 - weekday(iso));
  const year = Number(thursday.slice(0, 4));
  return [year, Math.floor(daysBetween(`${year}-01-01`, thursday) / 7) + 1];
}

export { addDays, daysBetween, weekday };
