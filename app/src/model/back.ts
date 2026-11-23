/**
 * Android's back button and back gesture (#846). The views say what back closes with `useBack`: the day sheet, the
 * QR scanner, the athlete's own plan form, *Ändra mina värden*, another tab than *Idag*. The last one opened is closed
 * first. With nothing open, the app goes to the background, as Android apps do on their first screen.
 *
 * Only the Android build listens (`listenBack`, from main.tsx, through `@capacitor/app`). In the browser (the PWA)
 * and in the tests nothing listens, and `useBack` only keeps a list in memory.
 */
import { useEffect, useRef } from "preact/hooks";

type Close = () => void;
const open: { close: Close }[] = [];

/** Closes the last overlay opened; false when nothing is open. */
export function goBack(): boolean {
  const top = open[open.length - 1];
  if (!top) return false;
  top.close();
  return true;
}

/** Back closes this while `active` is true. A view opened after it (the day sheet over the week) goes first. */
export function useBack(active: boolean, close: Close): void {
  const latest = useRef(close);
  latest.current = close;
  useEffect(() => {
    if (!active) return;
    const entry = { close: () => latest.current() };
    open.push(entry);
    return () => {
      const i = open.indexOf(entry);
      if (i >= 0) open.splice(i, 1);
    };
  }, [active]);
}

/** The part of `@capacitor/app` the back button needs; a stand-in in the tests. */
export interface BackPlugin {
  addListener(event: "backButton", f: () => void): Promise<{ remove(): Promise<void> }>;
  minimizeApp(): Promise<void>;
}

/** Listens to the back button: closes the last overlay, else puts the app in the background. Returns the stop. */
export async function listenBack(plugin: BackPlugin): Promise<() => void> {
  const handle = await plugin.addListener("backButton", () => {
    if (!goBack()) void plugin.minimizeApp().catch(() => {});
  });
  return () => void handle.remove();
}
