/**
 * The service worker without a network (#787): the app's own files come from the cache, also when the server answered
 * them with "Vary: Origin" and the page asks with an Origin header (a module script). Without `ignoreVary` the cache
 * missed, and the app opened as a white page in flight mode (seen in a clean Chromium against vite preview).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

type Handler = (e: { request: Request; respondWith: (r: Promise<Response>) => void }) => void;

function load() {
  const handlers: Record<string, Handler> = {};
  const calls: { url: string; opts: CacheQueryOptions | undefined }[] = [];
  const hit = new Response("cached");
  const self = {
    registration: { scope: "https://x.test/troskel/app/" },
    addEventListener: (type: string, h: Handler) => { handlers[type] = h; },
  };
  const caches = {
    match: async (req: Request | string, opts?: CacheQueryOptions) => {
      calls.push({ url: typeof req === "string" ? req : req.url, opts });
      return opts?.ignoreVary ? hit : undefined;
    },
  };
  const fetch = async () => { throw new TypeError("offline"); };
  const src = readFileSync(resolve(process.cwd(), "src/sw.js"), "utf-8")
    .replace("__VERSION__", "test").replace("__FILES__", "[]");
  new Function("self", "caches", "fetch", src)(self, caches, fetch);
  return { handlers, calls, hit };
}

it("answers the app's files from the cache without a network, whatever the server's Vary said", async () => {
  const { handlers, calls, hit } = load();
  let answer: Promise<Response> | null = null;
  const request = new Request("https://x.test/troskel/app/assets/index-abc.js", { headers: { Origin: "https://x.test" } });
  handlers.fetch({ request, respondWith: (r) => { answer = r; } });
  expect(await answer!).toBe(hit);
  expect(calls[0].opts).toMatchObject({ ignoreSearch: true, ignoreVary: true });
});
