/** The athlete's log in the views: ticking off a workout, sessions outside the plan, the week in numbers and the
 * season strip. */
import { useState } from "preact/hooks";
import { planSports } from "../engine/models";
import { t } from "../i18n";
import { addDays, dayMonth } from "../model/dates";
import {
  addExtra, extrasOn, halfOf, markOf, removeExtra, setMark, STATUSES, weekTotals, type Status, type TrainingLog,
} from "../model/log";
import { hours, planEnd, planStart, planTitle, workoutMinutes } from "../model/plan";
import { season } from "../model/season";
import type { IsoDate, StoredPlan, StoredWorkout } from "../model/types";
import { AutoNote } from "./health";

export interface LogProps {
  log: TrainingLog;
  onLog: (l: TrainingLog) => void;
}

/** A view without a log (a test, a screenshot): nothing ticked off, and nothing kept. */
export const NO_LOG: TrainingLog = { marks: {}, extras: [] };
export const ignore = () => {};

export const sportName = (s: string) => t(s === "other" ? "ui.sport.other" : `plan.sport.${s}`);

/** Done, partly or missed for one workout. A second tap on the chosen one takes the mark away. */
export function StatusPicker({ stored, w, log, onLog }: { stored: StoredPlan; w: StoredWorkout } & LogProps) {
  const mark = markOf(log, w);
  const pick = (s: Status) => onLog(setMark(log, w.key, mark?.status === s && log.marks[w.key] ? null : { status: s }));
  const title = planTitle(stored, w);
  return (
    <div class="status" data-key={w.key}>
      <span class="status-label muted small">{t("ui.log.label")}</span>
      <div class="segmented status-pick" role="radiogroup" aria-label={`${t("ui.log.label")} ${title}`}>
        {STATUSES.map((s) => (
          <button key={s} role="radio" aria-checked={mark?.status === s} class={`st-${s} ${mark?.status === s ? "on" : ""}`}
            onClick={() => pick(s)}>{t(`ui.log.${s}`)}</button>
        ))}
      </div>
      <AutoNote mark={log.marks[w.key] ?? null} day={w.date} />
      {mark?.status === "partial" ? (
        <label class="status-minutes">
          <span class="small">{t("ui.log.minutes")}</span>
          <input type="number" inputMode="numeric" min={0} max={workoutMinutes(w)} step={5}
            value={mark.minutes ?? halfOf(w)}
            onChange={(e) => {
              const v = Math.round(Number((e.target as HTMLInputElement).value));
              if (Number.isFinite(v)) onLog(setMark(log, w.key, { status: "partial", minutes: Math.max(0, v) }));
            }} />
        </label>
      ) : null}
    </div>
  );
}

/** The small mark on a day of the week board. */
export function StatusMark({ w, log }: { w: StoredWorkout; log: TrainingLog }) {
  const mark = markOf(log, w);
  if (!mark) return null;
  return <span class={`st-mark st-${mark.status}`}>{t(`ui.log.${mark.status}`)}</span>;
}

/** The sessions outside the plan on a day, with a form to add one ("+ yoga", sport and minutes). */
export function Extras({ stored, day, log, onLog }: { stored: StoredPlan; day: IsoDate } & LogProps) {
  const [open, setOpen] = useState(false);
  const sports = [...planSports(stored.request), "strength", "other"].filter((s, i, a) => a.indexOf(s) === i);
  const [name, setName] = useState("");
  const [sport, setSport] = useState(sports[0]);
  const [minutes, setMinutes] = useState(30);
  const list = extrasOn(log, day);
  const save = (e: Event) => {
    e.preventDefault();
    onLog(addExtra(log, { date: day, sport, minutes, name: name.trim() || sportName(sport) }));
    setOpen(false);
    setName("");
  };
  return (
    <div class="extras">
      {list.length ? (
        <ul class="extra-list">
          {list.map((x) => (
            <li key={x.id}>
              <span class="grow"><b>+ {x.name}</b> <span class="muted small">{sportName(x.sport)} · {hours(x.minutes)}</span></span>
              <button class="link-btn" aria-label={t("ui.extra.remove", { name: x.name })}
                onClick={() => onLog(removeExtra(log, x.id))}>×</button>
            </li>
          ))}
        </ul>
      ) : null}
      {open ? (
        <form class="extra-form" onSubmit={save}>
          <label><span class="small">{t("ui.extra.name")}</span>
            <input type="text" value={name} placeholder={t("ui.extra.name_hint")} maxLength={40}
              onInput={(e) => setName((e.target as HTMLInputElement).value)} /></label>
          <label><span class="small">{t("ui.extra.sport")}</span>
            <select value={sport} onChange={(e) => setSport((e.target as HTMLSelectElement).value)}>
              {sports.map((s) => <option key={s} value={s}>{sportName(s)}</option>)}
            </select></label>
          <label><span class="small">{t("ui.extra.minutes")}</span>
            <input type="number" inputMode="numeric" min={1} max={600} step={5} value={minutes}
              onInput={(e) => setMinutes(Number((e.target as HTMLInputElement).value) || 0)} /></label>
          <div class="extra-actions">
            <button type="submit" class="primary" disabled={minutes <= 0}>{t("ui.extra.save")}</button>
            <button type="button" class="secondary" onClick={() => setOpen(false)}>{t("ui.extra.cancel")}</button>
          </div>
        </form>
      ) : (
        <button class="link-btn" onClick={() => setOpen(true)}>{t("ui.extra.add")}</button>
      )}
    </div>
  );
}

/** Done against planned per sport, "Löpning 0:48 av 2:50", with a bar that turns green when the plan is reached. */
export function WeekNumbers({ stored, monday, log }: { stored: StoredPlan; monday: IsoDate; log: TrainingLog }) {
  const sum = weekTotals(stored, log, monday);
  return (
    <section class="numbers" aria-label={t("ui.sum.title")}>
      <h3>{t("ui.sum.title")}</h3>
      {!sum.rows.length && !sum.extras.length ? <p class="muted">{t("ui.sum.none")}</p> : null}
      <ul class="sum-rows">
        {sum.rows.map((r) => (
          <li key={r.sport} class={`sum-row ${r.reached ? "reached" : ""}`} data-sport={r.sport}>
            <span class="sum-name">{sportName(r.sport)}</span>
            <span class="sum-val num">{t("ui.sum.of", { done: hours(r.done), planned: hours(r.planned) })}</span>
            <span class="meter" aria-hidden="true"><i style={{ width: `${Math.min(100, (r.done / r.planned) * 100)}%` }} /></span>
          </li>
        ))}
        {sum.extras.map((x) => (
          <li key={x.id} class="sum-row extra">
            <span class="sum-name">+ {x.name}</span>
            <span class="sum-val"><span class="num">{hours(x.minutes)}</span> {t("ui.sum.extra")}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The blocks up to the goal, a cell per week, the easy weeks green and a line at today. */
export function SeasonStrip({ stored, today }: { stored: StoredPlan; today: IsoDate }) {
  const s = season(stored, today);
  if (!s.blocks.length) return null;
  const start = planStart(stored)!;
  const end = s.goal ? s.goal.date : planEnd(stored)!;
  return (
    <section class="season">
      <h3>{t("ui.season.title")}</h3>
      <p class="muted small">
        {s.goal ? t("ui.season.to_goal", { name: s.goal.name, date: dayMonth(s.goal.date) })
          : t("ui.season.whole", { start: dayMonth(start), end: dayMonth(end) })}
      </p>
      <div class="strip" role="img" aria-label={t("ui.season.label")}>
        {s.blocks.map((b, i) => (
          <div key={i} class={`strip-block block-${b.kind}`} style={{ flexGrow: b.weeks.length }}>
            <span class="strip-name">{t(`plan.block_short.${b.kind}`)}</span>
            <span class="strip-weeks">
              {b.weeks.map((w) => (
                <i key={w.index} class={`${w.easy ? "easy" : ""} kind-${w.kind}`}
                  title={`${t("ui.season.week", { i: w.index })} · ${dayMonth(w.monday)} – ${dayMonth(addDays(w.monday, 6))}${w.easy ? ` · ${t("ui.season.easy")}` : ""}`} />
              ))}
            </span>
          </div>
        ))}
        {s.marker != null ? <span class="strip-now" style={{ left: `${s.marker * 100}%` }} /> : null}
      </div>
      <p class="strip-legend muted small"><span class="key easy" /> {t("ui.season.easy")}</p>
    </section>
  );
}
