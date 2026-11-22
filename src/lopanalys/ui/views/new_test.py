"""Nytt test: the wizard (athlete -> test type -> protocol -> camera -> devices -> check -> start).

The sport is chosen first in the step *Testtyp* (running, cycling, SkiErg). It decides
which tests there are (``KINDS_BY_SPORT``), the protocols and their unit (km/h or watt), and which
ergometer the device step connects: the treadmill, a trainer or a Concept2 PM5. The threshold tests
of cycling and SkiErg are never filmed: bike fit and double poling technique are tests of their own
(``TECHNIQUE_KINDS``: good tests rather than many half-good ones). They are
always filmed, with views of their own: bike fit from the side and, with a second camera, from the
front; double poling from the side.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
                               QHeaderView, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from lopanalys.assessments.protocol import Protocol, gait_compatible
from lopanalys.core import simulated
from lopanalys.core.i18n import t
from lopanalys.core.models import Athlete
from lopanalys.core.sport import Sport, profile
from lopanalys.devices.base import DeviceInfo
from lopanalys.ui import demo, services, theme
from lopanalys.ui.bridge import run_async
from lopanalys.ui.context import CAMERA_OPTIONAL_KINDS, GAIT_KINDS, TECHNIQUE_KINDS, AppContext, TestPlan
from lopanalys.ui.fmt import clock, num, pace
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.views.athletes import AthleteRow
from lopanalys.ui.widgets.common import (Card, ChoiceCard, PageHeader, StatusPill, Stepper, button, clear_layout, field,
                                         label, page, scrolled)
from lopanalys.ui.widgets.video import VideoView, pose_in_guide

log = logging.getLogger(__name__)

KINDS = [("gait", "run", "ui.kind.gait"), ("lactate", "droplet", "ui.kind.lactate"),
         ("max_hr", "heart", "ui.kind.max_hr"), ("combined", "activity", "ui.kind.combined"),
         ("max_hr_gait", "run", "ui.kind.max_hr_gait"), ("hr_zones", "gauge", "ui.kind.hr_zones")]
# The tests of each sport: (kind, icon, text key). In cycling and SkiErg only the technique test films.
KINDS_BY_SPORT = {
    "run": KINDS,
    "bike": [("lactate", "droplet", "ui.kind.bike.lactate"), ("max_hr", "heart", "ui.kind.bike.ramp"),
             ("ftp", "gauge", "ui.kind.bike.ftp"), ("bikefit", "camera", "ui.kind.bike.bikefit")],
    "skierg": [("lactate", "droplet", "ui.kind.skierg.lactate"), ("max_hr", "heart", "ui.kind.skierg.ramp"),
               ("ski", "camera", "ui.kind.skierg.ski")],
}
LIBRARY_KIND = {"combined": "lactate", "max_hr_gait": "max_hr"}   # the protocols the filmed kinds are built on
CAMERA_STEP = 3   # index of the camera page in the stack: only shown for tests that film
MODES = [("ftms", "bluetooth"), ("qz", "refresh"), ("manual", "edit"), ("sim", "activity")]
MODE_ICONS = dict(MODES, pm5="bluetooth")
BLUETOOTH_MODES = ("ftms", "pm5")     # the modes that scan for a device and connect to it
WEIGHT_SPORTS = ("bike",)             # W/kg needs the body weight: asked for when it is missing
VIEWS = [("side_left", "ui.view.side_left"), ("side_right", "ui.view.side_right"), ("rear", "ui.view.rear"),
         ("front", "ui.view.front")]
# The views each kind can be filmed from: (first camera, second camera). Running: from the side or
# behind; bike fit: the side, and the front for the knees' track; double poling: the side only.
KIND_VIEWS = {"bikefit": (("side_left", "side_right"), ("front",)), "ski": (("side_left", "side_right"), ())}
RUN_VIEWS = (("side_left", "side_right", "rear"), ("rear", "side_left", "side_right"))   # the second: behind first
TECH_BUILDERS = ("bike_fit", "skierg_technique")
MIN_TECH_FPS = 60       # below this the analysis marks bike fit and double poling uncertain (pose.technique)


def choice(title: str, desc: str, icon: str) -> ChoiceCard:
    """A big checkable card with an icon, a title and a description line."""
    return ChoiceCard(title, desc, icon)


class PreviewGrabber:
    """Reads frames from a FrameSource on a thread and keeps the newest (camera preview).

    ``stop()`` never blocks the GUI thread: the source is closed on a helper thread, and the camera
    lease in ``services.make_source`` makes the next preview or the live view wait until it is closed
    (a GoPro still starting up would otherwise stop the next source's webcam mode).
    ``hand_over()`` instead gives the open (or still opening) source to the next page, so the camera
    does not have to start again. ``state`` is "starting", "running", "failed" or "stopped"; every
    step of the start is logged with its time.
    """

    def __init__(self, info: Any) -> None:
        self.info = info
        self.key = getattr(info, "id", None)          # which camera this is (compared, never logged)
        self.id = services.safe_source_id(info)       # for the log: an address without its password
        self.frame: Any = None
        self.error = ""
        self.state = "starting"
        self._src: Any = None
        self._stop = threading.Event()
        self._handed = threading.Event()
        self._t0 = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ui-preview")
        self._thread.start()

    def _since(self) -> float:
        return time.perf_counter() - self._t0

    def _run(self) -> None:
        try:
            self._src = services.make_source(self.info)
            log.info("preview of %s: source ready in %.2f s", self.id, self._since())
            if self._stop.is_set():
                return
            self._src.open()
            log.info("preview of %s: camera open %.2f s after start", self.id, self._since())
            if self._stop.is_set():
                return
            for fr in self._src.frames():
                if self._stop.is_set():
                    break
                if self.frame is None:
                    log.info("preview of %s: first frame %.2f s after start", self.id, self._since())
                    self.state = "running"
                self.frame = fr.image
        except Exception as e:
            if self._handed.is_set():
                log.info("preview of %s: start ended after the hand-over (%.1f s): %s", self.id, self._since(), e)
            elif not self._stop.is_set():
                log.exception("preview of %s failed after %.1f s", self.id, self._since())
                self.error = str(e) or type(e).__name__
                self.state = "failed"
            else:
                # The real reason is logged too: "stopped while starting" alone hid it.
                log.info("preview of %s stopped while starting (%.1f s): %s", self.id, self._since(), e)
        finally:
            if self.state != "failed":
                self.state = "stopped" if self._stop.is_set() else self.state
            if not self._handed.is_set():
                self._close_src()

    def _close_src(self) -> None:
        try:
            if self._src is not None:
                self._src.close()
        except Exception:
            log.debug("closing the preview source failed", exc_info=True)

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    def latest(self) -> Any:
        return self.frame

    def stop(self) -> None:
        if self._handed.is_set():
            return
        self._stop.set()
        threading.Thread(target=self._close_src, daemon=True, name="ui-preview-close").start()

    def hand_over(self) -> Any | None:
        """Stop showing frames and return the source (open or opening) instead of closing it.
        None (and a normal stop) when there is nothing worth handing over."""
        src = self._src
        if src is None or self.state == "failed" or self._stop.is_set() or getattr(src, "closed", False):
            self.stop()
            return None
        self._handed.set()
        self._stop.set()
        log.info("preview of %s: handing the source over (%s, %.1f s after start)", self.id, self.state, self._since())
        return src

    def join(self, timeout: float | None = None) -> bool:
        """Waits for the preview thread (tests; not on the GUI thread)."""
        self._thread.join(timeout)
        return not self._thread.is_alive()


class NewTestView(QWidget):
    """The wizard. Emits ``launch(LiveController)`` when the coach presses Start."""

    launch = Signal(object)
    want_new_athlete = Signal()

    STEPS = ["ui.wiz.athlete", "ui.wiz.kind", "ui.wiz.protocol", "ui.wiz.camera", "ui.wiz.devices", "ui.wiz.check"]

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.athlete: Athlete | None = None
        self.kind = "lactate"
        self.sport = services.sports()[0][0]     # the test's sport; running is the only one so far
        self.protocol: Protocol | None = None
        self.camera: Any = None
        self.view = str(ctx.setting("camera.view") or "side_left")
        self.camera2: Any = None          # the second camera of a test with two views
        self.view2 = str(ctx.setting("camera.view2") or "rear")
        self.preview2: PreviewGrabber | None = None
        self._kinds = list(KINDS)
        self.modes = list(MODES)
        self.mode = self._default_mode()
        self.tm_device: DeviceInfo | None = None
        self.hr_device: Any = None
        self.tm_connected = False
        self.hr_connected = False
        self._tm_busy = False     # a treadmill connect is running: no second one
        self._hr_busy = False
        self.preview: PreviewGrabber | None = None
        self.ctl: LiveController | None = None
        self._library: list[Protocol] = []
        self._sources: list[Any] = []
        self._cam2_touched = False        # the coach chose the second camera (or "none") in this wizard

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        w, v = page()
        outer.addWidget(w)
        self.header = PageHeader(t("ui.nav.new_test"), t("ui.wiz.subtitle"))
        v.addWidget(self.header)
        self.stepper = Stepper([t(s) for s in self.STEPS])   # _sync_steps() leaves out the steps that do not apply
        v.addWidget(self.stepper)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        nav = QHBoxLayout()
        self.back = button(t("ui.common.back"), "chevron-left", kind="ghost")
        self.back.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.next = button(t("ui.common.next"), "chevron-right", kind="primary", size="big")
        self.next.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.next.clicked.connect(self._next)
        self.hint = label("", "Hint", wrap=True)   # may be two lines (the technique rule)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        nav.addWidget(self.back)
        nav.addStretch(1)
        nav.addWidget(self.hint, 4)
        nav.addWidget(self.next)
        v.addLayout(nav)
        for build in (self._build_athlete, self._build_kind, self._build_protocol, self._build_camera,
                      self._build_devices, self._build_check):
            pg = build()
            # The protocol form has wrapped hints; on a small screen it scrolls instead of squeezing.
            self.stack.addWidget(scrolled(pg) if build == self._build_protocol else pg)
        from PySide6.QtCore import QTimer
        self._framing_timer = QTimer(self)
        self._framing_timer.timeout.connect(self._check_framing)
        self._framing_label: Any = None
        ctx.athletes_changed.connect(self._fill_athletes)
        ctx.bridge.device_status.connect(self._on_device_status)
        self.go(0)

    # ================================================================== steps
    def _build_athlete(self) -> QWidget:
        c = Card(t("ui.wiz.athlete_title"), t("ui.wiz.athlete_sub"))
        row = QHBoxLayout()
        self.a_search = QLineEdit()
        self.a_search.setPlaceholderText(t("ui.athletes.search"))
        self.a_search.addAction(theme.icon("search", "muted"), QLineEdit.ActionPosition.LeadingPosition)
        self.a_search.textChanged.connect(self._fill_athletes)
        row.addWidget(self.a_search, 1)
        nb = button(t("ui.athletes.add"), "plus")
        nb.clicked.connect(self.want_new_athlete)
        row.addWidget(nb)
        c.body.addLayout(row)
        self.a_list = QListWidget()
        self.a_list.currentItemChanged.connect(self._athlete_chosen)
        self.a_list.itemDoubleClicked.connect(lambda _: self._next())
        c.body.addWidget(self.a_list, 1)
        self._fill_athletes()
        return c

    def _fill_athletes(self, *_: Any) -> None:
        self.a_list.blockSignals(True)
        self.a_list.clear()
        try:
            rows = self.ctx.store.athletes(self.a_search.text())
        except Exception:
            rows = []
        for a in rows:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, a)
            it.setSizeHint(QSize(300, 62))
            self.a_list.addItem(it)
            self.a_list.setItemWidget(it, AthleteRow(a))
            if self.athlete and a.id == self.athlete.id:
                self.athlete = a        # the saved row: a consent saved while the wizard is open counts
                self.a_list.setCurrentItem(it)
        self.a_list.blockSignals(False)
        self._update_nav()

    def _athlete_chosen(self, it: QListWidgetItem | None, *_: Any) -> None:
        self.athlete = it.data(Qt.ItemDataRole.UserRole) if it else None
        self._update_nav()

    def preselect(self, a: Athlete) -> None:
        """Start the wizard for this athlete (from Idrottare)."""
        self.reset()
        self.athlete = a
        self._fill_athletes()
        self.go(1)

    def _default_mode(self) -> str:
        """The device mode a sport starts with: the setting for the treadmill, Bluetooth for the
        others. The simulator is never the default in a real test."""
        if self.ctx.demo:
            return "sim"
        first = profile(self.sport).load_sources[0]
        if self.sport != "run":
            return first
        mode = str(self.ctx.setting("devices.treadmill_mode") or first)
        return mode if mode in ("ftms", "qz", "manual") else first

    def _build_kind(self) -> QWidget:
        c = Card(t("ui.wiz.kind_title"), t("ui.wiz.kind_sub"))
        self.kind_sub = c.findChildren(QLabel)[1]
        # The sport is chosen first in this step. With one sport to choose from there is no choice
        # to show, so the row appears with the second sport.
        self.sport_box = QComboBox()
        for value, text in services.sports():
            self.sport_box.addItem(text, value)
        self.sport_box.currentIndexChanged.connect(self._sport_chosen)
        self.sport_field = field(t("ui.wiz.sport"), self.sport_box)
        self.sport_field.setVisible(self.sport_box.count() > 1)
        c.body.addWidget(self.sport_field)
        self.kind_grid = QGridLayout()
        self.kind_grid.setSpacing(14)
        self.kind_group = QButtonGroup(self)
        self.kind_group.idClicked.connect(self._kind_chosen)
        self._fill_kinds()
        c.body.addLayout(self.kind_grid)
        c.body.addStretch(1)
        return c

    def _fill_kinds(self) -> None:
        """The test cards of the chosen sport."""
        self._kinds = list(KINDS_BY_SPORT.get(self.sport, KINDS))
        self.kind_sub.setText(t("ui.wiz.kind_sub" if self.sport == "run" else f"ui.wiz.kind_sub.{self.sport}"))
        if self.kind not in [k for k, _, _ in self._kinds]:
            self.kind = "lactate"
        for b in self.kind_group.buttons():
            self.kind_group.removeButton(b)
            b.setParent(None)
            b.deleteLater()
        for i, (k, ic, key) in enumerate(self._kinds):
            b = choice(t(key), t(key + ".desc"), ic)
            b.setMinimumHeight(130)
            self.kind_group.addButton(b, i)
            self.kind_grid.addWidget(b, i // 2, i % 2)
            if k == self.kind:
                b.setChecked(True)

    def _sport_chosen(self, *_: Any) -> None:
        sport = self.sport_box.currentData() or self.sport
        if sport == self.sport:
            return
        self.sport = sport
        self._fill_kinds()
        if hasattr(self, "film_chk"):
            self.film_chk.setChecked(False)
        self._fill_library()
        self._fill_modes()          # another ergometer: the connected one is let go
        self._sync_steps()
        self._update_nav()

    def set_sport(self, sport: str) -> None:
        """Choose the sport as the coach does in the list."""
        i = self.sport_box.findData(sport)
        if i >= 0:
            self.sport_box.setCurrentIndex(i)

    def _kind_chosen(self, i: int) -> None:
        self.kind = self._kinds[i][0]
        self._fill_library()
        self._sync_steps()
        self._update_nav()

    def _power(self) -> bool:
        """True when the chosen sport's load is watt (cycling, SkiErg)."""
        return profile(self.sport).load_quantity == "power"

    # ---------------------------------------------------------------- body weight for W/kg
    def needs_weight(self) -> bool:
        return self.sport in WEIGHT_SPORTS and self.athlete is not None and not self.athlete.weight_kg

    def ask_weight(self) -> float | None:
        """Ask for today's body weight in kg (None: the coach closed the box)."""
        from PySide6.QtWidgets import QInputDialog
        v, ok = QInputDialog.getDouble(self, t("ui.wiz.weight_title"), t("ui.wiz.weight_ask"), 70.0, 30.0, 250.0, 1)
        return float(v) if ok else None

    def _ensure_weight(self) -> bool:
        """W/kg needs the weight: ask once when it is missing, and save it on the athlete."""
        if not self.needs_weight():
            return True
        v = self.ask_weight()
        if not v or self.athlete is None:
            return False
        self.athlete.weight_kg = round(v, 1)
        try:
            self.ctx.store.save_athlete(self.athlete)
            self.ctx.athletes_changed.emit()
        except Exception:
            log.exception("saving the weight failed")
        return True

    def films(self) -> bool:
        """True when the chosen test films the runner: the technique test and the combinations, and
        Puls- och fartzoner when the coach ticked the box. Only then is there a camera step, a
        preview and a frame to place the athlete in (a max HR test or a lactate test
        without a combination has no camera step)."""
        if self.kind in TECHNIQUE_KINDS:     # bike fit and double poling: always filmed
            return True
        if self.sport != "run":      # the threshold tests in watt are never filmed
            return False
        if self.kind in CAMERA_OPTIONAL_KINDS:
            return hasattr(self, "film_chk") and self.film_chk.isChecked()
        return self.kind in GAIT_KINDS

    def steps(self) -> list[int]:
        """The pages of the wizard that apply to the chosen test, as indexes of the stack."""
        return [i for i in range(len(self.STEPS)) if i != CAMERA_STEP or self.films()]

    def _step_name(self, i: int) -> str:
        """The name of a step in the row at the top; the device step is named after the sport's ergometer."""
        if self.STEPS[i] == "ui.wiz.devices" and self.sport != "run":
            return t(f"ui.wiz.devices.{self.sport}")
        return t(self.STEPS[i])

    def _sync_steps(self) -> None:
        """The row at the top shows only the steps that apply, and a test that is not filmed has no camera."""
        if not hasattr(self, "stepper"):
            return
        self._sync_views()
        if not self.films():
            self.camera = None
        active = self.steps()
        self.stepper.steps = [self._step_name(i) for i in active]
        cur = self.stack.currentIndex() if hasattr(self, "stack") else 0
        self.stepper.set_current(active.index(cur) if cur in active else 0)

    def _film_toggled(self, *_: Any) -> None:
        self._sync_steps()
        self._rebuild_protocol()

    def _build_protocol(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(18)
        c = Card(t("ui.wiz.protocol_title"), t("ui.wiz.protocol_sub"))
        self.lib = QComboBox()
        self.lib.currentIndexChanged.connect(self._library_chosen)
        c.body.addWidget(field(t("ui.wiz.library"), self.lib))
        g = QGridLayout()
        g.setSpacing(14)
        self.p_start = self._dspin(3, 25, 0.5, " km/h")
        self.p_step = self._dspin(0, 5, 0.1, " km/h")
        self.p_n = QSpinBox()
        self.p_n.setRange(1, 30)
        self.p_n.setSuffix(" " + t("ui.wiz.stages_unit"))
        self.p_len = QSpinBox()
        self.p_len.setRange(10, 1200)
        self.p_len.setSingleStep(30)
        self.p_len.setSuffix(" s")
        self.p_incline = self._dspin(0, 15, 0.5, " %")
        self.p_pause = QSpinBox()
        self.p_pause.setRange(0, 300)
        self.p_pause.setSingleStep(5)
        self.p_pause.setSuffix(" s")
        self.p_warm = QSpinBox()
        self.p_warm.setRange(0, 30)
        self.p_warm.setSuffix(" min")
        self.step_field = field(t("ui.wiz.step"), self.p_step)
        self.n_field = field(t("ui.wiz.n_stages"), self.p_n)
        self.start_field = field(t("ui.wiz.start_speed"), self.p_start)
        self.len_field = field(t("ui.wiz.stage_len"), self.p_len)
        self.incline_field = field(t("ui.wiz.incline"), self.p_incline)
        g.addWidget(self.start_field, 0, 0)
        g.addWidget(self.step_field, 0, 1)
        g.addWidget(self.n_field, 1, 0)
        g.addWidget(self.len_field, 1, 1)
        g.addWidget(self.incline_field, 2, 0)
        self.pause_field = field(t("ui.wiz.pause"), self.p_pause, t("ui.wiz.pause_hint"))
        g.addWidget(self.pause_field, 2, 1)
        self.warm_field = field(t("ui.wiz.warmup"), self.p_warm, t("ui.wiz.warmup_hint"))
        g.addWidget(self.warm_field, 3, 0)
        # Puls- och fartzoner: the technique is filmed only when the coach asks for it
        self.film_chk = QCheckBox(t("ui.wiz.film"))
        self.film_chk.toggled.connect(self._film_toggled)
        self.film_field = field(t("ui.wiz.film_title"), self.film_chk, t("ui.wiz.film_hint"))
        g.addWidget(self.film_field, 3, 1)
        for i in range(g.count()):
            g.itemAt(i).setAlignment(Qt.AlignmentFlag.AlignTop)
        c.body.addLayout(g)
        self._build_ramp_extras(c.body)
        c.body.addStretch(1)
        for w in (self.p_start, self.p_step, self.p_n, self.p_len, self.p_incline, self.p_pause, self.p_warm):
            w.valueChanged.connect(self._rebuild_protocol)
        h.addWidget(c, 5)
        pc = Card(t("ui.wiz.preview"))
        self.p_table = QTableWidget(0, 5)
        self.p_table.setHorizontalHeaderLabels([t("ui.wiz.col.stage"), "km/h", t("ui.wiz.col.incline"),
                                                t("ui.wiz.col.pace"), t("ui.wiz.col.time")])
        self.p_table.verticalHeader().hide()
        self.p_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.p_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.p_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.p_table.setShowGrid(False)
        self.p_total = label("", "Hint")
        pc.body.addWidget(self.p_table, 1)
        pc.body.addWidget(self.p_total)
        h.addWidget(pc, 4)
        self._fill_library()
        return wrap

    @staticmethod
    def _dspin(lo: float, hi: float, step: float, suffix: str) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(1)
        s.setSuffix(suffix)
        return s

    def _units(self) -> None:
        """The fields and the table in the unit of the sport: km/h with incline and min/km, or watt
        (with the pace per 500 m on a SkiErg)."""
        power = self._power()
        prof = profile(self.sport)
        for w in (self.p_start, self.p_step):
            w.blockSignals(True)
        self.p_start.setDecimals(0 if power else 1)
        self.p_start.setRange(*((20, 1500) if power else (3, 25)))
        self.p_start.setSingleStep(5 if power else 0.5)
        self.p_start.setSuffix(f" {prof.load_unit}")
        self.p_step.setDecimals(0 if power else 1)
        self.p_step.setRange(*((0, 200) if power else (0, 5)))
        self.p_step.setSingleStep(5 if power else 0.1)
        self.p_len.setRange(10, 3600 if power else 1200)
        for w in (self.p_start, self.p_step):
            w.blockSignals(False)
        self.incline_field.setVisible(prof.has_incline)
        self.p_table.setHorizontalHeaderLabels([t("ui.wiz.col.stage"), prof.load_unit, t("ui.wiz.col.incline"),
                                                t("ui.wiz.col.pace") if not power else (prof.pace_unit or ""),
                                                t("ui.wiz.col.time")])
        # the hint under the pause: nobody runs on a trainer or a SkiErg
        self.pause_field.findChildren(QLabel)[-1].setText(t("ui.wiz.pause_hint_power" if power else "ui.wiz.pause_hint"))
        self.p_table.setColumnHidden(2, not prof.has_incline)
        self.p_table.setColumnHidden(3, prof.pace_unit is None)

    def _fill_library(self) -> None:
        if not hasattr(self, "lib"):
            return
        self._units()
        want = LIBRARY_KIND.get(self.kind, self.kind)
        self._lib_for = (self.kind, self.athlete.id if self.athlete else None, self.sport)
        try:
            if self.kind == "hr_zones":   # from the athlete's earlier test first, then per level
                self._library = services.hr_zones_protocols(self.ctx.store, self.athlete)
            else:
                self._library = [p for p in services.default_protocols(self.sport) if p.kind == want]
        except Exception:
            log.exception("protocol library failed")
            self._library = []
        self.lib.blockSignals(True)
        self.lib.clear()
        for p in self._library:
            self.lib.addItem(p.name)
        self.lib.blockSignals(False)
        if self._library:
            self._library_chosen(0)
        else:
            self._rebuild_protocol()

    def _ramp_by_incline(self) -> bool:
        base = getattr(self, "_base", None)
        return bool(base and getattr(base, "builder", "") == "max_hr_ramp"
                    and (base.params or {}).get("mode", "incline") == "incline")

    def _library_chosen(self, i: int) -> None:
        if i < 0 or i >= len(self._library):
            return
        p = self._library[i]
        self._base = p
        pr = dict(getattr(p, "params", {}) or {})
        st = [s for s in p.stages if not s.is_warmup] or p.stages
        warm = sum(s.duration_s for s in p.stages if s.is_warmup)
        incline_ramp = self._ramp_by_incline()
        widgets = (self.p_start, self.p_step, self.p_n, self.p_len, self.p_incline, self.p_pause, self.p_warm)
        for w in widgets:
            w.blockSignals(True)
        power = self._power()
        ftp = p.builder == "bike_ftp_20"
        tech = p.builder in TECH_BUILDERS
        self.p_step.setSuffix(" %" if incline_ramp else f" {profile(self.sport).load_unit}")
        self.step_field.findChild(QLabel).setText(  # the field label
            (t("ui.wiz.step_incline") if incline_ramp else t("ui.wiz.step")).upper())
        self.start_field.findChild(QLabel).setText(
            t("ui.wiz.ftp_target" if ftp else "ui.wiz.work_power" if tech else
              "ui.wiz.start_power" if power else "ui.wiz.start_speed").upper())
        self.len_field.findChild(QLabel).setText(
            t("ui.wiz.ftp_len" if ftp else "ui.wiz.film_len" if tech else "ui.wiz.stage_len").upper())
        self.p_start.setValue(st[0].speed_kmh)
        if power:
            step = pr.get("step_w", p.increment_load or (st[1].load - st[0].load if len(st) > 1 else 0.0))
        elif incline_ramp:
            step = pr.get("incline_step_pct", p.increment_incline_pct or 1.0)
        else:
            step = pr.get("step_kmh", pr.get("speed_step_kmh", p.increment_speed_kmh or
                                            (st[1].speed_kmh - st[0].speed_kmh if len(st) > 1 else 1.0)))
        self.p_step.setValue(float(step))
        self.p_n.setValue(int(pr["stages"]) if p.builder == "hr_zones" else len(st))
        self.p_len.setValue(int(st[0].duration_s))
        self.p_incline.setValue(st[0].incline_pct)
        self.p_pause.setValue(int(p.lactate_pause_s))
        self.p_warm.setValue(int(round(warm / 60)))
        for w in widgets:
            w.blockSignals(False)
        self._load_sprint(p)
        self._rebuild_protocol()

    def _changes(self) -> dict[str, Any]:
        """The editor values as builder parameters for the chosen protocol."""
        start, step, n = self.p_start.value(), self.p_step.value(), self.p_n.value()
        stage_s, inc, pause = float(self.p_len.value()), self.p_incline.value(), float(self.p_pause.value())
        warm = float(self.p_warm.value() * 60)
        b = getattr(getattr(self, "_base", None), "builder", "")
        if b in ("bike_lactate_step", "skierg_lactate_step"):
            return dict(start_w=start, step_w=step, stages=n, stage_s=stage_s, pause_s=pause, warmup_s=warm)
        if b in ("bike_ramp", "skierg_ramp"):
            return dict(start_w=start, step_w=step, step_s=stage_s, warmup_s=warm)
        if b == "bike_ftp_20":
            return dict(target_w=start, effort_s=stage_s, warmup_s=warm)
        if b in TECH_BUILDERS:
            return dict(work_w=start, film_s=stage_s, warmup_s=warm)
        if b in ("lactate_step", "lactate_to_max"):
            return dict(start_kmh=start, step_kmh=step, stages=n, stage_s=stage_s, incline_pct=inc,
                        pause_s=pause, warmup_s=warm)
        if b == "max_hr_ramp":
            d: dict[str, Any] = dict(speed_kmh=start, step_s=stage_s, start_incline_pct=inc, warmup_s=warm)
            d["incline_step_pct" if self._ramp_by_incline() else "speed_step_kmh"] = step
            d.update(self._sprint_params())
            return d
        if b == "gait_check":
            return dict(speeds_kmh=[round(start + i * step, 1) for i in range(n)], stage_s=stage_s,
                        incline_pct=inc, warmup_s=warm)
        if b == "hr_zones":
            return dict(start_kmh=start, step_kmh=step, stages=n, stage_s=stage_s, incline_pct=inc, warmup_s=warm)
        return {}

    def _rebuild_protocol(self, *_: Any) -> None:
        k = self.kind
        base = getattr(self, "_base", None)
        b = getattr(base, "builder", "")
        self.pause_field.setVisible(k in ("lactate", "combined"))
        self.film_field.setVisible(k in CAMERA_OPTIONAL_KINDS and self.sport == "run")
        self.step_field.setVisible(b not in ("bike_ftp_20",) + TECH_BUILDERS)
        # the resting lactate value is only mentioned for the tests that take samples
        self.warm_field.findChildren(QLabel)[-1].setText(
            t("ui.wiz.warmup_hint") if k in ("lactate", "combined") else t("ui.wiz.warmup_hint_plain"))
        self.n_field.setVisible(b not in ("max_hr_ramp", "bike_ramp", "skierg_ramp", "bike_ftp_20") + TECH_BUILDERS)
        p: Protocol | None = None
        try:
            if base is not None and b:
                p = services.rebuild_protocol(base, **self._changes())
            if p is None and self._power():
                raise ValueError(t("ui.wiz.need_protocol"))
            if p is None:
                start, step, n = self.p_start.value(), self.p_step.value(), self.p_n.value()
                stage_s, inc, pause = float(self.p_len.value()), self.p_incline.value(), float(self.p_pause.value())
                if k in ("max_hr", "max_hr_gait"):
                    p = services.max_hr_ramp(start, step, stage_s, inc)
                elif k == "gait":
                    p = services.gait_check([round(start + i * step, 1) for i in range(n)], stage_s)
                else:
                    p = services.lactate_step(start, step, n, stage_s, inc, pause)
                if base is not None:
                    p.name = base.name
        except Exception as e:
            log.exception("protocol build failed")
            self.hint.setText(str(e))
            return
        if self.films() and k != "gait" and k not in TECHNIQUE_KINDS:   # filmed: gait values per stage, the film analysed after
            p.stages = [dataclasses.replace(s, record_gait=True) for s in p.stages]
            if k != "hr_zones":
                p.name = f"{t('ui.kind.' + k)}: {p.name}"
        self.protocol = p
        self.p_table.setRowCount(len(p.stages))
        total = 0.0
        for r, s in enumerate(p.stages):
            load_text = num(s.speed_kmh, profile(self.sport).load_decimals)
            if s.free:
                load_text = t("ui.wiz.free_stage", target=load_text)
            vals = [s.label or str(r + 1), load_text, f"{num(s.incline_pct)} %", pace(s.speed_kmh, self.sport),
                    clock(s.duration_s)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0 and s.sample_lactate:
                    it.setIcon(theme.icon("droplet", "watch"))
                if s.is_warmup:
                    it.setForeground(theme.color("muted"))
                self.p_table.setItem(r, c, it)
            total += s.duration_s + (p.lactate_pause_s if s.sample_lactate else 0)
            if p.builder == "hr_zones" and r == len(p.stages) - 1:   # the ramp's first stage
                self.p_table.item(r, 0).setIcon(theme.icon("heart", "fix"))
        self._update_nav()    # the technique rule: Next is locked, with the reason, while it is not met
        extra = t("ui.wiz.open_ended") if p.open_ended else ""
        self.p_total.setText(t("ui.wiz.total", time=clock(total)) + (f"  ·  {extra}" if extra else ""))
        self._show_ramp_plan(p)

    # ---------------------------------------------------- the max ramp: sprint and plan
    def _build_ramp_extras(self, body: Any) -> None:
        """The *Slutspurt* box and the line with the ramp's time to the belt's limits."""
        from PySide6.QtWidgets import QCheckBox
        self.sprint_box = QWidget()
        row = QHBoxLayout(self.sprint_box)
        row.setContentsMargins(0, 0, 0, 0)
        self.p_sprint = QCheckBox(t("ui.wiz.sprint"))
        self.p_sprint.setToolTip(t("ui.wiz.sprint_hint"))
        self.p_sprint_s = QSpinBox()
        self.p_sprint_s.setRange(10, 120)
        self.p_sprint_s.setSingleStep(5)
        self.p_sprint_s.setSuffix(" s")
        self.p_sprint_s.setValue(30)
        self.p_sprint_kmh = self._dspin(0.5, 5, 0.5, " km/h")
        self.p_sprint_kmh.setValue(2.0)
        row.addWidget(self.p_sprint)
        row.addWidget(field(t("ui.wiz.sprint_len"), self.p_sprint_s))
        row.addWidget(field(t("ui.wiz.sprint_kmh"), self.p_sprint_kmh))
        row.addStretch(1)
        body.addWidget(self.sprint_box)
        self.sprint_hint = label(t("ui.wiz.sprint_hint"), "Hint", wrap=True)
        body.addWidget(self.sprint_hint)
        for w in (self.p_sprint, self.p_sprint_s, self.p_sprint_kmh):
            (w.toggled if w is self.p_sprint else w.valueChanged).connect(self._rebuild_protocol)
        self.ramp_note = label("", "Hint", wrap=True)
        self.ramp_warn = label("", wrap=True)
        self.ramp_warn.setStyleSheet(f"color: {theme.TOKENS['watch']}; font-weight: 600;")
        self.ramp_use = button("", "activity")
        self.ramp_use.clicked.connect(self._use_suggested_speed)
        for w in (self.ramp_note, self.ramp_warn, self.ramp_use):
            body.addWidget(w)
            w.hide()
        self._suggested_kmh: float | None = None

    def _load_sprint(self, p: Protocol) -> None:
        on = p.builder == "max_hr_ramp"
        self.sprint_box.setVisible(on)
        self.sprint_hint.setVisible(on)
        for w in (self.p_sprint, self.p_sprint_s, self.p_sprint_kmh):
            w.blockSignals(True)
        self.p_sprint.setChecked(p.final_sprint_s > 0)
        if p.final_sprint_s > 0:
            self.p_sprint_s.setValue(int(p.final_sprint_s))
            self.p_sprint_kmh.setValue(p.final_sprint_kmh)
        for w in (self.p_sprint, self.p_sprint_s, self.p_sprint_kmh):
            w.blockSignals(False)

    def _sprint_params(self) -> dict[str, Any]:
        on = self.p_sprint.isChecked()
        self.p_sprint_s.setEnabled(on)
        self.p_sprint_kmh.setEnabled(on)
        return dict(final_sprint_s=float(self.p_sprint_s.value()) if on else 0.0,
                    final_sprint_kmh=self.p_sprint_kmh.value(), final_sprint_auto=on)

    def _show_ramp_plan(self, p: Protocol) -> None:
        """Time from step 1 to the belt's limits, the 8-12 min warning and a start speed from the last test."""
        if self.sport != "run":       # the plan is the treadmill's: limits in km/h and %, VO2 from running
            for w in (self.ramp_note, self.ramp_warn, self.ramp_use):
                w.hide()
            self._suggested_kmh = None
            return
        limits = services.treadmill_limits(getattr(self.ctx.devices, "treadmill", None), self.ctx.setting)
        a = self.athlete
        vo2 = services.previous_vo2max(self.ctx.store, a.id if a else None) if p.open_ended else None
        base = getattr(self, "_base", None)
        build = None
        if base is not None and base.builder == "max_hr_ramp":
            build = lambda v: services.rebuild_protocol(base, **{**self._changes(), "speed_kmh": v})  # noqa: E731
        prev = services.previous_max_ramp(self.ctx.store, a.id if a else None) if p.open_ended else None
        adv = services.ramp_advice(p, limits, vo2, build, prev) if p.open_ended else None
        due = services.max_test_due(self.ctx.store, a) if p.open_ended else ""
        note = " ".join(x for x in ((adv or {}).get("text", ""), due) if x)
        self.ramp_note.setText(note)
        self.ramp_note.setVisible(bool(note))
        warn = (adv or {}).get("warning", "")
        self.ramp_warn.setText(warn)
        self.ramp_warn.setVisible(bool(warn))
        v = (adv or {}).get("suggest_kmh")
        self._suggested_kmh = v
        show = bool(v) and abs(v - self.p_start.value()) > 1e-6
        self.ramp_use.setText(t("ui.wiz.ramp_suggest", kmh=num(v)) if v else "")
        self.ramp_use.setVisible(show)

    def _use_suggested_speed(self) -> None:
        if self._suggested_kmh:
            self.p_start.setValue(self._suggested_kmh)

    def _build_camera(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(18)
        c = Card(t("ui.wiz.camera_title"), t("ui.wiz.camera_sub"))
        top = QHBoxLayout()
        self.cam_status = StatusPill(t("ui.wiz.cam_searching"), "busy")
        top.addWidget(self.cam_status)
        top.addStretch(1)
        pb = button(t("ui.wiz.add_phone"), "camera", kind="ghost")
        pb.setToolTip(t("ui.wiz.add_phone_tip"))
        pb.clicked.connect(self._add_stream)
        top.addWidget(pb)
        fb = button(t("ui.wiz.open_file"), "folder", kind="ghost")
        fb.clicked.connect(self._open_file)
        top.addWidget(fb)
        rb = button(t("ui.common.search_again"), "refresh", kind="ghost")
        rb.clicked.connect(self._discover)
        top.addWidget(rb)
        c.body.addLayout(top)
        self.cam_list = QListWidget()
        self.cam_list.currentItemChanged.connect(self._camera_chosen)
        c.body.addWidget(self.cam_list, 1)
        sr = QHBoxLayout()                      # a saved phone or file: change or forget it
        sr.addStretch(1)
        self.cam_edit_btn = button(t("ui.wiz.cam_edit"), "edit", kind="ghost")
        self.cam_edit_btn.clicked.connect(self._edit_saved_camera)
        sr.addWidget(self.cam_edit_btn)
        self.cam_remove_btn = button(t("ui.wiz.cam_remove"), "trash", kind="ghost")
        self.cam_remove_btn.clicked.connect(self._remove_saved_camera)
        sr.addWidget(self.cam_remove_btn)
        c.body.addLayout(sr)
        self.cam_edit_btn.hide()
        self.cam_remove_btn.hide()
        self.cam_hint = label("", "Hint", wrap=True)   # what to do when a camera is connected but unusable
        self.cam_hint.hide()
        c.body.addWidget(self.cam_hint)
        hr = QHBoxLayout()
        self.cam_hint_retry = button(t("ui.common.search_again"), "refresh", kind="ghost")
        self.cam_hint_retry.clicked.connect(self._discover)
        self.cam_hint_retry.hide()
        hr.addWidget(self.cam_hint_retry)
        hr.addStretch(1)
        c.body.addLayout(hr)
        self.fps_hint = label("", "Hint", wrap=True)   # bike fit and double poling: the camera's frame rate is too low
        self.fps_hint.setStyleSheet(f"color: {theme.TOKENS['watch']};")
        self.fps_hint.hide()
        c.body.addWidget(self.fps_hint)
        c.body.addWidget(label(t("ui.wiz.view"), "SectionTitle"))
        vr = QHBoxLayout()
        self.view_group = QButtonGroup(self)
        for i, (k, key) in enumerate(VIEWS):
            b = button(t(key), kind="rpe")
            b.setCheckable(True)
            b.setMinimumHeight(44)
            self.view_group.addButton(b, i)
            vr.addWidget(b)
            if k == self.view:
                b.setChecked(True)
        self.view_group.idClicked.connect(self._view_chosen)
        c.body.addLayout(vr)
        self.view_hint = label(t("ui.wiz.view_hint"), "Hint", wrap=True)
        c.body.addWidget(self.view_hint)
        # The second camera: any other source, filming at the same time from its own side.
        self.cam2_title = label(t("ui.wiz.camera2"), "SectionTitle")
        c.body.addWidget(self.cam2_title)
        self.cam2_box = QComboBox()
        self.cam2_box.currentIndexChanged.connect(self._camera2_chosen)
        c.body.addWidget(self.cam2_box)
        r2 = QHBoxLayout()
        self.view2_group = QButtonGroup(self)
        for i, (k, key) in enumerate(VIEWS):
            b = button(t(key), kind="rpe")
            b.setCheckable(True)
            b.setMinimumHeight(36)
            self.view2_group.addButton(b, i)
            r2.addWidget(b)
            if k == self.view2:
                b.setChecked(True)
        self.view2_group.idClicked.connect(self._view2_chosen)
        c.body.addLayout(r2)
        self.cam2_hint = label(t("ui.wiz.camera2_hint"), "Hint", wrap=True)
        c.body.addWidget(self.cam2_hint)
        h.addWidget(c, 4)
        pc = Card(t("ui.wiz.camera_preview"))
        self.cam_view = VideoView()
        self.cam_view.placeholder = t("ui.wiz.choose_camera")
        pc.body.addWidget(self.cam_view, 3)
        self.cam2_view = VideoView()           # the second camera's preview, shown when one is chosen
        self.cam2_view.placeholder = t("ui.video.starting")
        self.cam2_view.hide()
        pc.body.addWidget(self.cam2_view, 2)
        # What the preview is doing while there is no picture yet: starting (spinner) or the error.
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._preview_status)
        h.addWidget(pc, 5)
        return wrap

    def _recent(self) -> list[str]:
        r = self.ctx.setting("camera.recent")
        return [x for x in r if isinstance(x, str)] if isinstance(r, list) else []

    def _open_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        fn, _ = QFileDialog.getOpenFileName(self, t("ui.wiz.open_file"), "",
                                            t("ui.wiz.video_files") + " (*.mp4 *.mov *.MP4 *.MOV *.avi *.mkv)")
        if not fn:
            return
        recent = [fn] + [x for x in self._recent() if x != fn]
        self.ctx.set_setting("camera.recent", recent[:8])
        self.camera = services.file_source_info(fn)
        self._discover()

    def _add_stream(self) -> None:
        """The phone as a camera: ask for its address (or search the network) and add it to the list.
        It becomes the second camera when a real camera is already chosen, otherwise the camera."""
        from lopanalys.ui.views.add_stream import AddStreamDialog
        dlg = AddStreamDialog(self)
        if not dlg.exec() or not dlg.url:
            return
        url = dlg.url
        self.ctx.set_setting("camera.recent", ([url] + [x for x in self._recent() if x != url])[:8])
        info = services.stream_source_info(url)
        if self.camera is not None and not demo.is_demo_source(self.camera) and self.camera.id != url:
            self.camera2 = info
        else:
            self.camera = info
        self._discover()

    def _saved_id(self, info: Any = None) -> str | None:
        """The chosen camera's entry in the saved cameras (``camera.recent``), or None."""
        info = self.camera if info is None else info
        sid = getattr(info, "id", None)
        return sid if isinstance(sid, str) and sid in self._recent() else None

    def _sync_saved_buttons(self, info: Any = None) -> None:
        sid = self._saved_id(info)
        self.cam_edit_btn.setVisible(sid is not None and services.looks_like_url(sid))
        self.cam_remove_btn.setVisible(sid is not None)

    def _replace_saved(self, old: str, new: str | None) -> None:
        """Change (``new``) or forget (None) a saved camera, and the remembered choices that pointed at it."""
        recent = [x for x in self._recent() if x != old or new is not None]
        recent = [new if x == old else x for x in recent]
        self.ctx.set_setting("camera.recent", list(dict.fromkeys(recent))[:8])
        for key in ("camera.default", "camera.second"):
            if self.ctx.setting(key) == old:
                self.ctx.set_setting(key, new or "")
        for attr in ("camera", "camera2"):
            if getattr(getattr(self, attr), "id", None) == old:
                setattr(self, attr, services.stream_source_info(new) if new else None)

    def _edit_saved_camera(self) -> None:
        """A saved phone: the dialog with its address and login filled in (e.g. a password added afterwards)."""
        from lopanalys.ui.views.add_stream import AddStreamDialog
        old = self._saved_id()
        if old is None:
            return
        dlg = AddStreamDialog(self, url=old)
        if not dlg.exec() or not dlg.url:
            return
        if dlg.url != old:
            self._stop_preview(first_only=True)
            self._replace_saved(old, dlg.url)
        self._discover()

    def _remove_saved_camera(self) -> None:
        old = self._saved_id()
        if old is None:
            return
        name = self.camera.label if self.camera is not None else old
        r = QMessageBox.question(self, t("ui.wiz.cam_remove"), t("ui.wiz.cam_remove_ask", name=name),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        self._stop_preview(first_only=True)
        self._replace_saved(old, None)
        self._discover()

    def _discover(self) -> None:
        self.cam_status.set(t("ui.wiz.cam_searching"), "busy")
        run_async(services.discover_sources, self._recent(), on_done=self._sources_found,
                  on_error=lambda m: self._sources_found([]))

    def _sources_found(self, found: list[Any]) -> None:
        found = list(found)
        if self.camera is not None and not demo.is_demo_source(self.camera) and \
                all(getattr(f, "id", None) != self.camera.id for f in found):
            found.append(self.camera)
        demo_info = demo.demo_info_for(self.kind)      # a runner, a cyclist or a skier
        if demo.is_demo_source(self.camera) and self.camera.id != demo_info.id:
            self.camera = None                         # another test type: the other demo athlete
        self._sources = found + [demo_info]
        self.cam_list.blockSignals(True)
        self.cam_list.clear()
        none = QListWidgetItem(theme.icon("x", "muted"), t("ui.wiz.no_camera"))
        none.setData(Qt.ItemDataRole.UserRole, None)
        self.cam_list.addItem(none)
        default = str(self.ctx.setting("camera.default") or "")
        pick = None
        for s in self._sources:
            kind = getattr(getattr(s, "kind", None), "value", "")
            ic = "camera" if kind != "file" or demo.is_demo_source(s) else "image"
            extra = []
            if getattr(s, "width", None):
                extra.append(f"{s.width}×{s.height}")
            if getattr(s, "fps", None):
                extra.append(f"{s.fps:.0f} fps")
            it = QListWidgetItem(theme.icon(ic, "accent"), s.label + (f"   ({', '.join(extra)})" if extra else ""))
            it.setData(Qt.ItemDataRole.UserRole, s)
            self.cam_list.addItem(it)
            if (self.camera is not None and s.id == self.camera.id) or (pick is None and s.id == default):
                pick = it
            if self.ctx.demo and demo.is_demo_source(s) and self.camera is None and pick is None:
                pick = it
        self.cam_list.blockSignals(False)
        real = len(self._sources) - 1
        self.cam_status.set(t("ui.wiz.cam_found", n=real) if real else t("ui.wiz.cam_none_found"),
                            "ok" if real else "watch")
        self._show_camera_hint(found)
        self.cam_list.setCurrentItem(pick or self.cam_list.item(0))
        self._fill_camera2()

    def _fill_camera2(self) -> None:
        """The choices for the second camera: every source but the first one, and the demo runner
        seen from behind. The remembered choice comes back when it is still there."""
        first = getattr(self.camera, "id", None)
        choices = [s for s in self._sources if not demo.is_demo_source(s) and s.id != first]
        demo2 = demo.demo_info_for(self.kind, second=True)
        if demo2 is not None:
            choices.append(demo2)
        want = getattr(self.camera2, "id", None)
        if want is None and not self._cam2_touched:
            want = str(self.ctx.setting("camera.second") or "") or None
            if want in demo.DEMO_IDS and not self.ctx.demo:
                want = None            # the demo athlete is never preselected in a real test
            if self.ctx.demo and self.kind == "bikefit" and demo2 is not None:
                want = demo2.id        # the demo bike fit shows both films
        box = self.cam2_box
        box.blockSignals(True)
        box.clear()
        box.addItem(t("ui.wiz.camera2_none"), None)
        pick = 0
        for s in choices:
            box.addItem(s.label, s)
            if first is not None and s.id == want:
                pick = box.count() - 1
        box.setCurrentIndex(pick)
        box.setEnabled(first is not None)
        box.blockSignals(False)
        self._camera2_chosen(pick, user=False)

    def _camera2_chosen(self, i: int, user: bool = True) -> None:
        chosen = self.cam2_box.itemData(i) if i >= 0 else None
        if user:
            self._cam2_touched = True
        same = self.preview2 is not None and self.preview2.running and \
            self.preview2.key == getattr(chosen, "id", None)
        self.camera2 = chosen
        for b in self.view2_group.buttons():
            b.setEnabled(chosen is not None)
        if same:
            return
        if self.preview2 is not None:
            self.preview2.stop()
            self.preview2 = None
        self.cam2_view.detach()
        self.cam2_view.set_image(None)
        self.cam2_view.setVisible(chosen is not None)
        if chosen is not None and self.stack.currentIndex() == 3:
            self.preview2 = PreviewGrabber(chosen)
            self.cam2_view.attach(self.preview2.latest, fps=15)
            self._preview_timer.start(150)
        self._show_fps_hint()
        self._update_nav()

    def _view2_chosen(self, i: int) -> None:
        self.view2 = VIEWS[i][0]

    def views_allowed(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The views of the first and the second camera for the chosen test (``KIND_VIEWS``)."""
        return KIND_VIEWS.get(self.kind, RUN_VIEWS)

    def _sync_views(self) -> None:
        """Only the views the test can use are shown; a view that does not fit is replaced."""
        if not hasattr(self, "view_group"):
            return
        first, second = self.views_allowed()
        if self.view not in first:
            self.view = first[0]
        if second and self.view2 not in second:
            self.view2 = second[0]
        for group, allowed, cur in ((self.view_group, first, self.view), (self.view2_group, second, self.view2)):
            for i, (k, _) in enumerate(VIEWS):
                b = group.button(i)
                b.setVisible(k in allowed)
                b.setChecked(k == cur)
        for w in (self.cam2_title, self.cam2_box, self.cam2_hint):
            w.setVisible(bool(second))
        self.view_hint.setText(t(f"ui.wiz.view_hint.{self.kind}" if self.kind in TECHNIQUE_KINDS else "ui.wiz.view_hint"))

    def _guide_view(self, view: str) -> str:
        """The framing box for a view: a cyclist from the side is wider than a runner."""
        return "bike_side" if self.kind == "bikefit" and view.startswith("side") else view

    def _show_fps_hint(self) -> None:
        """Bike fit and double poling need 60 frames a second: say so when the camera gives fewer."""
        cams = [c for c in (self.camera, self._second()) if c is not None and not demo.is_demo_source(c)]
        low = [c for c in cams if getattr(c, "fps", None) and float(c.fps) < MIN_TECH_FPS - 1]
        text = " ".join(t("ui.wiz.fps_low", name=c.label, fps=f"{float(c.fps):.0f}") for c in low)
        self.fps_hint.setText(text)
        self.fps_hint.setVisible(bool(text) and self.kind in TECHNIQUE_KINDS)

    def _show_camera_hint(self, found: list[Any]) -> None:
        """No GoPro in the list: say what to check. Windows bound to the wrong driver is said when it
        is seen; otherwise, when a GoPro was found here before or no camera at all was found, the
        camera itself (on, USB mode GoPro Connect, the cable, the USB icon). On the real test the log
        said "GoPro search: no USB network link": the camera was off or in the wrong USB mode.
        Not in the demo, which has no camera of its own."""
        hint = None
        gopro = any(getattr(getattr(s, "kind", None), "value", "") == "gopro_usb" for s in found)
        if gopro:
            self.ctx.set_setting("camera.gopro_seen", True)
        else:
            try:
                from lopanalys.camera.gopro import windows_driver_hint
                hint = windows_driver_hint()
            except Exception:  # noqa: BLE001
                hint = None
            live = [s for s in found if getattr(s, "live", True) and not demo.is_demo_source(s)
                    and getattr(getattr(s, "kind", None), "value", "") != "file"]
            if hint is None and not self.ctx.demo and (self.ctx.setting("camera.gopro_seen") or not live):
                hint = t("ui.wiz.gopro_check")
        self.cam_hint.setText(hint or "")
        self.cam_hint.setVisible(bool(hint))
        self.cam_hint_retry.setVisible(bool(hint))

    def _camera_chosen(self, it: QListWidgetItem | None, *_: Any) -> None:
        chosen = it.data(Qt.ItemDataRole.UserRole) if it else None
        self._sync_saved_buttons(chosen)
        if chosen is not None and self.preview is not None and self.preview.running and \
                self.preview.key == getattr(chosen, "id", None):
            # A new search found the camera that is already shown: keep its preview.
            self.camera = chosen
            self._update_nav()
            return
        self._stop_preview(first_only=True)
        services.release_parked(keep=chosen, keep2=self.camera2)   # other kept sources are not needed now
        self.camera = chosen
        if self._sources:
            self._fill_camera2()               # the first camera cannot also be the second
        self.cam_view.set_image(None)
        if self.camera is not None:
            self.preview = PreviewGrabber(self.camera)
            self.cam_view.attach(self.preview.latest, fps=20)
            self._preview_status()
            self._preview_timer.start(150)
        else:
            self.cam_view.detach()
            self.cam_view.set_busy(False)
            self.cam_view.placeholder = t("ui.wiz.choose_camera")
            self.cam_view.update()
        self._show_fps_hint()
        self._update_nav()

    def _preview_status(self) -> None:
        """Spinner and "Startar GoPron …" until the first frame, the error if the start failed."""
        pv2 = self.preview2
        waiting2 = False
        if pv2 is not None:
            waiting2 = pv2.state == "starting" and pv2.frame is None
            self.cam2_view.set_busy(waiting2)
            if pv2.state == "failed":
                self.cam2_view.placeholder = t("ui.wiz.preview_failed", detail=pv2.error)
                self.cam2_view.update()
            elif waiting2:
                self.cam2_view.placeholder = t("ui.video.starting")
        pv = self.preview
        if pv is None:
            if not waiting2:
                self._preview_timer.stop()
            return
        if pv.state == "starting" and pv.frame is None:
            gopro = getattr(getattr(pv.info, "kind", None), "value", "") == "gopro_usb"
            self.cam_view.placeholder = t("ui.wiz.preview_starting_gopro" if gopro else "ui.video.starting")
            self.cam_view.set_busy(True)
            return
        if not waiting2:
            self._preview_timer.stop()
        self.cam_view.set_busy(False)
        if pv.state == "failed":
            self.cam_view.placeholder = t("ui.wiz.preview_failed", detail=pv.error)
            self.cam_view.update()

    def _view_chosen(self, i: int) -> None:
        self.view = VIEWS[i][0]

    def _stop_preview(self, hand_over: bool = False, first_only: bool = False) -> None:
        """Stop the camera previews. With ``hand_over`` an open or starting source is kept for the
        check page (services.park_source) instead of being closed. ``first_only`` leaves the
        second camera's preview running (the coach only changed the first camera)."""
        self.cam_view.detach()
        self.cam_view.set_busy(False)
        if not first_only:
            self._preview_timer.stop()
            self.cam2_view.detach()
            self.cam2_view.set_busy(False)
        pairs = [("preview", self.camera)] + ([] if first_only else [("preview2", self.camera2)])
        for attr, cam in pairs:
            pv = getattr(self, attr)
            if pv is None:
                continue
            if hand_over and services.can_keep_source(cam):
                src = pv.hand_over()   # stops the preview itself when there is nothing to keep
                if src is not None:
                    services.park_source(cam, src)
            else:
                pv.stop()
            setattr(self, attr, None)

    def _build_devices(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(18)
        tm = self.tm_card = Card(t("ui.wiz.treadmill_title"), t("ui.wiz.treadmill_sub"))
        self.tm_sub = tm.findChildren(QLabel)[1]
        self.mode_grid = QGridLayout()
        self.mode_grid.setSpacing(10)
        self.mode_group = QButtonGroup(self)
        self.mode_group.idClicked.connect(self._mode_chosen)
        tm.body.addLayout(self.mode_grid)
        row = QHBoxLayout()
        self.tm_status = StatusPill(t("ui.dev.not_connected"), "idle")
        row.addWidget(self.tm_status)
        row.addStretch(1)
        self.tm_scan = button(t("ui.dev.scan"), "bluetooth")
        self.tm_scan.clicked.connect(self._scan_tm)
        row.addWidget(self.tm_scan)
        tm.body.addLayout(row)
        self.tm_list = QListWidget()
        self.tm_list.setMaximumHeight(140)
        tm.body.addWidget(self.tm_list)
        self.tm_connect = button(t("ui.dev.connect"), "check", kind="primary")
        self.tm_connect.clicked.connect(self._connect_tm)
        tm.body.addWidget(self.tm_connect)
        tm.body.addStretch(1)
        h.addWidget(tm, 1)

        hr = Card(t("ui.wiz.hr_title"), t("ui.wiz.hr_sub"))
        row = QHBoxLayout()
        self.hr_status = StatusPill(t("ui.dev.not_connected"), "idle")
        row.addWidget(self.hr_status)
        row.addStretch(1)
        self.hr_scan = button(t("ui.dev.scan"), "bluetooth")
        self.hr_scan.clicked.connect(self._scan_hr)
        row.addWidget(self.hr_scan)
        hr.body.addLayout(row)
        self.hr_list = QListWidget()
        hr.body.addWidget(self.hr_list, 1)
        self._fill_hr_list([])
        self.hr_connect = button(t("ui.dev.connect"), "heart", kind="primary")
        self.hr_connect.clicked.connect(self._connect_hr)
        hr.body.addWidget(self.hr_connect)
        self.hr_live = label("", "Hint")
        hr.body.addWidget(self.hr_live)
        h.addWidget(hr, 1)
        self.ctx.bridge.heart_rate.connect(self._hr_sample)
        self._fill_modes()
        return wrap

    def _mode_text(self, mode: str, desc: bool = False) -> str:
        """The name (or description) of a device mode in the words of the sport."""
        key = f"ui.mode.{mode}" if self.sport == "run" else f"ui.mode.{self.sport}.{mode}"
        return t(key + (".desc" if desc else ""))

    def _fill_modes(self) -> None:
        """The device modes of the chosen sport: the treadmill's, the trainer's or the SkiErg's."""
        if not hasattr(self, "mode_group"):
            return
        if self.tm_connected and not self._tm_busy:
            run_async(self.ctx.devices.disconnect_treadmill, on_done=lambda _: None,
                      on_error=lambda m: log.warning("ergometer disconnect failed: %s", m))
        self.tm_connected = False
        self.modes = [(m, MODE_ICONS.get(m, "activity")) for m in profile(self.sport).load_sources]
        for b in self.mode_group.buttons():
            self.mode_group.removeButton(b)
            b.setParent(None)
            b.deleteLater()
        default = self._default_mode()
        for i, (m, ic) in enumerate(self.modes):
            b = choice(self._mode_text(m), self._mode_text(m, desc=True), ic)
            self.mode_group.addButton(b, i)
            self.mode_grid.addWidget(b, i // 2, i % 2)
            if m == default:
                b.setChecked(True)
        self.tm_card.title.setText(t(f"ui.wiz.ergometer_title.{self.sport}"))
        self.tm_sub.setText(t(f"ui.wiz.ergometer_sub.{self.sport}"))
        self.mode = ""              # so that _mode_chosen sets the page up for the default mode
        self._mode_chosen([m for m, _ in self.modes].index(default))

    def _mode_chosen(self, i: int) -> None:
        if self._tm_busy:
            return
        if self.modes[i][0] == self.mode and self.tm_connected:
            # Clicking the mode that is already connected keeps the connection.
            self._update_nav()
            return
        if self.tm_connected:
            # Another mode replaces the connected treadmill: let it go now, off the GUI thread.
            run_async(self.ctx.devices.disconnect_treadmill, on_done=lambda _: None,
                      on_error=lambda m: log.warning("treadmill disconnect failed: %s", m))
        self.mode = self.modes[i][0]
        needs_scan = self.mode in BLUETOOTH_MODES
        self.tm_scan.setVisible(needs_scan)
        self.tm_list.setVisible(needs_scan)
        self.tm_list.clear()
        if needs_scan:
            self._add_remembered_tm([])     # the device used last time: Anslut works without a scan
        self.tm_device = None
        self.tm_connected = False
        self.tm_status.set(t("ui.dev.not_connected"), "idle")
        self.tm_connect.setText(t("ui.dev.connect") if needs_scan or self.mode == "qz" else t("ui.dev.use_mode"))
        self._update_nav()

    def _scan(self, fn: Callable[[], list[DeviceInfo]], pill: StatusPill, btn: Any, done: Callable[[list[Any]], None]) -> None:
        pill.set(t("ui.dev.scanning"), "busy")
        btn.setEnabled(False)

        def ok(found: list[Any]) -> None:
            btn.setEnabled(True)
            pill.set(t("ui.dev.found", n=len(found)) if found else t("ui.dev.none_found"), "ok" if found else "watch")
            done(found)

        def fail(msg: str) -> None:
            btn.setEnabled(True)
            pill.set(t("ui.dev.scan_failed"), "fix")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.bluetooth", detail=msg))
        run_async(fn, on_done=ok, on_error=fail)

    def _scan_tm(self) -> None:
        def show(found: list[Any]) -> None:
            self.tm_list.clear()
            for d in found:
                it = QListWidgetItem(theme.icon("bluetooth", "accent2"), self._dev_label(d))
                it.setData(Qt.ItemDataRole.UserRole, d)
                self.tm_list.addItem(it)
            self._add_remembered_tm(found)
            if self.tm_list.count():
                self.tm_list.setCurrentRow(0)
        sport = self.sport
        self._scan(lambda: self.ctx.devices.scan_ergometers(sport), self.tm_status, self.tm_scan, show)

    def _remembered_tm(self) -> DeviceInfo | None:
        """The Bluetooth ergometer of this sport used last time (the treadmill, the trainer, the SkiErg)."""
        kind = "treadmill" if self.sport == Sport.RUN.value else self.sport
        try:
            return self.ctx.devices.last_device(kind)
        except Exception:  # noqa: BLE001 - a broken setting: scan as before
            return None

    def _add_remembered_tm(self, found: list[Any]) -> None:
        """List the device used last time when the scan did not find it: a treadmill that leaves FTMS out
        of its advertisement until START is still one click away (2026-11-16)."""
        d = self._remembered_tm()
        if d is None or any(getattr(f, "address", "").lower() == d.address.lower() for f in found):
            return
        it = QListWidgetItem(theme.icon("bluetooth", "accent2"), t("ui.dev.last_used", name=d.name or d.address))
        it.setData(Qt.ItemDataRole.UserRole, d)
        self.tm_list.addItem(it)

    def _scan_hr(self) -> None:
        self._scan(self.ctx.devices.scan_heart_rate, self.hr_status, self.hr_scan, self._fill_hr_list)

    @staticmethod
    def _dev_label(d: Any) -> str:
        rssi = getattr(d, "rssi", None)
        return d.name + (f"   ({rssi} dBm)" if rssi is not None else "")

    def _fill_hr_list(self, found: list[Any]) -> None:
        self.hr_list.clear()
        for d in found:
            it = QListWidgetItem(theme.icon("heart", "fix"), self._dev_label(d))
            it.setData(Qt.ItemDataRole.UserRole, d)
            self.hr_list.addItem(it)
        none = QListWidgetItem(theme.icon("x", "muted"), t("ui.dev.no_hr"))
        none.setData(Qt.ItemDataRole.UserRole, None)
        self.hr_list.addItem(none)
        # The simulated strap last, marked "Övning", and preselected only in demo mode.
        sim = QListWidgetItem(theme.icon("activity", "muted"), t("ui.dev.sim_hr"))
        sim.setData(Qt.ItemDataRole.UserRole, "sim")
        sim.setForeground(theme.color("muted"))
        self.hr_list.addItem(sim)
        self.hr_list.setCurrentRow(0 if found else (self.hr_list.count() - 1 if self.ctx.demo else self.hr_list.count() - 2))

    def _set_tm_busy(self, busy: bool) -> None:
        """While a treadmill connect runs, the button, the scan and the mode cards cannot start another."""
        self._tm_busy = busy
        self.tm_connect.setEnabled(not busy)
        self.tm_scan.setEnabled(not busy)
        for b in self.mode_group.buttons():
            b.setEnabled(not busy)

    def _connect_tm(self) -> None:
        if self._tm_busy:
            return
        dev = None
        if self.mode in BLUETOOTH_MODES:
            it = self.tm_list.currentItem()
            if it is None:
                self.tm_status.set(t("ui.dev.pick_first"), "watch")
                return
            dev = it.data(Qt.ItemDataRole.UserRole)
        self.tm_status.set(t("ui.dev.connecting"), "busy")
        # The manager drops the old link first; that is not a lost connection.
        self.tm_connected = False
        self._set_tm_busy(True)
        self._update_nav()

        def ok(_: Any) -> None:
            self._set_tm_busy(False)
            self.tm_device = dev
            self.tm_connected = True
            key = "ui.dev.mode_chosen" if self.mode in ("manual", "sim") else "ui.dev.connected_mode"
            self.tm_status.set(t(key, mode=self._mode_text(self.mode)), "ok")
            self._update_nav()

        def fail(msg: str) -> None:
            self._set_tm_busy(False)
            self.tm_status.set(t("ui.dev.connect_failed"), "fix")
            self._update_nav()
            QMessageBox.warning(self, t("ui.app.name"),
                                t("ui.err.treadmill" if self.sport == "run" else "ui.err.ergometer", detail=msg))
        if self.sport == "run":
            run_async(self.ctx.devices.connect_treadmill, self.mode, dev, on_done=ok, on_error=fail)
        else:
            run_async(self.ctx.devices.connect_ergometer, self.sport, self.mode, dev, on_done=ok, on_error=fail)

    def _connect_hr(self) -> None:
        if self._hr_busy:
            return
        it = self.hr_list.currentItem()
        dev = it.data(Qt.ItemDataRole.UserRole) if it else None
        if dev is None:
            self.hr_device = None
            self.hr_connected = False
            self.hr_status.set(t("ui.dev.no_hr_chosen"), "idle")
            self._update_nav()
            return
        self.hr_status.set(t("ui.dev.connecting"), "busy")
        self.hr_connected = False   # the old strap is dropped first; not a lost connection
        self._set_hr_busy(True)

        def ok(_: Any) -> None:
            self._set_hr_busy(False)
            self.hr_device = dev
            self.hr_connected = True
            self.hr_status.set(t("ui.dev.connected"), "ok")
            self._update_nav()

        def fail(msg: str) -> None:
            self._set_hr_busy(False)
            self.hr_status.set(t("ui.dev.connect_failed"), "fix")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.hr", detail=msg))
        run_async(self.ctx.devices.connect_heart_rate, dev, on_done=ok, on_error=fail)

    def _set_hr_busy(self, busy: bool) -> None:
        self._hr_busy = busy
        self.hr_connect.setEnabled(not busy)
        self.hr_scan.setEnabled(not busy)

    def _hr_sample(self, s: Any) -> None:
        if hasattr(self, "hr_live"):
            self.hr_live.setText(t("ui.dev.hr_now", bpm=s.bpm))

    def _on_device_status(self, st: Any) -> None:
        state = getattr(getattr(st, "state", None), "value", "")
        pill = self.tm_status if st.device == "treadmill" else self.hr_status
        if state == "error":
            pill.set(st.message or t("ui.dev.connect_failed"), "fix")
            if st.device == "treadmill":
                self.tm_connected = False
            else:
                self.hr_connected = False
        elif state in ("disconnected", "connecting") and \
                (self.tm_connected if st.device == "treadmill" else self.hr_connected):
            # a link that drops is reported as "connecting": the backend tries again by itself
            pill.set(t("ui.dev.lost"), "watch")
        elif state == "connected":
            pill.set(t("ui.dev.connected") + (f": {st.name}" if st.name else ""), "ok")
        self._update_nav()

    def _build_check(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(18)
        vc = self.framing_card = Card(t("ui.wiz.framing"), t("ui.wiz.framing_sub"))
        self.check_view = VideoView()
        self.check_view.guide = True
        views = QHBoxLayout()
        views.setSpacing(12)
        views.addWidget(self.check_view, 3)
        self.check_view2 = VideoView()         # the second camera, with its own frame for the athlete
        self.check_view2.guide = True
        self.check_view2.hide()
        views.addWidget(self.check_view2, 2)
        vc.body.addLayout(views, 1)
        h.addWidget(vc, 6)
        cc = Card(t("ui.wiz.checklist"))
        self.check_box = QVBoxLayout()
        self.check_box.setSpacing(10)
        cc.body.addLayout(self.check_box)
        cc.body.addStretch(1)
        self.summary = label("", "Hint", wrap=True)
        cc.body.addWidget(self.summary)
        h.addWidget(cc, 4)
        return wrap

    def _fill_check(self) -> None:
        clear_layout(self.check_box)
        a = self.athlete
        filmed = self.films()
        mx = a.max_hr_for(self.sport) if a else None     # the sport's own max HR, never another sport's
        self.framing_card.setVisible(filmed)   # no frame to place the athlete in when nothing is filmed
        sub = self.framing_card.findChildren(QLabel)[1]
        sub.setText(t(f"ui.wiz.framing_sub.{self.kind}" if self.kind in TECHNIQUE_KINDS else "ui.wiz.framing_sub"))
        items = [
            (bool(a and a.height_cm), t("ui.check.height_ok", h=num(a.height_cm, 0)) if a and a.height_cm
             else t("ui.check.height_missing"), True, filmed),
            (bool(mx), t("ui.check.maxhr_ok", n=mx) if mx else t(
                "ui.check.maxhr_missing" if self.sport == "run" else "ui.check.maxhr_missing_sport"), False,
             self.kind not in TECHNIQUE_KINDS),     # bike fit and double poling do not use the max HR
            # The weight is shown for every sport, so that a wrong weight is seen before the report (2026-11-16:
            # a weight 10 kg off reached a report). It blocks only where W/kg needs it.
            (bool(a and a.weight_kg),
             t("ui.check.weight_ok" if self.sport in WEIGHT_SPORTS else "ui.check.weight_shown", kg=num(a.weight_kg))
             if a and a.weight_kg else t("ui.check.weight_missing" if self.sport in WEIGHT_SPORTS
                                          else "ui.check.weight_none"), self.sport in WEIGHT_SPORTS),
            (self.camera is not None, t("ui.check.camera_ok", name=self.camera.label) if self.camera is not None
             else t("ui.check.no_camera"), True, filmed),
            *([(True, t("ui.check.camera2_ok", name=self.camera2.label, view=t(f"ui.view.{self.view2}")), False, filmed)]
              if self._second() is not None else []),
            *([(False, t("ui.check.no_front_camera"), False)]
              if self.kind == "bikefit" and self._second() is None else []),
            (self.tm_connected, t("ui.check.treadmill_ok" if self.sport == "run" else f"ui.check.{self.sport}_ok",
                                  mode=self._mode_text(self.mode)) if self.tm_connected
             else t("ui.check.treadmill_missing" if self.sport == "run" else f"ui.check.{self.sport}_missing"), True),
            (self.hr_connected, t("ui.check.hr_ok") if self.hr_connected else t("ui.check.hr_missing"),
             self.kind in ("lactate", "max_hr", "combined", "max_hr_gait", "hr_zones", "ftp")),
            *([(True, t("ui.check.apps_closed." + self.sport), False)]
              if self.sport != "run" and self.mode in BLUETOOTH_MODES else []),
            (True, t("ui.check.framing"), False, filmed),
        ]
        # a fourth value False: the row is about the film and is left out when the test is not filmed
        items = [x[:3] for x in items if len(x) < 4 or x[3]]
        self._framing_label = None
        for n, (ok, text, required) in enumerate(items):
            row = QHBoxLayout()
            row.setSpacing(12)
            ic = label()
            col = "good" if ok else ("fix" if required else "watch")
            ic.setPixmap(theme.pixmap("check" if ok else "alert", col, 22))
            row.addWidget(ic)
            lb = label(text, wrap=True)
            lb.setStyleSheet("font-size: 15px;")
            row.addWidget(lb, 1)
            self.check_box.addLayout(row)
            if filmed and n == len(items) - 1:
                self._framing_label, self._framing_icon = lb, ic
        if self.protocol:
            self.summary.setText(t("ui.wiz.summary", athlete=a.name if a else "–", protocol=self.protocol.name,
                                   n=len(self.protocol.stages)))

    # ================================================================== navigation
    def reset(self) -> None:
        """Back to step 1 (after a test or when the coach starts over)."""
        self._teardown_ctl()
        self._stop_preview()
        services.release_parked()
        self.go(0)

    def _second(self) -> Any:
        """The second camera when the test is filmed, there is a first camera and it is another source."""
        if not self.films() or self.camera is None or self.camera2 is None or self.camera2.id == self.camera.id:
            return None
        if not self.views_allowed()[1]:        # double poling: one camera, from the side
            return None
        return self.camera2

    def _teardown_ctl(self) -> None:
        self.check_view.detach()
        self.check_view2.detach()
        if self.ctl is not None:
            self.ctl.abort()
            self.ctl = None

    def go(self, i: int) -> None:
        i = max(0, min(i, self.stack.count() - 1))
        prev = self.stack.currentIndex()
        if i == CAMERA_STEP and not self.films():
            i = CAMERA_STEP + 1 if prev < CAMERA_STEP else CAMERA_STEP - 1   # skipped, forwards and backwards
        if prev == 3 and i != 3:
            # Forward: the check page takes over the running camera instead of starting it again.
            self._stop_preview(hand_over=i > 3)
        if i < 3:
            services.release_parked()
        if prev == 5 and i != 5:
            self._teardown_ctl()
        self.stack.setCurrentIndex(i)
        self._sync_steps()
        if i == 2:
            if getattr(self, "_lib_for", None) != (self.kind, self.athlete.id if self.athlete else None, self.sport):
                self._fill_library()   # the zones test starts from this athlete's earlier test
            self._rebuild_protocol()
        if i == 3:
            self._discover()
        if i == 4 and self.ctx.demo and not self.tm_connected:
            self._connect_tm()
            self.hr_list.setCurrentRow(self.hr_list.count() - 1)   # the simulated strap (last)
            self._connect_hr()
        if i == 5:
            self._enter_check()
        self._update_nav()

    def _check_framing(self) -> None:
        """Live check that the runner is inside the framing guide (from the analyzer's pose)."""
        if self.stack.currentIndex() != 5 or self.ctl is None or self._framing_label is None:
            self._framing_timer.stop()
            return
        r = self.ctl.latest()
        err = getattr(self.ctl.analyzer, "error", None)
        if r is None and err:       # the camera (or the analysis) never started: say so instead of "Startar ..."
            self._check_camera_failed(t("ui.wiz.preview_failed", detail=err))
        ok = pose_in_guide(getattr(r, "pose", None), self._guide_view(self.view)) if self.camera is not None else None
        if ok is None:
            txt, ic, col = t("ui.check.framing_unknown"), "alert", "watch"
        elif ok:
            txt, ic, col = t("ui.check.framing_ok"), "check", "good"
        else:
            txt, ic, col = t("ui.check.framing_bad"), "alert", "fix"
        self._framing_label.setText(txt)
        self._framing_icon.setPixmap(theme.pixmap(ic, col, 22))

    def _check_camera_failed(self, text: str) -> None:
        if self.check_view.placeholder != text:
            self.check_view.placeholder = text
            self.check_view.update()

    def _enter_check(self) -> None:
        self._fill_check()
        self.check_view.guide_view = self._guide_view(self.view)
        self.ctl = ctl = LiveController(self.ctx, self.plan())
        # what the controller says before the test starts (the camera could not be started) is shown here;
        # from the start of the test the operator page listens
        ctl.error.connect(lambda m: self.ctl is ctl and self._check_camera_failed(m))
        sim = [] if self.ctx.demo else simulated.from_plan(self.mode if self.tm_connected else None,
                                                             self.hr_device if self.hr_connected else None)
        if sim:   # a practice run with a simulator: marked on the operator page, the results and the report
            self.ctl.session.result = dict(self.ctl.session.result or {}, simulated=sim)
        self.check_view.placeholder = t("ui.wiz.no_camera_chosen") if self.camera is None else t("ui.video.starting")
        self.ctl.open_camera()
        self.check_view.attach(self.ctl.latest)
        two = self._second() is not None
        self.check_view2.setVisible(two)
        if two:
            self.check_view2.guide_view = self._guide_view(self.view2)
            self.check_view2.placeholder = t("ui.video.starting")
            self.check_view2.attach(self.ctl.latest2)
        else:
            self.check_view2.detach()
        if self.camera is not None:
            self._framing_timer.start(500)

    def _download_model_then(self, cont: Callable[[], None]) -> None:
        """First run: fetch the pose model with a progress dialog, then continue."""
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog(t("ui.wiz.model_download"), t("ui.common.hide"), 0, 100, self)
        dlg.setWindowTitle(t("ui.wiz.model_title"))
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        dlg.show()
        self.next.setEnabled(False)

        def done(_: Any) -> None:
            dlg.close()
            self._update_nav()
            cont()

        def fail(msg: str) -> None:
            dlg.close()
            self._update_nav()
            QMessageBox.warning(self, t("ui.wiz.model_title"), t("ui.err.model", detail=msg))
            cont()
        run_async(services.ensure_model, on_done=done, on_error=fail,
                  on_progress=lambda v: dlg.setValue(int(max(0.0, min(1.0, v)) * 100)))

    def _fresh_athlete(self) -> Athlete | None:
        """The chosen athlete as saved now. The consent decides whether a film is saved, and it may
        have been changed in Idrottare after the athlete was chosen here."""
        if self.athlete is not None and self.athlete.id is not None:
            try:
                a = self.ctx.store.athlete(self.athlete.id)
            except Exception:
                a = None
            if a is not None:
                self.athlete = a
        return self.athlete

    def plan(self) -> TestPlan:
        self._fresh_athlete()
        assert self.athlete is not None and self.protocol is not None
        return TestPlan(self.athlete, self.kind, self.protocol, self.camera, self.view, self.mode,
                        self.tm_device, self.hr_device if self.hr_connected else None,
                        camera2=self._second(), view2=self.view2, sport=self.sport)

    def gait_ok(self) -> bool:
        """False when the chosen kind films the technique and the protocol does not allow it
        (assessments.protocol.gait_compatible): the tests are then done one at a time."""
        if self.kind == "gait" or not self.films() or self.protocol is None:
            return True
        return gait_compatible(self.protocol)

    def _ok_to_continue(self) -> tuple[bool, str]:
        i = self.stack.currentIndex()
        if i == 0 and self.athlete is None:
            return False, t("ui.wiz.need_athlete")
        if i == 2 and (self.protocol is None or not self.protocol.stages):
            return False, t("ui.wiz.need_protocol")
        if i == 2 and not self.gait_ok():
            return False, t("ui.wiz.gait_rule")
        if i == 3 and self.camera is None and self.kind == "gait":
            return False, t("ui.wiz.need_camera")
        if i == 3 and self.camera is None and self.kind in TECHNIQUE_KINDS:
            return False, t(f"ui.wiz.need_camera.{self.kind}")
        if i == 4 and not self.tm_connected:
            return False, t("ui.wiz.need_treadmill" if self.sport == "run" else f"ui.wiz.need_{self.sport}")
        return True, ""

    def _update_nav(self) -> None:
        if not hasattr(self, "next"):
            return
        ok, why = self._ok_to_continue()
        i = self.stack.currentIndex()
        self.back.setVisible(i > 0)
        self.next.setEnabled(ok)
        self.hint.setText(why)
        last = i == self.stack.count() - 1
        self.next.setText(t("ui.wiz.go_live") if last else t("ui.common.next"))
        self.next.setIcon(theme.icon("play" if last else "chevron-right", "#FFFFFF"))

    def _next(self) -> None:
        ok, _ = self._ok_to_continue()
        if not ok:
            return
        i = self.stack.currentIndex()
        if i == 1 and not self._ensure_weight():
            self.hint.setText(t("ui.wiz.need_weight"))
            return
        if i == 3:
            self.ctx.set_setting("camera.view", self.view)
            if self.camera is not None and not demo.is_demo_source(self.camera):
                self.ctx.set_setting("camera.default", self.camera.id)
            second = self._second()
            self.ctx.set_setting("camera.view2", self.view2)
            self.ctx.set_setting("camera.second", second.id if second is not None else "")
        real = [c for c in (self.camera, self._second()) if c is not None and not demo.is_demo_source(c)]
        if i == 4 and real and not services.model_ready():
            self._download_model_then(lambda: self.go(5))
            return
        if i == self.stack.count() - 1:
            ctl, self.ctl = self.ctl, None
            self.check_view.detach()
            if ctl is not None:
                a = self._fresh_athlete()     # the consent as saved now, not when the check step opened
                if a is not None and a.id == ctl.plan.athlete.id:
                    ctl.plan.athlete = a
                self.launch.emit(ctl)
            return
        self.go(i + 1)
