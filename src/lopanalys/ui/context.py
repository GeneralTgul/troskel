"""Application context shared by all views: bus, database, devices, settings and the running test."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QObject, Signal

from lopanalys.assessments.protocol import Phase, Protocol, RunnerState
from lopanalys.core.bus import Bus
from lopanalys.devices.base import DeviceInfo
from lopanalys.core.models import Athlete, Session, StepRecord, TestKind, TimeSample
from lopanalys.core.sport import DEFAULT_SPORT, sport_of
from lopanalys.ui import services
from lopanalys.ui.bridge import BusBridge

log = logging.getLogger(__name__)

SETTING_DEFAULTS: dict[str, Any] = {
    "branding.business": "",
    "branding.coach": "",
    "branding.contact": "",
    "branding.logo": "",
    "branding.accent": "#22A699",
    "app.language": "sv",
    "devices.treadmill_mode": "ftms",
    "devices.hr": "",
    # The belt's real range: QZ cannot report it. 0 = what the treadmill reports.
    "devices.max_speed_kmh": 0.0,
    "devices.max_incline_pct": 0.0,
    "camera.default": "",
    "camera.view": "side_left",
    "camera.second": "",            # the second camera of a test with two views ("" = one camera)
    "camera.view2": "rear",
    "report.printer": "",
    "tv.screen": -1,
    "tv.sound": True,
    "lock.pin": None,                     # ui.lock.hash_pin(); None: no lock
    "lock.idle_min": 10,                  # lock after this many minutes without use (0: never); needs a PIN
    "lock.customer_mode": True,           # hide the athlete list and the history while a test runs
    "privacy.video_retention": "30d",     # "after_analysis" | "30d" | "keep" (storage.db.VIDEO_RETENTION)
    # Mail to the athlete, see share.mail.DEFAULTS. The password is stored protected (share.secret).
    "mail.host": "smtp.gmail.com",
    "mail.port": 587,
    "mail.security": "starttls",
    "mail.user": "",
    "mail.password": None,
    "mail.sender_name": "",
    "mail.sender_address": "",
}


# The test kinds that film and analyse the technique ("hr_zones" only when a camera was chosen).
GAIT_KINDS = ("gait", "combined", "max_hr_gait", "hr_zones")
CAMERA_OPTIONAL_KINDS = ("hr_zones",)
# Bike fit and SkiErg double poling: always filmed, analysed by pose.bike and pose.ski after the
# test, never by the running chain. The film starts with the first stage after the warm-up.
TECHNIQUE_KINDS = ("bikefit", "ski")


@dataclass
class TestPlan:
    """Everything the wizard chose; enough to start a test."""
    athlete: Athlete
    kind: str                       # "gait" | "lactate" | "max_hr" | "combined" | "max_hr_gait" | "hr_zones" | "ftp"
                                    # | "bikefit" | "ski"
    protocol: Protocol
    camera: Any = None              # camera.base.SourceInfo or None (no video)
    view: str = "side_left"
    treadmill_mode: str = "sim"     # "ftms" | "qz" | "manual" | "sim"
    treadmill_device: Any = None
    hr_device: Any = None           # DeviceInfo, "sim" or None
    camera2: Any = None             # a second camera filming at the same time (two views), or None
    view2: str = "rear"
    sport: str = "run"              # core.sport.Sport value: running, cycling or SkiErg


@dataclass
class TestOutcome:
    """What a finished (or stopped) test hands to the results view."""
    plan: TestPlan
    session: Session
    records: list[StepRecord]
    samples: list[TimeSample]
    gait_summaries: list[dict[str, Any]] = field(default_factory=list)
    video_path: str | None = None
    warmup_records: list[StepRecord] = field(default_factory=list)


@dataclass
class ResumeFrom:
    """A test cut off by a crash, continued from ``from_index`` with what was saved before."""
    session: Session
    records: list[StepRecord]       # the stages run before the cut, warm-up left out
    warmup: list[StepRecord]
    samples: list[TimeSample]
    from_index: int                 # the stage to run first
    t_offset_s: float               # the test clock goes on from here


class AppContext(QObject):
    """Owns the long-lived services. One per app."""

    settings_changed = Signal()
    athletes_changed = Signal()
    sessions_changed = Signal()
    athlete_erased = Signal(int)    # the id: views that show this person let go of them
    closing = Signal()          # just before the database closes: pending saves are written now

    def __init__(self, demo: bool = False, store: services.Store | None = None, time_scale: float = 1.0) -> None:
        super().__init__()
        self.demo = demo
        self.time_scale = time_scale
        self.bus = Bus()
        self.bridge = BusBridge(self.bus, self)
        self.store = store or services.open_store()
        self.devices = services.make_device_manager(self.bus, time_scale, self._remember_device,
                                                    self._recall_device)
        self.live: Any = None  # ui.live_controller.LiveController while a test runs

    def setting(self, key: str) -> Any:
        try:
            return self.store.setting(key, SETTING_DEFAULTS.get(key))
        except Exception:
            log.exception("setting read failed: %s", key)
            return SETTING_DEFAULTS.get(key)

    def set_setting(self, key: str, value: Any) -> None:
        self.store.set_setting(key, value)

    def _remember_device(self, kind: str, info: DeviceInfo) -> None:
        try:
            self.store.set_setting(f"devices.last.{kind}", {"address": info.address, "name": info.name})
        except Exception:
            log.exception("remember device failed")

    def _recall_device(self, kind: str) -> DeviceInfo | None:
        try:
            d = self.store.setting(f"devices.last.{kind}")
        except Exception:
            return None
        return DeviceInfo(d["address"], d.get("name", "")) if isinstance(d, dict) and d.get("address") else None

    def housekeeping(self) -> dict[str, int]:
        """At program start: films past their retention are removed. Never raises."""
        done = {"films": 0, "copies": 0}
        try:
            if str(self.setting("privacy.video_retention")) != "keep":
                done["films"] = self.store.db.purge_expired_videos()
                if done["films"]:
                    self.sessions_changed.emit()
            done["copies"] = self.store.db.purge_safety_copies()
        except Exception:
            log.exception("housekeeping failed")
        return done

    def branding(self) -> dict[str, Any]:
        """Branding as a dict: business, coach, contact, logo, accent."""
        return {k.split(".", 1)[1]: self.setting(k) for k in SETTING_DEFAULTS if k.startswith("branding.")}

    def new_session(self, plan: TestPlan) -> Session:
        kind = {"gait": TestKind.GAIT, "lactate": TestKind.LACTATE_STEP, "max_hr": TestKind.MAX_HR,
                "hr_zones": TestKind.HR_ZONES, "ftp": TestKind.FTP,
                "bikefit": TestKind.BIKEFIT, "ski": TestKind.SKI_TECHNIQUE}.get(plan.protocol.kind, TestKind.LACTATE_STEP)
        # W/kg is computed with the weight on the day of the test, so it is kept on the test
        result = ({"weight_kg": plan.athlete.weight_kg}
                  if sport_of(plan.sport) is not DEFAULT_SPORT and plan.athlete.weight_kg else {})
        return Session(None, plan.athlete.id or 0, kind, datetime.now(timezone.utc),
                       protocol_name=plan.protocol.name, protocol=plan.protocol.to_dict(),
                       treadmill_mode=plan.treadmill_mode, sport=plan.sport, result=result,
                       camera_source=" + ".join(getattr(c, "label", "") for c in (plan.camera, plan.camera2) if c))

    def shutdown(self) -> None:
        try:
            self.closing.emit()
        except Exception:
            log.exception("closing handlers failed")
        if self.live is not None:
            try:
                self.live.abort()
            except Exception:
                log.exception("abort on shutdown failed")
        try:
            self.devices.disconnect_all()
        except Exception:
            log.exception("disconnect failed")
        self.bridge.close()
        self.store.close()


def is_active(state: RunnerState | None) -> bool:
    return state is not None and state.phase not in (Phase.IDLE, Phase.FINISHED, Phase.ABORTED)
