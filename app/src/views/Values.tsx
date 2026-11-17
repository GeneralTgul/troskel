import { t } from "../i18n";
import { BORROWS } from "../engine/models";
import { dayMonth } from "../model/dates";
import type { Sport, StoredCapacity } from "../model/types";
import { useState } from "preact/hooks";
import { VALUE_SPORTS, ValuesError, emptyValues, loadOf, ownValues, ownValuesOf, planWithValues, type OwnValues,
  type SportValues } from "../model/values";
import { planRoles } from "../engine/models";
import type { StoredPlan } from "../model/types";
import { sportName } from "./log";

interface Props {
  /** The plan's sports (the form's, or the plan's). */
  sports: Sport[];
  /** What the plan has now: a sport with a test shows the test until the athlete chooses own values. */
  capacity: StoredCapacity[];
  values: OwnValues;
  onChange: (values: OwnValues) => void;
}

/** A measured test the sport has, its own or the one it borrows (skis: the SkiErg's). */
function measured(capacity: StoredCapacity[], sport: Sport): { cap: StoredCapacity; borrowed: boolean } | null {
  const known = (c: StoredCapacity | undefined) => !!c && !ownValues(c) && (c.anchors != null || c.css_s100 != null);
  const own = capacity.find((c) => c.sport === sport);
  if (own) return known(own) ? { cap: own, borrowed: false } : null;
  const from = sport in BORROWS ? capacity.find((c) => c.sport === BORROWS[sport]) : undefined;
  return from && known(from) ? { cap: from, borrowed: true } : null;
}

/** The text of a ValuesError, with the sport's name. */
export function valuesErrorText(e: ValuesError): string {
  return t(e.key, { ...e.args, sport: sportName(String(e.args.sport ?? "")) });
}

/** *Dina värden* (#777): max HR, resting HR, the thresholds and the pace, watt or CSS per sport, typed in by the
 * athlete. A sport with a test from Tröskel shows the test; own values take its place only when the athlete asks. */
export function Values({ sports, capacity, values, onChange }: Props) {
  const shown = VALUE_SPORTS.filter((sp) => sports.includes(sp));
  const set = (sp: Sport, v: SportValues | undefined) => {
    const next = { ...values };
    if (v) next[sp] = v;
    else delete next[sp];
    onChange(next);
  };
  return (
    <div class="own-values">
      <p class="muted small">{t("ui.values.intro")}</p>
      {shown.map((sp) => {
        const test = measured(capacity, sp);
        const row = values[sp];
        const name = sportName(sp);
        if (test && !row) {
          return (
            <div key={sp} class="extra-form values-sport" data-sport={sp}>
              <p class="small"><b>{name}</b></p>
              <p class="muted small">
                {test.borrowed ? t("ui.values.borrowed", { sport: sportName(test.cap.sport) })
                  : test.cap.tested ? t("ui.values.measured", { date: dayMonth(test.cap.tested) }) : test.cap.source}
              </p>
              <button type="button" class="link-btn" onClick={() => {
                if (typeof confirm === "function" && !confirm(t("ui.values.replace_confirm", { sport: name }))) return;
                set(sp, emptyValues());
              }}>{t("ui.values.replace")}</button>
            </div>
          );
        }
        const v = row ?? emptyValues();
        const field = (k: keyof SportValues, label: string, mode: "numeric" | "text" = "numeric", hint = "") => (
          <label key={k}>
            <span class="small muted">{label}</span>
            <input type="text" inputMode={mode} name={`${sp}-${k}`} value={v[k]} placeholder={hint} maxLength={6}
              onInput={(e) => set(sp, { ...v, [k]: e.currentTarget.value })} />
          </label>
        );
        const load = loadOf(sp);
        return (
          <div key={sp} class="extra-form values-sport" data-sport={sp}>
            <p class="small"><b>{name}</b></p>
            <div class="time-fields">
              {load === "css" ? field("css", t("ui.values.css"), "text", "1:55") : (
                <>
                  {field("max_hr", t("ui.values.max_hr"))}
                  {field("resting_hr", t("ui.values.resting_hr"))}
                  {field("lt1_hr", t("ui.values.lt1_hr"))}
                  {field("lt2_hr", t("ui.values.lt2_hr"))}
                  {load === "pace" ? <>{field("lt1", t("ui.values.pace.lt1"), "text", "5:30")}
                    {field("lt2", t("ui.values.pace.lt2"), "text", "4:30")}</> : null}
                  {load === "watt" ? <>{field("lt1", t("ui.values.watt.lt1"))}
                    {field("lt2", t(sp === "bike" ? "ui.values.watt.ftp" : "ui.values.watt.lt2"))}</> : null}
                </>
              )}
            </div>
            {load === "css" ? <p class="muted small">{t("ui.values.css_help")}</p> : null}
            {load === "none" ? <p class="muted small">{t("ui.values.ski_help")}</p> : null}
            {test ? <button type="button" class="link-btn" onClick={() => set(sp, undefined)}>{t("ui.values.keep_test")}</button> : null}
          </div>
        );
      })}
      {shown.some((sp) => loadOf(sp) !== "css") ? <p class="muted small">{t("ui.values.help")}</p> : null}
    </div>
  );
}

interface ViewProps {
  stored: StoredPlan;
  /** Keeps the plan laid on the new values. */
  onSave: (p: StoredPlan) => void;
  onCancel: () => void;
}

/** *Ändra mina värden* on the plan page: the plan's own values, laid on the same plan (model/values.planWithValues). */
export function ValuesView({ stored, onSave, onCancel }: ViewProps) {
  const [values, setValues] = useState<OwnValues>(() => ownValuesOf(stored.request));
  const [error, setError] = useState<string | null>(null);
  const save = (e: Event) => {
    e.preventDefault();
    try {
      onSave(planWithValues(stored, values));
    } catch (err) {
      if (!(err instanceof ValuesError)) throw err;
      setError(valuesErrorText(err));
    }
  };
  return (
    <section class="view values">
      <h2>{t("ui.values.title")}</h2>
      <form onSubmit={save} noValidate>
        <Values sports={planRoles(stored.request).map((r) => r.sport)} capacity={stored.request.capacity} values={values}
          onChange={(v) => { setValues(v); setError(null); }} />
        {error ? <p class="notice error" role="alert">{error}</p> : null}
        <button type="submit" class="primary wide">{t("ui.values.save")}</button>
        <button type="button" class="secondary wide" onClick={onCancel}>{t("ui.values.cancel")}</button>
      </form>
    </section>
  );
}
