"""The result of a zones test: the box "Fyll i klockan" for the athlete's watch brand, the max HR as
both the highest 5-s mean and the highest single reading, the coach changing the max HR, the pace
first, and zones from max HR only saying so."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtWidgets import QLabel

from lopanalys.assessments import library, max_hr
from lopanalys.assessments import zone_systems as zs
from lopanalys.core.i18n import t
from lopanalys.core.models import Athlete, Session, StepRecord, TestKind, TimeSample
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.results import ResultsView

T0 = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)


def _texts(w) -> str:
    return " | ".join(lb.text() for lb in w.findChildren(QLabel))


def _ctx(tmp_path) -> AppContext:
    return AppContext(store=services.open_store(tmp_path / "db.sqlite"))


def _max_session(ctx: AppContext, resting_hr: int | None = None) -> tuple[Athlete, Session]:
    """A max test to the limit: the 5-s mean tops at 199, one single reading at 200."""
    a = ctx.store.save_athlete(Athlete(None, "Olle", "Löpare", max_hr=None, resting_hr=resting_hr))
    p = library.max_hr_ramp(12.0, warmup_s=0, start_incline_pct=1.0)
    recs = [StepRecord(i, 12.0, 1.0 + i, 60, hr_end=150 + 4 * i) for i in range(12)]
    end = 12 * 60.0
    smp = [TimeSample(float(k), int(round(120 + 78 * k / (end - 10)))) for k in range(int(end) - 10)]
    smp += [TimeSample(float(k), v) for k, v in zip(range(int(end) - 10, int(end) + 1),
                                                     (198, 199, 199, 200, 199, 198, 199, 199, 198, 198, 197))]
    smp += [TimeSample(end + k, 197 - k // 3) for k in range(1, 70)]
    s = Session(None, a.id, TestKind.MAX_HR, T0, T0 + timedelta(seconds=end + 70), protocol_name=p.name,
                protocol=p.to_dict(), result={"plan_kind": "max_hr", "exhaustion_t_s": end})
    ctx.store.db.save_test(s, recs, smp)
    return a, s


# ---------------------------------------------------------------------- the analysis
def test_max_hr_is_the_5_s_mean_and_the_single_reading_stands_beside_it():
    smp = [TimeSample(float(k), 150) for k in range(60)] + [
        TimeSample(60.0 + k, v) for k, v in enumerate((198, 199, 199, 200, 199, 198, 199))]
    r = max_hr.analyze(smp)
    assert r.peak_hr == 199 and r.peak_single_hr == 200


# ---------------------------------------------------------------------- the watch box (no Qt)
def _setup(lthr: int | None, mx: int | None, rest: int | None, estimated: bool = False) -> dict:
    return {"method": "lthr" if lthr else "max", "max_hr": mx, "resting_hr": rest, "lthr": lthr,
            "threshold_speed_kmh": 13.5 if lthr else None, "estimated": estimated, "sport": "run",
            "zones": [{"zone": i + 1, "hr_low": 100 + 15 * i, "hr_high": 115 + 15 * i, "pct_low": 50 + 10 * i,
                       "pct_high": 60 + 10 * i} for i in range(5)]}


def test_one_decision_per_watch_brand():
    lthr = _setup(177, 199, 50)
    hw = zs.watch_card(lthr, "huawei")
    assert hw["decision"] == "Välj LTHR-procent och skriv in tröskelpulsen 177."
    assert "Inställningar för träningspuls → Stående" in hw["where"]
    assert zs.watch_card(lthr, "garmin")["method"] == "lthr"
    # a watch whose zones go by max HR: the max HR, never a threshold it cannot take
    po = zs.watch_card(lthr, "polar")
    assert po["method"] == "max" and "maxpuls 199" in po["decision"] and po["where"] == t("assessments.watch.where.any")
    # no threshold: max HR, with the resting HR where the watch has heart-rate reserve
    only_max = _setup(None, 199, 52, estimated=True)
    assert zs.watch_card(only_max, "huawei")["decision"] == "Välj HRR-procent och skriv in maxpuls 199 och vilopuls 52."
    assert zs.watch_card(_setup(None, 199, None, True), "huawei")["decision"] == \
        "Välj Maxpulsprocent och skriv in maxpuls 199."
    assert "räknade ur maxpulsen" in zs.watch_card(only_max, "garmin")["speed_note"]
    assert "räknade ur maxpulsen" not in zs.watch_card(lthr, "garmin")["speed_note"]
    # neither a threshold nor a max HR: nothing to type in
    assert zs.watch_card(_setup(None, None, None, True), "coros")["method"] is None
    assert zs.watch_brand("nokia") == "garmin" and [k for k, _ in zs.watch_brand_choices()][:2] == ["garmin", "huawei"]


# ---------------------------------------------------------------------- the results view
def test_zones_test_puts_the_watch_first_and_remembers_the_brand(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    a, s = _max_session(ctx, resting_hr=50)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.resize(1400, 900)
    v.show()
    v.load_session(s.id)
    assert v._overview.indexOf(v.watch_card) == 0                 # above the tiles and the zone tables
    text = _texts(v.watch_card)
    assert "Välj zoner efter procent av pulsreserven (HRR) och skriv in maxpuls 199 och vilopuls 50." in text
    assert "Varför visar klockan andra zoner?" not in text        # the theory waits behind "Läs mer"
    v.watch_brand.setCurrentIndex(v.watch_brand.findData("huawei"))
    assert "Välj HRR-procent" in _texts(v.watch_card)
    assert ctx.store.athlete(a.id).watch_brand == "huawei"
    ctx.store.save_athlete(ctx.store.athlete(a.id))            # the athlete form does not forget it
    assert ctx.store.athlete(a.id).watch_brand == "huawei"
    v._toggle_watch_more()
    assert "Varför visar klockan andra zoner?" in _texts(v.watch_card)
    # zones from max HR only say so: the zone "Tröskel" is not a measured threshold
    assert t("assessments.zones.max_only") in v.zone_note.text()
    assert t("assessments.zones.max_only") in v.html()
    ctx.shutdown()


def test_max_hr_shows_both_values_and_the_coach_can_change_it(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    a, s = _max_session(ctx)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    assert v.max_hr == 199 and v.t_max.value == "199" and "högsta enskilda 200" in v.t_max.sub
    assert v.max_edit.value() == 199
    top = v.zone_list[-1]["hr_high"]
    assert top == 199
    v.max_edit.setValue(195)                                       # the coach's own value
    assert v.zone_list[-1]["hr_high"] == 195 and "195" in v.zone_note.text()
    v.flush()
    assert ctx.store.session(s.id).result["max_hr_set"] == 195
    assert ctx.store.session(s.id).result["zones"][-1]["hr_high"] == 195
    assert ctx.store.athlete(a.id).max_hr == 195               # the athlete's max HR follows
    v.load_session(s.id)                                           # and it comes back
    assert v._max_set == 195 and v.max_edit.value() == 195 and v.zone_list[-1]["hr_high"] == 195
    assert "zonerna använder 195" in v.t_max.sub
    ctx.shutdown()


def test_results_show_the_pace_first(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    a, s = _max_session(ctx)
    v = ResultsView(ctx)
    qtbot.addWidget(v)
    v.load_session(s.id)
    hh = v.table.horizontalHeader()
    assert hh.visualIndex(2) < hh.visualIndex(1)                   # Tempo before km/h
    z = {"speed_low": 11.0, "speed_high": 12.0}
    assert ResultsView._speed_range(z) == "5:27–5:00 min/km (11,0–12,0 km/h)"
    assert ResultsView._speed_range(dict(z, speed_high_extrapolated=True)) == "5:27–≈5:00 min/km (11,0–≈12,0 km/h)"
    ctx.shutdown()
