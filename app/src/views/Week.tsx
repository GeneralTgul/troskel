import { useState } from "preact/hooks";
import { t } from "../i18n";
import { addDays, dayMonth, isoWeek, mondayOf, weekDays, weekdayShort } from "../model/dates";
import { duration, hours, mainZone, noteText, weekMinutes, weekOf, workoutSeconds, workoutsOn, planTitle } from "../model/plan";
import type { IsoDate, StoredPlan, StoredWorkout } from "../model/types";
import { clock, shownZone, workoutColor, zoneColor, ZoneChip } from "./parts";

/** The colour of a day's card: that of its first workout that has one (a zone, or a test), so that strength before
 * a run (no zone) does not make the day look like a rest day. */
export function dayColor(ws: StoredWorkout[]): string {
  const w = ws.find((x) => x.family === "test" || mainZone(x));
  return w ? workoutColor(w) : zoneColor(null);
}

/** The week as a board on the wall: seven cards, today marked, rest days plain. */
export function Week({ stored, today, onCalendar }: { stored: StoredPlan; today: IsoDate; onCalendar?: () => void }) {
  const [monday, setMonday] = useState(mondayOf(today));
  const wk = weekOf(stored, monday);
  const thisWeek = mondayOf(today);
  return (
    <section class="view week">
      <header class="week-head">
        <button class="ghost" aria-label={t("ui.week.prev")} onClick={() => setMonday(addDays(monday, -7))}>‹</button>
        <div class="week-title">
          <h2>{t("ui.week.title", { n: isoWeek(monday) })}</h2>
          <p class="muted">
            <span class="week-dates">{dayMonth(monday)} – {dayMonth(addDays(monday, 6))}</span>
            {wk ? <span class="week-of">{t("ui.week.plan_week", { i: wk.index, n: stored.plan.weeks.length })}</span> : null}
          </p>
        </div>
        <button class="ghost" aria-label={t("ui.week.next")} onClick={() => setMonday(addDays(monday, 7))}>›</button>
      </header>
      {monday !== thisWeek ? (
        <button class="link-btn" onClick={() => setMonday(thisWeek)}>{t("ui.week.this")}</button>
      ) : null}
      {wk ? (
        <p class="week-sum">
          <span class="num">{hours(weekMinutes(wk))}</span> {t("ui.week.training")} ·{" "}
          {t(`plan.week.kind.${wk.kind}`)} · {t(`plan.block.${wk.block}`)}
        </p>
      ) : (
        <p class="notice">{t("ui.week.outside")}</p>
      )}
      <ol class="board">
        {weekDays(monday).map((day, i) => {
          const ws = workoutsOn(stored, day);
          const isToday = day === today;
          return (
            <li key={day} class={`day ${isToday ? "is-today" : ""} ${ws.length ? "" : "is-rest"} ${day < today ? "is-past" : ""}`}
              style={{ "--zc": dayColor(ws) }} aria-current={isToday ? "date" : undefined}>
              <div class="day-head">
                <span class="dow">{weekdayShort(i)}</span>
                <span class="dom">{dayMonth(day)}</span>
              </div>
              {ws.length ? ws.map((w) => (
                <div class="day-w" key={w.key}>
                  <div class="day-title">{planTitle(stored, w)}</div>
                  <div class="day-meta">
                    {workoutSeconds(w) ? <span class="num">{duration(workoutSeconds(w))}</span> : null}
                    {w.start ? <span>{clock(w.start)}</span> : null}
                  </div>
                  {shownZone(w) ? <ZoneChip zone={shownZone(w)!} /> : null}
                </div>
              )) : <div class="day-title rest-label">{t("ui.week.rest")}</div>}
            </li>
          );
        })}
      </ol>
      {wk?.notes.length ? <div class="week-notes">{wk.notes.map((n, i) => <p key={i}>{noteText(n)}</p>)}</div> : null}
      {onCalendar ? (
        <div class="calendar">
          <button class="secondary" onClick={onCalendar}>{t("ui.calendar.button")}</button>
          <p class="muted small">{t("ui.calendar.help")}</p>
        </div>
      ) : null}
    </section>
  );
}
