import { useRef, useState } from "preact/hooks";
import { t } from "../i18n";

interface Props {
  error: string | null;
  onText: (text: string) => void;
  onDemo: () => void;
  onQr?: () => void;
  /** *Gör ett schema själv* (#777): the plan from the athlete's own answers, without a coach. */
  onOwn?: () => void;
}

/** No plan yet: how to get one, and the example. A friend without a coach makes one first (#787); the coach's
 * link, code or file comes after. */
export function Welcome({ error, onText, onDemo, onQr, onOwn }: Props) {
  const file = useRef<HTMLInputElement>(null);
  const [paste, setPaste] = useState("");
  return (
    <section class="view welcome">
      <div class="hero">
        <div class="logo" aria-hidden="true">
          <svg viewBox="0 0 64 64"><path d="M8 48 L24 40 L36 26 L56 14" /></svg>
        </div>
        <h1>{t("ui.load.title")}</h1>
        <p>{t(onOwn ? "ui.load.intro_own" : "ui.load.intro")}</p>
      </div>
      {error ? <p class="notice error" role="alert">{error}</p> : null}
      <div class="actions">
        {onOwn ? (
          <>
            <p class="small">{t("ui.load.own_intro")}</p>
            <button class="primary wide" onClick={onOwn}>{t("ui.load.own")}</button>
            <h3>{t("ui.load.coach_title")}</h3>
            <p class="small">{t("ui.load.coach_intro")}</p>
          </>
        ) : null}
        <button class={`${onOwn ? "secondary" : "primary"} wide`} onClick={() => file.current?.click()}>{t("ui.load.file")}</button>
        {/* No `accept`: Android knows no type for ".troskel", and its file picker greys out every file that is not
            one of the listed types, the plan's own file among them. Any other file gives the reader's own message. */}
        <input ref={file} type="file" hidden
          onChange={async (e) => {
            const f = e.currentTarget.files?.[0];
            if (f) onText(await f.text());
            e.currentTarget.value = "";
          }} />
        {onQr ? <button class="secondary wide" onClick={onQr}>{t("ui.load.qr")}</button> : null}
        {/* noValidate: a pasted message with the link in it ("Här är schemat: https://…") is not a URL to the browser,
            which would refuse to send the form; the reader finds the link in the text itself. */}
        <form class="paste" noValidate onSubmit={(e) => { e.preventDefault(); if (paste.trim()) onText(paste); }}>
          <input type="url" placeholder={t("ui.load.paste")} aria-label={t("ui.load.paste")} value={paste}
            onInput={(e) => setPaste(e.currentTarget.value)} />
          <button class="secondary" disabled={!paste.trim()}>{t("ui.load.paste_go")}</button>
        </form>
        <button class="link-btn" onClick={onDemo}>{t("ui.load.demo")}</button>
      </div>
    </section>
  );
}
