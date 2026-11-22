import { useEffect, useRef, useState } from "preact/hooks";
import { deviceLang, setLang, t, type Lang } from "./i18n";
import type { Engine } from "./engine";
import { today as todayIn } from "./model/dates";
import { liveDemo } from "./model/demo";
import { toIcs } from "./model/ics";
import { linkData, PlanError, readPlanText } from "./model/link";
import { autoMarks, decodeHealth, healthData, mergeHealth, withAuto, withoutAuto, type HealthData } from "./model/health";
import { readHealthConnect, type HealthPluginLike } from "./model/healthconnect";
import { keepFor, type TrainingLog } from "./model/log";
import { emptyForm, formFromPlan, newPlanId, planFromForm, renewOwn, type RequestForm } from "./model/request";
import { athleteFile, athleteFileName, athleteLink, athletePayload } from "./model/share";
import { copyData, copyFileName, mergeLog, readCopyText, remindCopy, type Restored, type StorageState } from "./model/backup";
import { keptEdits } from "./model/adjust";
import { keptTimes } from "./model/times";
import { keptOwnValues } from "./model/values";
import type { PendingTimes, Store } from "./model/storage";
import type { StoredPlan } from "./model/types";
import { canKeepAwake, idleTimer, keepAwake, WALL_IDLE_MS } from "./model/wall";
import { About } from "./views/About";
import { Overview } from "./views/Overview";
import { Qr } from "./views/Qr";
import { Request } from "./views/Request";
import { Times } from "./views/Times";
import { ValuesView } from "./views/Values";
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
  /** Saves a file (model/save.ts); false when it was not saved (the share sheet closed in the app store builds). */
  download?: (name: string, text: string, type: string) => void | boolean | Promise<boolean | void>;
  hasCamera?: boolean;
  /** The wall mode's screen lock (wall.keepAwake); a stand-in in the tests. */
  keepAwake?: () => () => void;
  canKeepAwake?: boolean;
  /** Health Connect in the Android app (healthconnect.ts); missing in the browser and on iOS. */
  healthConnect?: HealthPluginLike | null;
  /** The id of a plan the athlete makes in the app (request.newPlanId); a fixed one in the tests. */
  newPlanId?: () => string;
  /** Whether the browser may empty the storage (backup.storageState, #790); missing in the tests: no reminder. */
  storageState?: () => Promise<StorageState>;
}

function errorText(e: unknown): string {
  return t(`ui.load.error.${e instanceof PlanError ? e.code : "broken"}`);
}

export function App(props: AppProps) {
  const { store, engine } = props;
  // The app stays open for days, on the wall and in a phone's memory: the date is read again every minute and each
  // time the app comes back to the front, and a new day draws the views again (`tick`).
  const [, setTick] = useState(0);
  const today = todayIn(props.now?.() ?? new Date(), props.timeZone);
  const shownDay = useRef(today);
  shownDay.current = today;
  useEffect(() => {
    const check = () => {
      if (todayIn(props.now?.() ?? new Date(), props.timeZone) !== shownDay.current) setTick((n) => n + 1);
    };
    const minute = setInterval(check, 60_000);
    globalThis.document?.addEventListener("visibilitychange", check);
    globalThis.addEventListener?.("pageshow", check);
    globalThis.addEventListener?.("focus", check);
    return () => {
      clearInterval(minute);
      globalThis.document?.removeEventListener("visibilitychange", check);
      globalThis.removeEventListener?.("pageshow", check);
      globalThis.removeEventListener?.("focus", check);
    };
  }, []);
  const [lang, setLangState] = useState<Lang>(() => {
    const l = store.settings().lang ?? deviceLang();
    setLang(l);
    return l;
  });
  // The athlete's own plan renews itself (#789): at the start, and when the day changes in the wall mode (below).
  const renewed = (s: StoredPlan | null, isDemo: boolean) => {
    const r = isDemo ? null : renewOwn(s, today, store.unavailable());
    if (r) store.setPlan(r, false);
    return r;
  };
  const [stored, setStored] = useState<StoredPlan | null>(() => renewed(store.plan(), store.isDemo()) ?? store.plan());
  const [demo, setDemo] = useState(() => store.isDemo());
  useEffect(() => {
    const r = renewed(stored, demo);
    if (r) setStored(r);
  }, [today, stored, demo]);
  const [pending, setPending] = useState<PendingTimes | null>(() => store.times());
  const [tab, setTab] = useState<Tab>(props.tab ?? "today");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [qr, setQr] = useState(false);
  const [welcome, setWelcome] = useState(false);
  // *Gör ett schema själv* (#777): the form while it is open.
  const [request, setRequest] = useState<RequestForm | null>(null);
  // *Ändra mina värden* (#777): open on the plan page.
  const [values, setValues] = useState(false);
  const [log, setLogState] = useState<TrainingLog>(() => store.log());
  const [health, setHealth] = useState<HealthData | null>(() => store.health());
  const [wall, setWall] = useState(() => store.settings().wall === true);
  const [hcOn, setHcOn] = useState(() => store.settings().healthConnect === true);
  // The storage (#790): may the browser empty it, and the day of the athlete's last copy.
  const [storage, setStorage] = useState<StorageState | null>(null);
  const [copied, setCopied] = useState(() => store.settings().copied);
  useEffect(() => {
    void props.storageState?.().then(setStorage).catch(() => {});
  }, []);
  // Bumped when the wall mode goes back home: the views start again, the week view on this week.
  const [home, setHome] = useState(0);

  // The views get the athlete's marks over the ones the health data gave; only the athlete's are kept.
  const auto = stored ? autoMarks(stored, health, today) : { marks: {}, unplanned: [] };
  const shown = withAuto(log, auto);
  const onLog = (l: TrainingLog) => {
    const own = withoutAuto(l);
    store.setLog(own);
    setLogState(own);
  };

  const readHealth = (data: string) => {
    try {
      const h = mergeHealth(store.health(), decodeHealth(data), today);
      store.setHealth(h);
      setHealth(h);
      setError(null);
      setToast(t("ui.health.loaded"));
    } catch {
      setError(t("ui.health.error"));
    }
  };

  // Health Connect: the button in "About" asks for the permission; afterwards the app reads at each start and
  // when it comes back to the front, without asking.
  const hc = props.healthConnect;
  const readHc = async (ask: boolean) => {
    if (!hc) return;
    const now = props.now?.() ?? new Date();
    const r = await readHealthConnect(hc, ask, now);
    if (r.ok) {
      const h = mergeHealth(store.health(), r.data, todayIn(now, props.timeZone));
      store.setHealth(h);
      setHealth(h);
      if (!store.settings().healthConnect) store.setSettings({ ...store.settings(), healthConnect: true });
      setHcOn(true);
      if (ask) {
        setError(null);
        setToast(t("ui.health.loaded"));
      }
    } else if (ask) setError(t(`ui.hc.${r.why}`));
  };

  useEffect(() => {
    if (!hc) return;
    const again = () => {
      if (store.settings().healthConnect && globalThis.document?.visibilityState !== "hidden") void readHc(false);
    };
    again();
    globalThis.document?.addEventListener("visibilitychange", again);
    return () => globalThis.document?.removeEventListener("visibilitychange", again);
  }, [hc]);

  const accept = (p: StoredPlan, isDemo = false, keepTab = false) => {
    // The dates that do not work stay with the same plan (a new test, a renewed plan): the new plan has workouts on
    // them again, so they wait with the times until the athlete saves. Another plan starts without them.
    const same = !isDemo && !demo && stored?.plan.id === p.plan.id;
    const off = same ? store.unavailable().filter((d) => d > today) : [];
    let kept = isDemo || demo ? null : keptTimes(stored, p, today, pending);
    if (off.length) {
      const days = [...new Set([...(kept?.unavailable ?? []), ...off])].sort();
      kept = { slots: kept?.slots ?? p.request.availability, unavailable: days, saved: today, kept: true };
    }
    store.setPlan(p, isDemo);
    store.setUnavailable(off);
    if (kept) store.setTimes(kept);
    onLog(keepFor(store.log(), p));
    setStored(p);
    setDemo(isDemo);
    setPending(kept);
    setError(null);
    setWelcome(false);
    setQr(false);
    setRequest(null);
    setValues(false);
    if (!keepTab) setTab("today");
    if (!isDemo) setToast(t("ui.load.loaded"));
  };

  // The athlete's own copy (#790): the plan, the marks, the sessions outside the plan and the waiting times come back.
  const restore = (c: Restored, keepTab: boolean) => {
    const other = stored && !demo && stored.plan.id !== c.plan.plan.id;
    if (other && typeof confirm === "function" && !confirm(t("ui.copy.replace_confirm"))) return;
    const kept = other || demo ? { marks: {}, extras: store.log().extras } : store.log();
    accept(c.plan, false, keepTab);
    onLog(mergeLog(keepFor(kept, c.plan), c.log));
    if (c.times) store.setTimes(c.times);
    setPending(c.times ?? store.times());
    setToast(t("ui.copy.restored"));
  };

  const readText = (text: string, keepTab = false) => {
    try {
      const c = readCopyText(text);
      if (c) return restore(c, keepTab);
      // The athlete's own values in a sport the coach's plan has no test in stay (#777); a test takes their place.
      // The athlete's moved, swapped and shortened workouts stay on the same plan (#777, model/adjust.keptEdits).
      const old = demo ? null : stored;
      accept(keptEdits(old, keptOwnValues(old, readPlanText(text))), false, keepTab);
    } catch (e) {
      setQr(false);
      setError(errorText(e));
    }
  };

  useEffect(() => {
    const take = (href: string | undefined) => {
      if (href && (linkData(href) || copyData(href))) {
        readText(href, true);
        props.onIncomingRead?.();
      } else if (href && healthData(href)) {
        readHealth(healthData(href)!);
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

  useEffect(() => {
    if (!wall) return;
    const release = (props.keepAwake ?? keepAwake)();
    const timer = idleTimer(WALL_IDLE_MS, () => {
      setTab("today");
      setWelcome(false);
      setQr(false);
      setRequest(null);
      setValues(false);
      setHome((h) => h + 1);
    });
    const poke = () => timer.poke();
    const events = ["pointerdown", "keydown", "touchstart", "wheel"];
    for (const e of events) globalThis.addEventListener?.(e, poke, { passive: true });
    timer.poke();
    return () => {
      timer.stop();
      for (const e of events) globalThis.removeEventListener?.(e, poke);
      release();
    };
  }, [wall]);

  const changeWall = (on: boolean) => {
    store.setSettings({ ...store.settings(), wall: on });
    setWall(on);
  };

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
    setLogState(store.log());
    setHealth(null);
    setHcOn(false);
    setWall(false);
    setStored(null);
    setDemo(false);
    setPending(null);
    setCopied(undefined);
    setError(null);
    setToast(t("ui.about.deleted"));
  };

  const calendar = stored && props.download
    ? () => void props.download!("troskel.ics", toIcs(stored, { from: today }), "text/calendar")
    : undefined;

  // The plan to the coach (#781): the plan as the phone has it, what is ticked off and the sessions outside it.
  const toCoach = () => athletePayload(stored!, log, auto, pending, today);
  const share = stored && !demo ? {
    file: props.download
      ? async () => { const p = toCoach(); return (await props.download!(athleteFileName(p), athleteFile(p), "application/json")) !== false; }
      : undefined,
    copy: async () => {
      try {
        await globalThis.navigator.clipboard.writeText(athleteLink(toCoach()));
        return true;
      } catch {
        return false;
      }
    },
  } : undefined;

  // The athlete's own copy (#790): the same payload as to the coach, to the athlete.
  const markCopied = () => {
    store.setSettings({ ...store.settings(), copied: today });
    setCopied(today);
  };
  const backup = stored && !demo ? {
    state: storage,
    copied,
    file: props.download
      ? async () => {
        // The day of the copy is remembered only when there is a copy: the share sheet can be closed without one.
        const ok = (await props.download!(copyFileName(today), athleteFile(toCoach()), "application/json")) !== false;
        if (ok) markCopied();
        return ok;
      }
      : undefined,
    copy: async () => {
      try {
        await globalThis.navigator.clipboard.writeText(athleteLink(toCoach()));
        markCopied();
        return true;
      } catch {
        return false;
      }
    },
  } : undefined;
  const remind = !!stored && !demo && remindCopy(storage, copied, today);

  // The athlete's own plan (#777): the form starts from the plan the phone has (its answers and tests), else empty.
  const openRequest = () => {
    setError(null);
    setRequest(stored && !demo ? formFromPlan(stored, today) : emptyForm(today));
  };
  const makePlan = (f: RequestForm) => {
    const p = planFromForm(f, { id: (props.newPlanId ?? newPlanId)(), today,
      targets: f.capacity.length && stored ? stored.targets : undefined });
    if (stored && !demo && typeof confirm === "function" && !confirm(t("ui.request.replace_confirm"))) return;
    accept(p);
    setToast(t("ui.request.made"));
  };

  const camera = props.hasCamera ? () => { setError(null); setQr(true); } : undefined;

  let body;
  if (qr) body = <Qr onText={readText} onClose={() => setQr(false)} />;
  else if (request)
    body = <Request form={request} today={today} onMake={makePlan} onCancel={() => setRequest(null)} />;
  else if (!stored || welcome)
    body = <Welcome error={error} onText={readText} onDemo={showDemo} onQr={camera} onOwn={openRequest} />;
  else if (tab === "today") body = <Today key={home} stored={stored} today={today} log={shown} onLog={onLog} health={health} />;
  else if (tab === "week")
    body = (
      <Week key={home} stored={stored} today={today} onCalendar={calendar} log={shown} onLog={onLog}
        onPlan={demo ? undefined
          : (p) => { store.setPlan(p, false); setStored(p); setToast(t("ui.adjust.saved")); }} />
    );
  else if (tab === "plan" && values)
    body = (
      <ValuesView stored={stored} onCancel={() => setValues(false)}
        onSave={(p) => { store.setPlan(p, false); setStored(p); setValues(false); setToast(t("ui.values.saved")); }} />
    );
  else if (tab === "plan")
    body = <Overview stored={stored} today={today} onCalendar={calendar} onValues={demo ? undefined : () => setValues(true)} />;
  else if (tab === "times")
    body = (
      <Times stored={stored} today={today} engine={engine} pending={pending}
        onSaveTimes={(p) => { store.setTimes(p); setPending(p); }}
        off={store.unavailable()}
        onRebuilt={(p, off) => { store.setPlan(p, demo); store.setUnavailable(off); setStored(p); setPending(null); }} />
    );
  else body = null;

  const showTabs = !!stored && !qr && !welcome && !request;
  return (
    <div class={`app ${showTabs ? "has-tabs" : ""}`} lang={lang}>
      {showTabs ? (
        <nav class="tabs" aria-label={t("ui.app.name")}>
          <span class="brand">{t("ui.app.name")}</span>
          {TABS.map((k) => (
            <button key={k} class={k === tab ? "on" : ""} aria-current={k === tab ? "page" : undefined}
              onClick={() => { setTab(k); setValues(false); }}>
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
        {remind && showTabs && tab === "today" ? (
          <p class="notice small copy-remind">
            {t(storage === "tab" ? "ui.copy.remind_tab" : "ui.copy.remind")}{" "}
            <button class="link-btn" onClick={() => setTab("about")}>{t("ui.copy.remind_go")}</button>
          </p>
        ) : null}
        {body}
        {(tab === "about" && showTabs) || (!stored && !qr && !request) ? (
          <About lang={lang} stored={stored} onLang={changeLang} onDelete={deleteAll}
            wall={wall} onWall={stored ? changeWall : undefined} canKeepAwake={props.canKeepAwake ?? canKeepAwake()}
            onNewPlan={stored && !welcome ? () => setWelcome(true) : undefined}
            onOwnPlan={stored && !welcome ? openRequest : undefined}
            healthConnect={hc ? { on: hcOn, read: () => void readHc(true), settings: () => void hc.openHealthConnectSettings().catch(() => {}) } : undefined}
            share={share} backup={backup} />
        ) : null}
      </main>
      {toast ? <div class="toast" role="status">{toast}</div> : null}
    </div>
  );
}
