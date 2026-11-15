/**
 * Texts in Swedish and English. The plan's texts (plan.*.json) are copied from the programme by
 * app/scripts/make_fixtures.py, so a plan reads the same in the app as in the printout; the app's own
 * texts are ui.*.json. Placeholders are Python's: "{name}".
 */
import planEn from "./plan.en.json";
import planSv from "./plan.sv.json";
import uiEn from "./ui.en.json";
import uiSv from "./ui.sv.json";

export type Lang = "sv" | "en";
export const LANGS: Lang[] = ["sv", "en"];

const TEXTS: Record<Lang, Record<string, string>> = {
  sv: { ...planSv, ...uiSv },
  en: { ...planEn, ...uiEn },
};

let current: Lang = "sv";

export function deviceLang(languages: readonly string[] = globalThis.navigator?.languages ?? []): Lang {
  for (const l of languages) {
    const base = l.toLowerCase().slice(0, 2);
    if (base === "sv" || base === "nb" || base === "nn" || base === "no" || base === "da") return "sv";
    if (base === "en") return "en";
  }
  return languages.length ? "en" : "sv";
}

export function setLang(lang: Lang): void {
  current = lang;
  if (typeof document !== "undefined") document.documentElement.lang = lang;
}

export function getLang(): Lang {
  return current;
}

export function has(key: string): boolean {
  return key in TEXTS[current] || key in TEXTS.sv;
}

export function t(key: string, args?: Record<string, unknown>): string {
  const text = TEXTS[current][key] ?? TEXTS.sv[key] ?? key;
  if (!args) return text;
  return text.replace(/\{(\w+)\}/g, (m, k: string) => (k in args ? String(args[k] ?? "") : m));
}

/** All keys of one language: the test that sv and en have the same keys. */
export function keys(lang: Lang): string[] {
  return Object.keys(TEXTS[lang]);
}
