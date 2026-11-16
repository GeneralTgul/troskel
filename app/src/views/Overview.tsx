import { mainSport } from "../engine/models";
import { t } from "../i18n";
import { addDays, daysBetween, dayMonth, mondayOf } from "../model/dates";
import { hours, noteText, planEnd, planStart, targetText, weekMinutes, weekZoneMinutes, zoneName } from "../model/plan";
import { ZONES, type IsoDate, type StoredPlan, type StoredWorkout } from "../model/types";
import { dayLabel, zoneColor } from "./parts";

function relative(day: IsoDate, today: IsoDate): string {
  const n = daysBetween(today, day);
  if (n === 0) return t("ui.plan.today");
  return n > 0 ? t("ui.plan.days_left", { n }) : t("ui.plan.passed");
}

/** The whole plan: goals, test days, the blocks and a bar per week with the time in each zone. */
export function Overview({ stored, today, onCalendar }: { stored: StoredPlan; today: IsoDate; onCalendar?: () => void }) {
  const weeks = stored.plan.weeks;
  const max = Math.max(1, ...weeks.map(weekMinutes));
  const goals = [...stored.request.goals].sort((a, b) => a.date.localeCompare(b.date));
  const tests: StoredWorkout[] = weeks.flatMap((w) => w.workouts).filter((w) => w.template_id === "test");
  const start = planStart(stored);
  const end = planEnd(stored);
  const thisWeek = mondayOf(today);
  const main = mainSport(stored.request);
  const cap = stored.request.capacity.find((c) => c.sport === main);
  const zt = stored.targets[main] ?? {};
  return (
    <section class="view overview two-col">
      <div class="col">
        <h2>{stored.athlete || t("ui.plan.title")}</h2>
        {start && end ? (
          <p class="muted">{t("ui.plan.span", { start: dayMonth(start), end: dayMonth(end), n: weeks.length })}</p>
        ) : null}

        <h3>{t("ui.plan.goals")}</h3>
        {goals.length ? (
          <ul class="list">
            {goals.map((g) => (
              <li key={g.name + g.date} class={`goal prio-${g.priority}`}>
                <span class="prio">{g.priority}</span>
                <span class="grow">
                  <b>{g.name}</b>
                  <span class="muted small">
                    {dayLabel(g.date)}{g.distance_km ? ` · ${t("plan.goal.distance", { km: String(g.distance_km) })}` : ""}
                    {g.target ? ` · ${g.target}` : ""}
                  </span>
                </span>
                <span class="small">{relative(g.date, today)}</span>
              </li>
            ))}
          </ul>
        ) : <p class="muted">{t("ui.plan.no_goals")}</p>}

        {tests.length ? (
          <>
            <h3>{t("ui.plan.tests")}</h3>
            <ul class="list">
              {tests.map((w) => (
                <li key={w.key}><span class="grow">{dayLabel(w.date)}</span><span class="small">{relative(w.date, today)}</span></li>
              ))}
            </ul>
          </>
        ) : null}

        <h3>{t("ui.plan.blocks")}</h3>
        <ul class="list blocks">
          {stored.plan.blocks.map((b) => (
            <li key={b.first_week}>
              <span class="grow">
                <b>{t(`plan.block.${b.kind}`)}</b>
                <span class="muted small">
                  {dayMonth(b.start)} – {dayMonth(addDays(b.start, b.weeks * 7 - 1))}
                </span>
                <span class="small">{noteText(b.reason)}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div class="col">
        <h3>{t("ui.plan.weeks")}</h3>
        <div class="bars" role="img" aria-label={t("ui.plan.weeks")}>
          {weeks.map((w) => {
            const zm = weekZoneMinutes(w);
            const total = weekMinutes(w);
            return (
              <div key={w.index} class={`bar ${w.monday === thisWeek ? "is-now" : ""} kind-${w.kind}`}
                title={`${t("ui.plan.weeks_short", { i: w.index })}: ${hours(total)}`}>
                <span class="bar-val">{hours(total)}</span>
                <div class="stack" style={{ height: `${(total / max) * 100}%` }}>
                  {[...ZONES].reverse().map((z) =>
                    zm[z] ? <i key={z} style={{ flexGrow: zm[z], background: zoneColor(z) }} /> : null)}
                </div>
                <span class="bar-label">{w.index}</span>
              </div>
            );
          })}
        </div>

        <h3>{t("ui.plan.zones")}</h3>
        <ul class="list zones">
          {ZONES.map((z) => (
            <li key={z} style={{ "--zc": zoneColor(z) }}>
              <span class="swatch" />
              <span class="grow"><span><b>{z}</b> {zoneName(z)}</span><span class="muted small">{targetText(zt[z], main)}</span></span>
            </li>
          ))}
        </ul>
        {cap?.tested ? (
          <p class="muted small">
            {cap.source ? t("ui.plan.tested_source", { date: dayMonth(cap.tested), source: cap.source })
              : t("ui.plan.tested", { date: dayMonth(cap.tested) })}
          </p>
        ) : null}
        {stored.plan.notes.map((n, i) => <p key={i} class="small">{noteText(n)}</p>)}
        {onCalendar ? (
          <div class="calendar">
            <button class="secondary" onClick={onCalendar}>{t("ui.calendar.button")}</button>
            <p class="muted small">{t("ui.calendar.help")}</p>
          </div>
        ) : null}
      </div>
    </section>
  );
}
