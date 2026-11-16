// Takes the app-store screenshots of the athlete's app, in Swedish and English, for every size the stores ask for.
//
//   TROSKEL_BASE=./ npx vite build && node store/screenshots.mjs [--out store/screenshots] [--only iphone69,android-phone]
//
// Starts `vite preview` on the built app (dist/), opens it with the example plan (public/demo.troskel) as a
// link (#p=…, the same way the coach sends a plan, so the app does not show the example banner) and photographs
// each tab. The clock is fixed to a weekday in the example plan's first week, so the pictures stay the same.
// Needs Playwright (not a dependency of the app): `npm i --no-save playwright@1.65.1 && npx playwright install chromium`.
// CHROMIUM_PATH points at another Chromium build when Playwright's own cannot be downloaded, and PLAYWRIGHT_PATH at
// a Playwright installed somewhere else than the app's node_modules (its folder, …/node_modules/playwright).
// Every picture is also checked: the tab labels stand on one line and nothing is wider than the screen. `--check`
// does only that, without pictures, also on a 320 px phone and with larger text; a problem ends the run with an error.
// The English pictures get English names for the example athlete, the races and the test (NAMES_EN): the example
// plan itself (public/demo.troskel) is Swedish.
// The PNGs land in store/screenshots/<lang>/<device>/, which git ignores: they are uploaded, not committed.
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { deflateSync } from "node:zlib";

const APP = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4179;
const FIXED_TIME = "2026-11-19T07:30:00";   // a Thursday in the example plan's first week, with a workout today

// CSS size × scale = the pixels the stores ask for (docs/app/butikerna/butikerna.md, Skärmbilderna).
export const DEVICES = {
  "iphone69": { width: 430, height: 932, scale: 3, mobile: true },      // 1290 × 2796, App Store 6.9"
  "iphone65": { width: 428, height: 926, scale: 3, mobile: true },      // 1284 × 2778, App Store 6.5"
  "ipad13": { width: 1032, height: 1376, scale: 2, mobile: true },      // 2064 × 2752, App Store 13" iPad
  "android-phone": { width: 360, height: 640, scale: 3, mobile: true }, // 1080 × 1920, Google Play phone (9:16)
  "android-7": { width: 603, height: 1072, scale: 2, mobile: true },    // 1206 × 2144, Google Play 7" tablet (9:16)
  "android-10": { width: 900, height: 1600, scale: 2, mobile: true },   // 1800 × 3200, Google Play 10" tablet (9:16)
};
// Only for --check: the narrowest phone, and the Android phone with the text a quarter larger.
export const CHECK_DEVICES = {
  "narrow-320": { width: 320, height: 568, scale: 2, mobile: true },
  "large-text": { width: 360, height: 640, scale: 2, mobile: true, fontPx: 21 },
};
const LANGS = { sv: "sv-SE", en: "en-US" };
// The example plan's Swedish names in the English pictures: every name in it (app/test/multisport_views.test.tsx).
export const NAMES_EN = {
  "Exempel Löpare": "Example Runner", "Stadsvarvet": "City Half", "Seedningslopp": "Seeding race",
  "Laktattrappa": "Lactate step test",
};

/** The plan with every text that is exactly one of `names` replaced, wherever it stands (athlete, goals, notes). */
export function renamed(value, names) {
  if (typeof value === "string") return Object.hasOwn(names, value) ? names[value] : value;
  if (Array.isArray(value)) return value.map((v) => renamed(v, names));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, renamed(v, names)]));
  return value;
}

/** What is wrong with the layout of the page as it stands: a tab label on two lines, or something wider than the screen. */
export async function layoutProblems(page) {
  return page.evaluate(() => {
    const out = [];
    const width = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth > width) out.push(`the page is ${document.documentElement.scrollWidth} px wide`);
    const tabs = [...document.querySelectorAll(".tabs button")];
    for (const b of tabs) {
      const label = b.querySelector("span");
      if (label.getClientRects().length > 1 || label.getBoundingClientRect().height > 1.5 * parseFloat(getComputedStyle(label).lineHeight))
        out.push(`the tab "${label.textContent}" is on two lines`);
      if (label.scrollWidth > b.clientWidth + 1) out.push(`the tab "${label.textContent}" is wider than its button`);
    }
    for (const el of document.querySelectorAll("main .chip .zn"))
      if (el.scrollWidth > el.clientWidth + 1) out.push(`the zone name "${el.textContent}" is cut off`);
    for (const el of document.querySelectorAll("main h2, main h3, main .day-title, main .targets dd, main .bar-label, main .chip")) {
      const r = el.getBoundingClientRect();
      const box = (el.closest(".workout, .day, .bars, .col") ?? document.documentElement).getBoundingClientRect();
      if (r.width && (r.right > width + 1 || r.right > box.right + 1 || r.left < box.left - 1))
        out.push(`"${el.textContent.trim().slice(0, 40)}" does not fit`);
    }
    return out;
  });
}
const TABS = ["today", "week", "plan", "times"];

/** The #p= data of a plan file: its JSON, deflated in the zlib format, as base64url without padding. */
export function linkData(planText) {
  const json = JSON.stringify(JSON.parse(planText));
  return deflateSync(Buffer.from(json, "utf-8"), { level: 9 }).toString("base64url");
}

function args(argv) {
  const out = { out: join(APP, "store", "screenshots"), only: null, check: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--out") out.out = resolve(argv[++i]);
    else if (argv[i] === "--only") out.only = argv[++i].split(",");
    else if (argv[i] === "--check") out.check = true;
  }
  return out;
}

async function waitFor(url, ms = 30000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`vite preview did not answer on ${url}`);
}

async function main() {
  const opt = args(process.argv.slice(2));
  const pw = process.env.PLAYWRIGHT_PATH;
  const { chromium } = await import(pw ? pathToFileURL(join(resolve(pw), "index.mjs")).href : "playwright");
  // vite preview serves dist/ under the base it was built with (TROSKEL_BASE, see vite.config.ts).
  const built = process.env.TROSKEL_BASE ?? "/troskel/app/";
  const base = `http://127.0.0.1:${PORT}${built.startsWith("/") ? built : "/"}`;
  // Vite itself, not through npx: killing npx left vite preview running on the port.
  const vite = join(APP, "node_modules", "vite", "bin", "vite.js");
  const preview = spawn(process.execPath, [vite, "preview", "--port", String(PORT), "--strictPort", "--host", "127.0.0.1"],
                        { cwd: APP, stdio: "inherit" });
  try {
    await waitFor(base);
    const demo = readFileSync(join(APP, "public", "demo.troskel"), "utf-8");
    const data = { sv: linkData(demo), en: linkData(JSON.stringify(renamed(JSON.parse(demo), NAMES_EN))) };
    const devices = opt.check ? { ...DEVICES, ...CHECK_DEVICES } : DEVICES;
    const tabs = opt.check ? [...TABS, "about"] : TABS;
    const problems = [];
    const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
    for (const [lang, locale] of Object.entries(LANGS)) {
      const labels = JSON.parse(readFileSync(join(APP, "src", "i18n", `ui.${lang}.json`), "utf-8"));
      for (const [name, d] of Object.entries(devices)) {
        if (opt.only && !opt.only.includes(name)) continue;
        const ctx = await browser.newContext({ locale, viewport: { width: d.width, height: d.height },
                                               deviceScaleFactor: d.scale, isMobile: d.mobile, hasTouch: true,
                                               colorScheme: "dark", timezoneId: "Europe/Stockholm" });
        const page = await ctx.newPage();
        await page.clock.setFixedTime(new Date(FIXED_TIME));
        await page.goto(`${base}#p=${data[lang]}`);
        await page.waitForLoadState("networkidle");
        if (d.fontPx) await page.addStyleTag({ content: `html, body { font-size: ${d.fontPx}px; }` });
        if (!opt.check) await page.waitForTimeout(4000);   // let the "plan loaded" message go away before the first picture
        const dir = join(opt.out, lang, name);
        if (!opt.check) mkdirSync(dir, { recursive: true });
        for (const [i, tab] of tabs.entries()) {
          const label = labels[`ui.tab.${tab}`];
          const button = page.getByRole("button", { name: label, exact: true }).or(page.getByRole("tab", { name: label, exact: true }));
          if (await button.count()) await button.first().click();
          await page.waitForTimeout(300);
          if (!opt.check) await page.screenshot({ path: join(dir, `${String(i + 1).padStart(2, "0")}-${tab}.png`) });
          for (const p of await layoutProblems(page)) problems.push(`${lang} ${name} ${tab}: ${p}`);
        }
        await ctx.close();
        console.log(opt.check ? `${lang} ${name}: checked` : `${lang} ${name}: ${TABS.length} screenshots in ${dir}`);
      }
    }
    await browser.close();
    if (problems.length) throw new Error(`the layout does not hold:\n  ${problems.join("\n  ")}`);
  } finally {
    preview.kill();
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((e) => { console.error(e); process.exit(1); });
}
