import { t } from "../i18n";
import { addDays, dayMonth } from "../model/dates";
import { planEnd, planStart, workoutsOn } from "../model/plan";
import type { IsoDate, StoredPlan } from "../model/types";
import { dayLabel, WorkoutCard } from "./parts";

function nextWorkoutDay(stored: StoredPlan, after: IsoDate): IsoDate | null {
  let best: IsoDate | null = null;
  for (const wk of stored.plan.weeks)
    for (const w of wk.workouts) if (w.date > after && (!best || w.date < best)) best = w.date;
  return best;
}

export function Today({ stored, today }: { stored: StoredPlan; today: IsoDate }) {
  const tomorrow = addDays(today, 1);
  const now = workoutsOn(stored, today);
  const next = workoutsOn(stored, tomorrow);
  const start = planStart(stored);
  const end = planEnd(stored);
  const coming = nextWorkoutDay(stored, tomorrow);
  return (
    <section class="view today two-col">
      <div class="col">
        <h2>{t("ui.today.title")} <span class="date">{dayLabel(today)}</span></h2>
        {start && today < start ? <p class="notice">{t("ui.today.before", { date: dayMonth(start) })}</p> : null}
        {end && today > end ? <p class="notice">{t("ui.today.after")}</p> : null}
        {now.length ? (
          now.map((w) => <WorkoutCard key={w.key} stored={stored} w={w} big />)
        ) : (
          <article class="workout big rest">
            <h3>{t("ui.today.rest")}</h3>
            <p>{t("ui.today.rest_text")}</p>
          </article>
        )}
      </div>
      <div class="col">
        <h2>{t("ui.today.tomorrow")}</h2>
        {next.length ? (
          next.map((w) => <WorkoutCard key={w.key} stored={stored} w={w} />)
        ) : (
          <article class="workout rest">
            <h3>{t("ui.today.rest")}</h3>
            {coming ? <p class="muted">{t("ui.today.next", { day: dayLabel(coming) })}</p> : null}
          </article>
        )}
      </div>
    </section>
  );
}
