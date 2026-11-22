import { useState } from "preact/hooks";
import { t } from "../i18n";
import type { StorageState } from "../model/backup";
import { dayMonth } from "../model/dates";

export interface BackupProps {
  /** Whether the browser may empty the storage; null until it is known (or in the tests). */
  state: StorageState | null;
  /** The day of the last copy. */
  copied?: string;
  /** Saves the copy as a `.troskel` file; missing where the app cannot save files, false when it was not saved. */
  file?: () => Promise<boolean>;
  /** Copies the link; false when the phone would not let the app copy. */
  copy: () => Promise<boolean>;
}

/** *En kopia av schemat* (#790): the plan, the marks and the times to a file or a link of the athlete's own, which
 * *Öppna en fil* or *Klistra in en länk* reads back on this or a new phone. No cloud: the athlete keeps it. */
export function Backup({ state, copied, file, copy }: BackupProps) {
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div class="backup">
      <h3>{t("ui.copy.title")}</h3>
      <p class="small">{t("ui.copy.what")}</p>
      {state ? <p class={state === "tab" || state === "home" ? "notice small" : "muted small"}>{t(`ui.copy.state.${state}`)}</p> : null}
      {file ? (
        <button class="secondary wide" onClick={async () => setMsg(t((await file()) ? "ui.copy.saved" : "ui.share.not_saved"))}>{t("ui.copy.file")}</button>
      ) : null}
      <button class="secondary wide" onClick={async () => setMsg(t((await copy()) ? "ui.copy.copied" : "ui.share.copy_failed"))}>
        {t("ui.copy.copy")}
      </button>
      {msg ? <p class="notice small" role="status">{msg}</p> : null}
      <p class="muted small">
        {copied ? t("ui.copy.last", { date: dayMonth(copied) }) : t("ui.copy.never")} {t("ui.copy.back")}
      </p>
    </div>
  );
}
