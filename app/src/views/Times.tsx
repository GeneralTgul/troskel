import { useState } from "preact/hooks";
import { t } from "../i18n";
import type { Engine } from "../engine";
import { addDays, dayMonth, weekdayLong } from "../model/dates";
import type { PendingTimes } from "../model/storage";
import { daysFromSlots, slotsFromDays, type DayTimes } from "../model/times";
import type { IsoDate, StoredPlan } from "../model/types";

interface Props {
  stored: StoredPlan;
  today: IsoDate;
  engine: Engine;
  pending: PendingTimes | null;
  onSaveTimes: (p: PendingTimes) => void;
  onRebuilt: (s: StoredPlan) => void;
}

/** Minutes and a clock time per weekday, and dates that do not work. */
export function Times({ stored, today, engine, pending, onSaveTimes, onRebuilt }: Props) {
  const [days, setDays] = useState<DayTimes[]>(() => daysFromSlots(pending?.slots ?? stored.request.availability));
  const [off, setOff] = useState<IsoDate[]>(() => pending?.unavailable ?? []);
  const [newDate, setNewDate] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const edit = (i: number, change: Partial<DayTimes>) => {
    setDays(days.map((d, j) => (j === i ? { ...d, ...change } : d)));
    setMsg(null);
  };

  const save = (e: Event) => {
    e.preventDefault();
    const slots = slotsFromDays(days, stored.request.availability, stored.request.sports);
    const unavailable = off.filter((d) => d > today).sort();
    const p: PendingTimes = { slots, unavailable, saved: today };
    if (engine.available) {
      try {
        onRebuilt(engine.rebuild(stored, { slots, unavailable, from: addDays(today, 1) }));
        setMsg(t("ui.times.saved_rebuilt"));
        return;
      } catch {
        onSaveTimes(p);
        setMsg(t("ui.times.failed"));
        return;
      }
    }
    onSaveTimes(p);
    setMsg(t("ui.times.saved_pending"));
  };

  return (
    <section class="view times">
      <h2>{t("ui.times.title")}</h2>
      <p class="muted">{t("ui.times.intro")}</p>
      {pending && !msg ? <p class="notice">{t("ui.times.pending", { date: dayMonth(pending.saved) })}</p> : null}
      <form onSubmit={save}>
        <ol class="time-rows">
          {days.map((d, i) => (
            <li key={i} class={d.on ? "" : "is-off"}>
              <label class="switch">
                <input type="checkbox" checked={d.on} onChange={(e) => edit(i, { on: e.currentTarget.checked })} />
                <span class="day-name">{weekdayLong(i)}</span>
              </label>
              {d.on ? (
                <div class="time-fields">
                  <label>
                    <span class="small muted">{t("ui.times.minutes")}</span>
                    <input type="number" inputMode="numeric" min={10} max={300} step={5} value={d.minutes}
                      onInput={(e) => edit(i, { minutes: Number(e.currentTarget.value) || 0 })} />
                  </label>
                  <label>
                    <span class="small muted">{t("ui.times.start")}</span>
                    <input type="time" value={d.start} onInput={(e) => edit(i, { start: e.currentTarget.value })} />
                  </label>
                  {d.start ? (
                    <button type="button" class="link-btn small" onClick={() => edit(i, { start: "" })}>
                      {t("ui.times.any_time")}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </li>
          ))}
        </ol>

        <h3>{t("ui.times.unavailable")}</h3>
        <p class="muted small">{t("ui.times.unavailable_help")}</p>
        <ul class="list">
          {off.map((d) => (
            <li key={d}>
              <span class="grow">{weekdayLong((new Date(d + "T12:00:00Z").getUTCDay() + 6) % 7)} {dayMonth(d)}</span>
              <button type="button" class="ghost" aria-label={t("ui.times.remove", { date: dayMonth(d) })}
                onClick={() => setOff(off.filter((x) => x !== d))}>×</button>
            </li>
          ))}
        </ul>
        <div class="add-date">
          <input type="date" min={addDays(today, 1)} value={newDate} onInput={(e) => setNewDate(e.currentTarget.value)} />
          <button type="button" class="secondary" disabled={!newDate}
            onClick={() => { if (newDate && !off.includes(newDate)) setOff([...off, newDate].sort()); setNewDate(""); }}>
            {t("ui.times.add_date")}
          </button>
        </div>

        <button type="submit" class="primary wide">{t("ui.times.save")}</button>
        {msg ? <p class="notice" role="status">{msg}</p> : null}
      </form>
    </section>
  );
}
