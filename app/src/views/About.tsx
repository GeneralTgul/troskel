import { LANGS, t, type Lang } from "../i18n";
import { dayMonth } from "../model/dates";
import type { StoredPlan } from "../model/types";
import { Backup, type BackupProps } from "./Backup";
import { Share } from "./Share";

export const SOURCE_URL = "https://github.com/GeneralTgul/troskel";
export const VERSION: string = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "0.0.0";

interface Props {
  lang: Lang;
  stored: StoredPlan | null;
  onLang: (l: Lang) => void;
  onDelete: () => void;
  onNewPlan?: () => void;
  /** *Gör ett schema själv* (#777), from the plan the phone has. */
  onOwnPlan?: () => void;
  wall?: boolean;
  /** Missing until there is a plan to show on the wall. */
  onWall?: (on: boolean) => void;
  canKeepAwake?: boolean;
  /** Health Connect, only in the Android app: `on` once the athlete let the app read it. */
  healthConnect?: { on: boolean; read: () => void; settings: () => void };
  /** Send the plan to the coach (#781): missing without a plan of the athlete's own (none, or the example). */
  share?: { file?: () => Promise<boolean>; copy: () => Promise<boolean> };
  /** The athlete's own copy (#790): missing without a plan of the athlete's own. */
  backup?: BackupProps;
}

/** The privacy page, also shown by Health Connect as the app's rationale for the permissions. */
export const PRIVACY_URL = "https://generaltgul.github.io/troskel/app/privacy.html";

export function About({ lang, stored, onLang, onDelete, onNewPlan, onOwnPlan, wall = false, onWall, canKeepAwake = true, healthConnect, share, backup }: Props) {
  return (
    <section class="view about">
      <h2>{t("ui.about.title")}</h2>

      <h3>{t("ui.about.language")}</h3>
      <div class="segmented" role="radiogroup" aria-label={t("ui.about.language")}>
        {LANGS.map((l) => (
          <button key={l} role="radio" aria-checked={l === lang} class={l === lang ? "on" : ""} onClick={() => onLang(l)}>
            {t(`ui.about.lang.${l}`)}
          </button>
        ))}
      </div>

      {onNewPlan ? <button class="secondary wide" onClick={onNewPlan}>{t("ui.load.new")}</button> : null}
      {onOwnPlan ? <button class="secondary wide" onClick={onOwnPlan}>{t("ui.load.own")}</button> : null}

      {onWall ? (
        <>
          <h3>{t("ui.about.wall_title")}</h3>
          <label class="switch">
            <input type="checkbox" checked={wall} onChange={(e) => onWall((e.target as HTMLInputElement).checked)} />
            <span class="day-name">{t("ui.about.wall_switch")}</span>
          </label>
          <p class="muted small">{t("ui.about.wall")}</p>
          {wall && !canKeepAwake ? <p class="notice small">{t("ui.about.wall_no_lock")}</p> : null}
        </>
      ) : null}

      {healthConnect ? (
        <>
          <h3>{t("ui.hc.title")}</h3>
          <p class="small">{t("ui.hc.why")}</p>
          <button class={`${healthConnect.on ? "secondary" : "primary"} wide`} onClick={healthConnect.read}>
            {t(healthConnect.on ? "ui.hc.again" : "ui.hc.read")}
          </button>
          {healthConnect.on ? <p class="muted small">{t("ui.hc.on")}</p> : null}
          <p class="small">
            <button class="link-btn" onClick={healthConnect.settings}>{t("ui.hc.settings")}</button>
            {" · "}
            <a href={PRIVACY_URL} target="_blank" rel="noopener">{t("ui.hc.privacy")}</a>
          </p>
        </>
      ) : null}

      {backup ? <Backup {...backup} /> : null}

      {share ? <Share onFile={share.file} onCopy={share.copy} /> : null}

      <h3>{t("ui.about.privacy_title")}</h3>
      <p>{t("ui.about.privacy")}</p>
      <p class="muted small">{t("ui.offline")}</p>
      <button class="danger wide" onClick={() => {
        if (typeof confirm !== "function" || confirm(t("ui.about.delete_confirm"))) onDelete();
      }}>{t("ui.about.delete")}</button>

      <h3>{t("ui.about.license_title")}</h3>
      <p>{t("ui.about.license")}</p>
      <p><a href={SOURCE_URL} target="_blank" rel="noopener">{t("ui.about.source")}</a></p>
      <p class="muted small">
        {t("ui.about.version", { v: VERSION })}
        {stored ? ` · ${t("ui.about.plan", { date: dayMonth(stored.plan.created.slice(0, 10)), v: stored.plan.engine_version || "–" })}` : ""}
      </p>
    </section>
  );
}
