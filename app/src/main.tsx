import { Capacitor } from "@capacitor/core";
import { render } from "preact";
import { App, TABS, type Tab } from "./App";
import { engine } from "./engine";
import { storageState } from "./model/backup";
import { healthConnectPlugin } from "./model/healthconnect";
import { setupOffline } from "./model/offline";
import { saveFile } from "./model/save";
import { openStore } from "./model/storage";
import "./styles.css";
import { Broken } from "./views/Broken";

// ?view=week opens a view directly (screenshots, a shortcut on the home screen).
const view = new URLSearchParams(location.search).get("view") as Tab | null;

// The app store builds read Capacitor Preferences first (asynchronous); the browser has localStorage at once.
// Health Connect only in the Android build; null elsewhere (and if the plugin cannot load).
// Broken: a view that breaks (a saved plan the app cannot draw) shows a way to delete all data, not a white page.
Promise.all([openStore(), healthConnectPlugin().catch(() => null)]).then(([store, healthConnect]) => render(
  <Broken store={store} reload={() => location.reload()}><App
    tab={view && TABS.includes(view) ? view : undefined}
    store={store}
    engine={engine}
    loadDemo={() => fetch(`${import.meta.env.BASE_URL}demo.troskel`).then((r) => r.text())}
    incoming={location.href}
    // The plan is now on the device: take it out of the address bar and the history.
    onIncomingRead={() => history.replaceState(null, "", location.pathname + location.search)}
    // A download in the browser, the phone's share sheet in the app store builds (model/save.ts).
    download={saveFile()}
    hasCamera={!!navigator.mediaDevices?.getUserMedia}
    healthConnect={healthConnect}
    storageState={() => storageState()}
  /></Broken>,
  document.getElementById("app")!,
));

// The web app keeps its files with a service worker; the app store builds have none (model/offline.ts).
addEventListener("load", () => void setupOffline({
  native: Capacitor.isNativePlatform(), prod: import.meta.env.PROD, base: import.meta.env.BASE_URL,
  workers: "serviceWorker" in navigator ? navigator.serviceWorker : undefined,
  caches: "caches" in globalThis ? caches : undefined,
}));
