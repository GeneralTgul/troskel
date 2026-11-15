/**
 * Plain calendar dates as ISO strings ("2026-11-16"). The week starts on Monday (ISO 8601), and "today"
 * is the date on the wall clock in a time zone: the device's, unless one is given (Europe/Stockholm in the tests).
 * The arithmetic runs in UTC so that summer time never moves a date.
 */
import { t } from "../i18n";
import { WEEKDAY_KEYS, type IsoDate } from "./types";

const DAY_MS = 86_400_000;

function toUtc(iso: IsoDate): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function fromUtc(ms: number): IsoDate {
  return new Date(ms).toISOString().slice(0, 10);
}

export function today(now: Date = new Date(), timeZone?: string): IsoDate {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function addDays(iso: IsoDate, n: number): IsoDate {
  return fromUtc(toUtc(iso) + n * DAY_MS);
}

export function daysBetween(a: IsoDate, b: IsoDate): number {
  return Math.round((toUtc(b) - toUtc(a)) / DAY_MS);
}

/** 0 = Monday ... 6 = Sunday. */
export function weekday(iso: IsoDate): number {
  return (new Date(toUtc(iso)).getUTCDay() + 6) % 7;
}

export function mondayOf(iso: IsoDate): IsoDate {
  return addDays(iso, -weekday(iso));
}

export function weekDays(monday: IsoDate): IsoDate[] {
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

/** The ISO 8601 week number (Swedish "vecka 47"). */
export function isoWeek(iso: IsoDate): number {
  const thursday = addDays(iso, 3 - weekday(iso));
  const jan1 = `${thursday.slice(0, 4)}-01-01`;
  return Math.floor(daysBetween(jan1, thursday) / 7) + 1;
}

export function weekdayShort(i: number): string {
  return t(`plan.weekday.${WEEKDAY_KEYS[i]}`);
}

export function weekdayLong(i: number): string {
  return t(`ui.weekday_long.${WEEKDAY_KEYS[i]}`);
}

/** "16 nov" in the plan's words. */
export function dayMonth(iso: IsoDate): string {
  const [, m, d] = iso.split("-").map(Number);
  return t("plan.fmt.day", { day: d, month: t(`plan.month.${m}`) });
}
