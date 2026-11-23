// @vitest-environment happy-dom
// Android's back button (#846): it closes the day sheet and the QR scanner, goes back to *I dag*, and only then puts
// the app in the background. The App plugin is a stand-in that keeps the listener.
import { render } from "preact";
import { act } from "preact/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, type Tab } from "../src/App";
import { stub } from "../src/engine";
import { setLang } from "../src/i18n";
import { goBack, listenBack, type BackPlugin } from "../src/model/back";
import { memoryStore } from "../src/model/storage";
import type { StoredPlan } from "../src/model/types";
import { demoPlan, demoText } from "./helpers";

let root: HTMLElement;
let press: () => void;
let stop: () => void;
const plugin = {
  minimizeApp: vi.fn(async () => {}),
  addListener: vi.fn(async (_e: "backButton", f: () => void) => {
    press = f;
    return { remove: async () => {} };
  }),
} satisfies BackPlugin;

beforeEach(async () => {
  plugin.minimizeApp.mockClear();
  stop = await listenBack(plugin);
});

afterEach(() => {
  stop();
  if (root) render(null, root);
  document.body.innerHTML = "";
  setLang("sv");
});

async function mount(plan: StoredPlan | null, tab?: Tab) {
  const store = memoryStore();
  if (plan) store.setPlan(plan);
  store.setSettings({ lang: "en" });
  root = document.createElement("div");
  document.body.append(root);
  await act(async () => {
    render(<App store={store} engine={stub} now={() => new Date("2026-11-25T08:00:00Z")} timeZone="Europe/Stockholm"
      tab={tab} loadDemo={async () => demoText()} hasCamera />, root);
  });
}

const back = () => act(async () => { press(); });
const click = (el: Element | null | undefined) => act(async () => {
  expect(el).toBeTruthy();
  (el as HTMLElement).click();
});
const current = () => root.querySelector(".tabs [aria-current=page]")?.textContent;

describe("the back button", () => {
  it("listens to backButton", () => {
    expect(plugin.addListener).toHaveBeenCalledWith("backButton", expect.any(Function));
  });

  it("closes the day sheet, then goes back to today, then puts the app in the background", async () => {
    await mount(demoPlan(), "week");
    await click(root.querySelector(".board .day[role=button]"));
    expect(root.querySelector(".sheet[role=dialog]")).not.toBeNull();

    await back();
    expect(root.querySelector(".sheet")).toBeNull();
    expect(root.querySelector(".week .board")).not.toBeNull();
    expect(plugin.minimizeApp).not.toHaveBeenCalled();

    await back();
    expect(root.querySelector(".week")).toBeNull();
    expect(current()).toContain("Today");
    expect(plugin.minimizeApp).not.toHaveBeenCalled();

    await back();
    expect(plugin.minimizeApp).toHaveBeenCalledOnce();
  });

  it("closes the QR scanner and goes back to the welcome page", async () => {
    await mount(null);
    await click([...root.querySelectorAll("button")].find((b) => b.textContent === "Scan a QR code"));
    expect(root.querySelector(".qr[role=dialog]")).not.toBeNull();

    await back();
    expect(root.querySelector(".qr")).toBeNull();
    expect(root.querySelector(".welcome")).not.toBeNull();
    expect(plugin.minimizeApp).not.toHaveBeenCalled();

    await back();
    expect(plugin.minimizeApp).toHaveBeenCalledOnce();
  });

  it("closes *Change my values* before it leaves the plan tab", async () => {
    await mount(demoPlan(), "plan");
    await click([...root.querySelectorAll(".overview button")].find((b) => b.textContent === "Change my values"));
    expect(root.querySelector(".view.values")).not.toBeNull();

    await back();
    expect(root.querySelector(".view.values")).toBeNull();
    expect(root.querySelector(".overview")).not.toBeNull();
    expect(current()).toContain("Plan");

    await back();
    expect(current()).toContain("Today");
  });

  it("forgets what a view opened when the view goes away", async () => {
    await mount(demoPlan(), "week");
    await click(root.querySelector(".board .day[role=button]"));
    await act(async () => { render(null, root); });
    expect(goBack()).toBe(false);
  });
});
