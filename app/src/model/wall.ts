/**
 * The wall mode for a tablet on the wall: the screen is kept on (the Screen Wake Lock API, which the app store builds
 * have through their web view where it supports it), and the app goes back to today and this week after three
 * minutes without a touch.
 */

export const WALL_IDLE_MS = 3 * 60_000;

export interface Timers {
  set: (f: () => void, ms: number) => unknown;
  clear: (id: unknown) => void;
}

const realTimers: Timers = {
  set: (f, ms) => setTimeout(f, ms),
  clear: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
};

/** A timer that calls `onIdle` once `ms` have passed since the last `poke`. */
export function idleTimer(ms: number, onIdle: () => void, timers: Timers = realTimers) {
  let id: unknown = null;
  const stop = () => {
    if (id != null) timers.clear(id);
    id = null;
  };
  return {
    poke() {
      stop();
      id = timers.set(() => {
        id = null;
        onIdle();
      }, ms);
    },
    stop,
  };
}

interface Sentinel { release(): Promise<void> }
export interface WakeLockApi { request(type: "screen"): Promise<Sentinel> }

export const canKeepAwake = (nav: { wakeLock?: unknown } | undefined = globalThis.navigator as never) => !!nav?.wakeLock;

/**
 * Keeps the screen on while the page is shown. The browser lets the lock go when the page is hidden, so it is taken
 * again each time the page comes back. Returns the function that lets it go for good. Does nothing without the API.
 */
export function keepAwake(
  lock: WakeLockApi | undefined = (globalThis.navigator as { wakeLock?: WakeLockApi } | undefined)?.wakeLock,
  doc: Pick<Document, "visibilityState" | "addEventListener" | "removeEventListener"> | undefined = globalThis.document,
): () => void {
  if (!lock) return () => {};
  let held: Sentinel | null = null;
  let stopped = false;
  const take = () => {
    if (stopped || (doc && doc.visibilityState !== "visible")) return;
    lock.request("screen").then((s) => {
      if (stopped) void s.release().catch(() => {});
      else held = s;
    }).catch(() => {
      /* refused (battery saver, no user gesture yet): the screen follows the device's own setting */
    });
  };
  const onVisible = () => {
    if (doc?.visibilityState === "visible") take();
  };
  doc?.addEventListener("visibilitychange", onVisible);
  take();
  return () => {
    stopped = true;
    doc?.removeEventListener("visibilitychange", onVisible);
    if (held) void held.release().catch(() => {});
    held = null;
  };
}
