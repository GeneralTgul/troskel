"""The coach's main window: left navigation and the pages (Idrottare, Nytt test, Historik, Testerna,
Inställningar), plus the live operator page while a test runs and the results page after."""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from lopanalys.core.i18n import t
from lopanalys.ui import bridge, theme
from lopanalys.ui.context import GAIT_KINDS, TECHNIQUE_KINDS, AppContext, is_active
from lopanalys.ui.lock import IdleWatcher, LockScreen, check_pin
from lopanalys.ui.views.athletes import AthletesView
from lopanalys.ui.views.history import HistoryView
from lopanalys.ui.views.new_test import NewTestView
from lopanalys.ui.views.operator import OperatorView
from lopanalys.ui.views.plan import PlanView
from lopanalys.ui.views.results import ResultsView
from lopanalys.ui.views.settings import SettingsView
from lopanalys.ui.views.tests_info import TestsInfoView
from lopanalys.ui.widgets.common import StatusPill, label

log = logging.getLogger(__name__)


class NavButton(QPushButton):
    def __init__(self, text: str, icon: str) -> None:
        super().__init__(text)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setIcon(theme.icon(icon, "muted"))
        self.setIconSize(QSize(20, 20))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon = icon
        self.toggled.connect(lambda on: self.setIcon(theme.icon(self._icon, "accent" if on else "muted")))


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.operator: OperatorView | None = None
        self.setWindowTitle(t("ui.app.name") + (f"  ·  {t('ui.app.demo')}" if ctx.demo else ""))
        self.setWindowIcon(theme.icon("logo", "accent", 64))
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)
        root = QWidget()
        root.setObjectName("Root")
        # The lock is a page of its own over everything: nothing of the program shows behind it.
        self.shell = QStackedWidget()
        self.shell.addWidget(root)
        self.lock_screen = LockScreen(self._check_pin)
        self.lock_screen.unlocked.connect(self.unlock)
        self.shell.addWidget(self.lock_screen)
        self.setCentralWidget(self.shell)
        self.idle = IdleWatcher(self)
        self.idle.idle.connect(self._idle)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QWidget()
        side.setObjectName("Sidebar")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(248)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 22, 16, 18)
        sv.setSpacing(6)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.logo = QLabel()
        brand.addWidget(self.logo)
        self._apply_logo()
        ctx.settings_changed.connect(self._apply_logo)
        bcol = QVBoxLayout()
        bcol.setSpacing(0)
        bcol.addWidget(label(t("ui.app.name"), "Brand"))
        self.brand_sub = label("", "BrandSub")
        bcol.addWidget(self.brand_sub)
        brand.addLayout(bcol, 1)
        sv.addLayout(brand)
        sv.addSpacing(22)

        self.stack = QStackedWidget()
        self.athletes = AthletesView(ctx)
        self.new_test = NewTestView(ctx)
        self.history = HistoryView(ctx)
        self.tests_info = TestsInfoView(ctx)
        self.settings = SettingsView(ctx)
        self.results = ResultsView(ctx)
        self.plan = PlanView(ctx)
        self._plan_from: tuple[str, QWidget] = ("athletes", self.athletes)   # the nav button and page it was opened from
        for w in (self.athletes, self.new_test, self.history, self.tests_info, self.settings, self.results, self.plan):
            self.stack.addWidget(w)

        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav_buttons: dict[str, NavButton] = {}
        self.live_btn = NavButton(t("ui.nav.live"), "tv")
        self.live_btn.hide()
        self.live_btn.clicked.connect(self.show_live)
        self.nav.addButton(self.live_btn)
        sv.addWidget(self.live_btn)
        for key, icon, page in (("athletes", "users", self.athletes), ("new_test", "run", self.new_test),
                                ("history", "history", self.history), ("tests", "chart", self.tests_info),
                                ("settings", "settings", self.settings)):
            b = NavButton(t(f"ui.nav.{key}"), icon)
            b.clicked.connect(lambda _=False, p=page: self._nav(p))
            self.nav.addButton(b)
            self.nav_buttons[key] = b
            sv.addWidget(b)
        sv.addStretch(1)
        sv.addWidget(label(t("ui.nav.devices"), "BrandSub"))
        self.tm_pill = StatusPill(t("ui.nav.treadmill_off"), "idle")
        self.hr_pill = StatusPill(t("ui.nav.hr_off"), "idle")
        sv.addWidget(self.tm_pill)
        sv.addWidget(self.hr_pill)
        if ctx.demo:
            sv.addSpacing(8)
            badge = label(t("ui.app.demo_badge"), "DemoBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sv.addWidget(badge)
        h.addWidget(side)
        h.addWidget(self.stack, 1)

        self.athletes.start_test.connect(self.start_wizard_for)
        self.athletes.due_changed.connect(self._due_changed)
        self._due_changed(len(self.athletes.due))
        self.athletes.open_plan.connect(lambda a: self.open_plan(a))
        self.results.plan_requested.connect(self.open_plan)
        self.plan.back.connect(self._plan_back)
        self.new_test.want_new_athlete.connect(lambda: self.go("athletes") or self.athletes.form_new())
        self.new_test.launch.connect(self.launch_test)
        self.history.open_session.connect(self.open_session)
        self.results.back.connect(self._results_back)
        self.results.resume_requested.connect(self.resume_test)
        self.results.erase_requested.connect(self._erase_after_report)
        self._pending_resume: Any = None
        ctx.settings_changed.connect(self._refresh_brand)
        ctx.bridge.device_status.connect(self._device_status)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_tv_full)
        self._refresh_brand()
        theme.polish_inputs(self)
        self.go("new_test" if ctx.store.athletes() else "athletes")
        self.settings.lock_requested.connect(self.lock)
        self.settings.lock_changed.connect(self._arm_idle)
        if self.has_pin():
            self.lock()             # the program opens locked; the question about a cut-off test waits
        else:
            QTimer.singleShot(0, self.offer_interrupted)
        self._arm_idle()

    # ------------------------------------------------------------------ lock and customer mode
    def has_pin(self) -> bool:
        return bool(self.ctx.setting("lock.pin"))

    def _check_pin(self, pin: str) -> bool:
        return check_pin(pin, self.ctx.setting("lock.pin"))

    def locked(self) -> bool:
        return self.shell.currentWidget() is self.lock_screen

    def lock(self) -> None:
        """Show the lock page (only when there is a PIN to open it with)."""
        if self.has_pin() and not self.locked():
            self._offer_after_unlock = not getattr(self, "_offered", False)
            self.shell.setCurrentWidget(self.lock_screen)

    def unlock(self) -> None:
        self.shell.setCurrentIndex(0)
        self._arm_idle()
        if getattr(self, "_offer_after_unlock", False):
            self._offer_after_unlock = False
            QTimer.singleShot(0, self.offer_interrupted)

    def _arm_idle(self) -> None:
        self.idle.set_minutes(float(self.ctx.setting("lock.idle_min") or 0) if self.has_pin() else 0)

    def _idle(self) -> None:
        """Nobody touched the program for a while: lock it, but never during a test."""
        if self.ctx.live is None:
            self.lock()
        else:
            self._arm_idle()        # a test is running: count again from now

    def _due_changed(self, n: int) -> None:
        """The reminder shows in the navigation too: "Idrottare (2)"."""
        self.nav_buttons["athletes"].setText(t("ui.nav.athletes") + (f" ({n})" if n else ""))

    def _customer_mode(self, on: bool) -> None:
        """While a test runs the athlete list, the history and the wizard are out of reach, so the
        customer on the treadmill sees no other person (the setting ``lock.customer_mode``)."""
        hide = on and bool(self.ctx.setting("lock.customer_mode"))
        for key in ("athletes", "history", "new_test"):
            self.nav_buttons[key].setVisible(not hide)

    # ------------------------------------------------------------------ tests cut off
    def offer_interrupted(self) -> None:
        """At start: a test that was running when the program died is marked interrupted and offered."""
        from lopanalys.ui import recovery
        self._offered = True
        try:
            cut = recovery.collect_interrupted(self.ctx.store)
        except Exception:
            log.exception("looking for interrupted tests failed")
            return
        if not cut:
            return
        self.ctx.sessions_changed.emit()
        s = cut[0]
        a = self.ctx.store.athlete(s.athlete_id)
        when = (s.ended or s.started).astimezone().strftime("%H:%M")
        text = t("ui.recover.ask", time=when, name=a.name if a else "–", protocol=s.protocol_name)
        if len(cut) > 1:
            text += "\n\n" + t("ui.recover.more", n=len(cut) - 1)
        r = QMessageBox.question(self, t("ui.recover.title"), text)
        if r == QMessageBox.StandardButton.Yes and s.id is not None:
            self.open_session(s.id)

    def resume_test(self, info: Any) -> None:
        """Continue a cut-off test: the wizard connects the devices and the camera again, and the
        test that starts from it is the old one, from its next stage (``LiveController.resume_from``)."""
        a = self.ctx.store.athlete(info.session.athlete_id)
        if a is None:
            return
        self._pending_resume = info
        QMessageBox.information(self, t("ui.recover.title"), t("ui.recover.resume_how"))
        self.start_wizard_for(a)
        self.new_test.set_sport(info.session.sport.value)    # the device step connects the test's own ergometer

    def go(self, key: str) -> None:
        self.nav_buttons[key].setChecked(True)
        self.stack.setCurrentWidget({"athletes": self.athletes, "new_test": self.new_test,
                                     "history": self.history, "tests": self.tests_info,
                                     "settings": self.settings}[key])

    def _leave_ok(self, to: QWidget | None = None) -> bool:
        """False when the coach chose to stay on an unsaved athlete form; the nav button follows.
        Leaving the results page saves what was changed there."""
        cur = self.stack.currentWidget()
        if cur is self.results:
            self.results.flush()
        if cur is self.plan and to is not self.plan and not self.plan.leave_ok():
            self.nav_buttons[self._plan_from[0]].setChecked(True)
            return False
        if cur is not self.athletes or to is self.athletes or self.athletes.leave_ok():
            return True
        self.nav_buttons["athletes"].setChecked(True)
        return False

    def _nav(self, page: QWidget) -> None:
        if self._leave_ok(page):
            self.stack.setCurrentWidget(page)

    def _refresh_brand(self) -> None:
        b = self.ctx.branding()
        self.brand_sub.setText(b.get("business") or t("ui.app.tagline"))

    def _device_status(self, st: Any) -> None:
        state = getattr(getattr(st, "state", None), "value", "")
        pill = self.tm_pill if st.device == "treadmill" else self.hr_pill
        base = t("ui.nav.hr")
        if st.device == "treadmill":      # the ergometer's place: the treadmill, the trainer or the SkiErg
            sport = getattr(self.ctx.devices, "ergometer_sport", None) or getattr(self.new_test, "sport", "run")
            base = t("ui.nav.treadmill" if sport == "run" else f"ui.nav.ergometer.{sport}")
        mapping = {"connected": "ok", "connecting": "busy", "scanning": "busy", "error": "fix", "disconnected": "idle"}
        pill.set(f"{base}: {st.name}" if state == "connected" and st.name else f"{base}: {t('ui.devstate.' + state)}",
                 mapping.get(state, "idle"))

    # ------------------------------------------------------------------ flow
    def _apply_logo(self) -> None:
        """The coach's own logo (Settings) in the sidebar and as window icon; the app mark when none is set."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon, QPixmap
        path = str(self.ctx.setting("branding.logo") or "")
        pm = QPixmap(path) if path else QPixmap()
        if pm.isNull():
            self.logo.setPixmap(theme.pixmap("logo", "accent", 34))
            self.setWindowIcon(theme.icon("logo", "accent", 64))
            return
        dpr = self.devicePixelRatioF()
        small = pm.scaled(int(40 * dpr), int(40 * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        small.setDevicePixelRatio(dpr)
        self.logo.setPixmap(small)
        self.setWindowIcon(QIcon(pm))

    def open_plan(self, athlete: Any, session_id: int | None = None) -> None:
        """Träningsschema for an athlete, from Idrottare or from a result (then with that test chosen)."""
        cur = self.stack.currentWidget()
        if athlete is None or athlete.id is None or not self._leave_ok(self.plan):
            return
        if cur is not self.plan:
            self._plan_from = ("history", cur) if cur is self.results else ("athletes", self.athletes)
        self.nav_buttons[self._plan_from[0]].setChecked(True)
        self.plan.open(athlete, session_id)
        self.stack.setCurrentWidget(self.plan)

    def _plan_back(self) -> None:
        if not self.plan.leave_ok():
            return
        key, page = self._plan_from
        self.nav_buttons[key].setChecked(True)
        if page is self.results:
            self.results.refresh_plan_card()
        self.stack.setCurrentWidget(page if self.stack.indexOf(page) >= 0 else self.athletes)

    def start_wizard_for(self, athlete: Any) -> None:
        if not self._leave_ok():
            return
        self.nav_buttons["new_test"].setChecked(True)
        self.stack.setCurrentWidget(self.new_test)
        self.new_test.preselect(athlete)

    def _erase_after_report(self, athlete_id: int, keep: Any) -> None:
        if self.athletes.erase(athlete_id, list(keep or [])):
            self.go("athletes")

    def _consent_ok(self, ctl: Any) -> bool:
        """Before a test starts: without a registered consent to the test the coach is asked,
        and without consent to film the coach is told that nothing is recorded."""
        a, plan = ctl.plan.athlete, ctl.plan
        if not a.consents("test"):
            r = QMessageBox.question(self, t("ui.consent.title"), t("ui.consent.ask_test", name=a.name),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return False
        if plan.kind in TECHNIQUE_KINDS and not a.consents("film"):
            # Bike fit and double poling are measured on the film only: without it there is no test.
            QMessageBox.information(self, t("ui.consent.title"), t("ui.consent.no_film_technique", name=a.name))
            return False
        if plan.camera is not None and plan.kind in GAIT_KINDS and not a.consents("film"):
            QMessageBox.information(self, t("ui.consent.title"), t("ui.consent.no_film", name=a.name))
        return True

    def launch_test(self, ctl: Any) -> None:
        if not self._consent_ok(ctl):
            self._pending_resume = None
            ctl.abort()
            self.new_test.reset()
            self.go("athletes")
            self.athletes.select(ctl.plan.athlete.id)
            return
        r, self._pending_resume = self._pending_resume, None
        if r is not None and ctl.plan.athlete.id == r.session.athlete_id:
            ctl.resume_from(r)
        if self.operator is not None:
            self._drop_operator()
        self.ctx.live = ctl
        self._customer_mode(True)
        self.operator = OperatorView(self.ctx, ctl)
        self.operator.finished.connect(self.test_finished)
        self.stack.addWidget(self.operator)
        self.live_btn.show()
        self.live_btn.setChecked(True)
        self.stack.setCurrentWidget(self.operator)
        self.operator.show_tv()

    def _results_back(self) -> None:
        self.go("history")
        a = self.results.athlete
        if a is not None and a.id is not None:
            self.history.select_athlete(a.id)

    def show_live(self) -> None:
        if self.operator is not None and self._leave_ok(self.operator):
            self.stack.setCurrentWidget(self.operator)

    def test_finished(self, outcome: Any) -> None:
        self.ctx.live = None
        self._customer_mode(False)
        self.live_btn.hide()
        op = self.operator
        if outcome is None:
            self._drop_operator()
            self.new_test.reset()
            self.go("new_test")
            return
        self.results.flush()
        self.results.load_outcome(outcome)
        if outcome.plan.athlete.id is not None:
            self.history.select_athlete(outcome.plan.athlete.id)
        self.nav_buttons["history"].setChecked(True)
        self.stack.setCurrentWidget(self.results)
        self.new_test.reset()
        # leave the 'finished' screen on the TV for a moment, then close it
        QTimer.singleShot(15000, lambda: op is self.operator and self._drop_operator())

    def _drop_operator(self) -> None:
        if self.operator is None:
            return
        self.operator.shutdown()
        self.stack.removeWidget(self.operator)
        self.operator.deleteLater()
        self.operator = None

    def open_session(self, session_id: int) -> None:
        self.results.flush()
        try:
            self.results.load_session(session_id)
        except Exception as e:
            log.exception("open session failed")
            QMessageBox.warning(self, t("ui.app.name"), t("ui.err.db", detail=str(e)))
            return
        self.stack.setCurrentWidget(self.results)

    def _toggle_tv_full(self) -> None:
        if self.operator and self.operator.tv:
            tv = self.operator.tv
            tv.showNormal() if tv.isFullScreen() else tv.showFullScreen()

    def closeEvent(self, e: QCloseEvent) -> None:
        if not self._leave_ok():
            e.ignore()
            return
        if self.ctx.live is not None and is_active(self.ctx.live.state):
            r = QMessageBox.question(self, t("ui.app.name"), t("ui.app.quit_running"))
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        self.results.flush()
        self.new_test.reset()
        self._drop_operator()
        self.idle.stop()
        self.ctx.shutdown()
        bridge.wait_all(500)
        super().closeEvent(e)
