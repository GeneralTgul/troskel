/** Small pieces the views share: zone colours, a zone chip, a workout card and the targets of a zone. */
import { getLang, t } from "../i18n";
import { dayMonth, weekdayLong } from "../model/dates";
import {
  duration, feelText, mainTarget, mainZone, workoutDrills, workoutHow, workoutLines, workoutPurpose, workoutSeconds,
  workoutTips, planTitle, zoneName, hrText, paceText, powerText,
} from "../model/plan";
import type { ComponentChildren } from "preact";
import type { IsoDate, StoredPlan, StoredWorkout, ZoneCode } from "../model/types";
import { weekday } from "../model/dates";

/** As core/zone_colors.py: the colours of a Garmin watch, zone 1..5. */
export const ZONE_COLORS: Record<ZoneCode, string> = {
  Z1: "#9AA3AD", Z2: "#2F80ED", Z3: "#2EAD4B", Z4: "#F2994A", Z5: "#E5484D",
};

/** The zone a workout is shown with: none on a test day, where the warm-up's zone would say "easy" about a
 * test to exhaustion. */
export const shownZone = (w: StoredWorkout): ZoneCode | null => (w.family === "test" ? null : mainZone(w));
/** A test day's colour (styles.css): not the accent, which in the dark mode is the orange of zone 4. */
export const TEST_COLOR = "var(--test)";
/** The colour of a workout's card: its zone's, the test colour on a test day, grey without a zone. */
export const workoutColor = (w: StoredWorkout) => (w.family === "test" ? TEST_COLOR : zoneColor(mainZone(w)));

export const zoneColor = (z: string | null | undefined) => (z && z in ZONE_COLORS ? ZONE_COLORS[z as ZoneCode] : "var(--rest)");

export function ZoneChip({ zone }: { zone: string }) {
  return (
    <span class="chip" style={{ "--zc": zoneColor(zone) }}>
      <b>{zone}</b> <span class="zn">{zoneName(zone)}</span>
    </span>
  );
}

/** A race's distance as the printout writes it: "21,1 km" in Swedish, "21.1 km" in English. */
export function distanceText(km: number): string {
  const n = String(Math.round(km * 10) / 10);
  return t("plan.goal.distance", { km: getLang() === "sv" ? n.replace(".", ",") : n });
}

export const clock = (start: string | null) => (start ? start.slice(0, 5) : "");

export function when(w: StoredWorkout): string {
  return w.start ? t("ui.today.at", { time: clock(w.start) }) : t("ui.today.any_time");
}

export function dayLabel(day: IsoDate): string {
  return `${weekdayLong(weekday(day))} ${dayMonth(day)}`;
}

/** What the pace of a sport is called: per km in running, per 500 m on the SkiErg and per 100 m in the pool. */
export const PACE_LABEL: Record<string, string> = { skierg: "ui.workout.split", swim: "ui.workout.swim_pace" };

/** The box for the day (the guide): why, how, the feel (zone and effort of 10), tips and, for the technique swim,
 * drills to choose from. */
export function Guide({ w }: { w: StoredWorkout }) {
  if (w.goal) return null;
  const feel = feelText(w);
  const tips = workoutTips(w);
  const drills = workoutDrills(w);
  return (
    <div class="explain guide">
      <p><b>{t("ui.workout.why")}.</b> {workoutPurpose(w)}</p>
      {feel ? <p class="feel"><b>{t("ui.guide.feel")}.</b> {feel}</p> : null}
      <p><b>{t("ui.workout.how")}.</b> {workoutHow(w)}</p>
      {tips.length ? (
        <>
          <h4>{t("ui.guide.tips")}</h4>
          <ul class="tips">{tips.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </>
      ) : null}
      {drills.length ? (
        <>
          <h4>{t("ui.guide.drills")}</h4>
          <ol class="tips drills">{drills.map((x, i) => <li key={i}>{x}</li>)}</ol>
        </>
      ) : null}
    </div>
  );
}

/** The workout as the big card of the day: title, length, the main zone's targets and every part. `children` come
 * at the end of a big card: the athlete's mark. */
export function WorkoutCard({ stored, w, big = false, children }: {
  stored: StoredPlan; w: StoredWorkout; big?: boolean; children?: ComponentChildren;
}) {
  const zone = shownZone(w);
  const secs = workoutSeconds(w);
  const tg = zone ? mainTarget(stored, w) : undefined;
  const lines = w.goal ? [] : workoutLines(stored, w);
  return (
    <article class={`workout ${big ? "big" : ""}`} style={{ "--zc": workoutColor(w) }} data-key={w.key}>
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
          {paceText(tg, w.sport) ? (<div><dt>{t(PACE_LABEL[w.sport] ?? "ui.workout.pace")}</dt>
            <dd>{paceText(tg, w.sport)}</dd></div>) : null}
        </dl>
      ) : zone && big ? <p class="muted">{t("ui.workout.no_targets")}</p> : null}
      {w.goal ? (
        <p class="purpose">
          {[w.goal.distance_km ? distanceText(w.goal.distance_km) : "", w.goal.target]
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
      {big ? <Guide w={w} /> : null}
      {children}
    </article>
  );
}
