import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import preact from "@preact/preset-vite";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8"));

// GitHub Pages serves the app under /troskel/app/. The app store builds (Capacitor) set TROSKEL_BASE=./ .
const BASE = process.env.TROSKEL_BASE ?? "/troskel/app/";

function publicFiles(dir: string, root = dir): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? publicFiles(p, root) : [relative(root, p).split("\\").join("/")];
  });
}

/** Writes sw.js from src/sw.js with the list of every built file, so the app works offline after one visit. */
function serviceWorker(): Plugin {
  return {
    name: "troskel-service-worker",
    apply: "build",
    generateBundle(_, bundle) {
      const files = ["./", ...Object.keys(bundle), ...publicFiles("public")].filter((f) => f !== "sw.js");
      const hash = createHash("sha256");
      for (const [name, chunk] of Object.entries(bundle))
        hash.update(name).update(chunk.type === "chunk" ? chunk.code : String(chunk.source));
      const src = readFileSync("src/sw.js", "utf-8")
        .replace("__VERSION__", `${pkg.version}-${hash.digest("hex").slice(0, 10)}`)
        .replace("__FILES__", JSON.stringify(files.map((f) => (f === "./" ? f : `./${f}`))));
      this.emitFile({ type: "asset", fileName: "sw.js", source: src });
    },
  };
}

export default defineConfig({
  base: BASE,
  plugins: [preact(), serviceWorker()],
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  build: { target: "es2020", sourcemap: false },
  test: { environment: "node", include: ["test/**/*.test.{ts,tsx}"] },
});
