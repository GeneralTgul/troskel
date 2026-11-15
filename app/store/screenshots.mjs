// Takes the app-store screenshots of the athlete's app, in Swedish and English, for every size the stores ask for.
//
//   TROSKEL_BASE=./ npx vite build && node store/screenshots.mjs [--out store/screenshots] [--only iphone69,android-phone]
//
// Starts `vite preview` on the built app (dist/), opens it with the example plan (public/demo.troskel) as a
// link (#p=…, the same way the coach sends a plan, so the app does not show the example banner) and photographs
// each tab. The clock is fixed to a weekday in the example plan's first week, so the pictures stay the same.
// Needs Playwright (not a dependency of the app): `npm i --no-save playwright@1.65.1 && npx playwright install chromium`.
// CHROMIUM_PATH points at another Chromium build when Playwright's own cannot be downloaded.
// The PNGs land in store/screenshots/<lang>/<device>/, which git ignores: they are uploaded, not committed.
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const APP = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4179;
const FIXED_TIME = "2026-11-18T07:30:00";   // a Wednesday in the example plan's first week

// CSS size × scale = the pixels the stores ask for (docs/app/butikerna/butikerna.md, Skärmbilderna).
export const DEVICES = {
  "iphone69": { width: 430, height: 932, scale: 3, mobile: true },      // 1290 × 2796, App Store 6.9"
  "iphone65": { width: 428, height: 926, scale: 3, mobile: true },      // 1284 × 2778, App Store 6.5"
  "ipad13": { width: 1032, height: 1376, scale: 2, mobile: true },      // 2064 × 2752, App Store 13" iPad
  "android-phone": { width: 360, height: 640, scale: 3, mobile: true }, // 1080 × 1920, Google Play phone (9:16)
  "android-7": { width: 603, height: 1072, scale: 2, mobile: true },    // 1206 × 2144, Google Play 7" tablet (9:16)
  "android-10": { width: 900, height: 1600, scale: 2, mobile: true },   // 1800 × 3200, Google Play 10" tablet (9:16)
};
const LANGS = { sv: "sv-SE", en: "en-US" };
const TABS = ["today", "week", "plan", "times"];

/** The #p= data of a plan file: its JSON, deflated in the zlib format, as base64url without padding. */
export function linkData(planText) {
  const json = JSON.stringify(JSON.parse(planText));
  return deflateSync(Buffer.from(json, "utf-8"), { level: 9 }).toString("base64url");
}

function args(argv) {
  const out = { out: join(APP, "store", "screenshots"), only: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--out") out.out = resolve(argv[++i]);
    else if (argv[i] === "--only") out.only = argv[++i].split(",");
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
  const { chromium } = await import("playwright");
  // vite preview serves dist/ under the base it was built with (TROSKEL_BASE, see vite.config.ts).
  const built = process.env.TROSKEL_BASE ?? "/troskel/app/";
  const base = `http://127.0.0.1:${PORT}${built.startsWith("/") ? built : "/"}`;
  const preview = spawn("npx", ["vite", "preview", "--port", String(PORT), "--strictPort", "--host", "127.0.0.1"],
                        { cwd: APP, stdio: "inherit" });
  try {
    await waitFor(base);
    const data = linkData(readFileSync(join(APP, "public", "demo.troskel"), "utf-8"));
    const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
    for (const [lang, locale] of Object.entries(LANGS)) {
      const labels = JSON.parse(readFileSync(join(APP, "src", "i18n", `ui.${lang}.json`), "utf-8"));
      for (const [name, d] of Object.entries(DEVICES)) {
        if (opt.only && !opt.only.includes(name)) continue;
        const ctx = await browser.newContext({ locale, viewport: { width: d.width, height: d.height },
                                               deviceScaleFactor: d.scale, isMobile: d.mobile, hasTouch: true,
                                               colorScheme: "dark", timezoneId: "Europe/Stockholm" });
        const page = await ctx.newPage();
        await page.clock.setFixedTime(new Date(FIXED_TIME));
        await page.goto(`${base}#p=${data}`);
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(4000);   // let the "plan loaded" message go away before the first picture
        const dir = join(opt.out, lang, name);
        mkdirSync(dir, { recursive: true });
        for (const [i, tab] of TABS.entries()) {
          const label = labels[`ui.tab.${tab}`];
          const button = page.getByRole("button", { name: label, exact: true }).or(page.getByRole("tab", { name: label, exact: true }));
          if (await button.count()) await button.first().click();
          await page.waitForTimeout(300);
          await page.screenshot({ path: join(dir, `${String(i + 1).padStart(2, "0")}-${tab}.png`) });
        }
        await ctx.close();
        console.log(`${lang} ${name}: ${TABS.length} screenshots in ${dir}`);
      }
    }
    await browser.close();
  } finally {
    preview.kill();
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((e) => { console.error(e); process.exit(1); });
}
