"""The coach's live page: a mirror of the TV board plus the operator controls
(start/pause/stop, 'Utmattad', 'Bandet är inställt', lactate entry, RPE)."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMessageBox, QScrollArea, QVBoxLayout,
                               QWidget)

from lopanalys.assessments.protocol import Phase, RunnerState
from lopanalys.core import simulated
from lopanalys.core.i18n import t
from lopanalys.core.sport import profile
from lopanalys.ui.context import AppContext
from lopanalys.ui.fmt import clock, load, num, pace, speed
from lopanalys.ui.live_controller import LiveController
from lopanalys.ui.sound import Cues
from lopanalys.ui.views.live_board import LiveBoard, LiveWindow
from lopanalys.ui.widgets.common import Card, PageHeader, StatusPill, button, label
from lopanalys.ui.widgets.inputs import NumberPad, RpePicker


START_STEP_KMH = 0.5     # the start speed moves this much per press during the warm-up


class OperatorView(QWidget):
    """Shown while a test runs. Emits ``finished(TestOutcome)`` when the test ends."""

    finished = Signal(object)

    def __init__(self, ctx: AppContext, ctl: LiveController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx, self.ctl = ctx, ctl
        self.tv: LiveWindow | None = None
        self.started = False
        self._sprinted = False
        self.cues = Cues(bool(ctx.setting("tv.sound")))
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 24)
        root.setSpacing(16)
        hdr = PageHeader(ctl.plan.athlete.name, f"{ctl.plan.protocol.name}  ·  {self._devices_line()}")
        sim = simulated.label(simulated.of_session(ctl.session), ctl.session.sport)
        self.sim_pill = StatusPill(sim, "watch") if sim else None   # a practice run, never a real test
        if self.sim_pill is not None:
            hdr.add_action(self.sim_pill)
        self.pill = StatusPill(t("ui.live.status.ready"), "idle")
        hdr.add_action(self.pill)
        self.tv_btn = button(t("ui.live.show_tv"), "tv")
        self.tv_btn.clicked.connect(self.show_tv)
        hdr.add_action(self.tv_btn)
        root.addWidget(hdr)

        body = QHBoxLayout()
        body.setSpacing(20)
        root.addLayout(body, 1)
        self.board = LiveBoard(ctl, compact=True)
        body.addWidget(self.board, 1)

        side = QWidget()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 6, 0)
        sv.setSpacing(14)
        ctrl = Card(t("ui.live.controls"))
        self.start_btn = button(t("ui.live.start"), "play", kind="primary", size="big")
        self.start_btn.clicked.connect(self.start)
        self.ready_btn = button(t("ui.live.speed_set"), "check", kind="primary", size="big")
        self.ready_btn.clicked.connect(ctl.operator_ready)
        self.ready_btn.hide()
        row = QHBoxLayout()
        row.setSpacing(10)
        self.pause_btn = button(t("ui.live.pause"), "pause", size="big")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = button(t("ui.live.stop"), "stop", kind="danger", size="big")
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.pause_btn)
        row.addWidget(self.stop_btn)
        self.exh_btn = button(t("ui.live.exhausted"), "flag", kind="warning", size="big")
        self.exh_btn.setToolTip(t("ui.live.exhausted_tip"))
        self.exh_btn.clicked.connect(self.exhausted)
        ctrl.body.addWidget(self.start_btn)
        ctrl.body.addWidget(self.ready_btn)
        ctrl.body.addLayout(row)
        ctrl.body.addWidget(self.exh_btn)
        # The warm-up: end it when it is enough, and move the start speed (all stages move with it)
        self.warm_box = QWidget()
        wv = QVBoxLayout(self.warm_box)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(8)
        self.start_now_btn = button(t("ui.live.start_now"), "chevron-right", kind="primary", size="big",
                                    tooltip=t("ui.live.start_now_tip"))
        self.start_now_btn.clicked.connect(self.start_now)
        wv.addWidget(self.start_now_btn)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self.slower_btn = button(t("ui.live.start_slower"), tooltip=t("ui.live.start_speed_tip"))
        self.slower_btn.clicked.connect(lambda: self.shift_start(-START_STEP_KMH))
        self.start_speed = label("", "Hint", wrap=True)
        self.start_speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.faster_btn = button(t("ui.live.start_faster"), tooltip=t("ui.live.start_speed_tip"))
        self.faster_btn.clicked.connect(lambda: self.shift_start(START_STEP_KMH))
        srow.addWidget(self.slower_btn)
        srow.addWidget(self.start_speed, 1)
        srow.addWidget(self.faster_btn)
        self.speed_row = QWidget()
        self.speed_row.setLayout(srow)
        self.speed_row.setVisible(not self._power())
        wv.addWidget(self.speed_row)
        self.warm_box.hide()
        ctrl.body.addWidget(self.warm_box)
        self.sprint_btn = button(t("ui.live.final_sprint"), "run", size="big")
        self.sprint_btn.setToolTip(t("ui.live.final_sprint_tip"))
        self.sprint_btn.clicked.connect(self.ctl_final_sprint)
        self.sprint_btn.setVisible(getattr(ctl.plan.protocol, "final_sprint_s", 0) > 0)
        ctrl.body.addWidget(self.sprint_btn)
        # how near the automatic final sprint is: for the coach only, the TV does not show it
        self.sprint_note = label("", "Hint", wrap=True)
        self.sprint_note.hide()
        ctrl.body.addWidget(self.sprint_note)
        # What the ergometer says in a test in watt: the PM5 took the workout or did not, the
        # trainer lost the control. Empty and hidden until something is said.
        self.device_note = label("", "Hint", wrap=True)
        self.device_note.hide()
        ctrl.body.addWidget(self.device_note)
        if self._power():
            ctx.bridge.log_line.connect(self._device_said)
        sv.addWidget(ctrl)

        self.lac_card = Card(t("ui.live.lactate_entry"))
        self.lac_for = label("", "Hint", wrap=True)
        self.lac_card.body.addWidget(self.lac_for)
        self.pad = NumberPad("mmol/L", 30.0)
        self.pad.submitted.connect(self.on_lactate)
        self.lac_card.body.addWidget(self.pad)
        self.lac_list = QListWidget()
        self.lac_list.setMaximumHeight(120)
        self.lac_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lac_card.body.addWidget(self.lac_list)
        self.lac_list.hide()
        has_lactate = any(s.sample_lactate for s in ctl.plan.protocol.stages)
        self.lac_card.setVisible(has_lactate)
        sv.addWidget(self.lac_card)

        self.rpe_card = Card(t("ui.live.rpe"))
        self.rpe = RpePicker()
        self.rpe.picked.connect(lambda v: ctl.enter_rpe(v))
        self.rpe_card.body.addWidget(self.rpe)
        sv.addWidget(self.rpe_card)
        sv.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        scroll.setFixedWidth(400)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(scroll)

        ctl.state_changed.connect(self.on_state)
        ctl.cue.connect(self.cues.play)
        ctl.finished.connect(self._finished)
        ctl.error.connect(lambda m: QMessageBox.warning(self, t("ui.app.name"), m))
        self._update_buttons(None)
        self._update_lac_for()

    def _devices_line(self) -> str:
        p = self.ctl.plan
        cams = [c.label for c in (p.camera, getattr(p, "camera2", None)) if c is not None]
        sport = getattr(p, "sport", "run") or "run"
        mode = t(f"ui.mode.{p.treadmill_mode}" if sport == "run" else f"ui.mode.{sport}.{p.treadmill_mode}")
        return mode + (f"  ·  {' + '.join(cams)}" if cams else "")

    def _device_said(self, text: str) -> None:
        self.device_note.setText(text)
        self.device_note.setVisible(bool(text))

    def _power(self) -> bool:
        """True for a test in watt (cycling, SkiErg): the texts name the load with its unit."""
        return profile(getattr(self.ctl.plan, "sport", "run")).load_quantity == "power"

    def _load(self, stage: Any) -> str:
        return load(stage.speed_kmh, getattr(self.ctl.plan, "sport", "run"))

    # ------------------------------------------------------------------ actions
    def start(self) -> None:
        if not self.ctl.start():
            return
        self.started = True
        self._update_buttons(None)

    def toggle_pause(self) -> None:
        if self.ctl.paused:
            self.ctl.resume()
        else:
            self.ctl.pause()
        self._update_buttons(self.ctl.state)
        if self.ctl.state:
            self.board.on_state(self.ctl.state)
            if self.tv:
                self.tv.board.on_state(self.ctl.state)

    def stop(self) -> None:
        if not self.started:
            self.ctl.abort()
            self.finished.emit(None)
            return
        if self.ctl.state is not None and self.ctl.state.phase == Phase.RECOVERY:
            self.ctl.stop()
            return
        r = QMessageBox.question(self, t("ui.live.stop_title"), t("ui.live.stop_confirm"))
        if r == QMessageBox.StandardButton.Yes:
            self.ctl.stop()

    def exhausted(self) -> None:
        self.ctl.exhausted()

    def start_now(self) -> None:
        """*Börja testet nu*: the warm-up is enough, the first stage starts."""
        self.ctl.start_now()

    def shift_start(self, delta: float) -> None:
        self.ctl.shift_start(delta)
        self._update_warmup()

    def _first_stage(self) -> Any:
        return next((s for s in self.ctl.stages() if not s.is_warmup), None)

    def _update_warmup(self, st: RunnerState | None = None) -> None:
        warm = self.started and self.ctl.in_warmup()
        self.warm_box.setVisible(warm)
        if not warm:
            return
        st = st or self.ctl.state
        phase = st.phase if st is not None else getattr(self.ctl.runner, "phase", None)
        self.start_now_btn.setEnabled(phase == Phase.RUNNING and not self.ctl.paused)
        first = self._first_stage()
        if first is not None and not self._power():
            self.start_speed.setText(t("ui.live.start_speed", pace=pace(first.speed_kmh), speed=num(first.speed_kmh)))

    def ctl_final_sprint(self) -> None:
        if self.ctl.final_sprint():
            self._sprinted = True
            self.sprint_btn.setEnabled(False)

    def on_lactate(self, v: float) -> None:
        i = self.ctl.lactate_stage()
        self.ctl.enter_lactate(v, i)
        self._refresh_lac_list()

    def _refresh_lac_list(self) -> None:
        self.lac_list.clear()
        stages = self.ctl.stages()
        for i, v in sorted(self.ctl.lactate.items()):
            sp = speed(stages[i].speed_kmh) if i < len(stages) else "–"
            if self._power() and i < len(stages):
                self.lac_list.addItem(t("ui.live.lactate_row_load", name=self.ctl.stage_name(i),
                                        load=self._load(stages[i]), v=num(v)))
                continue
            self.lac_list.addItem(t("ui.live.lactate_row", name=self.ctl.stage_name(i), speed=sp, v=num(v)))
        self.lac_list.setVisible(self.lac_list.count() > 0)

    def show_tv(self) -> None:
        if self.tv is None:
            scr = self.ctx.setting("tv.screen")       # 0 is the first screen, not "choose by itself"
            self.tv = LiveWindow(self.ctl, -1 if scr in (None, "") else int(scr))
            if self.ctl.state:
                self.tv.board.on_state(self.ctl.state)
        self.tv.show_on_screen()
        self.tv.raise_()

    def close_tv(self) -> None:
        if self.tv is not None:
            self.tv.close()
            self.tv.deleteLater()
            self.tv = None

    # ------------------------------------------------------------------ state
    def on_state(self, st: RunnerState) -> None:
        self._update_buttons(st)
        self._update_warmup(st)
        self._update_lac_for()
        self._update_sprint_note(st)
        pills = {Phase.COUNTDOWN: ("starting", "busy"), Phase.RUNNING: ("running", "ok"),
                 Phase.LACTATE_PAUSE: ("lactate", "watch"), Phase.AWAIT_OPERATOR: ("operator", "busy"),
                 Phase.FINISHED: ("done", "ok"), Phase.ABORTED: ("stopped", "fix"),
                 Phase.RECOVERY: ("recovery", "ok")}
        key, state = pills.get(st.phase, ("ready", "idle"))
        if self.ctl.paused:
            key, state = "paused", "watch"
        self.pill.set(t(f"ui.live.status.{key}"), state)
        if st.phase == Phase.LACTATE_PAUSE:
            self.pad.setFocus()

    def _update_lac_for(self) -> None:
        st = self.ctl.state
        i = self.ctl.lactate_stage() if st else 0
        stages = self.ctl.stages()
        sp = speed(stages[i].speed_kmh) if i < len(stages) else "–"
        if self._power() and i < len(stages):
            self.lac_for.setText(t("ui.live.lactate_for_load", name=self.ctl.stage_name(i), load=self._load(stages[i])))
            return
        self.lac_for.setText(t("ui.live.lactate_for", name=self.ctl.stage_name(i), speed=sp))

    def _update_buttons(self, st: RunnerState | None) -> None:
        running = self.started and st is not None and st.phase not in (Phase.FINISHED, Phase.ABORTED)
        recovering = st is not None and st.phase == Phase.RECOVERY
        self.start_btn.setVisible(not self.started)
        self.pause_btn.setEnabled(running and not recovering)
        self.exh_btn.setEnabled(running and not recovering)
        self.sprint_btn.setEnabled(running and not recovering and not self._sprinted)
        self.stop_btn.setText(t("ui.live.end_recovery") if recovering else t("ui.live.stop"))
        self.stop_btn.setEnabled(True)
        self.pause_btn.setText(t("ui.live.resume") if self.ctl.paused else t("ui.live.pause"))
        from lopanalys.ui import theme
        self.pause_btn.setIcon(theme.icon("play" if self.ctl.paused else "pause"))
        self.ready_btn.setVisible(st is not None and st.phase == Phase.AWAIT_OPERATOR)
        if st is not None and st.phase == Phase.AWAIT_OPERATOR and st.stage is not None:
            self.ready_btn.setText(t("ui.live.load_set_to", load=self._load(st.stage)) if self._power() else
                                   t("ui.live.speed_set_to", speed=speed(st.stage.speed_kmh)))

    def _update_sprint_note(self, st: RunnerState) -> None:
        """The line under *Slutspurt*: what the automatic sprint waits for, or when it started."""
        text = self.sprint_text(getattr(st, "sprint", None), st.phase)
        self.sprint_note.setText(text)
        self.sprint_note.setVisible(bool(text))

    @staticmethod
    def sprint_text(o: Any, phase: Phase) -> str:
        """The text for a ``SprintOutlook``; empty when there is nothing to say."""
        if o is None:
            return ""
        if o.started_s is not None:
            why = (t("ui.live.sprint_why.time", min=int(round(o.after_s / 60))) if o.reason == "time"
                   else t(f"ui.live.sprint_why.{o.reason or 'plateau'}"))
            return t("ui.live.sprint_started", at=clock(o.started_s), why=why)
        if phase not in (Phase.COUNTDOWN, Phase.RUNNING, Phase.LACTATE_PAUSE, Phase.AWAIT_OPERATOR):
            return ""
        lim = dict(limit=num(o.limit_bpm, 0), min=clock(o.min_ramp_s), after=clock(o.after_s))
        if not o.in_ramp:
            return t("ui.live.sprint_before", **lim)
        left = clock(o.remaining_s)
        if o.hr_rise_bpm is None:
            return t("ui.live.sprint_wait_nohr", left=left, **lim)
        rise = round(o.hr_rise_bpm)
        sign = "+" if rise >= 0 else "\u2212"
        return t("ui.live.sprint_wait", rise=f"{sign}{abs(rise)}", left=left, **lim)

    def _finished(self, outcome: Any) -> None:
        self.cues.play("go")
        self.finished.emit(outcome)

    def shutdown(self) -> None:
        self.close_tv()
        self.board.detach_video()
