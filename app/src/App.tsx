import { useEffect, useState } from "preact/hooks";
import { deviceLang, setLang, t, type Lang } from "./i18n";
import type { Engine } from "./engine";
import { today as todayIn } from "./model/dates";
import { liveDemo } from "./model/demo";
import { toIcs } from "./model/ics";
import { linkData, PlanError, readPlanText } from "./model/link";
import type { PendingTimes, Store } from "./model/storage";
import type { StoredPlan } from "./model/types";
import { About } from "./views/About";
import { Overview } from "./views/Overview";
import { Qr } from "./views/Qr";
import { Times } from "./views/Times";
import { Today } from "./views/Today";
import { Week } from "./views/Week";
import { Welcome } from "./views/Welcome";

export type Tab = "today" | "week" | "plan" | "times" | "about";
export const TABS: Tab[] = ["today", "week", "plan", "times", "about"];
const ICONS: Record<Tab, string> = {
  today: "M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8",
  week: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
  plan: "M5 20V12M10 20V8M15 20v-6M20 20V4",
  times: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 7v5l3 2",
  about: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 11v6M12 7.5v.5",
};

export interface AppProps {
  store: Store;
  engine: Engine;
  now?: () => Date;
  timeZone?: string;
  tab?: Tab;
  /** The example plan: fetches public/demo.troskel in the browser, a fixture in the tests. */
  loadDemo: () => Promise<string>;
  /** A link the app was opened with (location.href), read once. */
  incoming?: string;
  onIncomingRead?: () => void;
  download?: (name: string, text: string, type: string) => void;
  hasCamera?: boolean;
}

function errorText(e: unknown): string {
  return t(`ui.load.error.${e instanceof PlanError ? e.code : "broken"}`);
}

export function App(props: AppProps) {
  const { store, engine } = props;
  const today = todayIn(props.now?.() ?? new Date(), props.timeZone);
  const [lang, setLangState] = useState<Lang>(() => {
    const l = store.settings().lang ?? deviceLang();
    setLang(l);
    return l;
  });
  const [stored, setStored] = useState<StoredPlan | null>(() => store.plan());
  const [demo, setDemo] = useState(() => store.isDemo());
  const [pending, setPending] = useState<PendingTimes | null>(() => store.times());
  const [tab, setTab] = useState<Tab>(props.tab ?? "today");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [qr, setQr] = useState(false);
  const [welcome, setWelcome] = useState(false);

  const accept = (p: StoredPlan, isDemo = false, keepTab = false) => {
    store.setPlan(p, isDemo);
    setStored(p);
    setDemo(isDemo);
    setPending(null);
    setError(null);
    setWelcome(false);
    setQr(false);
    if (!keepTab) setTab("today");
    if (!isDemo) setToast(t("ui.load.loaded"));
  };

  const readText = (text: string, keepTab = false) => {
    try {
      accept(readPlanText(text), false, keepTab);
    } catch (e) {
      setQr(false);
      setError(errorText(e));
    }
  };

  useEffect(() => {
    const take = (href: string | undefined) => {
      if (href && linkData(href)) {
        readText(href, true);
        props.onIncomingRead?.();
      }
    };
    take(props.incoming);
    const onHash = () => take(globalThis.location?.href);
    globalThis.addEventListener?.("hashchange", onHash);
    return () => globalThis.removeEventListener?.("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(id);
  }, [toast]);

  const changeLang = (l: Lang) => {
    setLang(l);
    store.setSettings({ ...store.settings(), lang: l });
    setLangState(l);
  };

  const showDemo = async () => {
    try {
      accept(liveDemo(readPlanText(await props.loadDemo()), today), true);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const deleteAll = () => {
    store.clear();
    setStored(null);
    setDemo(false);
    setPending(null);
    setToast(t("ui.about.deleted"));
  };

  const calendar = stored && props.download
    ? () => props.download!("troskel.ics", toIcs(stored, { from: today }), "text/calendar")
    : undefined;

  const camera = props.hasCamera ? () => { setError(null); setQr(true); } : undefined;

  let body;
  if (qr) body = <Qr onText={readText} onClose={() => setQr(false)} />;
  else if (!stored || welcome)
    body = <Welcome error={error} onText={readText} onDemo={showDemo} onQr={camera} />;
  else if (tab === "today") body = <Today stored={stored} today={today} />;
  else if (tab === "week") body = <Week stored={stored} today={today} onCalendar={calendar} />;
  else if (tab === "plan") body = <Overview stored={stored} today={today} onCalendar={calendar} />;
  else if (tab === "times")
    body = (
      <Times stored={stored} today={today} engine={engine} pending={pending}
        onSaveTimes={(p) => { store.setTimes(p); setPending(p); }}
        onRebuilt={(p) => { store.setPlan(p, demo); setStored(p); setPending(null); }} />
    );
  else body = null;

  const showTabs = !!stored && !qr && !welcome;
  return (
    <div class={`app ${showTabs ? "has-tabs" : ""}`} lang={lang}>
      {showTabs ? (
        <nav class="tabs" aria-label={t("ui.app.name")}>
          <span class="brand">{t("ui.app.name")}</span>
          {TABS.map((k) => (
            <button key={k} class={k === tab ? "on" : ""} aria-current={k === tab ? "page" : undefined}
              onClick={() => setTab(k)}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d={ICONS[k]} /></svg>
              <span>{t(`ui.tab.${k}`)}</span>
            </button>
          ))}
        </nav>
      ) : null}
      <main>
        {demo && showTabs ? (
          <div class="banner">
            <span>{t("ui.demo.banner")}</span>
            <button class="link-btn" onClick={deleteAll}>{t("ui.demo.leave")}</button>
          </div>
        ) : null}
        {error && showTabs ? <p class="notice error" role="alert">{error}</p> : null}
        {body}
        {(tab === "about" && showTabs) || (!stored && !qr) ? (
          <About lang={lang} stored={stored} onLang={changeLang} onDelete={deleteAll}
            onNewPlan={stored && !welcome ? () => setWelcome(true) : undefined} />
        ) : null}
      </main>
      {toast ? <div class="toast" role="status">{toast}</div> : null}
    </div>
  );
}
