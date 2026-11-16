import { useEffect, useRef, useState } from "preact/hooks";
import { t } from "../i18n";
import { addDays, dayMonth, isoWeek, mondayOf, weekDays, weekdayShort } from "../model/dates";
import { duration, hours, mainZone, noteText, weekMinutes, weekOf, workoutSeconds, workoutsOn, planTitle } from "../model/plan";
import type { IsoDate, StoredPlan, StoredWorkout } from "../model/types";
import { extrasOn } from "../model/log";
import { Extras, StatusMark, StatusPicker, WeekNumbers, type LogProps, NO_LOG, ignore } from "./log";
import { clock, dayLabel, shownZone, workoutColor, WorkoutCard, zoneColor, ZoneChip } from "./parts";

/** The colour of a day's card: that of its first workout that has one (a zone, or a test), so that strength before
 * a run (no zone) does not make the day look like a rest day. */
export function dayColor(ws: StoredWorkout[]): string {
  const w = ws.find((x) => x.family === "test" || mainZone(x));
  return w ? workoutColor(w) : zoneColor(null);
}

/** One day on top of the week: its workouts as big cards with the box for the day, the marks and the sessions
 * outside the plan. A tap on the background, Escape or *Close* closes it. */
function DaySheet({ stored, day, today, log, onLog, onClose }: {
  stored: StoredPlan; day: IsoDate; today: IsoDate; onClose: () => void;
} & LogProps) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    globalThis.addEventListener?.("keydown", onKey);
    return () => globalThis.removeEventListener?.("keydown", onKey);
  }, []);
  const ws = workoutsOn(stored, day);
  return (
    <div class="sheet-back" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="sheet" role="dialog" aria-modal="true" aria-label={dayLabel(day)}>
        <header class="sheet-head">
          <h2>{dayLabel(day)}</h2>
          <button ref={close} class="ghost" aria-label={t("ui.day.close")} onClick={onClose}>×</button>
        </header>
        {ws.length ? ws.map((w) => (
          <WorkoutCard key={w.key} stored={stored} w={w} big>
            {day <= today ? <StatusPicker stored={stored} w={w} log={log} onLog={onLog} /> : null}
          </WorkoutCard>
        )) : <article class="workout rest"><h3>{t("ui.today.rest")}</h3></article>}
        {day <= today ? <Extras stored={stored} day={day} log={log} onLog={onLog} /> : null}
      </div>
    </div>
  );
}

/** The week as a board on the wall: seven cards, today marked, rest days plain. A tap on a day opens it. */
export function Week({ stored, today, onCalendar, log = NO_LOG, onLog = ignore }: {
  stored: StoredPlan; today: IsoDate; onCalendar?: () => void;
} & Partial<LogProps>) {
  const [monday, setMonday] = useState(mondayOf(today));
  const [open, setOpen] = useState<IsoDate | null>(null);
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
          const extra = extrasOn(log, day);
          return (
            <li key={day} class={`day ${isToday ? "is-today" : ""} ${ws.length ? "" : "is-rest"} ${day < today ? "is-past" : ""}`}
              style={{ "--zc": dayColor(ws) }} aria-current={isToday ? "date" : undefined}
              role="button" tabIndex={0} aria-label={t("ui.day.open", { day: dayLabel(day) })}
              onClick={() => setOpen(day)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(day); } }}>
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
                  <StatusMark w={w} log={log} />
                </div>
              )) : !extra.length ? <div class="day-title rest-label">{t("ui.week.rest")}</div> : null}
              {extra.map((x) => (
                <div class="day-w day-extra" key={x.id}>
                  <div class="day-title">+ {x.name}</div>
                  <div class="day-meta"><span class="num">{duration(x.minutes * 60)}</span></div>
                </div>
              ))}
            </li>
          );
        })}
      </ol>
      <WeekNumbers stored={stored} monday={monday} log={log} />
      {wk?.notes.length ? <div class="week-notes">{wk.notes.map((n, i) => <p key={i}>{noteText(n)}</p>)}</div> : null}
      {onCalendar ? (
        <div class="calendar">
          <button class="secondary" onClick={onCalendar}>{t("ui.calendar.button")}</button>
          <p class="muted small">{t("ui.calendar.help")}</p>
        </div>
      ) : null}
      {open ? <DaySheet stored={stored} day={open} today={today} log={log} onLog={onLog} onClose={() => setOpen(null)} /> : null}
    </section>
  );
}
