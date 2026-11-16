import { LANGS, t, type Lang } from "../i18n";
import { dayMonth } from "../model/dates";
import type { StoredPlan } from "../model/types";

export const SOURCE_URL = "https://github.com/GeneralTgul/troskel";
export const VERSION: string = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "0.0.0";

interface Props {
  lang: Lang;
  stored: StoredPlan | null;
  onLang: (l: Lang) => void;
  onDelete: () => void;
  onNewPlan?: () => void;
  wall?: boolean;
  /** Missing until there is a plan to show on the wall. */
  onWall?: (on: boolean) => void;
  canKeepAwake?: boolean;
}

export function About({ lang, stored, onLang, onDelete, onNewPlan, wall = false, onWall, canKeepAwake = true }: Props) {
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
