// @vitest-environment happy-dom
// A saved plan the app cannot draw (#846): the error screen with *Ta bort all data*, not a white page.
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { stub } from "../src/engine";
import { setLang } from "../src/i18n";
import { memoryStore, type Store } from "../src/model/storage";
import type { StoredPlan } from "../src/model/types";
import { Broken } from "../src/views/Broken";
import { demoPlan, demoText } from "./helpers";

let root: HTMLElement;

afterEach(() => {
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
  vi.restoreAllMocks();
});

async function mount(store: Store, reload: () => void) {
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(
      <Broken store={store} reload={reload}>
        <App store={store} engine={stub} now={() => new Date("2026-11-25T08:00:00Z")} timeZone="Europe/Stockholm"
          tab="week" loadDemo={async () => demoText()} />
      </Broken>, root);
  });
}

/** A plan that passes the check when it is read (a plan, weeks, a request) but whose weeks are broken. */
function brokenPlan(): StoredPlan {
  const p = demoPlan();
  return { ...p, plan: { ...p.plan, weeks: [null, 7] as unknown as StoredPlan["plan"]["weeks"] } };
}

describe("a broken saved plan", () => {
  it("shows the error screen, and its button deletes all data and starts again", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const store = memoryStore();
    store.setPlan(brokenPlan());
    store.setSettings({ lang: "en" });
    expect(store.plan()).not.toBeNull();
    const reload = vi.fn();
    await mount(store, reload);

    const alert = root.querySelector(".broken[role=alert]");
    expect(alert?.textContent).toContain("Something went wrong");
    const button = [...root.querySelectorAll("button")].find((b) => b.textContent === "Delete all data and start again");
    expect(button).toBeTruthy();
    vi.stubGlobal("confirm", () => true);
    await act(async () => { button!.click(); await new Promise((r) => setTimeout(r, 0)); });
    vi.unstubAllGlobals();

    expect(store.plan()).toBeNull();
    expect(store.settings()).toEqual({});
    expect(reload).toHaveBeenCalledOnce();
  });

  it("a good plan draws the app as before", async () => {
    const store = memoryStore();
    store.setPlan(demoPlan());
    store.setSettings({ lang: "sv" });
    await mount(store, () => {});
    expect(root.querySelector(".broken")).toBeNull();
    expect(root.querySelector(".week .board")).toBeTruthy();
  });
});
