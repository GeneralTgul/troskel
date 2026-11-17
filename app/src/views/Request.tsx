import { useState } from "preact/hooks";
import { t } from "../i18n";
import { PlanError } from "../engine";
import { LEVELS } from "../engine/models";
import { weekdayLong } from "../model/dates";
import {
  FORM_ROLES, FormError, MAX_SLOT, MAX_STRENGTH, MAX_WEEKS, MILESTONE_PRIORITIES, MIN_SLOT, PLAN_SPORTS, newGoal,
  type FormDay, type FormGoal, type RequestForm,
} from "../model/request";
import type { IsoDate } from "../model/types";
import { sportName } from "./log";

interface Props {
  form: RequestForm;
  today: IsoDate;
  /** Builds and keeps the plan; throws FormError or PlanError when the answers give none. */
  onMake: (f: RequestForm) => void;
  onCancel: () => void;
}

function errorText(e: unknown): string {
  if (e instanceof FormError || e instanceof PlanError) {
    const args: Record<string, unknown> = { ...e.args };
    if (typeof args.day === "number") args.day = weekdayLong(args.day);
    return t(e.key, args);
  }
  return t("ui.request.err.failed");
}

const num = (v: string): number | null => (v.trim() === "" ? null : Number(v));

/** *Gör ett schema själv* (#777): the answers the coach's form asks for, and a plan from the app's engine. */
export function Request({ form, today, onMake, onCancel }: Props) {
  const [f, setF] = useState<RequestForm>(form);
  const [error, setError] = useState<string | null>(null);
  const set = (change: Partial<RequestForm>) => { setF({ ...f, ...change }); setError(null); };
  const chosen = f.sports.map((s) => s.sport);
  const day = (i: number, change: Partial<FormDay>) => set({ days: f.days.map((d, j) => (j === i ? { ...d, ...change } : d)) });
  const goal = (i: number, change: Partial<FormGoal>) => set({ goals: f.goals.map((g, j) => (j === i ? { ...g, ...change } : g)) });

  const toggleSport = (sport: string, on: boolean) => {
    const sports = on ? [...f.sports, { sport, role: f.sports.length ? "support" : "main", share: 0 }]
      : f.sports.filter((s) => s.sport !== sport);
    if (sports.length && !sports.some((s) => s.role === "main")) sports[0] = { ...sports[0], role: "main" };
    set({ sports, days: f.days.map((d) => ({ ...d, sports: d.sports.filter((s) => s !== sport) })) });
  };
  const setRole = (sport: string, role: string) => set({
    sports: f.sports.map((s) => (s.sport === sport ? { ...s, role }
      : role === "main" && s.role === "main" ? { ...s, role: "support" } : s)),
  });

  const submit = (e: Event) => {
    e.preventDefault();
    try {
      onMake(f);
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <section class="view request">
      <h2>{t("ui.request.title")}</h2>
      <p class="muted">{t("ui.request.intro")}</p>
      {f.capacity.length ? <p class="notice small">{t("ui.request.tests_kept")}</p>
        : <p class="notice small">{t("ui.request.no_test")}</p>}
      <form onSubmit={submit} noValidate>
        <h3>{t("ui.request.sports")}</h3>
        <ul class="list request-sports">
          {PLAN_SPORTS.map((sp) => {
            const row = f.sports.find((s) => s.sport === sp);
            return (
              <li key={sp}>
                <label class="switch grow">
                  <input type="checkbox" checked={!!row} onChange={(e) => toggleSport(sp, e.currentTarget.checked)} />
                  <span class="day-name">{sportName(sp)}</span>
                </label>
                {row && f.sports.length > 1 ? (
                  <select aria-label={t("ui.request.role_of", { sport: sportName(sp) })} value={row.role}
                    onChange={(e) => setRole(sp, e.currentTarget.value)}>
                    {FORM_ROLES.map((r) => <option key={r} value={r}>{t(`ui.request.role.${r}`)}</option>)}
                  </select>
                ) : null}
              </li>
            );
          })}
        </ul>
        <label class="field">
          <span class="small muted">{t("ui.request.strength")}</span>
          <select name="strength" value={String(f.strength_sessions)} onChange={(e) => set({ strength_sessions: Number(e.currentTarget.value) })}>
            {Array.from({ length: MAX_STRENGTH + 1 }, (_, n) => <option key={n} value={String(n)}>{n}</option>)}
          </select>
        </label>

        <h3>{t("ui.request.level_title")}</h3>
        <div class="time-fields">
          <label>
            <span class="small muted">{t("ui.request.level")}</span>
            <select name="level" value={f.level} onChange={(e) => set({ level: e.currentTarget.value })}>
              <option value="">{t("ui.request.level_choose")}</option>
              {LEVELS.map((lv) => <option key={lv} value={lv}>{t(`plan.level.${lv}`)}</option>)}
            </select>
          </label>
          <label>
            <span class="small muted">{t("ui.request.recent")}</span>
            <input type="number" inputMode="numeric" min={0} max={3000} step={10} value={f.recent_weekly_minutes ?? ""}
              onInput={(e) => set({ recent_weekly_minutes: num(e.currentTarget.value) })} />
          </label>
        </div>
        <p class="muted small">{t("ui.request.recent_help")}</p>

        <h3>{t("ui.request.days")}</h3>
        <p class="muted small">{t("ui.request.days_help")}</p>
        <ol class="time-rows">
          {f.days.map((d, i) => (
            <li key={i} class={d.on ? "" : "is-off"}>
              <label class="switch">
                <input type="checkbox" checked={d.on} onChange={(e) => day(i, { on: e.currentTarget.checked })} />
                <span class="day-name">{weekdayLong(i)}</span>
              </label>
              {d.on ? (
                <div class="time-fields">
                  <label>
                    <span class="small muted">{t("ui.times.minutes")}</span>
                    <input type="number" inputMode="numeric" min={MIN_SLOT} max={MAX_SLOT} step={5} value={d.minutes}
                      onInput={(e) => day(i, { minutes: Number(e.currentTarget.value) || 0 })} />
                  </label>
                  <label>
                    <span class="small muted">{t("ui.times.start")}</span>
                    <input type="time" value={d.start} onInput={(e) => day(i, { start: e.currentTarget.value })} />
                  </label>
                  {chosen.length > 1 ? (
                    <label>
                      <span class="small muted">{t("ui.request.day_sport")}</span>
                      <select value={d.sports.length === 1 ? d.sports[0] : ""}
                        onChange={(e) => day(i, { sports: e.currentTarget.value ? [e.currentTarget.value] : [] })}>
                        <option value="">{t("ui.request.all_sports")}</option>
                        {chosen.map((s) => <option key={s} value={s}>{sportName(s)}</option>)}
                      </select>
                    </label>
                  ) : null}
                </div>
              ) : null}
            </li>
          ))}
        </ol>

        <h3>{t("ui.request.goal_title")}</h3>
        <div class="segmented" role="radiogroup" aria-label={t("ui.request.goal_title")}>
          {(["goal", "weeks"] as const).map((m) => (
            <button key={m} type="button" role="radio" aria-checked={f.mode === m} class={f.mode === m ? "on" : ""}
              onClick={() => set({ mode: m })}>{t(`ui.request.mode.${m}`)}</button>
          ))}
        </div>
        {f.mode === "goal" ? (
          <>
            {f.goals.map((g, i) => (
              <div key={i} class="extra-form request-goal">
                <p class="small"><b>{t(i === 0 ? "ui.request.goal_main" : "ui.request.milestone")}</b></p>
                <label><span class="small">{t("ui.request.goal_name")}</span>
                  <input type="text" maxLength={60} value={g.name} placeholder={t("ui.request.goal_name_hint")}
                    onInput={(e) => goal(i, { name: e.currentTarget.value })} /></label>
                <label><span class="small">{t("ui.request.goal_date")}</span>
                  <input type="date" min={today} value={g.date} onInput={(e) => goal(i, { date: e.currentTarget.value })} /></label>
                {chosen.length > 1 ? (
                  <label><span class="small">{t("ui.extra.sport")}</span>
                    <select value={g.sport || chosen[0]} onChange={(e) => goal(i, { sport: e.currentTarget.value })}>
                      {chosen.map((s) => <option key={s} value={s}>{sportName(s)}</option>)}
                    </select></label>
                ) : null}
                <label><span class="small">{t("ui.request.goal_km")}</span>
                  <input type="number" inputMode="decimal" min={0} max={1000} step={0.1} value={g.distance_km ?? ""}
                    onInput={(e) => goal(i, { distance_km: num(e.currentTarget.value) })} /></label>
                {i === 0 ? (
                  <label><span class="small">{t("ui.request.goal_target")}</span>
                    <input type="text" maxLength={40} value={g.target} placeholder={t("ui.request.goal_target_hint")}
                      onInput={(e) => goal(i, { target: e.currentTarget.value })} /></label>
                ) : (
                  <>
                    <label><span class="small">{t("ui.request.priority")}</span>
                      <select value={g.priority} onChange={(e) => goal(i, { priority: e.currentTarget.value })}>
                        {MILESTONE_PRIORITIES.map((p) => <option key={p} value={p}>{t(`ui.request.priority.${p}`)}</option>)}
                      </select></label>
                    <button type="button" class="link-btn" onClick={() => set({ goals: f.goals.filter((_, j) => j !== i) })}>
                      {t("ui.request.remove_milestone", { name: g.name || t("ui.request.milestone") })}
                    </button>
                  </>
                )}
              </div>
            ))}
            <button type="button" class="link-btn" onClick={() => set({ goals: [...f.goals, newGoal("B")] })}>
              {t("ui.request.add_milestone")}
            </button>
          </>
        ) : (
          <label class="field">
            <span class="small muted">{t("ui.request.weeks")}</span>
            <input type="number" inputMode="numeric" min={4} max={MAX_WEEKS} step={1} value={f.weeks ?? ""}
              onInput={(e) => set({ weeks: num(e.currentTarget.value) })} />
          </label>
        )}

        <h3>{t("ui.request.start_title")}</h3>
        <div class="time-fields">
          <label>
            <span class="small muted">{t("ui.request.start")}</span>
            <input type="date" value={f.start} onInput={(e) => set({ start: e.currentTarget.value })} />
          </label>
          <label>
            <span class="small muted">{t("ui.request.name")}</span>
            <input type="text" maxLength={60} value={f.athlete} onInput={(e) => set({ athlete: e.currentTarget.value })} />
          </label>
        </div>
        <p class="muted small">{t("ui.request.name_help")}</p>

        {error ? <p class="notice error" role="alert">{error}</p> : null}
        <button type="submit" class="primary wide">{t("ui.request.make")}</button>
        <button type="button" class="secondary wide" onClick={onCancel}>{t("ui.request.cancel")}</button>
      </form>
    </section>
  );
}
