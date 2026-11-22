/**
 * The service worker (src/sw.js) keeps the web app's files so that it opens without a network. The app store builds
 * carry their files in the app itself and must not have one: its cache would answer with the files of the version
 * that was installed before, so an updated app would open as the old one. A build that registered one earlier gets it
 * taken away, with its caches.
 */

interface Registration { unregister(): Promise<boolean> }
export interface WorkerContainer {
  register(url: string): Promise<unknown>;
  getRegistrations?: () => Promise<readonly Registration[]>;
}
export interface CacheNames {
  keys(): Promise<string[]>;
  delete(name: string): Promise<boolean>;
}

export interface OfflineEnv {
  native: boolean;
  /** A built app (not the dev server, which has no sw.js). */
  prod: boolean;
  base: string;
  workers?: WorkerContainer;
  caches?: CacheNames;
}

/** "registered" in the browser, "removed" in an app store build, "none" where there is nothing to do. */
export async function setupOffline(env: OfflineEnv): Promise<"registered" | "removed" | "none"> {
  if (!env.workers) return "none";
  try {
    if (env.native) {
      for (const r of (await env.workers.getRegistrations?.()) ?? []) await r.unregister();
      for (const k of (await env.caches?.keys()) ?? []) if (k.startsWith("troskel-")) await env.caches!.delete(k);
      return "removed";
    }
    if (!env.prod) return "none";
    await env.workers.register(`${env.base}sw.js`);
    return "registered";
  } catch {
    return "none";
  }
}
