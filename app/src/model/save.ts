/**
 * Saving a file the app made (the calendar file, the copy, the plan to the coach).
 *
 * The browser downloads it: a link with `download` to a blob. A web view in an app has no downloads: Android's
 * WebView and iOS's WKWebView do nothing with such a link unless the app handles it, so in the app store builds the
 * button did nothing and the app still said the file was saved. There the file is written to the app's cache
 * (`@capacitor/filesystem`) and handed to the phone's share sheet (`@capacitor/share`), where the athlete saves it to
 * Files or sends it on. Nothing leaves the phone unless the athlete chooses a receiver there.
 *
 * `false`: the file was not saved (the share sheet was closed, or the write failed).
 */
import { Capacitor } from "@capacitor/core";

export type SaveFile = (name: string, text: string, type: string) => Promise<boolean>;

export const browserSave: SaveFile = async (name, text, type) => {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return true;
};

/** What the native save needs of the phone: write a text file in the cache and give its address, and share it. */
export interface NativeIo {
  write(name: string, text: string): Promise<string>;
  share(name: string, uri: string): Promise<void>;
}

/** The Capacitor plugins as plain functions. The modules are loaded when a file is saved, and the plugins are only
 * called, never handed on: a plugin is a Proxy that answers `then` too, and a promise resolved with one never
 * settles (#757). */
export const capacitorIo: NativeIo = {
  async write(name, text) {
    const fs = await import("@capacitor/filesystem");
    const res = await fs.Filesystem.writeFile({ path: name, data: text, directory: fs.Directory.Cache, encoding: fs.Encoding.UTF8 });
    return res.uri;
  },
  async share(name, uri) {
    const mod = await import("@capacitor/share");
    await mod.Share.share({ title: name, dialogTitle: name, files: [uri] });
  },
};

/** A file name without anything a path could use. */
export const safeName = (name: string) => name.replace(/[^\w.\-]+/g, "-").replace(/\.{2,}/g, "").replace(/^[.\-]+/, "") || "troskel.txt";

export const nativeSave = (io: NativeIo = capacitorIo): SaveFile => async (name, text) => {
  try {
    const file = safeName(name);
    await io.share(file, await io.write(file, text));
    return true;
  } catch {
    return false;
  }
};

/** The way to save a file where the app runs. */
export const saveFile = (native = Capacitor.isNativePlatform()): SaveFile => (native ? nativeSave() : browserSave);
