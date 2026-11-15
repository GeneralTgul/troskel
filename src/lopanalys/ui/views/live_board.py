"""The live board: video with skeleton, big tiles, stage countdown, prompts, charts and gait.

Used full screen on the TV (``LiveWindow``) and as the mirror in the coach's operator view.
Everything is driven by a ``LiveController``; the board itself holds no test logic.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetricsF, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from lopanalys.assessments.cadence import cadence_label
from lopanalys.assessments.protocol import Phase, RunnerState
from lopanalys.core.i18n import t
from lopanalys.core.sport import profile
from lopanalys.ui import theme
from lopanalys.ui.context import CAMERA_OPTIONAL_KINDS, GAIT_KINDS, TECHNIQUE_KINDS
from lopanalys.ui.fmt import clock, load, num, pace
from lopanalys.ui.live_controller import LiveController, gait_ref, gait_status
from lopanalys.ui.widgets.charts import LactateChart, TimeChart
from lopanalys.ui.widgets.tiles import CountdownRing, GaitTile, MetricTile, PromptBanner
from lopanalys.ui.widgets.video import VideoView


class NextStageCard(QWidget):
    """'Nästa' with the next speed/incline and whether a lactate sample follows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.speed = "–"
        self.lines: list[str] = []
        self.caption = t("ui.live.next")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(120, 120)

    def set_next(self, caption: str, speed: str, lines: list[str]) -> None:
        self.caption, self.speed, self.lines = caption, speed, lines
        self.update()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        s = min(r.height(), r.width())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color("surface"))
        p.drawRoundedRect(r, max(10.0, s * 0.07), max(10.0, s * 0.07))
        pad = max(12.0, s * 0.1)
        f = theme.font(max(11, int(s * 0.085)), QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(theme.color("muted"))
        p.drawText(QRectF(r.left() + pad, r.top() + pad - 2, r.width(), f.pixelSize() * 1.5),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.caption.upper())
        vf = theme.font(int(s * 0.25), QFont.Weight.Bold, tabular=True)
        avail = r.width() - pad * 2
        need = QFontMetricsF(vf).horizontalAdvance(self.speed)
        if need > avail:
            vf.setPixelSize(max(10, int(vf.pixelSize() * avail / need)))
        p.setFont(vf)
        p.setPen(theme.color("text"))
        p.drawText(QRectF(r.left() + pad, r.top() + pad + f.pixelSize() * 1.6, r.width() - pad * 2, s * 0.3),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.speed)
        lf = theme.font(max(11, int(s * 0.085)), QFont.Weight.DemiBold)
        p.setFont(lf)
        y = r.top() + pad + f.pixelSize() * 1.6 + s * 0.34
        for i, line in enumerate(self.lines):
            hot = line.startswith("!")
            p.setPen(theme.color("watch") if hot else theme.color("muted"))
            p.drawText(QRectF(r.left() + pad, y, r.width() - pad * 2, lf.pixelSize() * 1.5),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(lf).elidedText(line[1:] if hot else line, Qt.TextElideMode.ElideRight,
                                                    r.width() - pad * 2))
            y += lf.pixelSize() * 1.55


class Header(QWidget):
    """Top bar: logo + business, athlete and test, status and total time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.logo: QPixmap | None = None
        self.business = ""
        self.athlete = ""
        self.test = ""
        self.status = ""
        self.status_colour = "muted"
        self.total = "0:00"
        self.setMinimumHeight(48)

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        h = r.height()
        x = r.left()
        if self.logo and not self.logo.isNull():
            lh = h * 0.8
            lw = self.logo.width() * lh / max(1, self.logo.height())
            p.drawPixmap(QRectF(x, r.top() + (h - lh) / 2, lw, lh), self.logo, QRectF(self.logo.rect()))
            x += lw + h * 0.3
        else:
            sz = int(h * 0.62)
            p.drawPixmap(int(x), int(r.top() + (h - sz) / 2), theme.pixmap("logo", "accent", sz))
            x += sz + h * 0.25
        bf = theme.font(int(h * 0.3), QFont.Weight.Bold)
        p.setFont(bf)
        p.setPen(theme.color("muted"))
        bw = p.fontMetrics().horizontalAdvance(self.business) if self.business else 0
        if self.business:
            p.drawText(QRectF(x, r.top(), bw + 4, h), Qt.AlignmentFlag.AlignVCenter, self.business)
            x += bw + h * 0.45
            p.setPen(theme.color("line"))
            p.drawLine(int(x - h * 0.22), int(r.top() + h * 0.25), int(x - h * 0.22), int(r.bottom() - h * 0.25))
        af = theme.font(int(h * 0.42), QFont.Weight.Bold)
        p.setFont(af)
        p.setPen(theme.color("text"))
        aw = p.fontMetrics().horizontalAdvance(self.athlete)
        p.drawText(QRectF(x, r.top(), aw + 4, h), Qt.AlignmentFlag.AlignVCenter, self.athlete)
        x += aw + h * 0.35
        p.setFont(theme.font(int(h * 0.3), QFont.Weight.DemiBold))
        p.setPen(theme.color("muted"))
        p.drawText(QRectF(x, r.top(), r.width() * 0.4, h), Qt.AlignmentFlag.AlignVCenter, self.test)
        # right side: status pill + total clock
        tf = theme.font(int(h * 0.48), QFont.Weight.Bold, tabular=True)
        p.setFont(tf)
        tw = p.fontMetrics().horizontalAdvance(self.total)
        p.setPen(theme.color("text"))
        p.drawText(QRectF(r.right() - tw - 2, r.top(), tw + 2, h), Qt.AlignmentFlag.AlignVCenter, self.total)
        if self.status:
            sf = theme.font(int(h * 0.27), QFont.Weight.Bold)
            p.setFont(sf)
            sw = p.fontMetrics().horizontalAdvance(self.status) + h * 0.9
            pill = QRectF(r.right() - tw - h * 0.4 - sw, r.top() + h * 0.2, sw, h * 0.6)
            c = theme.color(self.status_colour, 45)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
            p.setBrush(theme.color(self.status_colour))
            d = pill.height() * 0.32
            p.drawEllipse(QRectF(pill.left() + pill.height() * 0.35, pill.center().y() - d / 2, d, d))
            p.setPen(theme.color("text"))
            p.drawText(pill.adjusted(pill.height() * 0.35 + d * 1.6, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self.status)


class LiveBoard(QWidget):
    """The full live layout. ``compact`` drops the header and charts for the operator mirror."""

    GAIT = [("overstride_cm", "ui.gait.overstride", "cm", 1), ("shin_angle_deg", "ui.gait.shin", "°", 0),
            ("trunk_lean_deg", "ui.gait.trunk", "°", 0), ("vertical_oscillation_cm", "ui.gait.vo", "cm", 1)]
    GAIT_REAR = [("pelvic_drop_deg", "ui.gait.pelvic_drop", "°", 1), ("knee_valgus_deg", "ui.gait.valgus", "°", 0),
                 ("step_width_cm", "ui.gait.step_width", "cm", 1), ("vertical_oscillation_cm", "ui.gait.vo", "cm", 1)]
    # The second view's row of a test with two cameras: the bounce is already in the side row.
    GAIT_REAR_2 = GAIT_REAR[:3] + [("arm_cross_cm", "ui.gait.arm_cross", "cm", 1)]

    def __init__(self, ctl: LiveController, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctl = ctl
        self.compact = compact
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        plan = ctl.plan
        # The sport decides the tiles: speed, incline and steps for running; watt, target and pedal
        # revolutions for cycling; watt, pace per 500 m and strokes for SkiErg.
        self.sport = getattr(plan, "sport", "run") or "run"
        self.prof = profile(self.sport)
        self.power = self.prof.load_quantity == "power"
        self.total_stages = sum(1 for s in plan.protocol.stages if not s.is_warmup)
        self.open_ended = plan.protocol.open_ended

        root = QVBoxLayout(self)
        gap = 12 if compact else 18
        root.setContentsMargins(0 if compact else 28, 0 if compact else 20, 0 if compact else 28, 0 if compact else 24)
        root.setSpacing(gap)
        self.header = Header()
        brand = ctl.ctx.branding()
        self.header.business = brand.get("business") or ""
        if brand.get("logo"):
            pm = QPixmap(str(brand["logo"]))
            self.header.logo = pm if not pm.isNull() else None
        self.header.athlete = plan.athlete.first_name    # the TV is seen by the whole room: first name only
        self.header.test = plan.protocol.name
        if not compact:
            root.addWidget(self.header, 0)

        # widgets
        self.video = VideoView(radius=18)
        self.video.placeholder = t("ui.video.no_camera_test") if plan.camera is None else t("ui.video.starting")
        self.banner = PromptBanner(self.video)
        gait_row = QHBoxLayout()
        gait_row.setSpacing(gap)
        gait_row.setContentsMargins(0, 0, 0, 0)
        # Bike fit and double poling: the running tiles would measure the wrong thing. In their place a row
        # of the test's own values (pose.technique_live): the same chain and ranges as the report, live,
        # as the median of the newest revolutions or strokes.
        tech = self.tech = plan.kind in TECHNIQUE_KINDS
        self.gait_tiles: dict[str, tuple[GaitTile, int]] = {}
        self.tech_tiles: dict[str, tuple[GaitTile, Any]] = {}
        self.tech2_tiles: dict[str, tuple[GaitTile, Any]] = {}
        spec = [] if tech else self.GAIT_REAR if plan.view == "rear" else self.GAIT
        for key, title, unit, dec in spec:
            tile = GaitTile(t(title), unit)
            tile.reference = gait_ref(key)
            self.gait_tiles[key] = (tile, dec)
            gait_row.addWidget(tile)
        gw = QWidget()
        gw.setLayout(gait_row)
        if tech and plan.camera is not None:
            gw = self._tech_row(plan.view, self.tech_tiles, gap) or gw
        if not self.tech_tiles and (tech or (plan.camera is None and (plan.kind not in GAIT_KINDS
                                                                       or plan.kind in CAMERA_OPTIONAL_KINDS))):
            gw.hide()
        # Two views: a second picture beside the first and a second row of tiles, each with
        # the measures of its own view. With one camera none of this exists and the layout is as before.
        self.two = getattr(ctl, "analyzer2", None) is not None
        self.video2: VideoView | None = None
        self.gait2_tiles: dict[str, tuple[GaitTile, int]] = {}
        video_area: QWidget = self.video
        gw2: QWidget | None = None
        if self.two:
            view2 = getattr(plan, "view2", "rear")
            self.video.caption = t(f"ui.view.{plan.view}")
            self.video2 = VideoView(radius=18)
            self.video2.caption = t(f"ui.view.{view2}")
            self.video2.placeholder = t("ui.video.starting")
            video_area = QWidget()
            vl = QHBoxLayout(video_area)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(gap)
            vl.addWidget(self.video, 3)
            vl.addWidget(self.video2, 2)
            row2 = QHBoxLayout()
            row2.setSpacing(gap)
            row2.setContentsMargins(0, 0, 0, 0)
            spec2 = [] if tech else self.GAIT_REAR_2 if view2 == "rear" else self.GAIT
            for key, title, unit, dec in spec2:
                tile = GaitTile(t(title), unit)
                tile.reference = gait_ref(key)
                self.gait2_tiles[key] = (tile, dec)
                row2.addWidget(tile)
            gw2 = QWidget()
            gw2.setLayout(row2)
            if tech:
                gw2 = self._tech_row(view2, self.tech2_tiles, gap) or gw2
                gw2.setVisible(bool(self.tech2_tiles))
        self.ring = CountdownRing()
        self.next_card = NextStageCard()
        self.t_hr = MetricTile(t("ui.live.hr"), t("ui.unit.bpm"), "heart")
        if self.power:
            self.t_speed = MetricTile(t("ui.live.power"), "W", "gauge")
            # the second tile: the pace per 500 m on a SkiErg, the target watt on a trainer
            self.t_incline = (MetricTile(t("ui.live.pace_500"), self.prof.pace_unit or "", "activity")
                              if self.prof.pace_unit else MetricTile(t("ui.live.target_power"), "W", "chevron-right"))
            self.t_cadence = MetricTile(t(f"ui.live.cadence.{self.sport}"), t(f"ui.unit.cadence.{self.sport}"), "activity")
        else:
            self.t_speed = MetricTile(t("ui.live.speed"), "km/h", "gauge")
            self.t_incline = MetricTile(t("ui.live.incline"), "%", "incline")
            self.t_cadence = MetricTile(t("ui.live.cadence"), t("ui.unit.spm"), "steps")
        self.hr_chart = TimeChart(t("ui.live.hr_chart"), window_s=900, colour="fix")
        self.lac_chart = LactateChart(t("ui.live.lactate_chart"), live=True)
        self.lac_chart.x_unit = self.prof.load_unit
        self.lac_chart.x_decimals = self.prof.load_decimals
        # Watt over time takes the place under the picture in a test on a trainer or a SkiErg: those
        # tests are not filmed, so the picture area only carries the prompts.
        self.power_chart = TimeChart(t("ui.live.power_chart"), window_s=900, colour="accent")
        self.power_chart.y_min_span = 60.0
        self.power_chart.setVisible(self.power and not compact)
        if self.power and plan.camera is None:
            self.video.placeholder = ""
        has_lac = plan.protocol.kind == "lactate" or any(s.sample_lactate for s in plan.protocol.stages)

        if compact:
            # laptop mirror: wide video with the stage column beside it, tiles and gait below
            top = QHBoxLayout()
            top.setSpacing(gap)
            top.addWidget(video_area, 4 if self.two else 3)
            col = QVBoxLayout()
            col.setSpacing(gap)
            col.addWidget(self.ring, 3)
            col.addWidget(self.next_card, 2)
            top.addLayout(col, 1)
            root.addLayout(top, 1)
            tiles = QHBoxLayout()
            tiles.setSpacing(gap)
            for w in (self.t_hr, self.t_speed, self.t_incline, self.t_cadence):
                tiles.addWidget(w)
            tw = QWidget()
            tw.setLayout(tiles)
            tiles.setContentsMargins(0, 0, 0, 0)
            tw.setFixedHeight(130)
            root.addWidget(tw)
            gw.setFixedHeight(112 if gw2 is None else 96)
            root.addWidget(gw)
            if gw2 is not None:
                gw2.setFixedHeight(96)
                root.addWidget(gw2)
            self.hr_chart.hide()
            self.lac_chart.hide()
        else:
            # TV: video and gait on the left, stage, tiles and charts on the right
            body = QHBoxLayout()
            body.setSpacing(gap)
            root.addLayout(body, 1)
            left = QVBoxLayout()
            left.setSpacing(gap)
            left.addWidget(video_area, 5)
            # a bike fit or double poling: the angles are what the room looks at, the watt curve gives way
            rows = 2 if self.tech_tiles else 1
            if self.power:
                left.addWidget(self.power_chart, 3 if self.tech2_tiles else 5)
            left.addWidget(gw, rows)
            if gw2 is not None:
                left.addWidget(gw2, rows)
            body.addLayout(left, 62)
            right = QVBoxLayout()
            right.setSpacing(gap)
            top = QHBoxLayout()
            top.setSpacing(gap)
            top.addWidget(self.ring, 1)
            top.addWidget(self.next_card, 1)
            right.addLayout(top, 5)
            grid = QGridLayout()
            grid.setSpacing(gap)
            grid.addWidget(self.t_hr, 0, 0)
            grid.addWidget(self.t_speed, 0, 1)
            grid.addWidget(self.t_incline, 1, 0)
            grid.addWidget(self.t_cadence, 1, 1)
            right.addLayout(grid, 8)
            charts = QHBoxLayout()
            charts.setSpacing(gap)
            charts.setContentsMargins(0, 0, 0, 0)
            charts.addWidget(self.hr_chart)
            charts.addWidget(self.lac_chart)
            self.lac_chart.setVisible(has_lac)
            cw = QWidget()
            cw.setLayout(charts)
            right.addWidget(cw, 5)
            body.addLayout(right, 38)

        if ctl.zones:
            self.hr_chart.bands = [(z["hr_low"] or 0, z["hr_high"] or 250, c)
                                   for z, c in zip(ctl.zones, ["muted", "accent2", "accent", "watch", "fix"])]
        ctl.state_changed.connect(self.on_state)
        ctl.hr_changed.connect(self.on_hr)
        ctl.treadmill_changed.connect(self.on_treadmill)
        ctl.gait_changed.connect(self.on_gait)
        if self.tech_tiles:
            ctl.tech_changed.connect(lambda v: self.on_tech(v, self.tech_tiles))
        if self.tech2_tiles:
            ctl.tech2_changed.connect(lambda v: self.on_tech(v, self.tech2_tiles))
        ctl.lactate_changed.connect(self.on_lactate)
        self._cd_phase: Phase | None = None
        self._cd_max = 0.0
        self._camera_cadence: float | None = None
        self.video.attach(ctl.latest)
        if self.video2 is not None:
            ctl.gait2_changed.connect(self.on_gait2)
            self.video2.attach(ctl.latest2)
            self._lost_timer = QTimer(self)
            self._lost_timer.timeout.connect(self._check_camera2)
            self._lost_timer.start(1000)
        self._show_idle()

    # ------------------------------------------------------------------ updates
    def resizeEvent(self, e: Any) -> None:
        super().resizeEvent(e)
        h = self.height()
        self.header.setFixedHeight(max(44, int(h * 0.065)))
        self._place_banner()

    def _place_banner(self) -> None:
        vr = self.video.rect()
        bh = max(70, int(vr.height() * 0.2))
        m = int(vr.width() * 0.04)
        self.banner.setGeometry(m, m, vr.width() - 2 * m, bh)

    def _stage_text(self, i: int) -> str:
        stages = self.ctl.stages()
        if i < len(stages) and stages[i].is_warmup:
            return t("ui.live.warmup").upper()
        n = sum(1 for s in stages[: i + 1] if not s.is_warmup)
        if self.open_ended or n > self.total_stages:
            return t("ui.live.stage_open", n=n)
        return t("ui.live.stage_of", n=n, total=self.total_stages)

    def _show_idle(self) -> None:
        stages = self.ctl.plan.protocol.stages
        first = stages[0] if stages else None
        self.ring.set_state(t("ui.live.ready"), "0:00", t("ui.live.waiting_start"), 0.0)
        if first:
            self.next_card.set_next(t("ui.live.first"), self._load_text(first), self._stage_lines(first))
        self.header.status, self.header.status_colour = t("ui.live.status.ready"), "muted"
        self.header.update()

    def _load_text(self, stage: Any) -> str:
        """A stage's load with the sport's unit ("12,0 km/h", "200 W"); a free stage says so."""
        if getattr(stage, "free", False):
            return t("ui.live.free")
        return load(stage.speed_kmh, self.sport)

    def _stage_lines(self, stage: Any) -> list[str]:
        """The small lines under a stage's load: the incline for running, the pace on a SkiErg."""
        lines = []
        if self.prof.has_incline:
            lines.append(t("ui.live.incline_line", v=num(stage.incline_pct)))
        elif self.prof.pace_unit and not getattr(stage, "free", False):
            lines.append(t("ui.live.pace_line", p=pace(stage.speed_kmh, self.sport), unit=self.prof.pace_unit))
        elif getattr(stage, "free", False):
            lines.append(t("ui.live.free_line", target=load(stage.speed_kmh, self.sport)))
        lines.append(t("ui.live.duration_line", v=clock(stage.duration_s)))
        return lines

    def on_state(self, st: RunnerState) -> None:
        self.header.total = clock(st.total_elapsed_s)
        ph = st.phase
        stage = st.stage
        dur = st.stage_elapsed_s + st.stage_remaining_s
        frac = st.stage_elapsed_s / dur if dur > 0 else 0.0
        cd = getattr(st, "countdown_s", None)
        big_cd = clock(cd if cd is not None else st.stage_remaining_s)
        if ph != self._cd_phase:
            self._cd_phase, self._cd_max = ph, 0.0
        if cd is not None and ph in (Phase.COUNTDOWN, Phase.LACTATE_PAUSE, Phase.RECOVERY):
            self._cd_max = max(self._cd_max, cd)
            frac = 1.0 - cd / self._cd_max if self._cd_max > 0 else 0.0
        ann = getattr(st, "announcement", "") or ""
        status, scol = t("ui.live.status.running"), "good"
        if self.ctl.paused:
            status, scol = t("ui.live.status.paused"), "watch"
            self.banner.show_prompt(t("ui.live.paused"), t("ui.live.paused_sub"), "watch", "pause")
        elif ph == Phase.COUNTDOWN:
            status, scol = t("ui.live.status.starting"), "accent2"
            self.ring.set_state(t("ui.live.starts_in"), big_cd, t("ui.live.get_ready"), frac, "accent2")
            self.banner.show_prompt(st.prompt or t("ui.live.get_ready_banner"), ann, "accent", "run")
        elif ph == Phase.LACTATE_PAUSE:
            status, scol = t("ui.live.status.lactate"), "watch"
            self.ring.set_state(t("ui.live.lactate_short"), big_cd, t("ui.live.pause_left"), frac, "watch")
            self.banner.show_prompt(t("ui.live.take_lactate"), ann or t("ui.live.take_lactate_sub"), "watch",
                                    "droplet", flash=True)
        elif ph == Phase.AWAIT_OPERATOR:
            status, scol = t("ui.live.status.operator"), "accent2"
            sp = num(stage.speed_kmh) if stage else "–"
            self.ring.set_state(self._stage_text(st.stage_index), "–:–", t("ui.live.waiting_operator"), 0.0, "accent2")
            self.banner.show_prompt(st.prompt or t("ui.live.set_speed", speed=sp),
                                    "" if self.power else
                                    t("ui.live.set_speed_sub", incline=num(stage.incline_pct) if stage else "–"),
                                    "accent2", "gauge", flash=True)
        elif ph == Phase.RECOVERY:
            status, scol = t("ui.live.status.recovery"), "good"
            self.ring.set_state(t("ui.live.recovery").upper(), big_cd, t("ui.live.recovery_sub"), frac, "good")
            self.banner.show_prompt(t("ui.live.recovery_banner"), ann or t("ui.live.recovery_banner_sub"), "good", "heart")
        elif ph == Phase.RUNNING:
            if getattr(st, "low_cadence", False):     # ERG cannot hold the watt at a low cadence
                self.banner.show_prompt(t("ui.live.low_cadence"), t("ui.live.low_cadence_sub"), "watch", "alert",
                                        flash=True)
            elif getattr(st, "self_paced", False) and st.prompt:
                # the athlete holds the load: the target stays on the screen, amber when it is missed
                off = getattr(st, "off_target", False)
                self.banner.show_prompt(st.prompt, t("ui.live.off_target") if off else ann,
                                        "watch" if off else "accent", "gauge")
            elif st.prompt:  # e.g. lactate sample on the move, or the coming change
                self.banner.show_prompt(st.prompt, ann, "watch", "droplet" if st.stage and st.stage.sample_lactate else "gauge",
                                        flash=True)
            elif ann and st.stage_remaining_s <= 15:
                self.banner.show_prompt(ann, "", "accent2", "chevron-right")
            else:
                self.banner.clear()
            last = st.next_stage is None
            self.ring.set_state(self._stage_text(st.stage_index), clock(st.stage_remaining_s),
                                t("ui.live.to_end") if last else t("ui.live.to_next"), frac,
                                "watch" if st.stage_remaining_s <= 10 else "accent")
        elif ph == Phase.FINISHED:
            status, scol = t("ui.live.status.done"), "good"
            self.ring.set_state(t("ui.live.done"), clock(st.total_elapsed_s), t("ui.live.total_time"), 1.0, "good")
            self.banner.show_prompt(t("ui.live.finished"), t("ui.live.finished_sub"), "good", "check")
        elif ph == Phase.ABORTED:
            status, scol = t("ui.live.status.stopped"), "fix"
            self.banner.show_prompt(t("ui.live.stopped"), "", "fix", "stop")
        self.header.status, self.header.status_colour = status, scol
        self.header.update()
        nxt = st.next_stage
        if ph == Phase.AWAIT_OPERATOR or ph == Phase.COUNTDOWN:
            nxt = stage
        if nxt is not None and ph not in (Phase.FINISHED, Phase.ABORTED, Phase.RECOVERY):
            lines = self._stage_lines(nxt)
            if stage is not None and stage.sample_lactate and ph == Phase.RUNNING:
                lines.insert(0, "!" + t("ui.live.lactate_after"))
            cap = t("ui.live.first") if ph in (Phase.COUNTDOWN, Phase.AWAIT_OPERATOR) else t("ui.live.next")
            self.next_card.set_next(cap, self._load_text(nxt), lines)
        elif ph == Phase.RUNNING and self.tech:      # bike fit and double poling: an even stage, then the end
            self.next_card.set_next(t("ui.live.next"), t("ui.live.last_stage"), [t("ui.live.film_steady")])
        elif ph == Phase.RUNNING:
            self.next_card.set_next(t("ui.live.next"), t("ui.live.last_stage"), [t("ui.live.keep_going")])
        elif ph == Phase.RECOVERY:
            self.next_card.set_next(t("ui.live.next"), t("ui.live.walk" if not self.power else
                                                         f"ui.live.easy.{self.sport}"), [t("ui.live.recovery_sub")])
        self._state = st
        if self.power:
            self._power_tiles(self.ctl.treadmill)
        elif stage is not None and (self.ctl.treadmill is None or not getattr(self.ctl.treadmill, "measured", True)):
            self._speed_tiles(stage.speed_kmh, stage.incline_pct, target=True)
        self._place_banner()

    def on_hr(self, v: tuple[int, int]) -> None:
        bpm, zone = v
        col = theme.ZONE_COLORS[zone - 1] if zone else None
        mx = self.ctl.plan.athlete.max_hr_for(self.ctl.plan.sport)
        sub = t("ui.live.zone", z=zone) if zone else ""
        if mx:
            sub += ("  ·  " if sub else "") + f"{round(100 * bpm / mx)} % {t('ui.live.of_max')}"
        self.t_hr.set_value(str(bpm), sub, col, bpm / mx if mx else None, tint_value=zone >= 2)
        self.hr_chart.add(self.ctl.elapsed(), bpm)
        if not self.power and self._strap_cadence() is not None:
            self._cadence_tile()

    def _strap_cadence(self) -> float | None:
        fn = getattr(self.ctl, "strap_cadence", None)
        return fn() if callable(fn) else None

    def _cadence_tile(self) -> None:
        """The strap's cadence (RSC) when it has one, else the camera's; the title says which."""
        strap = self._strap_cadence()
        cad = strap if strap is not None else self._camera_cadence
        title = cadence_label("strap" if strap is not None else None, "ui.live.cadence")
        if self.t_cadence.title != title:
            self.t_cadence.title = title
            self.t_cadence.update()
        st = gait_status("cadence_spm", cad)
        self.t_cadence.set_value(num(cad, 0), t(f"ui.status.{st}") if st else "",
                                 {"ok": None, "watch": "watch", "fix": "fix"}.get(st or "ok"))

    def on_treadmill(self, s: Any) -> None:
        if self.power:
            self._power_tiles(s)
            if getattr(s, "measured", True) and getattr(s, "power_w", None) is not None:
                self.power_chart.add(self.ctl.elapsed(), float(s.power_w))
            return
        self._speed_tiles(s.speed_kmh, s.incline_pct, target=not getattr(s, "measured", True))

    def _power_tiles(self, s: Any) -> None:
        """Watt, target or pace, and cadence from a trainer or a SkiErg. In manual mode there are no
        readings: the watt tile shows the target, marked as such."""
        st = getattr(self, "_state", None)
        stage = st.stage if st is not None else None
        target = getattr(st, "target_load", None) if st is not None else None
        if target is None and stage is not None and not stage.free and st is not None and st.phase in (
                Phase.RUNNING, Phase.COUNTDOWN, Phase.AWAIT_OPERATOR):
            target = stage.speed_kmh
        measured = s is not None and getattr(s, "measured", True) and getattr(s, "power_w", None) is not None
        watt = s.speed_kmh if measured else target
        off = bool(st is not None and getattr(st, "off_target", False))
        kg = self.ctl.plan.athlete.weight_kg
        subs = []
        if not measured:
            subs.append(t("ui.live.target"))
        elif target and self.prof.pace_unit:              # SkiErg: the watt tile says what to hold
            subs.append(t("ui.live.target_w", w=num(target, 0)))
        if watt and kg and self.sport == "bike":
            subs.append(f"{num(watt / kg, 2)} W/kg")
        self.t_speed.set_value(num(watt, 0), "  ·  ".join(subs), "watch" if off else None)
        if self.prof.pace_unit:
            p_now = getattr(s, "pace_500m_s", None) if measured else None
            now = clock(p_now) if p_now else pace(watt, self.sport)
            self.t_incline.set_value(now, t("ui.live.target_pace", p=pace(target, self.sport)) if target else "",
                                     "watch" if off else None)
        else:
            free = bool(stage is not None and stage.free and st is not None and st.phase == Phase.RUNNING)
            self.t_incline.set_value(t("ui.live.free") if free else num(target, 0),
                                     t("ui.live.free_sub") if free else (t("ui.live.erg") if measured and target else ""))
        cad = getattr(s, "cadence", None) if s is not None else None
        low = bool(st is not None and getattr(st, "low_cadence", False))
        self.t_cadence.set_value(num(cad, 0) if cad else "–", t("ui.live.cadence_low") if low else "",
                                 "watch" if low else None)

    def _speed_tiles(self, speed: float | None, incline: float | None, target: bool) -> None:
        sub = t("ui.live.pace", p=pace(speed)) + (f"  ·  {t('ui.live.target')}" if target else "")
        self.t_speed.set_value(num(speed), sub)
        self.t_incline.set_value(num(incline), t("ui.live.target") if target else "")

    def on_gait(self, g: Any) -> None:
        if g is None or self.tech:      # the running chain says nothing about a cyclist or a skier
            return
        self._camera_cadence = getattr(g, "cadence_spm", None)
        self._cadence_tile()
        for key, (tile, dec) in self.gait_tiles.items():
            v = getattr(g, key, None)
            s = gait_status(key, v)
            tile.set_value(num(v, dec), s, t(f"ui.status.{s}") if s else t("ui.status.none"), gait_ref(key))

    def on_gait2(self, g: Any) -> None:
        """The second view's summary: its own row of tiles."""
        if g is None or self.tech:
            return
        for key, (tile, dec) in self.gait2_tiles.items():
            v = getattr(g, key, None)
            s = gait_status(key, v)
            tile.set_value(num(v, dec), s, t(f"ui.status.{s}") if s else t("ui.status.none"), gait_ref(key))

    def _tech_row(self, view: str, tiles: dict[str, tuple[GaitTile, Any]], gap: int) -> QWidget | None:
        """The row of a bike fit's or a double poling's live values for one view; None when it has none."""
        from lopanalys.pose.technique import EXPERIENCE
        from lopanalys.pose.technique_live import MIN_CYCLES, metrics, refs
        kind = self.ctl.plan.kind
        keys = metrics(kind, view)
        if not keys:
            return None
        R = refs(kind, view)
        row = QHBoxLayout()
        row.setSpacing(gap)
        row.setContentsMargins(0, 0, 0, 0)
        waiting = t(f"ui.tech.waiting.{kind}", n=MIN_CYCLES)
        for key in keys:
            ref = R[key]
            tile = GaitTile(t(f"ui.tech.{key}"), ref.unit)
            tile.reference = ref.text() + (f"  ·  {t('technique.basis.experience')}" if ref.basis == EXPERIENCE else "")
            tile.set_value("–", None, waiting, tile.reference)
            tiles[key] = (tile, ref)
            row.addWidget(tile)
        w = QWidget()
        w.setLayout(row)
        return w

    def on_tech(self, v: Any, tiles: dict[str, tuple[GaitTile, Any]]) -> None:
        """A bike fit's or a double poling's live values: "–" until enough revolutions or strokes."""
        if v is None:
            return
        from lopanalys.pose.technique_live import MIN_CYCLES
        values, status = getattr(v, "values", {}) or {}, getattr(v, "status", {}) or {}
        waiting = t(f"ui.tech.waiting.{self.ctl.plan.kind}", n=MIN_CYCLES)
        for key, (tile, ref) in tiles.items():
            val = values.get(key)
            st = status.get(key)
            if val is None:
                text = waiting if getattr(v, "cycles", 0) < MIN_CYCLES else t("ui.status.none")
                tile.set_value("–", None, text, tile.reference)
            else:
                tile.set_value(num(val, ref.decimals), st, t(f"ui.status.{st}") if st else t("ui.status.none"),
                               tile.reference)

    def _check_camera2(self) -> None:
        """Says so in the second picture when its camera is lost; the test goes on with the first."""
        if self.video2 is None:
            return
        lost = bool(self.ctl.camera2_lost())
        want = t("ui.live.camera2_lost") if lost else t(f"ui.view.{getattr(self.ctl.plan, 'view2', 'rear')}")
        if self.video2.caption != want:
            self.video2.caption = want
            self.video2.update()

    def detach_video(self) -> None:
        """Stop polling the camera pictures (the page or the TV window is closing)."""
        self.video.detach()
        if self.video2 is not None:
            self.video2.detach()
            self._lost_timer.stop()

    def on_lactate(self, pts: list[tuple[float, float]]) -> None:
        self.lac_chart.set_data(pts)


class LiveWindow(QWidget):
    """Frameless TV window. F11 toggles full screen, Esc leaves full screen (or closes)."""

    def __init__(self, ctl: LiveController, screen_index: int = -1) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(t("ui.live.tv_title"))
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#Root {{ background: {theme.TOKENS['ink']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.board = LiveBoard(ctl)
        lay.addWidget(self.board)
        self.screen_index = screen_index
        self.resize(1920, 1080)

    def show_on_screen(self) -> None:
        """Show full screen on the chosen screen (the TV); falls back to a normal window."""
        from PySide6.QtGui import QGuiApplication
        screens = QGuiApplication.screens()
        idx = self.screen_index
        if idx < 0 or idx >= len(screens):
            idx = len(screens) - 1 if len(screens) > 1 else 0
        if screens:
            g = screens[idx].geometry()
            self.setGeometry(g)
        if len(screens) > 1:
            self.showFullScreen()
        else:
            self.show()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif e.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.close()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e: Any) -> None:
        self.board.detach_video()
        super().closeEvent(e)
