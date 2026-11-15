import { useRef, useState } from "preact/hooks";
import { t } from "../i18n";

interface Props {
  error: string | null;
  onText: (text: string) => void;
  onDemo: () => void;
  onQr?: () => void;
}

/** No plan yet: how to get one, and the example. */
export function Welcome({ error, onText, onDemo, onQr }: Props) {
  const file = useRef<HTMLInputElement>(null);
  const [paste, setPaste] = useState("");
  return (
    <section class="view welcome">
      <div class="hero">
        <div class="logo" aria-hidden="true">
          <svg viewBox="0 0 64 64"><path d="M8 48 L24 40 L36 26 L56 14" /></svg>
        </div>
        <h1>{t("ui.load.title")}</h1>
        <p>{t("ui.load.intro")}</p>
      </div>
      {error ? <p class="notice error" role="alert">{error}</p> : null}
      <div class="actions">
        <button class="primary wide" onClick={() => file.current?.click()}>{t("ui.load.file")}</button>
        <input ref={file} type="file" hidden accept=".troskel,.json,application/json,text/plain"
          onChange={async (e) => {
            const f = e.currentTarget.files?.[0];
            if (f) onText(await f.text());
            e.currentTarget.value = "";
          }} />
        {onQr ? <button class="secondary wide" onClick={onQr}>{t("ui.load.qr")}</button> : null}
        <form class="paste" onSubmit={(e) => { e.preventDefault(); if (paste.trim()) onText(paste); }}>
          <input type="url" placeholder={t("ui.load.paste")} aria-label={t("ui.load.paste")} value={paste}
            onInput={(e) => setPaste(e.currentTarget.value)} />
          <button class="secondary" disabled={!paste.trim()}>{t("ui.load.paste_go")}</button>
        </form>
        <button class="link-btn" onClick={onDemo}>{t("ui.load.demo")}</button>
      </div>
    </section>
  );
}
