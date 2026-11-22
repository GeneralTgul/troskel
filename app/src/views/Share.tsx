import { useState } from "preact/hooks";
import { t } from "../i18n";

interface Props {
  /** Saves the `.troskel` file (share.athleteFile); false when it was not saved. */
  onFile?: () => Promise<boolean>;
  /** Copies the link (share.athleteLink); false when the phone would not let the app copy. */
  onCopy: () => Promise<boolean>;
}

/** "Send the plan to your coach" (#781): what goes along, the athlete's consent, then a file or a link. The app
 * sends nothing itself. */
export function Share({ onFile, onCopy }: Props) {
  const [ok, setOk] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div class="share">
      <h3>{t("ui.share.title")}</h3>
      <p class="small">{t("ui.share.what")}</p>
      <p class="muted small">{t("ui.share.not")}</p>
      <label class="switch">
        <input type="checkbox" checked={ok} onChange={(e) => { setOk((e.target as HTMLInputElement).checked); setMsg(null); }} />
        <span>{t("ui.share.consent")}</span>
      </label>
      {onFile ? (
        <button class="secondary wide" disabled={!ok} onClick={async () => setMsg(t((await onFile()) ? "ui.share.saved" : "ui.share.not_saved"))}>
          {t("ui.share.file")}
        </button>
      ) : null}
      <button class="secondary wide" disabled={!ok}
        onClick={async () => setMsg(t((await onCopy()) ? "ui.share.copied" : "ui.share.copy_failed"))}>
        {t("ui.share.copy")}
      </button>
      {msg ? <p class="notice small" role="status">{msg}</p> : null}
    </div>
  );
}
