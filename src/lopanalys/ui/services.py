"""The UI's single doorway to the other modules (camera, pose, devices, assessments, storage, report).

Every call into another package goes through this file, and results are normalised into the plain
dicts/dataclasses the views use, so the views never depend on the exact shape of another module's
return types. The other packages are imported normally (a missing one is a broken installation and
must fail loudly); only the heavy ones (pose.live/pose.offline, report.builder, report.pdf) are
imported inside the function that needs them, to keep them out of the start-up time.
"""
from __future__ import annotations

import atexit
import inspect
import logging
import threading
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from lopanalys.assessments import catalog
from lopanalys.assessments import hr_zones as hr_zones_mod
from lopanalys.assessments import lactate as lactate_mod
from lopanalys.assessments import library, library_power
from lopanalys.assessments import max_hr as max_hr_mod
from lopanalys.assessments import power as power_mod
from lopanalys.assessments import ramp as ramp_mod
from lopanalys.assessments import vo2max as vo2max_mod
from lopanalys.assessments import zone_systems
from lopanalys.assessments.protocol import Protocol
from lopanalys.assessments.runner import TestRunner
from lopanalys.camera import discovery
from lopanalys.camera.base import SourceInfo, SourceKind
from lopanalys.camera.lease import ExclusiveSource
from lopanalys.camera.recorder import Recorder
from lopanalys.core.bus import Bus
from lopanalys.core.i18n import t
from lopanalys.core.models import MAX_KINDS, Athlete, Session, StepRecord, TestKind, TimeSample
from lopanalys.core.sport import DEFAULT_SPORT, Sport, profile, selectable_sports, sport_of
from lopanalys.devices.base import DeviceInfo
from lopanalys.devices.manager import DeviceManager
from lopanalys.pose import models as pose_models
from lopanalys.storage.db import Database
from lopanalys.ui import demo

log = logging.getLogger(__name__)


# =============================================================================================
# Storage
# =============================================================================================

class Store:
    """What the UI needs from storage. Wraps ``storage.db.Database``."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # athletes
    def athletes(self, query: str = "") -> list[Athlete]:
        rows = self.db.list_athletes()
        q = query.strip().lower()
        if q:
            rows = [a for a in rows if q in a.name.lower()]
        return sorted(rows, key=lambda a: (a.last_name.lower(), a.first_name.lower()))

    def athlete(self, athlete_id: int) -> Athlete | None:
        return self.db.get_athlete(athlete_id)

    def save_athlete(self, a: Athlete) -> Athlete:
        return self.db.save_athlete(a)

    def set_watch_brand(self, athlete_id: int, brand: str) -> None:
        """Remember the athlete's watch brand (the box "Fyll i klockan" in the result)."""
        self.db.set_watch_brand(athlete_id, brand)

    def delete_athlete(self, athlete_id: int) -> None:
        self.db.delete_athlete(athlete_id)

    def export_athlete(self, athlete_id: int, dest_zip: Path) -> Path:
        """Everything about one person in a zip (storage.privacy.export_athlete_zip)."""
        from lopanalys.storage import privacy
        return privacy.export_athlete_zip(self.db, athlete_id, dest_zip)

    def retention_due(self) -> dict[int, dict[str, Any]]:
        """The athletes to review, by id: not tested for 24 months, or tests kept without consent to keep."""
        return {d["athlete"].id: d for d in self.db.retention_due()}

    def erase_athlete(self, athlete_id: int, keep: list[Path] | None = None) -> dict[str, Any]:
        """Erase a person completely; returns storage's summary (see ``erasure_text``)."""
        return self.db.erase_athlete(athlete_id, keep or [])

    # sessions
    def save_session(self, s: Session) -> Session:
        return self.db.save_session(s)

    def history(self, athlete_id: int, kind: str | None = None, sport: Any = None) -> list[Session]:
        """The athlete's tests, newest first, optionally of one kind (a TestKind value) and one sport."""
        return self.db.history(athlete_id, kind, sport)

    def previous_session(self, s: Session) -> Session | None:
        """The athlete's test of the same kind and sport just before ``s``."""
        return self.db.previous_session(s)

    def backup(self, dest: Path, password: str | None = None) -> bool:
        """Database-consistent backup through storage, encrypted with ``password``. Always True."""
        self.db.backup(dest, password)
        return True

    def restore(self, backup: Path, password: str | None = None) -> None:
        """Replace the database with a backup, in place: the database is closed, replaced and opened
        again (which migrates it and erases again who was erased since the backup was made)."""
        path, kroot = self.db.path, self.db.keyframes_root
        self.db.close()
        try:
            Database.restore_backup(backup, path, kroot, password)
        finally:
            self.db = Database(path, keyframes_root=kroot)

    def session(self, session_id: int) -> Session | None:
        return self.db.get_session(session_id)

    def save_records(self, session_id: int, records: list[StepRecord]) -> None:
        self.db.save_step_records(session_id, records)

    def records(self, session_id: int) -> list[StepRecord]:
        return self.db.step_records(session_id)

    def save_samples(self, session_id: int, samples: list[TimeSample]) -> None:
        self.db.save_time_samples(session_id, samples)

    def samples(self, session_id: int) -> list[TimeSample]:
        return self.db.time_samples(session_id)

    def save_gait(self, session_id: int, data: dict[str, Any]) -> None:
        self.db.save_gait_analysis(session_id, data)

    def gait(self, session_id: int) -> dict[str, Any] | None:
        return self.db.gait_analysis(session_id)

    # bike fit and SkiErg technique (pose.technique)
    def save_technique(self, session_id: int, data: dict[str, Any]) -> None:
        self.db.add_technique_analysis(session_id, data)

    def technique(self, session_id: int) -> dict[str, Any] | None:
        """``{"kind", "views": [analysis, ...]}`` of a bike fit or a SkiErg test, or None."""
        return self.db.technique_analysis(session_id)

    def previous_technique(self, s: Session, kind: str, view: str) -> tuple[dict[str, Any], Any] | None:
        """(analysis, date) of the athlete's latest earlier test of the same kind and camera view."""
        for old in self.history(s.athlete_id):
            if old.id is None or old.id == s.id or (s.started and old.started and old.started >= s.started):
                continue
            tech = self.technique(old.id)
            if tech and tech["kind"] == kind:
                for v in tech["views"]:
                    if v.get("view") == view and (v.get("quality") or {}).get("level") != "poor":
                        return v, old.started
        return None

    # settings (JSON-able values)
    def setting(self, key: str, default: Any = None) -> Any:
        v = self.db.get_setting(key, default)
        return default if v is None else v

    def set_setting(self, key: str, value: Any) -> None:
        self.db.set_setting(key, value)

    def close(self) -> None:
        self.db.close()


def erasure_text(name: str, summary: dict[str, Any]) -> str:
    """What the coach reads after an erasure: what went, and the list of what the program cannot reach."""
    lines = [t("ui.erase.done", name=name, sessions=summary.get("sessions", 0), files=summary.get("files", 0)), "",
             t("ui.erase.left_title")]
    for x in summary.get("left", []):
        lines.append("•  " + t(f"ui.erase.left.{x['kind']}", path=x["path"], until=x.get("until") or ""))
    lines.append("•  " + t("ui.erase.left_always"))
    lines += ["", t("ui.erase.restore_note")]
    return "\n".join(lines)


def open_store(path: Path | None = None) -> Store:
    """The app's database (``path`` None: the one in the data folder)."""
    return Store(Database(path))


# =============================================================================================
# Protocols
# =============================================================================================

# Protocol.builder -> the library function that made it.
_BUILDERS: dict[str, Callable[..., Protocol]] = {
    f.__name__: f for f in (library.lactate_step, library.lactate_to_max, library.max_hr_ramp, library.gait_check,
                          library.hr_zones)}
_BUILDERS.update(library_power.BUILDERS)      # cycling and SkiErg, in watt


def sports() -> list[tuple[str, str]]:
    """(value, label) of the sports that can be chosen for a test: running, cycling and SkiErg."""
    return [(s.value, t(f"ui.sport.{s.value}")) for s in selectable_sports()]


def default_protocols(sport: Any = None) -> list[Protocol]:
    """The protocol library of a sport (assessments.library); running when no sport is given."""
    return library.default_protocols(sport=sport)


def estimated_threshold_kmh(store: Any, athlete: Athlete | None) -> float | None:
    """The athlete's threshold speed from the latest earlier test that says something about it: LT2 of
    a lactate test, or the speed at 87 % of max HR from a zones test without lactate. For the start
    speed of the next test only."""
    if athlete is None or athlete.id is None:
        return None
    try:
        for s in store.history(athlete.id, None, DEFAULT_SPORT):     # running tests only: the speed is km/h
            res = s.result or {}
            v = (res.get("lt2") or {}).get("speed_kmh") or (res.get("hr_speed") or {}).get("est_threshold_kmh")
            if v and 6.0 <= float(v) <= 25.0:
                return float(v)
    except Exception:
        log.exception("threshold estimate from earlier tests failed")
    return None


def hr_zones_protocols(store: Any = None, athlete: Athlete | None = None) -> list[Protocol]:
    """The library of the zones test without lactate: from the athlete's earlier test first when
    there is one, then one protocol per level."""
    out = [p for p in library.default_protocols() if p.builder == "hr_zones"]
    est = estimated_threshold_kmh(store, athlete) if store is not None else None
    if est:
        out.insert(0, library.hr_zones_for_level(estimated_lt2_kmh=est))
    return out


def rebuild_protocol(base: Protocol, **changes: Any) -> Protocol | None:
    """Re-run the library builder that made ``base`` with some parameters changed.

    Returns None when the protocol has no builder (then the caller builds it another way).
    """
    fn = _BUILDERS.get(base.builder)
    if fn is None:
        return None
    # params may hold more than the builder takes (lactate_step_for_level adds level/estimated_lt2_kmh)
    accepted = inspect.signature(fn).parameters
    p = fn(**{k: v for k, v in {**base.params, **changes}.items() if k in accepted})
    p.name = base.name
    return p


def lactate_step(start_kmh: float, step_kmh: float, stages: int, stage_s: float = 240,
                 incline_pct: float = 1.0, pause_s: float = 30) -> Protocol:
    """Incremental lactate step protocol with the coach's parameters."""
    return library.lactate_step(start_kmh, step_kmh, stages, stage_s=stage_s, incline_pct=incline_pct,
                                pause_s=pause_s)


def max_hr_ramp(start_kmh: float = 10.0, step_kmh: float = 0.5, stage_s: float = 60,
                incline_pct: float = 1.0) -> Protocol:
    """Ramp to exhaustion for max heart rate: the speed rises ``step_kmh`` every ``stage_s`` seconds."""
    return library.max_hr_ramp(start_kmh, mode="speed", speed_step_kmh=step_kmh, step_s=stage_s,
                               start_incline_pct=incline_pct)


def gait_check(speeds_kmh: list[float], stage_s: float = 120) -> Protocol:
    """Running technique protocol: a few speeds with gait recording."""
    return library.gait_check(speeds_kmh, stage_s=stage_s)


# =============================================================================================
# Devices and the test runner
# =============================================================================================

def make_device_manager(bus: Bus, time_scale: float = 1.0,
                        remember: Callable[[str, DeviceInfo], None] | None = None,
                        recall: Callable[[str], DeviceInfo | None] | None = None) -> DeviceManager:
    """The device manager (remembers the last devices; ``time_scale`` speeds up the simulators)."""
    return DeviceManager(bus, remember=remember, recall=recall, sim_time_scale=time_scale)


def make_runner(protocol: Protocol, treadmill: Any, heart_rate: Any, bus: Bus,
                time_scale: float = 1.0, limits: library.TreadmillLimits | None = None) -> TestRunner:
    """The runner that drives one test. ``limits``: the coach's setting for the belt (belt_limits);
    it belongs to the treadmill and is not used for a test in watt."""
    return TestRunner(protocol, treadmill, heart_rate, bus, time_scale=time_scale, limits=limits)


def belt_limits(setting: Callable[[str], Any]) -> library.TreadmillLimits | None:
    """The belt's range from the settings ``devices.max_speed_kmh`` / ``devices.max_incline_pct``
    (0 = not set), or None when neither is set."""
    def num(key: str) -> float:
        try:
            return max(0.0, float(setting(key) or 0))
        except (TypeError, ValueError):
            return 0.0
    v, inc = num("devices.max_speed_kmh"), num("devices.max_incline_pct")
    if not v and not inc:
        return None
    return library.TreadmillLimits(min_speed_kmh=0.0, max_speed_kmh=v, max_incline_pct=inc, min_incline_pct=0.0)


def treadmill_limits(treadmill: Any, setting: Callable[[str], Any]) -> library.TreadmillLimits:
    """What the test can ask of the belt: the connected treadmill's range narrowed by the setting."""
    base = library.TreadmillLimits.of(treadmill) if treadmill is not None else library.TreadmillLimits()
    own = belt_limits(setting)
    return ramp_mod.narrower(base, own) if own else base


# =============================================================================================
# Analysis: lactate, zones, max HR
# =============================================================================================

@dataclass
class Threshold:
    speed_kmh: float | None
    hr: float | None
    lactate_mmol: float | None


@dataclass
class LactateView:
    """Normalised lactate result for the UI. ``raw`` is the real LactateResult (for zones/report)."""
    method: str
    points: list[tuple[float, float, float | None]]   # (speed, lactate, hr)
    curve: list[tuple[float, float]] = field(default_factory=list)
    lt1: Threshold | None = None
    lt2: Threshold | None = None
    raw: Any = None
    warnings: list[str] = field(default_factory=list)
    lt1_method: str | None = None
    lt2_method: str | None = None


def lactate_methods() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """((id, label) for LT1 methods, (id, label) for LT2 methods)."""
    def labelled(ids: list[str]) -> list[tuple[str, str]]:
        return [(k, t(f"assessments.lactate.method.{k}")) for k in ids]
    return labelled(lactate_mod.LT1_METHODS), labelled(lactate_mod.LT2_METHODS)


def analyze_lactate(records: list[StepRecord], lt1_method: str | None = None, lt2_method: str | None = None,
                    max_hr: int | None = None, sport: Any = None) -> LactateView | None:
    """LT1/LT2 from the stage records with the chosen methods (None without lactate values).

    ``max_hr`` and ``sport`` belong together: the max HR of the test's sport (default running)."""
    recs = [r for r in records if r.lactate_mmol is not None]
    pts = [(r.speed_kmh, r.lactate_mmol, r.hr_end) for r in recs]
    if not pts:
        return None
    try:
        res = lactate_mod.analyze(recs, max_hr, lt1_method=lt1_method or None, lt2_method=lt2_method or None,
                                  sport=sport)
    except Exception:
        log.exception("lactate analysis failed")
        return LactateView("", pts)

    def thr(v: lactate_mod.Threshold | None) -> Threshold | None:
        return None if v is None else Threshold(v.speed_kmh, v.hr, v.lactate_mmol)

    return LactateView(res.lt2_method or "", pts, [(float(a), float(b)) for a, b in res.curve], thr(res.lt1),
                       thr(res.lt2), res, [w.text for w in res.warnings], res.lt1_method, res.lt2_method)


def sport_thresholds(store: Any, athlete: Athlete, sport: Any = None) -> LactateView | None:
    """The athlete's thresholds in one sport: from the latest lactate test in that sport that gave
    both LT1 and LT2, with the methods the coach saved (None: no such test in the sport).

    Thresholds are not stored per athlete, they are read from the tests, so a corrected lactate
    value or another method shows everywhere. Only the max HR is stored per athlete and sport."""
    if athlete.id is None:
        return None
    sp = sport_of(sport)
    for s in store.history(athlete.id, TestKind.LACTATE_STEP.value, sp):
        if s.id is None:
            continue
        res = s.result or {}
        warm = {i for i, st in enumerate((s.protocol or {}).get("stages", [])) if st.get("is_warmup")}
        records = [r for r in store.records(s.id) if r.index not in warm]
        lv = analyze_lactate(records, res.get("lt1_method"), res.get("lt2_method"), athlete.max_hr_for(sp), sp)
        if lv is not None and lv.lt1 and lv.lt2:
            return lv
    return None


def zone_system_choices() -> list[tuple[str, str]]:
    """(key, label) of every zone system, the default first."""
    keys = [zone_systems.DEFAULT_SYSTEM] + [k for k in zone_systems.SYSTEMS if k != zone_systems.DEFAULT_SYSTEM]
    return [(k, zone_systems.SYSTEMS[k].name) for k in keys]


def zone_system_info(system: str | None) -> dict[str, str]:
    """{"key", "name", "note"} of a zone system (the default one for an unknown key)."""
    sys = zone_systems.SYSTEMS.get(system or "") or zone_systems.SYSTEMS[zone_systems.DEFAULT_SYSTEM]
    return {"key": sys.key, "name": sys.name, "note": sys.note}


def _anchors(lactate: LactateView | None, max_hr: int | None, resting_hr: int | None,
             sport: Any = None) -> zone_systems.Anchors:
    raw = lactate.raw if lactate is not None else None
    return zone_systems.Anchors.from_lactate(raw, max_hr, resting_hr, sport)


def _zone_dict(z: zone_systems.Zone) -> dict[str, Any]:
    return dict(name=z.name if z.name.startswith(z.code) else f"{z.code}  {z.name}", code=z.code,
                hr_low=z.hr_low, hr_high=z.hr_high, speed_low=z.speed_low_kmh, speed_high=z.speed_high_kmh,
                description=z.purpose, estimated=z.estimated, speed_basis=z.speed_basis,
                speed_low_extrapolated=z.speed_low_extrapolated,
                speed_high_extrapolated=z.speed_high_extrapolated, raw=z)


def own_peak_counts(session: Session | None) -> bool:
    """False for a test that was stopped (or cut off) before its end and is not a max test: its highest
    heart rate is not a max HR, and neither a max HR nor zones are computed from it. Before 2026-11-15 a
    technique test stopped half-way got "max HR 89" and zones from it."""
    if session is None:
        return True
    if session.status not in ("stopped", "interrupted"):
        return True
    return session.kind in MAX_KINDS or (session.result or {}).get("exhaustion_t_s") is not None


def zones(lactate: LactateView | None, max_hr: int | None, resting_hr: int | None,
          system: str = zone_systems.DEFAULT_SYSTEM, sport: Any = None) -> list[dict[str, Any]]:
    """Training zones in one system, as dicts: name, code, hr_low, hr_high, speed_low, speed_high,
    description, estimated, raw (the Zone).

    From the lactate thresholds when there are any, else estimated from max HR ("estimated" is then
    True); [] when neither can be used. ``sport``: the sport ``max_hr`` belongs to (default running);
    with a lactate test the zones are in the test's sport.
    """
    try:
        return [_zone_dict(z) for z in zone_systems.convert(_anchors(lactate, max_hr, resting_hr, sport), system)]
    except Exception:
        log.exception("zones failed")
        return []


def zone_comparison(lactate: LactateView | None, max_hr: int | None, resting_hr: int | None,
                    sport: Any = None) -> list[dict[str, Any]]:
    """The same thresholds in a 3-zone system, a 5-zone system and on a Garmin watch:
    [{"key", "name", "zones": [zone dicts]}]."""
    try:
        found = zone_systems.compare(_anchors(lactate, max_hr, resting_hr, sport))
    except Exception:
        log.exception("zone comparison failed")
        return []
    return [{"key": k, "name": zone_systems.SYSTEMS[k].name, "zones": [_zone_dict(z) for z in zs]}
            for k, zs in found.items()]


def analyze_hr_speed(records: list[StepRecord], samples: list[TimeSample], protocol: dict[str, Any] | None,
                     end_t_s: float | None = None) -> Any:
    """The relation heart rate -> speed of a zones test without lactate (hr_zones.HrSpeedResult), or None."""
    try:
        return hr_zones_mod.analyze(records, samples, end_t_s=end_t_s, steps=hr_zones_mod.step_indexes(protocol))
    except Exception:
        log.exception("hr-speed analysis failed")
        return None


def with_zone_speeds(zone_list: list[dict[str, Any]], relation: Any) -> list[dict[str, Any]]:
    """The zone dicts of zones() with speeds from the heart rate -> speed relation (marked as such)."""
    if relation is None or not zone_list:
        return zone_list
    try:
        return [_zone_dict(z) for z in hr_zones_mod.zone_speeds([z["raw"] for z in zone_list], relation)]
    except Exception:
        log.exception("zone speeds failed")
        return zone_list


def hr_speed_dict(relation: Any, max_hr: int | None) -> dict[str, Any] | None:
    """The relation as saved in Session.result["hr_speed"] and handed to the report."""
    if relation is None:
        return None
    return dict(relation.to_dict(), est_threshold_kmh=relation.est_threshold_kmh(max_hr),
                quality_text=hr_zones_mod.quality_text(relation))


def hr_speed_quality(relation: Any) -> str:
    """One line on how well the steps follow the line (hr_zones.quality_text)."""
    return hr_zones_mod.quality_text(relation) if relation is not None else ""


def speed_from_hr_text(long: bool = False) -> str:
    """The label for zone speeds that come from heart rate: "Fart ur puls, inte ur laktat"."""
    return t("assessments.hrzones.speed_from_hr_long" if long else "assessments.hrzones.speed_from_hr")


def extrapolated_text(relation: Any) -> str:
    """What the ≈ before a speed means, with the measured range; "" without a relation."""
    r = relation.to_dict() if hasattr(relation, "to_dict") else relation
    if not r or not r.get("ok"):
        return ""
    return t("assessments.hrzones.extrapolated", lo=library.fmt_kmh(r["speed_min"]), hi=library.fmt_kmh(r["speed_max"]),
             km=library.fmt_pct(r.get("extrapolate_kmh") or hr_zones_mod.EXTRAPOLATE_KMH))


def easy_zone(zone_list: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The zone for easy running: the second of five or more, the first in a 3- or 4-zone system."""
    if not zone_list:
        return None
    return zone_list[1] if len(zone_list) >= 5 else zone_list[0]


def garmin_setup(lactate: LactateView | None, max_hr: int | None, resting_hr: int | None,
                 max_measured: bool = True, sport: Any = None) -> dict[str, Any] | None:
    """What the athlete enters in Garmin Connect (zone_systems.GarminSetup.to_dict()), or None.

    ``max_measured`` False: ``max_hr`` is just the highest heart rate of a submaximal test, and is
    not given as the max HR for the watch.
    """
    try:
        g = zone_systems.garmin_setup(_anchors(lactate, max_hr, resting_hr, sport), max_measured)
    except Exception:
        log.exception("garmin setup failed")
        return None
    return g.to_dict() if g is not None else None


def estimate_vo2max(records: list[StepRecord], max_hr: int | None, resting_hr: int | None,
                    to_exhaustion: bool = False, protocol: dict[str, Any] | None = None,
                    likely_max: bool | None = None) -> dict[str, Any] | None:
    """Estimated VO2max with method and interval (vo2max.VO2maxResult.to_dict()), or None.

    ``records``: the work stages. ``protocol``: Session.protocol, for the planned stage lengths.
    The estimate is built on the running equation (speed and incline), so a test in another sport
    gets none.
    """
    if sport_of((protocol or {}).get("sport")) is not DEFAULT_SPORT:
        return None
    try:
        durations = [float(st.get("duration_s") or 0) for st in (protocol or {}).get("stages", [])]
        r = vo2max_mod.estimate(records, max_hr, resting_hr, to_exhaustion=to_exhaustion,
                                stage_durations=durations or None, likely_max=likely_max)
    except Exception:
        log.exception("vo2max estimate failed")
        return None
    return r.to_dict() if r is not None else None


def garmin_card(setup: dict[str, Any] | None) -> dict[str, Any] | None:
    """Display texts for garmin_setup() (zone_systems.garmin_card)."""
    return zone_systems.garmin_card(setup)


def watch_card(setup: dict[str, Any] | None, brand: str | None) -> dict[str, Any] | None:
    """What to type into the athlete's watch of ``brand`` (zone_systems.watch_card)."""
    return zone_systems.watch_card(setup, brand)


def watch_brand_choices() -> list[tuple[str, str]]:
    """(key, name) of the watch brands for the box "Fyll i klockan"."""
    return zone_systems.watch_brand_choices()


def garmin_card_title() -> str:
    """The heading of the watch box ("Fyll i klockan")."""
    return t("assessments.garmin.title")


def vo2max_card(result: dict[str, Any] | None, athlete: Athlete | None = None) -> dict[str, Any] | None:
    """Display texts for estimate_vo2max() (vo2max.card), with the athlete's VO2max from the watch."""
    return vo2max_mod.card(result, getattr(athlete, "watch_vo2max", None), getattr(athlete, "watch_vo2max_date", None))


def zones_estimated_text() -> str:
    """The label for zones that were estimated from max HR."""
    return t("assessments.zones.estimated")


def max_only_text() -> str:
    """One sentence for zones from max HR only: a zone called "Tröskel" is not a measured threshold."""
    return t("assessments.zones.max_only")


def tests_info(sport: Any = None) -> list[dict[str, Any]]:
    """The tests of a sport described for the athlete (assessments.catalog); running by default."""
    return catalog.tests_info(sport)


@dataclass
class MaxHrView:
    peak: int | None         # the highest 5-s mean: the max HR that is used
    raw: Any = None          # assessments.max_hr.MaxHrResult
    likely_max: bool | None = None
    texts: list[str] = field(default_factory=list)
    single: int | None = None    # the highest single reading, shown beside the peak


# ---------------------------------------------------------------- the max test in the guide
MAX_TEST_DUE_DAYS = 730      # max HR falls with the years; suggest a new test after two


def previous_vo2max(store: "Store", athlete_id: int | None) -> float | None:
    """The VO2max estimated in the athlete's latest test that has one (ml/kg/min), else None."""
    if athlete_id is None:
        return None
    try:
        for s in store.history(athlete_id, None, DEFAULT_SPORT):
            v = (((s.result or {}).get("vo2max") or {}).get("best") or {}).get("value")
            if v:
                return float(v)
    except Exception:
        log.exception("previous vo2max failed")
    return None


def previous_max_ramp(store: "Store", athlete_id: int | None) -> dict[str, Any] | None:
    """The athlete's latest max test: {"minutes" (the ramp until the stop), "max_incline_pct",
    "start_kmh", "mode"} or None."""
    if athlete_id is None:
        return None
    try:
        for s in store.history(athlete_id, TestKind.MAX_HR.value, DEFAULT_SPORT):
            if s.id is None:
                continue
            stages = (s.protocol or {}).get("stages", [])
            warm = {i for i, st in enumerate(stages) if st.get("is_warmup")}
            recs = [r for r in store.records(s.id) if r.index not in warm]
            if not recs:
                continue
            return {"minutes": sum(r.duration_s for r in recs) / 60.0,
                    "max_incline_pct": max(r.incline_pct for r in recs),
                    "start_kmh": recs[0].speed_kmh,
                    "mode": ((s.protocol or {}).get("params") or {}).get("mode", "")}
    except Exception:
        log.exception("previous max test failed")
    return None


def ramp_path(protocol: Protocol, limits: library.TreadmillLimits) -> str:
    """What the ramp does at the belt's limits, in words: "Trappan ökar 0,5 km/h varje minut till bandets
    högsta fart (18,0 km/h) och sedan 1 % varje minut till högsta lutningen (12 %)." (ramp.next_step).
    Before 2026-11-15 the line gave the minutes to each limit, which was hard to read."""
    fk, fp = library.fmt_kmh, library.fmt_pct
    step_s = protocol.stages[-1].duration_s if protocol.stages else 60.0   # the steps the ramp adds are this long
    per = t("ui.wiz.per_minute") if abs(step_s - 60) < 1e-6 else t("ui.wiz.per_s", s=int(round(step_s)))
    p = protocol
    if p.increment_speed_kmh > 0:
        first = t("ui.wiz.ramp_up_speed", step=fk(p.increment_speed_kmh), per=per, kmh=fk(limits.max_speed_kmh))
        then = (t("ui.wiz.ramp_then_incline", step=fp(p.overflow_incline_pct), per=per, pct=fp(limits.max_incline_pct))
                if p.overflow_incline_pct > 0 and limits.max_incline_pct > 0 else "")
    elif p.increment_incline_pct > 0:
        first = t("ui.wiz.ramp_up_incline", step=fp(p.increment_incline_pct), per=per, pct=fp(limits.max_incline_pct))
        then = (t("ui.wiz.ramp_then_speed", step=fk(p.overflow_speed_kmh), per=per, kmh=fk(limits.max_speed_kmh))
                if p.overflow_speed_kmh > 0 else "")
    else:
        return ""
    return t("ui.wiz.ramp_path", first=first, then=(" " + then) if then else "")


def ramp_advice(protocol: Protocol, limits: library.TreadmillLimits, target_vo2: float | None = None,
                build: Callable[[float], Protocol] | None = None,
                previous: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The guide's line under an open-ended protocol: what the ramp does at the belt's limits
    (ramp_path), a warning when the ramp will probably take over 12 or under 6 min, and a start speed.

    The start speed comes from ``previous`` (previous_max_ramp) when there is one: a ramp that took more
    than 12 min or reached the belt's highest incline gives +1 km/h (and the speed ramp as the other
    choice), one under 6 min -1 km/h. Without one, from ``target_vo2`` and ``build`` (a function
    start speed -> protocol). None: not a ramp."""
    pl = ramp_mod.plan(protocol, limits, target_vo2)
    if pl is None:
        return None
    fk, fp = library.fmt_kmh, library.fmt_pct
    text = ramp_path(protocol, limits)
    if pl.expected_min is not None:
        text += " " + t("ui.wiz.ramp_expected", min=fp(round(pl.expected_min)))
    warning = {"long": t("ui.wiz.ramp_long"), "short": t("ui.wiz.ramp_short")}.get(pl.warning, "")
    suggest = None
    hint = ""
    if previous and build is not None:
        mins, top = float(previous["minutes"]), float(previous["max_incline_pct"])
        at_top = limits.max_incline_pct > 0 and top >= limits.max_incline_pct - 1e-6
        incline = (protocol.params or {}).get("mode", "incline") == "incline"
        if mins > ramp_mod.WARN_LONG_MIN or (at_top and incline):
            suggest = limits.speed(float(previous["start_kmh"]) + 1.0)
            key = "ui.wiz.prev_long_incline" if incline else "ui.wiz.prev_long"
            hint = t(key, min=fp(round(mins)), pct=fp(top))
        elif mins < ramp_mod.WARN_SHORT_MIN:
            suggest = limits.speed(float(previous["start_kmh"]) - 1.0)
            hint = t("ui.wiz.prev_short", min=fp(round(mins)))
        if hint:
            warning = ""            # the previous test says more than the estimate
    elif target_vo2 and build is not None:
        try:
            suggest = ramp_mod.suggest_speed(build, target_vo2, limits)
        except Exception:
            log.exception("start speed suggestion failed")
    return {"text": text.strip(), "warning": hint or warning, "suggest_kmh": suggest, "plan": pl}


def max_test_due(store: "Store", athlete: Athlete | None, today: Any = None) -> str:
    """A suggestion of a new max test when the athlete's latest one is older than two years ("" else)."""
    from datetime import datetime, timezone
    if athlete is None or athlete.id is None:
        return ""
    try:
        tests = store.history(athlete.id, TestKind.MAX_HR.value, DEFAULT_SPORT)
    except Exception:
        log.exception("max test history failed")
        return ""
    if not tests or not tests[0].started:
        return ""
    now = today or datetime.now(timezone.utc)
    days = (now - tests[0].started).days
    if days <= MAX_TEST_DUE_DAYS:
        return ""
    return t("ui.athletes.max_test_due", years=days // 365, date=tests[0].started.astimezone().strftime("%Y-%m-%d"))


def zone_basis(used_max: float | None, athlete: Athlete | None, view: "MaxHrView | None",
               session: Session | None) -> str:
    """Which max HR the zones use and where it comes from (assessments.max_hr.zone_basis)."""
    peak = view.peak if view else None
    likely = view.likely_max if view else None
    if session is not None and session.kind != TestKind.MAX_HR and not (session.result or {}).get("exhaustion_t_s"):
        peak = likely = None          # a submaximal test's top is not a candidate
    sport = sport_of(session.sport) if session is not None else DEFAULT_SPORT
    run = sport is DEFAULT_SPORT          # where the saved value came from is only kept for running
    saved = athlete.max_hr_for(sport) if athlete is not None else None
    return max_hr_mod.zone_basis(used_max, saved, getattr(athlete, "max_hr_source", "") if run else "",
                                 getattr(athlete, "max_hr_date", None) if run else None, peak, likely,
                                 session.started.date() if session and session.started else None)


def max_hr_update(stored: int | None, peak: int | None) -> str:
    """What a max test's peak does to the athlete's saved max HR (never lowered by itself).

    "set": no saved value, "raise": the peak is higher, "ask": the peak is lower (the coach is asked,
    keeping the saved value is the default), "same": equal or no peak.
    """
    if not peak:
        return "same"
    if not stored:
        return "set"
    if peak > stored:
        return "raise"
    return "ask" if peak < stored else "same"


def analyze_max_hr(samples: list[TimeSample], records: list[StepRecord] | None = None,
                   end_t_s: float | None = None, age: float | None = None, sport: Any = None) -> MaxHrView:
    """Peak heart rate of the test with the true-max criteria (an empty view if the analysis fails).
    ``sport``: the age criterion is only judged in running."""
    try:
        rpe = next((r.rpe for r in reversed(records or []) if r.rpe), None)
        r = max_hr_mod.analyze(samples, end_t_s=end_t_s, records=records, age=age, final_rpe=rpe, sport=sport)
    except Exception:
        log.exception("max hr analysis failed")
        return MaxHrView(None)
    texts = [c.text for c in r.criteria if c.text] + [w.get("text", "") for w in r.warnings]
    return MaxHrView(r.peak_hr, r, r.likely_max, texts, r.peak_single_hr)


def analyze_power(session: Session | None, samples: list[TimeSample]) -> dict[str, Any] | None:
    """FTP, best one-minute power and W/kg of a test in watt (power.PowerResult.to_dict()), or None
    for a test in km/h. FTP comes from the 20 minute test and from a cycling ramp; a SkiErg ramp
    gets the best minute only. The weight is the one saved with the test."""
    if session is None or profile(session.sport).load_quantity != "power":
        return None
    res = session.result or {}
    kind = session.kind.value if isinstance(session.kind, TestKind) else str(session.kind)
    if sport_of(session.sport).value != "bike" and kind in ("ftp", "max_hr"):
        kind = "other"                      # FTP is a cycling measure
    try:
        r = power_mod.analyze(samples, kind, res.get("weight_kg"), measured=session.treadmill_mode != "manual")
    except Exception:
        log.exception("power analysis failed")
        return None
    return r.to_dict()


def w_per_kg(watt: float | None, weight_kg: float | None) -> float | None:
    """Watt per kilogram (None without both)."""
    return power_mod.w_per_kg(watt, weight_kg)


def no_own_test_text(sport: Any) -> str:
    """What the result says when a sport has neither a max HR nor a lactate test of its own: zones
    are not carried over from another sport (the size of the difference is personal)."""
    return t("ui.res.no_own_test", sport=t(f"ui.sport.{sport_of(sport).value}").lower())


# =============================================================================================
# Camera and pose
# =============================================================================================

def discover_sources(recent: list[str] | None = None) -> list[Any]:
    """Camera sources that can be opened now (camera.discovery); ``recent`` files/URLs are added."""
    try:
        return list(discovery.discover_sources(recent or []))
    except Exception:
        log.exception("camera discovery failed")
        return []


def probe_stream_address(text: str) -> list[Any]:
    """The streams that answer at what the coach typed (camera.network.probe_address; blocking)."""
    from lopanalys.camera import network
    return network.probe_address(text)


def find_network_cameras() -> list[Any]:
    """Cameras that announce themselves on the local network (mDNS) and answer (blocking)."""
    from lopanalys.camera import network
    return network.find_network_cameras()


def stream_source_info(url: str) -> SourceInfo:
    """SourceInfo for a network camera's address (the phone as a camera)."""
    from lopanalys.camera import network
    return network.stream_info(url)


def looks_like_url(text: str) -> bool:
    from lopanalys.camera import network
    return bool(network.URL_RE.match(text.strip()))


# A camera preview that the wizard hands over instead of closing: the check page (LiveController)
# takes it through make_source, so the GoPro does not stop and start again between the pages.
PARK_S = 300.0
_parked: dict[str, tuple[Any, threading.Timer]] = {}
_parked_mu = threading.Lock()


def _source_key(info: Any) -> str:
    """One key per camera, for the kept sources, the lease and the log: a network camera's address
    without its user name and password."""
    from lopanalys.camera.base import safe_url
    return f"{info.kind.value}:{safe_url(info.id)}"


def safe_source_id(info: Any) -> str:
    """A source's id for log lines: a network camera's address without its user name and password."""
    from lopanalys.camera.base import safe_url
    return safe_url(str(getattr(info, "id", "")))


def can_keep_source(info: Any) -> bool:
    """True for the cameras make_source can hand over (live cameras; not files or the demo runner)."""
    return info is not None and not demo.is_demo_source(info) and getattr(info, "kind", None) not in (None, SourceKind.FILE)


def park_source(info: Any, src: Any) -> None:
    """Keep an open (or opening) camera source for the next ``make_source(info)``; closed after PARK_S."""
    key = _source_key(info)
    timer = threading.Timer(PARK_S, _expire, (key, src))
    timer.daemon = True
    with _parked_mu:
        old = _parked.pop(key, None)
        _parked[key] = (src, timer)
    if old is not None and old[0] is not src:
        old[1].cancel()
        _close_later(old[0])
    timer.start()
    log.info("camera %s: preview source kept for the next page", key)


def _expire(key: str, src: Any) -> None:
    with _parked_mu:
        cur = _parked.get(key)
        if cur is None or cur[0] is not src:
            return
        del _parked[key]
    log.info("camera %s: the kept preview source was not used within %.0f s, closing it", key, PARK_S)
    _close(src)


def _take_parked(info: Any) -> Any | None:
    with _parked_mu:
        item = _parked.pop(_source_key(info), None)
    if item is None:
        return None
    src, timer = item
    timer.cancel()
    if getattr(src, "closed", False):
        log.info("camera %s: the kept preview source had closed, opening a new one", _source_key(info))
        return None
    log.info("camera %s: taking over the preview source (no new start)", _source_key(info))
    return src


def release_parked(keep: Any = None, keep2: Any = None) -> None:
    """Close kept preview sources (all, or all but the cameras ``keep`` and ``keep2``) on a helper thread."""
    keys = {_source_key(c) for c in (keep, keep2) if c is not None and getattr(c, "kind", None) is not None}
    with _parked_mu:
        gone = [(k, v) for k, v in _parked.items() if k not in keys]
        for k, _ in gone:
            del _parked[k]
    for k, (src, timer) in gone:
        timer.cancel()
        log.info("camera %s: closing the kept preview source", k)
        _close_later(src)


def _close(src: Any) -> None:
    try:
        src.close()
    except Exception:
        log.exception("closing a kept camera source failed")


def _close_later(src: Any) -> None:
    threading.Thread(target=_close, args=(src,), daemon=True, name="camera-close").start()


@atexit.register
def _close_parked_at_exit() -> None:
    with _parked_mu:
        items = list(_parked.values())
        _parked.clear()
    for src, timer in items:
        timer.cancel()
        _close(src)   # leaves the GoPro's webcam mode, so it can record afterwards


def make_source(info: Any) -> Any:
    """Open a FrameSource for a SourceInfo (demo sources are handled by ui.demo)."""
    if demo.is_demo_source(info):
        return demo.DemoSource(info)
    if info.kind == SourceKind.FILE:
        return discovery.make_source(info, pace="realtime", loop=True)
    kept = _take_parked(info)
    if kept is not None:
        return kept
    # A camera has one opener at a time: the next preview or the live view waits until the previous
    # source has closed completely (a late GoPro close would stop the new webcam stream).
    return ExclusiveSource(discovery.make_source(info), _source_key(info))


def make_live_analyzer(source: Any, bus: Bus, height_cm: float, view: Any, second: bool = False,
                       name: str = "", labels: bool = True, technique: str | None = None) -> Any:
    """pose.live.LiveAnalyzer, or the demo analyzer for the synthetic runner, cyclist or skier.

    ``second``: the second camera of a test with two views. It publishes on its own bus topics and
    analyses at most ``pose.multiview.SECOND_VIEW_MAX_FPS`` frames a second, so the main view keeps
    the processor; ``name`` is what its log lines call it. ``technique`` "bikefit" or "ski": the
    skeleton without the runner's angles, and the bike fit's or the stroke's values live
    (``pose.technique_live``) on ``TOPIC_TECH_SUMMARY``. ``labels`` False alone: the skeleton only."""
    from lopanalys.core.bus import TOPIC_GAIT_STEP_2, TOPIC_GAIT_SUMMARY_2, TOPIC_TECH_SUMMARY, TOPIC_TECH_SUMMARY_2
    tech_topic = TOPIC_TECH_SUMMARY_2 if second else TOPIC_TECH_SUMMARY
    if demo.is_demo_source(getattr(source, "info", None)):
        if second:
            return demo.DemoAnalyzer(source, bus, height_cm, view, topic=TOPIC_GAIT_SUMMARY_2, tech_topic=tech_topic)
        return demo.DemoAnalyzer(source, bus, height_cm, view, tech_topic=tech_topic)
    from lopanalys.pose.live import LiveAnalyzer  # lazy: pulls in scipy (and mediapipe when started)
    if second:
        from lopanalys.pose.multiview import SECOND_VIEW_MAX_FPS
        return LiveAnalyzer(source, bus, height_cm, view, name=name or "second",
                            topics=(TOPIC_GAIT_STEP_2, TOPIC_GAIT_SUMMARY_2), max_fps=SECOND_VIEW_MAX_FPS,
                            labels=labels, technique=technique, tech_topic=tech_topic)
    return LiveAnalyzer(source, bus, height_cm, view, name=name, labels=labels, technique=technique,
                        tech_topic=tech_topic)


def model_ready() -> bool:
    """True when the pose model file is already there (bundled or downloaded)."""
    try:
        return pose_models.find_model("full") is not None
    except Exception:
        log.exception("pose model check failed")
        return False


def ensure_model(progress: Callable[[float], None] | None = None) -> Path:
    """Download the pose model if needed (blocking; call from a worker)."""
    return pose_models.ensure_model("full", progress_cb=progress)


def analyze_video(path: str, height_cm: float, view: Any, progress_cb: Callable[[float], None] | None,
                  keyframes_dir: Path | None, max_seconds: float | None = None) -> dict[str, Any]:
    """Offline gait analysis of a recording (blocking; call from a worker). Returns GaitAnalysis.to_dict().

    ``max_seconds``: only the film up to then (maxpuls med löpteknik: not the last minutes of the ramp)."""
    from lopanalys.pose import offline  # lazy: pulls in scipy and mediapipe
    return offline.analyze_video(path, height_cm, view, progress_cb=progress_cb, keyframes_dir=keyframes_dir,
                                 max_seconds=max_seconds).to_dict()


def analyze_technique(kind: str, path: str, height_cm: float | None, view: Any,
                      progress_cb: Callable[[float], None] | None = None, keyframes_dir: Path | None = None,
                      previous: dict[str, Any] | None = None, demo: bool = False) -> dict[str, Any]:
    """Offline analysis of a bike fit ("bikefit") or SkiErg ("ski") film (blocking; call from a worker).

    Returns ``pose.technique.TechniqueAnalysis.to_dict()``. ``previous``: the athlete's last analysis
    of the same kind and view, for the comparison. ``demo``: the test was filmed with the demo
    athlete, so a film of that athlete is analysed with the poses it was drawn from (there is no pose
    model in demo mode); the result points at the test's own film."""
    if kind == "bikefit":
        from lopanalys.pose.bike import analysis  # lazy: pulls in scipy and mediapipe
    else:
        from lopanalys.pose.ski import analysis  # type: ignore[no-redef]
    view = getattr(view, "value", view)
    if not demo:
        return analysis.analyze_video(path, height_cm, view, progress_cb=progress_cb, keyframes_dir=keyframes_dir,
                                      previous=previous).to_dict()
    from lopanalys.core import paths
    out_dir = Path(keyframes_dir) if keyframes_dir else paths.keyframes_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    film, estimator = _demo_film(kind, str(view), height_cm, out_dir / f"{Path(path).stem}-{view}-demo.mp4")
    try:
        a = analysis.analyze_video(film, height_cm, view, progress_cb=progress_cb, keyframes_dir=out_dir,
                                   previous=previous, estimator=estimator)
    finally:
        Path(film).unlink(missing_ok=True)
    a.video_path = str(path)
    return a.to_dict()


def _demo_film(kind: str, view: str, height_cm: float | None, dest: Path) -> tuple[Any, Any]:
    """(film, estimator) of the demo athlete (``demo`` is a parameter name in analyze_technique)."""
    return demo.technique_demo_film(kind, view, height_cm, dest)


def previous_or_none(store: "Store", s: Session, kind: str, view: str) -> dict[str, Any] | None:
    """The athlete's last analysis of this kind and view before ``s`` (for the comparison), or None."""
    prev = store.previous_technique(s, kind, view)
    return prev[0] if prev else None


def technique_record(store: "Store", s: Session) -> dict[str, Any] | None:
    """Everything the result page and the report show of a bike fit or a SkiErg test:
    ``{"kind", "views", "checklist", "previous_date"}``; None for other tests."""
    if s.id is None:
        return None
    tech = store.technique(s.id)
    if tech is None:
        return None
    prev = store.previous_technique(s, tech["kind"], tech["views"][0].get("view", ""))
    tech["previous_date"] = prev[1].date().isoformat() if prev and prev[1] else None
    tech["checklist"] = dict((s.result or {}).get("bikefit_checklist") or {})
    return tech


def save_checklist(store: "Store", s: Session, values: dict[str, Any]) -> dict[str, Any]:
    """Save the manual bike fit checklist with the test. Returns what was stored."""
    from lopanalys.pose.bike import checklist
    clean = checklist.clean(values)
    s.result = dict(s.result or {})
    s.result["bikefit_checklist"] = clean
    store.save_session(s)
    return clean


def match_views(first: dict[str, Any], second: dict[str, Any], offset_s: float) -> dict[str, Any] | None:
    """How the steps of two analysed views fit together on the shared clock (pose.multiview): the
    number of contacts found in both and the second camera's lag. None when it cannot be told."""
    try:
        from lopanalys.pose.multiview import match_analyses
        m = match_analyses(first, second, offset_s)
    except Exception:
        log.exception("matching the two views failed")
        return None
    if m is None:
        return None
    log.info("two views: %d of %d steps found in both, the second camera is %.0f ms behind (spread %s ms)",
             m.matched, min(m.first, m.second), m.lag_s * 1000,
             "?" if m.spread_ms is None else f"{m.spread_ms:.0f}")
    return m.to_dict()


def file_source_info(path: str) -> SourceInfo:
    """SourceInfo for a video file chosen by the coach (SD card recording)."""
    return SourceInfo(SourceKind.FILE, path, Path(path).name, live=False)


def make_recorder(path: Path, fps: float, size: tuple[int, int]) -> Recorder:
    """A recorder for the live video (camera.recorder)."""
    return Recorder(path, fps, size)


# =============================================================================================
# Report
# =============================================================================================

def _brand(builder: Any, branding: dict[str, Any]) -> Any:
    return builder.Branding(business_name=branding.get("business") or "", coach_name=branding.get("coach") or "",
                            contact=(branding.get("contact") or "").replace("\n", " · "),
                            logo_path=branding.get("logo") or None,
                            accent_color=branding.get("accent") or "#22A699")


def render_tests_info(branding: dict[str, Any], sport: Any = None) -> str:
    """The one-page sheet "Om testerna" of a sport as HTML (report.builder.render_tests_info)."""
    from lopanalys.report import builder  # lazy: pulls in matplotlib and jinja2
    return builder.render_tests_info(_brand(builder, branding), sport=sport)


def render_report(*args: Any, **kw: Any) -> str:
    """The report as HTML (report.builder.render_html); the arguments are those of report_data()."""
    from lopanalys.report import builder  # lazy: pulls in matplotlib and jinja2
    return builder.render_html(report_data(*args, **kw))


def _zone_dicts(zone_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [z["raw"].to_dict() for z in zone_list]


def _compare_dicts(zone_compare: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    return [{"name": c["name"], "zones": _zone_dicts(c["zones"])} for c in zone_compare or []] or None


def report_data(athlete: Athlete, session: Session, records: list[StepRecord],
                lactate: LactateView | None, zone_list: list[dict[str, Any]], max_hr: MaxHrView | None,
                gait: dict[str, Any] | None, previous: dict[str, Any] | None, branding: dict[str, Any],
                samples: list[TimeSample] | None = None, comment: str = "", *,
                zone_system: str | None = None, zone_compare: list[dict[str, Any]] | None = None,
                garmin: dict[str, Any] | None = None, vo2max: dict[str, Any] | None = None,
                hr_speed: dict[str, Any] | None = None, power: dict[str, Any] | None = None,
                technique: dict[str, Any] | None = None) -> Any:
    """The report of one test as report.builder.ReportData.

    ``previous``: {"session", "records", "lactate" (LactateView|None)} of the previous test, or None.
    ``zone_system``: the system ``zone_list`` is in. ``zone_compare``, ``garmin``, ``vo2max``: the
    results of zone_comparison(), garmin_setup() and estimate_vo2max().
    """
    from lopanalys.report import builder  # lazy: pulls in matplotlib and jinja2
    brand = _brand(builder, branding)

    def lac(lv: LactateView | None) -> dict[str, Any] | None:
        return lv.raw.to_dict() if lv is not None and lv.raw is not None else None

    prev = None
    if previous:
        prev = builder.ReportData(athlete=athlete, session=previous["session"], records=previous.get("records", []),
                                  lactate=lac(previous.get("lactate")), branding=brand)
    kw: dict[str, Any] = dict(athlete=athlete, session=session, records=records, lactate=lac(lactate),
                              zones=_zone_dicts(zone_list) or None,
                              max_hr=max_hr.raw.to_dict() if max_hr is not None and max_hr.raw is not None else None,
                              gait=gait, previous=prev, branding=brand, samples=samples or [],
                              zone_system=zone_system_info(zone_system) if zone_list else None,
                              zone_compare=_compare_dicts(zone_compare), garmin=garmin, vo2max=vo2max,
                              hr_speed=hr_speed, power=power, technique=technique)
    if comment:
        kw["coach_comment"] = comment
    return builder.ReportData(**kw)


def render_collected_report(store: Any, athlete: Athlete, sessions: list[Session], branding: dict[str, Any]) -> str:
    """One report for several tests of one athlete (Historik → Samlad rapport), as HTML.

    Every test is analysed as the result page does it, with its saved threshold methods and comment.
    The zones, the watch values and VO2max are computed over the tests together: from the lactate
    thresholds and the highest measured max HR. report.builder.collect() merges the rest."""
    from lopanalys.report import builder  # lazy: pulls in matplotlib and jinja2
    sports_in = {sport_of(s.sport) for s in sessions}
    if len(sports_in) > 1:
        # zones and max HR are per sport: tests in different sports are never merged into one report
        raise ValueError(t("ui.hist.collect_one_sport"))
    sport = next(iter(sports_in), DEFAULT_SPORT)
    saved_max = athlete.max_hr_for(sport)
    rest = athlete.resting_hr
    parts, lactates, peaks = [], [], []
    vo2_src: tuple[Session, list[StepRecord], bool, bool | None] | None = None
    systems: list[tuple[bool, str]] = []
    relation: Any = None      # heart rate -> speed of the latest zones test without lactate
    for s in sorted(sessions, key=lambda x: x.started):
        if s.id is None:
            continue
        res = s.result or {}
        warm = {i for i, st in enumerate((s.protocol or {}).get("stages", [])) if st.get("is_warmup")}
        records = [r for r in store.records(s.id) if r.index not in warm]
        samples = store.samples(s.id)
        age = (s.started.date() - athlete.birth_date).days / 365.25 if athlete.birth_date else None
        to_exh = res.get("exhaustion_t_s") is not None
        rel = None
        if s.kind == TestKind.HR_ZONES:
            rel = analyze_hr_speed(records, samples, s.protocol, res.get("exhaustion_t_s"))
            to_exh = to_exh and rel is not None and rel.ramp_reached   # a stop in the steps is not a max
            relation = rel or relation
        mv = analyze_max_hr(samples, records, res.get("exhaustion_t_s"), age, sport)
        peak = (mv.peak if mv else None) or max((r.hr_end or 0 for r in records), default=0) or None
        lv = analyze_lactate(records, res.get("lt1_method"), res.get("lt2_method"), saved_max or peak, sport)
        if lv is not None and lv.lt2:
            lactates.append(lv)
        if peak and (s.kind == TestKind.MAX_HR or (s.kind == TestKind.HR_ZONES and to_exh)):
            peaks.append(peak)
        if to_exh and (vo2_src is None or s.kind in MAX_KINDS):
            vo2_src = (s, records, True, mv.likely_max if mv else None)
        elif vo2_src is None and s.kind == TestKind.LACTATE_STEP:
            vo2_src = (s, records, False, None)
        if res.get("zone_system"):   # the coach's choice, from the lactate test first
            systems.append((s.kind != TestKind.LACTATE_STEP, res["zone_system"]))
        parts.append(report_data(athlete, s, records, lv, [], mv, store.gait(s.id), None, branding, samples,
                                 res.get("coach_comment", ""), hr_speed=hr_speed_dict(rel, peak),
                                 power=analyze_power(s, samples)))
    if not parts:
        raise ValueError("no saved tests")
    system = next((v for other, v in systems if not other), systems[0][1] if systems else None)
    lv = lactates[0] if lactates else None
    mx = max([x for x in (saved_max, *peaks) if x], default=None)
    max_known = bool(saved_max or peaks) or bool(vo2_src and vo2_src[2])
    zone_list = zones(lv, mx, rest, system or zone_systems.DEFAULT_SYSTEM, sport)
    if lv is None:      # no thresholds: the zones get their speed from heart rate, when a test gave that
        zone_list = with_zone_speeds(zone_list, relation)
    override: dict[str, Any] = dict(
        zones=_zone_dicts(zone_list) or None, zone_system=zone_system_info(system) if zone_list else None,
        zone_compare=_compare_dicts(zone_comparison(lv, mx, rest, sport)),
        garmin=garmin_setup(lv, mx, rest, max_known, sport))
    if vo2_src is not None:
        s, records, exh, likely = vo2_src
        override["vo2max"] = estimate_vo2max(records, mx if max_known else None, rest, exh, s.protocol, likely)
    return builder.render_html(builder.collect(parts, **override))


def html_to_pdf(html: str, path: Path) -> None:
    """Save the report as PDF (report.pdf). Must be called on the GUI thread (Qt)."""
    from lopanalys.report import (
        pdf,  # lazy: Qt print support now, Qt WebEngine when it renders
    )
    pdf.html_to_pdf(html, path)


def print_report(html: str, printer_name: str | None) -> None:
    """Send the report to a printer (report.pdf)."""
    from lopanalys.report import (
        pdf,  # lazy: Qt print support now, Qt WebEngine when it renders
    )
    pdf.print_html(html, printer_name=printer_name)


def report_target(store: Any, athlete: Athlete | None, tail: str) -> Path:
    """Where the "save as PDF" dialog starts. Inside the data folder the file never carries the
    person's name: ``reports/rapport-<athlete id>-<tail>.pdf``. When the coach last saved a report
    somewhere else (a USB stick, the customer's folder), the dialog starts there, with the name."""
    from lopanalys.core import paths
    tail = tail.removesuffix(".pdf").replace(" ", "_").replace("/", "-").replace("\\", "-")
    last = str(store.setting("report.folder") or "")
    data = paths.data_dir().resolve()
    if last and Path(last).is_dir() and not Path(last).resolve().is_relative_to(data):
        who = (athlete.name if athlete and athlete.name else "rapport").replace(" ", "_")
        return Path(last) / f"{who}_{tail}.pdf"
    return paths.reports_dir() / f"rapport-{athlete.id if athlete and athlete.id else 0}-{tail}.pdf"


def register_report(store: Any, path: Path, athlete: Athlete | None, session_id: int | None = None) -> None:
    """Remember where a report was saved, so erasing the person finds it, and the folder for next time."""
    try:
        store.set_setting("report.folder", str(Path(path).parent))
        if athlete is not None and athlete.id is not None:
            store.db.add_saved_file("report", path, athlete_id=athlete.id, session_id=session_id)
    except Exception:
        log.exception("registering the saved report failed")


def available_printers() -> list[str]:
    """Printer names for the picker (the system default first); [] if they cannot be listed."""
    from lopanalys.report import pdf  # lazy: Qt print support
    try:
        return pdf.available_printers()
    except Exception:
        log.exception("printer listing failed")
        return []


# =============================================================================================
# The CSV export (Inställningar → Exportera till Excel)
# =============================================================================================

EXPORT_HEADER = ["idrottare", "datum", "test", "protokoll", "steg", "km/h", "lutning", "puls", "laktat", "rpe",
                 "sport", "watt", "watt för hand"]


def export_rows(store: Any, athlete_id: int | None = None) -> list[list[Any]]:
    """The header and one row per stage of every test (one person's when ``athlete_id``), for a CSV
    with semicolons and decimal commas. The first ten columns are as before sport existed; a test in
    watt has its load under ``watt`` (km/h and incline empty), and ``watt för hand`` says "ja" for a
    stage whose mean power the coach wrote in (assessments.erg_values)."""
    from lopanalys.assessments import erg_values

    def dec(v: Any) -> str:
        return "" if v is None else str(v).replace(".", ",")

    rows: list[list[Any]] = [list(EXPORT_HEADER)]
    for a in store.athletes():
        if athlete_id is not None and a.id != athlete_id:
            continue
        for s in store.history(a.id):
            prof = profile(s.sport)
            power = prof.load_quantity == "power"
            for r in store.records(s.id):
                rows.append([a.name, s.started.astimezone().strftime("%Y-%m-%d %H:%M"), s.kind.value,
                             s.protocol_name, r.index + 1, "" if power else dec(r.speed_kmh),
                             "" if power else dec(r.incline_pct), r.hr_end or "", dec(r.lactate_mmol), r.rpe or "",
                             t(f"ui.sport.{prof.sport.value}"), dec(r.speed_kmh) if power else "",
                             "ja" if power and erg_values.is_manual(s, r.index) else ""])
    return rows


# =============================================================================================
# FIT file and mail to the athlete
# =============================================================================================

def fit_file(store: Any, session: Session, records: list[StepRecord] | None = None,
             samples: list[TimeSample] | None = None) -> tuple[str, bytes]:
    """(neutral file name, bytes) of the test as a FIT activity. ``records`` (warm-up included) and
    ``samples`` default to what is saved. Every sport gets a file: the encoder writes a run, a ride or a
    SkiErg session after ``session.sport``. Raises share.fit.FitError when the test has no time series."""
    from lopanalys.share import fit
    if session.id is None:
        raise fit.FitError("no_samples")
    return fit.file_name(session), fit.encode_activity(
        session, store.records(session.id) if records is None else records,
        store.samples(session.id) if samples is None else samples)


def fit_target(store: Any, session: Session) -> Path:
    """Where the "save FIT file" dialog starts: the folder reports were last saved in, else the data
    folder. The name is the test's id and date wherever it is saved, never the person."""
    from lopanalys.core import paths
    from lopanalys.share import fit
    last = str(store.setting("report.folder") or "")
    return (Path(last) if last and Path(last).is_dir() else paths.reports_dir()) / fit.file_name(session)


def register_fit(store: Any, path: Path, athlete: Athlete | None, session_id: int | None) -> None:
    """Remember where a FIT file was saved, so erasing the person finds it."""
    try:
        if athlete is not None and athlete.id is not None:
            store.db.add_saved_file("fit", path, athlete_id=athlete.id, session_id=session_id)
    except Exception:
        log.exception("registering the saved FIT file failed")


def mail_config(store: Any) -> Any:
    """The SMTP account from Inställningar → E-post as share.mail.MailConfig (the password decrypted)."""
    from lopanalys.share import mail, secret

    def get(key: str) -> Any:
        return store.setting(f"mail.{key}", mail.DEFAULTS[f"mail.{key}"])

    try:
        port = int(get("port"))
    except (TypeError, ValueError):
        port = 0
    return mail.MailConfig(host=str(get("host") or ""), port=port, security=str(get("security") or ""),
                           user=str(get("user") or ""), password=secret.reveal(get("password")) or "",
                           sender_name=str(get("sender_name") or store.setting("branding.coach") or ""),
                           sender_address=str(get("sender_address") or ""),
                           reply_to=mail.first_address(str(store.setting("branding.contact") or "")))


def set_mail_password(store: Any, password: str) -> None:
    """Store the app password protected (share.secret), or remove it with ""."""
    from lopanalys.share import secret
    store.set_setting("mail.password", secret.protect(password) if password else None)


def mail_block_reason(store: Any, athlete: Athlete | None) -> str | None:
    """Why nothing can be mailed to this athlete (for the grey button), or None when it can.

    Mail goes only to a person with an address who ticked the consent box "contact". The address and
    the consent are read from the database, not from the object a view happens to hold."""
    from lopanalys.core.models import valid_email
    athlete = store.athlete(athlete.id) if athlete is not None and athlete.id is not None else None
    if athlete is None:
        return t("ui.mail.block.no_athlete")
    if not athlete.email or not valid_email(athlete.email):
        return t("ui.mail.block.no_address", name=athlete.first_name)
    if not athlete.consents("contact"):
        return t("ui.mail.block.no_consent", name=athlete.first_name)
    if mail_config(store).missing():
        return t("ui.mail.block.not_set_up")
    return None


def mail_greeting(athlete: Athlete, day: str, branding: dict[str, Any], fit: bool, tests: int = 1) -> tuple[str, str]:
    """(subject, body) of the mail to the athlete, from the template (share.mail.greeting)."""
    from lopanalys.share import mail
    return mail.greeting(athlete.first_name, day, branding.get("coach") or "", branding.get("business") or "",
                         fit=fit, tests=tests)


def send_mail(cfg: Any, to: str, subject: str, body: str, attachments: list[tuple[str, bytes, str]]) -> None:
    """Send one mail with attachments ``(name, bytes, mime)``. Blocks: call it from a worker.
    Raises share.mail.MailError with a message for the coach."""
    from lopanalys.share import mail
    msg = mail.build_message(cfg, to, subject, body, [mail.Attachment(*a) for a in attachments])
    mail.send(cfg, msg)


def send_test_mail(cfg: Any, to: str | None = None) -> None:
    """"Skicka ett provmejl": a mail from the account to ``to`` (default the account itself).
    Blocks; raises share.mail.MailError."""
    from lopanalys.share import mail
    mail.send(cfg, mail.probe_message(cfg, to))


def mailed_text(session: Session | None) -> str:
    """"Skickat till idrottaren ..." for the latest time the test was mailed, or ""."""
    sent = (session.result or {}).get("mailed") if session else None
    return _sent_line(sent[-1]) if sent else ""


def _sent_line(last: dict[str, Any]) -> str:
    """One mailing {"at", "what"} as "Skickat till idrottaren 2026-11-15 14:02: rapporten + FIT-filen"."""
    from datetime import datetime
    try:
        when = datetime.fromisoformat(last["at"]).astimezone().strftime("%Y-%m-%d %H:%M")
    except (KeyError, ValueError, TypeError):
        when = ""
    what = " + ".join(t(f"ui.mail.what.{w}") for w in last.get("what", []))
    return t("ui.mail.sent_line", when=when, what=what)


# =============================================================================================
# Training plan
# =============================================================================================

PLAN_TEST_KINDS = (TestKind.LACTATE_STEP, TestKind.MAX_HR, TestKind.HR_ZONES)
HEALTH_ITEMS = ("asked", "cleared", "advice")   # the boxes of the health declaration, i18n ui.plan.health.<item>
HEALTH_VALID_DAYS = 365


@dataclass
class StoredPlan:
    """The athlete's saved plan: the plan itself, the test it is built on and the health declaration."""
    plan: Any                               # plan.models.Plan
    session_id: int | None
    health: dict[str, Any]
    row_id: int | None = None
    updated: str = ""


def plan_test_ok(s: Session) -> bool:
    """True for a test a plan can be built on: a running lactate, max HR or zones test. The plans are built for
    running (plan.models.RUNNING); a test in another sport has other zones and never moves them."""
    return s.kind in PLAN_TEST_KINDS and sport_of(s.sport) is DEFAULT_SPORT


def plan_tests(store: Store, athlete_id: int) -> list[Session]:
    """The tests a plan can be built on: running lactate, max HR and zones tests that were not cut off, newest first."""
    return [s for s in store.history(athlete_id)
            if plan_test_ok(s) and getattr(s, "status", "finished") in ("finished", "stopped")]


def sport_tests(store: Store, athlete_id: int, sport: str) -> list[Session]:
    """The lactate, max HR and zones tests of one sport that were not cut off, newest first (#666, step 4)."""
    return [s for s in store.history(athlete_id)
            if s.kind in PLAN_TEST_KINDS and sport_of(s.sport).value == sport
            and getattr(s, "status", "finished") in ("finished", "stopped")]


def sport_capacity(store: Store, athlete: Athlete, sport: str) -> Any:
    """The capacity of a sport in a plan with several sports: its newest test, else the max HR in the sport,
    else nothing. None for skis, which have no tests in Tröskel: the engine borrows for them
    (plan.models.BORROWS). Swimming takes its newest CSS test (``swim_capacity``); without one it is None and
    swimming is planned on effort."""
    from lopanalys.plan.models import Capacity
    if sport == Sport.SWIM.value:
        return swim_capacity(store, athlete)
    if sport not in {s.value for s in Sport} or athlete.id is None:
        return None
    tests = sport_tests(store, athlete.id, sport)
    if tests:
        return plan_capacity(store, athlete, tests[0])
    mx = athlete.max_hr_for(sport)
    return Capacity(sport, zone_systems.Anchors(max_hr=mx, resting_hr=athlete.resting_hr, sport=sport)) if mx \
        else Capacity(sport)


def sport_test_list(store: Store, athlete_id: int, sport: str) -> list[Session]:
    """The tests a plan can take a sport's zones from, newest first: the CSS tests for swimming, else the sport's
    lactate, max HR and zones tests (``sport_tests``); none for a sport without tests in Tröskel."""
    if sport == Sport.SWIM.value:
        return css_tests(store, athlete_id)
    return sport_tests(store, athlete_id, sport) if sport in {s.value for s in Sport} else []


def sport_capacity_of(store: Store, athlete: Athlete, sport: str, session_id: int | None) -> Any:
    """The capacity of a sport from the test chosen in the plan form (#666): that test's, or with ``session_id``
    None (*Inget test*) the max HR in the sport, else nothing; swimming without a test is planned on effort (None)."""
    from lopanalys.plan.models import Capacity
    session = store.session(session_id) if session_id is not None else None
    if session is not None:
        return retest_capacity(store, athlete, session)
    if sport == Sport.SWIM.value or sport not in {s.value for s in Sport}:
        return None
    mx = athlete.max_hr_for(sport)
    return Capacity(sport, zone_systems.Anchors(max_hr=mx, resting_hr=athlete.resting_hr, sport=sport)) if mx \
        else Capacity(sport)


def css_tests(store: Store, athlete_id: int) -> list[Session]:
    """The athlete's valid CSS tests (swimming, written in by hand), newest first."""
    from lopanalys.assessments import css
    return [s for s in store.history(athlete_id, TestKind.SWIM_CSS.value) if css.from_result(s.result) is not None]


def css_test_of(session: Session | None) -> Any:
    """The ``assessments.css.CssTest`` of a stored session, with its date; None when the session is not one."""
    from lopanalys.assessments import css
    if session is None or session.kind != TestKind.SWIM_CSS:
        return None
    return css.from_result(session.result, session.started.astimezone().date())


def save_css_test(store: Store, athlete: Athlete, test: Any, session: Session | None = None,
                  notes: str = "") -> Session:
    """Save a CSS test as a manual session of the athlete: no steps and no samples, the two times in
    ``session.result`` (assessments.css.to_result). With ``session`` the stored test is corrected."""
    from datetime import datetime, time

    from lopanalys.assessments import css
    if athlete.id is None:
        raise ValueError("the athlete is not saved")
    day = test.tested or date.today()
    when = datetime.combine(day, time(12, 0)).astimezone()
    s = session if session is not None else Session(id=None, athlete_id=athlete.id, kind=TestKind.SWIM_CSS,
                                                    started=when)
    s.kind, s.sport, s.started, s.ended = TestKind.SWIM_CSS, Sport.SWIM, when, when
    s.protocol_name, s.protocol, s.treadmill_mode, s.status = t("ui.css.name"), {}, "manual", "finished"
    s.notes = notes
    s.result = css.to_result(test)
    return store.save_session(s)


def swim_capacity(store: Store, athlete: Athlete) -> Any:
    """Swimming's capacity for the plan: the pace per 100 m of the newest CSS test, or None without one."""
    from lopanalys.assessments import css
    if athlete.id is None:
        return None
    tests = css_tests(store, athlete.id)
    test = css_test_of(tests[0]) if tests else None
    return css.capacity(test, tests[0].protocol_name or t("ui.css.name")) if test is not None else None


def plan_capacity(store: Store, athlete: Athlete, session: Session | None) -> Any:
    """What the athlete can do, for the plan: the anchors of ``session`` as the result page computes them
    (saved threshold methods; the athlete's known max HR before the test's peak). Without a test: the
    athlete's max HR alone (estimated zones), or nothing.

    A zones test (Puls- och fartzoner) gives the max HR of its ramp and the relation heart rate ->
    speed of its steps, so the estimated zones get a pace. Its peak counts as a max HR only when the
    athlete ran to the limit in the ramp (the rule of the result page and the collected report): a
    test stopped in the steady steps leaves the max HR to the athlete's saved value."""
    from lopanalys.plan import resolve
    from lopanalys.plan.models import RUNNING, Capacity
    if session is not None and session.id is not None and sport_of(session.sport) is not DEFAULT_SPORT:
        return _sport_capacity(store, athlete, session)
    if session is None or session.id is None:
        if athlete.max_hr:
            return Capacity(RUNNING, zone_systems.Anchors(max_hr=athlete.max_hr, resting_hr=athlete.resting_hr))
        return Capacity(RUNNING)
    res = session.result or {}
    warm = {i for i, st in enumerate((session.protocol or {}).get("stages", [])) if st.get("is_warmup")}
    records = [r for r in store.records(session.id) if r.index not in warm]
    age = (session.started.date() - athlete.birth_date).days / 365.25 if athlete.birth_date else None
    samples = store.samples(session.id)
    mv = analyze_max_hr(samples, records, res.get("exhaustion_t_s"), age)
    peak = (mv.peak if mv else None) or max((r.hr_end or 0 for r in records), default=0) or None
    line = None
    if session.kind == TestKind.HR_ZONES:
        rel = analyze_hr_speed(records, samples, session.protocol, res.get("exhaustion_t_s"))
        line = resolve.speed_line(rel)
        if res.get("exhaustion_t_s") is None or rel is None or not rel.ramp_reached:
            peak = None                     # a stop in the steady steps is not a max
    mx = athlete.max_hr or peak
    lv = analyze_lactate(records, res.get("lt1_method"), res.get("lt2_method"), mx)
    a = _anchors(lv if lv is not None and lv.lt2 else None, mx, athlete.resting_hr)
    tested = session.started.astimezone().date()
    if not (a.max_hr or a.has_thresholds):
        return Capacity(RUNNING, None, tested, session.protocol_name)
    # without the lactate curve: what is stored and read back is the same as what was built on
    return Capacity(RUNNING, dataclass_replace(a, source=None), tested, session.protocol_name, line)


def _sport_capacity(store: Store, athlete: Athlete, session: Session) -> Any:
    """The capacity of a bike or SkiErg test: its thresholds in watt and the max HR in the sport. A zones test
    in these sports gives no speed line (the plan's targets are watt from lactate)."""
    from lopanalys.plan.models import Capacity
    sport = sport_of(session.sport).value
    res = session.result or {}
    warm = {i for i, st in enumerate((session.protocol or {}).get("stages", [])) if st.get("is_warmup")}
    records = [r for r in store.records(session.id) if r.index not in warm]
    peak = max((r.hr_end or 0 for r in records), default=0) or None
    mx = athlete.max_hr_for(session.sport) or (peak if res.get("exhaustion_t_s") is not None else None)
    lv = analyze_lactate(records, res.get("lt1_method"), res.get("lt2_method"), mx, sport)
    a = _anchors(lv if lv is not None and lv.lt2 else None, mx, athlete.resting_hr, sport)
    tested = session.started.astimezone().date()
    if not (a.max_hr or a.has_thresholds):
        return Capacity(sport, None, tested, session.protocol_name)
    return Capacity(sport, dataclass_replace(a, source=None), tested, session.protocol_name)


def load_plan(store: Store, athlete: Athlete) -> StoredPlan | None:
    """The athlete's saved plan, or None. The targets follow the test it is built on as that test is
    now (a corrected lactate value moves them); if the test is gone, the anchors saved with the plan."""
    from lopanalys.plan import codec
    if athlete.id is None:
        return None
    row = store.db.get_plan(athlete.id)
    if row is None:
        return None
    req = codec.request_from_dict(row["request"], athlete.name)
    plan = codec.plan_from_dict(row["plan"], req)
    s = store.session(row["session_id"]) if row["session_id"] else None
    if s is not None:
        try:
            cap = plan_capacity(store, athlete, s)
            others = tuple(c for c in req.capacity if c.sport != cap.sport)
            plan = dataclass_replace(plan, request=dataclass_replace(req, capacity=others + (cap,)))
        except Exception:
            log.exception("reading the plan's test failed, using the saved anchors")
    return StoredPlan(plan, row["session_id"] if s is not None else None, row["health"], row["id"], row["updated"])


def save_plan(store: Store, athlete: Athlete, plan: Any, session_id: int | None, health: dict[str, Any] | None,
              event: str = "saved", detail: dict[str, Any] | None = None) -> int:
    from lopanalys.plan import codec
    assert athlete.id is not None
    return store.db.save_plan(athlete.id, request=codec.request_to_dict(plan.request), plan=codec.plan_to_dict(plan),
                              health=health, session_id=session_id, engine_version=plan.engine_version, event=event,
                              detail=detail)


def delete_plan(store: Store, athlete_id: int) -> bool:
    return store.db.delete_plan(athlete_id)


def health_valid(health: dict[str, Any] | None, today: date | None = None) -> bool:
    """True when every box of the health declaration was ticked within the last 12 months."""
    h = health or {}
    try:
        day = date.fromisoformat(h.get("date") or "")
    except ValueError:
        return False
    return set(HEALTH_ITEMS) <= set(h.get("confirmed", [])) and (today or date.today()) - day <= timedelta(HEALTH_VALID_DAYS)


def test_sport(session: Session) -> str | None:
    """The plan sport a test gives zones to: running, the bike or the SkiErg for a lactate, max HR or zones test,
    swimming for a CSS test; None for any other test."""
    if session.kind == TestKind.SWIM_CSS:
        return Sport.SWIM.value
    return sport_of(session.sport).value if session.kind in PLAN_TEST_KINDS else None


def retest_candidates(store: Store, athlete: Athlete, stored: StoredPlan | None = None) -> list[Session]:
    """The tests the saved plan can be laid on, one per sport of the plan, running first (#666): the newest
    lactate, max HR or zones test in running, the bike or the SkiErg, or the newest CSS test, when it is newer than
    the test the plan's targets in that sport come from. Skis take the SkiErg's (plan.models.BORROWS). Empty when
    there is no plan or nothing newer."""
    from lopanalys.plan.models import BORROWS, RUNNING
    stored = stored or load_plan(store, athlete)
    if stored is None or athlete.id is None:
        return []
    out: list[Session] = []
    tests = plan_tests(store, athlete.id)
    sports = stored.plan.request.plan_sports
    if RUNNING in sports and tests and tests[0].id != stored.session_id:
        basis = store.session(stored.session_id) if stored.session_id else None
        if basis is None or tests[0].started > basis.started:
            out.append(tests[0])
    for sport in dict.fromkeys(BORROWS.get(sp, sp) for sp in sports if sp != RUNNING):
        newest = (css_tests(store, athlete.id) if sport == Sport.SWIM.value
                  else sport_tests(store, athlete.id, sport) if sport in {x.value for x in Sport} else [])
        if not newest:
            continue
        own = stored.plan.request.own_capacity(sport)
        day = newest[0].started.astimezone().date()
        if own is None or own.tested is None or day > own.tested:
            out.append(newest[0])
    return out


def retest_candidate(store: Store, athlete: Athlete, stored: StoredPlan | None = None) -> Session | None:
    """The first of ``retest_candidates``: the newer test the plan page and the result offer, or None."""
    found = retest_candidates(store, athlete, stored)
    return found[0] if found else None


def retest_capacity(store: Store, athlete: Athlete, session: Session) -> Any:
    """The plan's capacity from a test: a CSS test's pace per 100 m, else the thresholds (``plan_capacity``)."""
    from lopanalys.assessments import css
    if session.kind == TestKind.SWIM_CSS:
        test = css_test_of(session)
        if test is None:
            raise ValueError("not a valid CSS test")
        return css.capacity(test, session.protocol_name or t("ui.css.name"))
    return plan_capacity(store, athlete, session)


def retest_offer_text(session: Session) -> str:
    """The offer of a newer test on the plan page and after a CSS test: running as before, another sport by name."""
    sport = test_sport(session)
    when = f"{session.started.astimezone():%Y-%m-%d}"
    if sport is None or sport == Sport.RUN.value:
        return t("ui.plan.retest_offer", test=session.protocol_name, date=when)
    return t("ui.plan.retest_offer_sport", test=session.protocol_name, date=when, sport=t(f"plan.sport.{sport}"))


def retest_done_text(session: Session, today: date) -> str:
    sport = test_sport(session)
    if sport is None or sport == Sport.RUN.value:
        return t("ui.plan.retest_done", date=today.isoformat())
    return t("ui.plan.retest_done_sport", date=today.isoformat(), sport=t(f"plan.sport.{sport}"))


def apply_retest(store: Store, athlete: Athlete, session: Session, today: date | None = None) -> StoredPlan:
    """Lay the rest of the saved plan on a new test (plan.resolve.retest): from today on every workout in the test's
    sport gets the targets of ``session``; earlier workouts keep theirs, the other sports keep their tests, and no
    workout changes its day. A test in running becomes the plan's test (``session_id``); a test in another sport
    (the bike, the SkiErg, a CSS test) moves only that sport's targets, and the running test stays."""
    from lopanalys.plan import resolve, rolling
    from lopanalys.plan.models import RUNNING
    stored = load_plan(store, athlete)
    if stored is None:
        raise ValueError("no plan")
    today = today or date.today()
    cap = retest_capacity(store, athlete, session)
    old = stored.plan.request.capacity_for(cap.sport)
    plan = resolve.retest(stored.plan, cap, from_date=today)
    plan = rolling.roll_forward(plan, today)             # the same path as Förnya: the next weeks get their workouts
    session_id = session.id if cap.sport == RUNNING else stored.session_id
    save_plan(store, athlete, plan, session_id, None, "retest",
              {"from_session": stored.session_id, "to_session": session.id, "from_date": today.isoformat(),
               "sport": cap.sport, "tested_before": old.tested.isoformat() if old.tested else None,
               "horizon": [stored.plan.horizon, plan.horizon]})
    return StoredPlan(plan, session_id, stored.health, stored.row_id)


def renew_plan(store: Store, athlete: Athlete, today: date | None = None) -> StoredPlan:
    """*Förnya*: the saved plan with its workouts up to four weeks from today (plan.rolling.roll_forward).
    The form, the test and the health declaration stay as saved; detailed weeks never change. Saved
    (with a "renewed" event) only when there was something to detail."""
    from lopanalys.plan import rolling
    stored = load_plan(store, athlete)
    if stored is None:
        raise ValueError("no plan")
    today = today or date.today()
    plan = rolling.roll_forward(stored.plan, today)
    if plan is stored.plan:
        return stored
    save_plan(store, athlete, plan, stored.session_id, None, "renewed",
              {"from_week": stored.plan.horizon, "to_week": plan.horizon, "date": today.isoformat(),
               "engine_version": plan.engine_version})
    return StoredPlan(plan, stored.session_id, stored.health, stored.row_id)


def plan_renew_due(store: Store, athlete: Athlete, today: date | None = None) -> StoredPlan | None:
    """The athlete's saved plan when its detailed weeks end within 7 days and more weeks follow, else None."""
    from lopanalys.plan import rolling
    stored = load_plan(store, athlete)
    if stored is None or not rolling.renew_soon(stored.plan, today or date.today()):
        return None
    return stored


def render_plan(plan: Any, branding: dict[str, Any]) -> str:
    """The training plan's printout as HTML (report.plan_builder)."""
    from lopanalys.report import (  # lazy: pulls in matplotlib and jinja2
        builder,
        plan_builder,
    )
    return plan_builder.render_plan_html(plan, _brand(builder, branding))


def plan_ics(plan: Any) -> bytes:
    from lopanalys.plan import ics
    return ics.to_ics(plan).encode("utf-8")


def plan_app(plan: Any) -> tuple[str, bytes]:
    """The plan for the athlete's app: (the link, the contents of the ``.troskel`` file), from the same payload."""
    from lopanalys.plan import link
    stored = link.stored_plan(plan)
    return link.plan_link(stored), link.plan_file(stored)


def plan_app_file_name(plan: Any) -> str:
    """``traningsschema-<start>.troskel``, as the mail's other attachments."""
    from lopanalys.plan.link import FILE_SUFFIX
    return f"{t('ui.mail.plan_file')}-{plan.start.isoformat()}{FILE_SUFFIX}"


def plan_mail_greeting(athlete: Athlete, plan: Any, branding: dict[str, Any], app_link: str = "") -> tuple[str, str]:
    """(subject, body) of the mail with the training plan (share.mail.plan_greeting), with the app's link."""
    from lopanalys.share import mail
    return mail.plan_greeting(athlete.first_name, plan.start.isoformat(), len(plan.weeks),
                              branding.get("coach") or "", branding.get("business") or "", app_link=app_link)


def plan_mailed_text(store: Store, stored: StoredPlan | None) -> str:
    """"Skickat till idrottaren ..." for the latest time the saved plan was mailed, or ""."""
    if stored is None or stored.row_id is None:
        return ""
    sent = [e for e in store.db.plan_events(stored.row_id) if e["kind"] == "mailed"]
    if not sent:
        return ""
    return _sent_line({"at": sent[-1]["at"], "what": sent[-1]["detail"].get("what", [])})
