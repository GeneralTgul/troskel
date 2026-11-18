import { useEffect, useRef, useState } from "preact/hooks";
import { t } from "../i18n";
import { addDays, dayMonth, daysBetween, isoWeek, mondayOf, weekDays, weekdayShort } from "../model/dates";
import { duration, hours, mainZone, noteText, weekMinutes, weekOf, workoutSeconds, workoutsOn, planTitle } from "../model/plan";
import type { IsoDate, StoredPlan, StoredWorkout } from "../model/types";
import { dayState, extrasOn, markOf, type DayState } from "../model/log";
import { Extras, SeasonStrip, StatusPicker, WeekNumbers, type LogProps, NO_LOG, ignore } from "./log";
import { adjustable } from "../model/adjust";
import { AdjustBox } from "./Adjust";
import { clock, dayLabel, shownZone, SportIcon, workoutColor, WorkoutCard, zoneColor } from "./parts";

/** The colour of a day's card: that of its first workout that has one (a zone, or a test), so that strength before
 * a run (no zone) does not make the day look like a rest day. */
export function dayColor(ws: StoredWorkout[]): string {
  const w = ws.find((x) => x.family === "test" || mainZone(x));
  return w ? workoutColor(w) : zoneColor(null);
}

/** One day on top of the week: its workouts as big cards with the box for the day, the marks and the sessions
 * outside the plan. A tap on the background, Escape or *Close* closes it. */
function DaySheet({ stored, day, today, log, onLog, onClose, onPlan }: {
  stored: StoredPlan; day: IsoDate; today: IsoDate; onClose: () => void; onPlan?: (p: StoredPlan) => void;
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
            {onPlan && adjustable(stored, w, today) && !markOf(log, w)
              ? <AdjustBox stored={stored} w={w} today={today} onPlan={onPlan} /> : null}
          </WorkoutCard>
        )) : <article class="workout rest"><h3>{t("ui.today.rest")}</h3></article>}
        {day <= today ? <Extras stored={stored} day={day} log={log} onLog={onLog} />
          : <p class="muted small extra-later">{t("ui.extra.later")}</p>}
      </div>
    </div>
  );
}

/** The next race from today and the ones after it, with days left: the hall screen's race card. */
function Races({ stored, today }: { stored: StoredPlan; today: IsoDate }) {
  const ahead = [...stored.request.goals].filter((g) => g.date >= today).sort((a, b) => a.date.localeCompare(b.date));
  if (!ahead.length) return null;
  const left = (d: IsoDate) => daysBetween(today, d);
  const days = (n: number) => (n === 0 ? t("ui.race.today") : t(n === 1 ? "ui.race.day" : "ui.race.days", { n }));
  const [next, ...rest] = ahead;
  return (
    <section class="races card" aria-label={t("ui.race.title")}>
      <div class="race-next">
        <SportIcon sport={next.sport} />
        <div>
          <p class="kicker">{t("ui.race.title")}</p>
          <p class="race-days num">{days(left(next.date))}</p>
          <p class="muted">{next.name} · {dayMonth(next.date)}</p>
        </div>
      </div>
      {rest.length ? (
        <ul class="race-list">
          {rest.slice(0, 3).map((g) => (
            <li key={g.name + g.date}>
              <SportIcon sport={g.sport} />
              <span class="grow"><b>{g.name}</b> <span class="muted small">{dayMonth(g.date)}</span></span>
              <span class="num">{t("ui.race.short", { n: left(g.date) })}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** Where a day stands, at the right of its row: done, partly, missed, rest or today. */
function DayStateMark({ state }: { state: DayState }) {
  if (!state) return null;
  const text = state === "today" ? t("ui.week.today") : state === "rest" ? t("ui.week.rest") : t(`ui.log.${state}`);
  return <span class={`st-mark st-${state}`}>{text}</span>;
}

/** The week as the hall screen shows it: a row per day with the workouts as pills in their zone's colour and the
 * sport's icon, the sessions outside the plan as "+ name", and the day's state at the right. Beside it on a wide
 * screen the week in numbers and the next races, under it the season. A tap on a day opens it, and there the athlete
 * can move, swap and shorten a workout (`onPlan`, #777: not in the example plan). */
export function Week({ stored, today, onCalendar, log = NO_LOG, onLog = ignore, onPlan }: {
  stored: StoredPlan; today: IsoDate; onCalendar?: () => void; onPlan?: (p: StoredPlan) => void;
} & Partial<LogProps>) {
  const [monday, setMonday] = useState(mondayOf(today));
  const [open, setOpen] = useState<IsoDate | null>(null);
  const wk = weekOf(stored, monday);
  const thisWeek = mondayOf(today);
  const offset = Math.round(daysBetween(thisWeek, monday) / 7);
  const kicker = offset === 0 ? t("ui.week.this") : offset === -1 ? t("ui.week.prev") : offset === 1 ? t("ui.week.next")
    : `${dayMonth(monday)} – ${dayMonth(addDays(monday, 6))}`;
  return (
    <section class="view week">
      <div class="week-main">
        <header class="week-head">
          <button class="week-arrow" aria-label={t("ui.week.prev")} onClick={() => setMonday(addDays(monday, -7))}>‹</button>
          <div class="week-title card">
            <p class="kicker">{kicker}</p>
            <div class="week-line">
              <h2>{t("ui.week.title", { n: isoWeek(monday) })}</h2>
              {wk ? <span class="week-block">· {t(`plan.block.${wk.block}`)}</span> : null}
            </div>
            <p class="muted small">
              {wk ? <><span class="num">{hours(weekMinutes(wk))}</span> {t("ui.week.training")} · {t(`plan.week.kind.${wk.kind}`)}
                <span class="week-of">{t("ui.week.plan_week", { i: wk.index, n: stored.plan.weeks.length })}</span></>
                : t("ui.week.outside")}
            </p>
          </div>
          <button class="week-arrow" aria-label={t("ui.week.next")} onClick={() => setMonday(addDays(monday, 7))}>›</button>
        </header>
        {offset ? <button class="link-btn this-week" onClick={() => setMonday(thisWeek)}>{t("ui.week.this")}</button> : null}
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
                <div class="pills">
                  {ws.map((w) => {
                    const mark = markOf(log, w);
                    const zone = shownZone(w);
                    return (
                      <div class={`day-w pill ${mark ? `is-${mark.status}` : ""}`} key={w.key} style={{ "--zc": workoutColor(w) }}>
                        {mark?.status === "done" ? <span class="tick" aria-hidden="true">✓</span> : <SportIcon sport={w.sport} />}
                        <span class="day-title">{planTitle(stored, w)}</span>
                        {workoutSeconds(w) ? <span class="num">{duration(workoutSeconds(w))}</span> : null}
                        {zone ? <span class="chip" style={{ "--zc": zoneColor(zone) }}><b>{zone}</b></span> : null}
                        {w.start ? <span class="pill-time muted">{clock(w.start)}</span> : null}
                      </div>
                    );
                  })}
                  {!ws.length && !extra.length ? <span class="day-w pill rest-pill"><span class="day-title">{t("ui.week.rest")}</span></span> : null}
                  {extra.map((x) => (
                    <span class="day-extra muted small" key={x.id}>+ {x.name} {duration(x.minutes * 60)}</span>
                  ))}
                </div>
                <DayStateMark state={ws.length ? dayState(log, ws, day, today) : extra.length ? null : "rest"} />
              </li>
            );
          })}
        </ol>
        {wk?.notes.length ? <div class="week-notes">{wk.notes.map((n, i) => <p key={i}>{noteText(n)}</p>)}</div> : null}
      </div>
      <aside class="week-side">
        <Races stored={stored} today={today} />
        <div class="card"><WeekNumbers stored={stored} monday={monday} log={log} /></div>
      </aside>
      <div class="week-season card"><SeasonStrip stored={stored} today={today} /></div>
      {onCalendar ? (
        <div class="calendar">
          <button class="secondary" onClick={onCalendar}>{t("ui.calendar.button")}</button>
          <p class="muted small">{t("ui.calendar.help")}</p>
        </div>
      ) : null}
      {open ? <DaySheet stored={stored} day={open} today={today} log={log} onLog={onLog} onClose={() => setOpen(null)} onPlan={onPlan} /> : null}
    </section>
  );
}
