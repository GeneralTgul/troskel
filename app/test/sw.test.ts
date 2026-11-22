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

it("opens the app from the cache at once, without waiting for a network that never answers (#844)", async () => {
  const handlers: Record<string, Handler> = {};
  const shell = new Response("the app");
  const privacy = new Response("privacy");
  const asked: string[] = [];
  const self = {
    registration: { scope: "https://x.test/troskel/app/" },
    addEventListener: (type: string, h: Handler) => { handlers[type] = h; },
  };
  const caches = {
    match: async (req: Request | string) => {
      const url = typeof req === "string" ? req : req.url;
      return url === "https://x.test/troskel/app/" ? shell : url.endsWith("/privacy.html") ? privacy : undefined;
    },
  };
  // A weak connection: the request neither answers nor fails.
  const fetch = (req: Request) => { asked.push(req.url); return new Promise<Response>(() => {}); };
  const src = readFileSync(resolve(process.cwd(), "src/sw.js"), "utf-8")
    .replace("__VERSION__", "test").replace("__FILES__", "[]");
  new Function("self", "caches", "fetch", src)(self, caches, fetch);

  const open = (url: string) => {
    let answer: Promise<Response> | null = null;
    const request = { url, method: "GET", mode: "navigate" } as unknown as Request;
    handlers.fetch({ request, respondWith: (r) => { answer = r; } });
    return Promise.race([answer!, new Promise<string>((r) => setTimeout(() => r("waited for the network"), 50))]);
  };
  expect(await open("https://x.test/troskel/app/?view=week")).toBe(shell);
  expect(await open("https://x.test/troskel/app/index.html")).toBe(shell);   // not kept under that name: the app itself
  expect(await open("https://x.test/troskel/app/privacy.html")).toBe(privacy);
  expect(asked).toEqual([]);
});

it("is registered in the browser, and taken away with its caches in an app store build (#844)", async () => {
  const { setupOffline } = await import("../src/model/offline");
  const seen: string[] = [];
  const workers = {
    register: async (url: string) => { seen.push(`register ${url}`); },
    getRegistrations: async () => [{ unregister: async () => { seen.push("unregister"); return true; } }],
  };
  const caches = {
    keys: async () => ["troskel-0.1.0-abc", "other"],
    delete: async (name: string) => { seen.push(`delete ${name}`); return true; },
  };
  expect(await setupOffline({ native: false, prod: true, base: "/troskel/app/", workers, caches })).toBe("registered");
  expect(seen).toEqual(["register /troskel/app/sw.js"]);
  // An updated app must open as the new version: the cache of the version before must not answer.
  seen.length = 0;
  expect(await setupOffline({ native: true, prod: true, base: "./", workers, caches })).toBe("removed");
  expect(seen).toEqual(["unregister", "delete troskel-0.1.0-abc"]);
  expect(await setupOffline({ native: false, prod: false, base: "/", workers, caches })).toBe("none"); // the dev server
  expect(await setupOffline({ native: true, prod: true, base: "./" })).toBe("none");                   // iOS: no workers
});
