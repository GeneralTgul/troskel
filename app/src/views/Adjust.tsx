import { useState } from "preact/hooks";
import { t } from "../i18n";
import {
  editOf, moveDays, moveWorkout, original, shortenTo, shortenWorkout, swapWith, swapWorkouts, undoEdit,
} from "../model/adjust";
import { duration, planTitle, workoutMinutes } from "../model/plan";
import type { IsoDate, StoredPlan, StoredWorkout } from "../model/types";
import { dayLabel } from "./parts";

/**
 * *Ändra passet* under a workout in the day sheet (#777, step 3): move it to another day of the week, swap it with
 * another workout of the week, or shorten it, and undo the change. Every change is an edit on the plan
 * (model/adjust.ts), which stays when the plan is rebuilt with new times or laid on a new test.
 */
export function AdjustBox({ stored, w, today, onPlan }: {
  stored: StoredPlan; w: StoredWorkout; today: IsoDate; onPlan: (p: StoredPlan) => void;
}) {
  const [open, setOpen] = useState(false);
  const days = moveDays(stored, w, today);
  const others = swapWith(stored, w, today);
  const lengths = open ? shortenTo(stored, w) : [];
  const [day, setDay] = useState("");
  const [other, setOther] = useState("");
  const [minutes, setMinutes] = useState("");
  const edit = editOf(stored, w.key);
  const orig = edit ? original(stored, w.key) : undefined;
  const changed: string[] = [];
  if (edit?.date && orig) changed.push(t("ui.adjust.moved", { day: dayLabel(orig.date) }));
  if (edit?.minutes != null && orig) changed.push(t("ui.adjust.shortened", { from: duration(workoutMinutes(orig) * 60) }));

  return (
    <div class="adjust">
      {changed.length ? <p class="muted small adjust-note">{changed.join(" ")}</p> : null}
      {!open ? (
        <div class="adjust-row">
          <button type="button" class="link-btn" onClick={() => setOpen(true)}>{t("ui.adjust.open")}</button>
          {edit ? <button type="button" class="link-btn" onClick={() => onPlan(undoEdit(stored, w.key))}>{t("ui.adjust.undo")}</button> : null}
        </div>
      ) : (
        <fieldset class="adjust-box">
          <legend>{t("ui.adjust.title")}</legend>
          {days.length ? (
            <label class="adjust-row">
              <span>{t("ui.adjust.move")}</span>
              <select value={day} onChange={(e) => setDay((e.target as HTMLSelectElement).value)}>
                <option value="">–</option>
                {days.map((d) => <option key={d} value={d}>{dayLabel(d)}</option>)}
              </select>
              <button type="button" class="secondary" disabled={!day} onClick={() => onPlan(moveWorkout(stored, w.key, day))}>
                {t("ui.adjust.move_btn")}
              </button>
            </label>
          ) : null}
          {others.length ? (
            <label class="adjust-row">
              <span>{t("ui.adjust.swap")}</span>
              <select value={other} onChange={(e) => setOther((e.target as HTMLSelectElement).value)}>
                <option value="">–</option>
                {others.map((x) => <option key={x.key} value={x.key}>{dayLabel(x.date)}: {planTitle(stored, x)}</option>)}
              </select>
              <button type="button" class="secondary" disabled={!other}
                onClick={() => onPlan(swapWorkouts(stored, w, others.find((x) => x.key === other)!))}>
                {t("ui.adjust.swap_btn")}
              </button>
            </label>
          ) : null}
          {lengths.length ? (
            <label class="adjust-row">
              <span>{t("ui.adjust.shorten")}</span>
              <select value={minutes} onChange={(e) => setMinutes((e.target as HTMLSelectElement).value)}>
                <option value="">–</option>
                {lengths.map((m) => <option key={m} value={String(m)}>{duration(m * 60)}</option>)}
              </select>
              <button type="button" class="secondary" disabled={!minutes}
                onClick={() => onPlan(shortenWorkout(stored, w.key, Number(minutes)))}>
                {t("ui.adjust.shorten_btn")}
              </button>
            </label>
          ) : null}
          {!days.length && !others.length && !lengths.length ? <p class="muted small">{t("ui.adjust.nothing")}</p> : null}
          <p class="muted small">{t("ui.adjust.help")}</p>
          <div class="adjust-row">
            {edit ? <button type="button" class="link-btn" onClick={() => onPlan(undoEdit(stored, w.key))}>{t("ui.adjust.undo")}</button> : null}
            <button type="button" class="link-btn" onClick={() => setOpen(false)}>{t("ui.adjust.close")}</button>
          </div>
        </fieldset>
      )}
    </div>
  );
}
