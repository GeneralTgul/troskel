/** The phone's health data in the views: today's form on "Today", and a line under a workout the data ticked off. */
import { t } from "../i18n";
import { dayMonth } from "../model/dates";
import { dayForm, type AutoMark, type HealthData } from "../model/health";
import type { Mark } from "../model/log";
import type { IsoDate } from "../model/types";

const one = (x: number) => (Math.round(x * 10) / 10).toLocaleString(undefined, { maximumFractionDigits: 1 });

/** Today's form: "Take it easy today" or "Train as planned", why, and what the data did not have. */
export function FormCard({ health, today }: { health: HealthData | null; today: IsoDate }) {
  const form = dayForm(health, today);
  if (!form) return null;
  return (
    <section class={`card form ${form.tired ? "tired" : "fresh"}`} aria-label={t("ui.health.form")}>
      <h3>{t(form.tired ? "ui.health.tired" : "ui.health.fresh")}</h3>
      {form.reasons.length ? (
        <ul class="form-reasons small">
          {form.reasons.map((r) => (
            <li key={r.signal}>
              {t(`ui.health.${r.signal}`, { value: one(r.value), mean: r.mean != null ? one(r.mean) : "" })}
            </li>
          ))}
        </ul>
      ) : null}
      {form.missing.length ? (
        <p class="muted small">{t("ui.health.missing", { what: form.missing.map((s) => t(`ui.health.name.${s}`)).join(", ") })}</p>
      ) : null}
    </section>
  );
}

/** Under a workout the health data ticked off: from Health, and on which day when it was another. */
export function AutoNote({ mark, day }: { mark: Mark | null; day: IsoDate }) {
  const a = mark as AutoMark | null;
  if (!a?.auto) return null;
  return (
    <p class="muted small auto-note">
      {a.on === day ? t("ui.health.auto") : t("ui.health.auto_on", { date: dayMonth(a.on) })}
    </p>
  );
}
