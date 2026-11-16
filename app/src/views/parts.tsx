/** Small pieces the views share: zone colours, a zone chip, a workout card and the targets of a zone. */
import { t } from "../i18n";
import { dayMonth, weekdayLong } from "../model/dates";
import {
  duration, mainZone, targetsOn, workoutHow, workoutLines, workoutPurpose, workoutSeconds, planTitle, zoneName,
  hrText, paceText, powerText,
} from "../model/plan";
import type { IsoDate, StoredPlan, StoredWorkout, ZoneCode } from "../model/types";
import { weekday } from "../model/dates";

/** As core/zone_colors.py: the colours of a Garmin watch, zone 1..5. */
export const ZONE_COLORS: Record<ZoneCode, string> = {
  Z1: "#9AA3AD", Z2: "#2F80ED", Z3: "#2EAD4B", Z4: "#F2994A", Z5: "#E5484D",
};

export const zoneColor = (z: string | null | undefined) => (z && z in ZONE_COLORS ? ZONE_COLORS[z as ZoneCode] : "var(--rest)");

export function ZoneChip({ zone }: { zone: string }) {
  return (
    <span class="chip" style={{ "--zc": zoneColor(zone) }}>
      <b>{zone}</b> {zoneName(zone)}
    </span>
  );
}

export const clock = (start: string | null) => (start ? start.slice(0, 5) : "");

export function when(w: StoredWorkout): string {
  return w.start ? t("ui.today.at", { time: clock(w.start) }) : t("ui.today.any_time");
}

export function dayLabel(day: IsoDate): string {
  return `${weekdayLong(weekday(day))} ${dayMonth(day)}`;
}

/** The workout as the big card of the day: title, length, the main zone's targets and every part. */
export function WorkoutCard({ stored, w, big = false }: { stored: StoredPlan; w: StoredWorkout; big?: boolean }) {
  const zone = mainZone(w);
  const secs = workoutSeconds(w);
  const tg = zone ? targetsOn(stored, w.sport, w.date)[zone] : undefined;
  const lines = w.goal ? [] : workoutLines(stored, w);
  return (
    <article class={`workout ${big ? "big" : ""}`} style={{ "--zc": zoneColor(zone) }} data-key={w.key}>
      <header>
        <div class="kicker">
          {when(w)}
          {w.quality && !w.goal ? <span class="tag">{t("ui.workout.quality")}</span> : null}
          {w.goal ? <span class="tag">{t("ui.workout.race")}</span> : null}
        </div>
        <h3>{planTitle(stored, w)}</h3>
        <div class="stats">
          {secs ? <span class="num">{duration(secs)}</span> : null}
          {zone ? <ZoneChip zone={zone} /> : null}
        </div>
      </header>
      {tg ? (
        <dl class="targets">
          {hrText(tg) ? (<div><dt>{t("ui.workout.hr")}</dt><dd>{hrText(tg)}</dd></div>) : null}
          {powerText(tg) ? (<div><dt>{t("ui.workout.power")}</dt><dd>{powerText(tg)}</dd></div>) : null}
          {paceText(tg, w.sport) ? (<div><dt>{t(w.sport === "skierg" ? "ui.workout.split" : "ui.workout.pace")}</dt>
            <dd>{paceText(tg, w.sport)}</dd></div>) : null}
        </dl>
      ) : zone && big ? <p class="muted">{t("ui.workout.no_targets")}</p> : null}
      {w.goal ? (
        <p class="purpose">
          {[w.goal.distance_km ? t("plan.goal.distance", { km: String(w.goal.distance_km) }) : "", w.goal.target]
            .filter(Boolean).join(" · ") || t("plan.workout.race.purpose")}
        </p>
      ) : null}
      {lines.length ? (
        <ol class="parts">
          {lines.map((l, i) => (
            <li key={i} style={{ "--zc": zoneColor(l.zone) }}>
              <span class="pk">{l.kind}</span>
              <span class="pw">{l.what}{l.zone ? ` · ${l.zone}` : ""}</span>
              {big && l.target && l.zone !== zone ? <span class="pt">{l.target}</span> : null}
              {l.rest ? <span class="pr">{l.rest}</span> : null}
              {big ? <span class="pr">{l.rpe}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
      {big && !w.goal ? (
        <div class="explain">
          <p><b>{t("ui.workout.why")}.</b> {workoutPurpose(w)}</p>
          <p><b>{t("ui.workout.how")}.</b> {workoutHow(w)}</p>
        </div>
      ) : null}
    </article>
  );
}
