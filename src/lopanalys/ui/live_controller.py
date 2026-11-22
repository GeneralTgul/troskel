"""Runs one test: camera + analyzer, devices, the protocol runner, recording, and saving.

Both the coach's operator view and the TV window listen to the same controller, so they always
show the same state. All bus traffic arrives through ``BusBridge`` on the GUI thread.

Saving (a test must always be saved): the session is written
to the database with status "running" when the test starts, and again on every completed stage,
every lactate or RPE value and every ``CHECKPOINT_S`` seconds (with the 1 Hz samples). When the test
ends (finished, exhausted, stopped, window or program closed) it is saved as "finished" or
"stopped" without the coach doing anything. A test still "running" at the next program start was
cut off; ``ui.main_window`` offers to open it and, when reasonable, to continue it (``resume_from``).

Two views: with ``plan.camera2`` a second source runs beside the first, with its own analyzer,
recorder and summary (``latest2()``, ``gait2_changed``). Nothing is calibrated between them; both
stamp their frames with the same clock, and the offset between the two films is saved with the
test. The second view's live analysis has a ceiling for its frame rate, lowered further when the
main view cannot keep up with its camera (``_balance``). With one camera nothing of this runs.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from lopanalys.assessments.protocol import Phase, Protocol, RunnerState
from lopanalys.core import paths
from lopanalys.core.i18n import t
from lopanalys.core.models import StepRecord, TimeSample, distance_m
from lopanalys.ui import demo, services
from lopanalys.ui.context import GAIT_KINDS, TECHNIQUE_KINDS, AppContext, ResumeFrom, TestOutcome, TestPlan

log = logging.getLogger(__name__)

# Traffic-light targets for the live gait tiles come from pose.findings (the single source of reference
# ranges, shared with the findings and the report). Cadence and overstride depend on speed and height,
# so the controller calls update_gait_targets() when either is known or changes.
_targets: dict[str, Any] = {}
_targets_key: tuple[float | None, float | None] | None = None


def update_gait_targets(speed_kmh: float | None = None, height_cm: float | None = None) -> None:
    """Recompute the live reference ranges for this speed (rounded to 0.5 km/h) and athlete height."""
    global _targets, _targets_key
    key = (round(speed_kmh * 2) / 2 if speed_kmh else None, height_cm)
    if key == _targets_key:
        return
    try:
        from lopanalys.pose.findings import live_targets
        _targets, _targets_key = live_targets(speed_kmh=key[0], height_cm=height_cm), key
    except Exception:  # the tiles then show values without a status
        log.exception("live gait targets unavailable")
        _targets, _targets_key = {}, key


def gait_ref(metric: str) -> str:
    """Localised target range for a live gait tile, e.g. 'minst 160 steg/min'; '' when unknown."""
    if _targets_key is None:
        update_gait_targets()
    r = _targets.get(metric)
    return r.text if r is not None else ""


def gait_status(metric: str, value: float | None) -> str | None:
    """'ok' | 'watch' | 'fix' for a live gait value, None when unknown."""
    if _targets_key is None:
        update_gait_targets()
    r = _targets.get(metric)
    if value is None or r is None:
        return None
    sev = r.classify(value)
    return sev.value if sev is not None else None


STRAP_CADENCE_STALE_S = 5.0   # older strap cadence is not shown; the camera's is used
CHECKPOINT_S = 10.0           # the running test (with its samples) is written this often
CADENCE_LOG_S = 60.0          # what the cadence tiles show is logged this often
BALANCE_S = 5.0               # two views: how often the second view's analysis rate is reconsidered


class _RecorderThread:
    """Writes each new analyzer frame to a camera.recorder.Recorder (off the GUI thread)."""

    def __init__(self, analyzer: Any, path: Path, raw: bool = False, gated: bool = False,
                 poll_s: float = 1 / 60) -> None:
        self.analyzer, self.path = analyzer, path
        # raw: every frame the camera delivered, not only the analysed ones (the second view's live
        # analysis leaves frames on purpose; its film must not).
        self.raw = raw and callable(getattr(analyzer, "latest_frame", None))
        # gated: nothing is written until arm() (bike fit and double poling film the working stage only)
        self.armed = threading.Event()
        if not gated:
            self.armed.set()
        self.poll_s = poll_s              # a 120 fps camera needs a poll faster than its frames
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ui-recorder")
        self.ok = False
        self.first_t: float | None = None   # the first frame's time on the shared clock

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        rec = None
        last = None
        try:
            while not self._stop.wait(self.poll_s):
                if not self.armed.is_set():
                    continue
                if self.raw:
                    fr = self.analyzer.latest_frame()
                else:
                    r = self.analyzer.latest()
                    fr = getattr(r, "frame", None) if r else None
                if fr is None or getattr(fr, "index", None) == last:
                    continue
                last = fr.index
                if self.first_t is None:
                    self.first_t = float(fr.t)
                img = fr.image
                if rec is None:
                    fps = getattr(getattr(self.analyzer, "source", None), "info", None)
                    fps = getattr(fps, "fps", None) or 30.0
                    rec = services.make_recorder(self.path, fps, (img.shape[1], img.shape[0]))
                rec.write(fr)   # the Frame, so the sidecar gets the capture time, not this poll's
                self.ok = True
        except Exception:
            log.exception("recording failed")
        finally:
            if rec is not None:
                try:
                    rec.close()
                except Exception:
                    log.exception("recorder close failed")

    def arm(self) -> None:
        self.armed.set()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class LiveController(QObject):
    """One running test. Create, ``open_camera()`` for preview, ``start()``, then react to signals."""

    state_changed = Signal(object)        # RunnerState
    hr_changed = Signal(object)           # (bpm, zone 1..5 | 0)
    treadmill_changed = Signal(object)    # TreadmillState
    gait_changed = Signal(object)         # GaitSummary
    gait2_changed = Signal(object)        # GaitSummary of the second camera view
    tech_changed = Signal(object)         # pose.technique_live.TechniqueLive (bike fit, double poling)
    tech2_changed = Signal(object)        # the same for the second camera view
    lactate_changed = Signal(object)      # list[(speed, mmol)]
    stage_changed = Signal(int)           # new stage index
    cue = Signal(str)                     # 'tick' | 'go' | 'alert'
    finished = Signal(object)             # TestOutcome
    error = Signal(str)

    def __init__(self, ctx: AppContext, plan: TestPlan) -> None:
        super().__init__()
        self.ctx, self.plan = ctx, plan
        self.runner: Any = None
        self.analyzer: Any = None
        self.source: Any = None
        self.session = ctx.new_session(plan)
        self.state: RunnerState | None = None
        self.hr: int | None = None
        self.hr_series: list[tuple[float, int]] = []
        self.cadence_series: list[tuple[float, float]] = []   # strap cadence (RSC): (elapsed s, steps/min)
        self._cad_logged: dict[str, float] = {}               # log_cadence_tile: when each view was logged
        self.treadmill: Any = None
        self.gait: Any = None
        self.lactate: dict[int, float] = {}
        self.rpe: dict[int, int] = {}
        self.stage_gait: dict[int, dict[str, Any]] = {}
        self.analyzer2: Any = None            # the second camera view (plan.camera2)
        self.source2: Any = None
        self.gait2: Any = None
        self.tech: Any = None                 # the newest live values of a bike fit or double poling
        self.tech2: Any = None
        self.stage_gait2: dict[int, dict[str, Any]] = {}
        self._recorder2: _RecorderThread | None = None
        self.paused = False
        self._done = False
        self._t0 = time.perf_counter()
        self._last_stage = -1
        self._recorder: _RecorderThread | None = None
        self._resume: ResumeFrom | None = None
        self._started = False
        self._closing = False
        self._saved_counts = (0, 0)
        self._save_failed = False
        self._ckpt = QTimer(self)
        self._ckpt.setInterval(int(CHECKPOINT_S * 1000))
        self._ckpt.timeout.connect(lambda: self._checkpoint(samples=True))
        self._balance_timer = QTimer(self)
        self._balance_timer.setInterval(int(BALANCE_S * 1000))
        self._balance_timer.timeout.connect(self._balance)
        self._balance_n = 0
        a = plan.athlete
        mx = a.max_hr_for(plan.sport)      # the zones on the TV are the sport's own, never another sport's
        self.zones = services.zones(None, mx, a.resting_hr, sport=plan.sport) if mx else []
        b = ctx.bridge
        b.test_state.connect(self._on_state)
        b.heart_rate.connect(self._on_hr)
        b.treadmill.connect(self._on_treadmill)
        b.gait_summary.connect(self._on_gait)
        b.gait_summary_2.connect(self._on_gait2)
        b.tech_summary.connect(self._on_tech)
        b.tech_summary_2.connect(self._on_tech2)
        b.test_cue.connect(self._on_cue)

    # ---------------------------------------------------------------- setup
    def open_camera(self) -> None:
        """Open the chosen source and start live analysis (also before the test, for framing)."""
        if self.plan.camera is None or self.analyzer is not None:
            return
        two = self.plan.camera2 is not None
        try:
            self.source = services.make_source(self.plan.camera)
            self.analyzer = services.make_live_analyzer(self.source, self.ctx.bus,
                                                        self.plan.athlete.height_cm or 175.0, self.plan.view,
                                                        name=self.plan.view if two else "", **self._technique())
            self.analyzer.start()
        except Exception as e:
            log.exception("camera start failed")
            self.analyzer = None
            self.error.emit(t("ui.err.camera_start", detail=str(e)))
        if two:
            self._open_camera2()

    def _open_camera2(self) -> None:
        """The second view: its own source and analyzer. If it cannot start, the test runs with one."""
        try:
            self.source2 = services.make_source(self.plan.camera2)
            self.analyzer2 = services.make_live_analyzer(self.source2, self.ctx.bus,
                                                         self.plan.athlete.height_cm or 175.0, self.plan.view2,
                                                         second=True, name=self.plan.view2, **self._technique())
            self.analyzer2.start()
            log.info("two views: %s (%s) and %s (%s)", self.plan.view, getattr(self.plan.camera, "kind", ""),
                     self.plan.view2, getattr(self.plan.camera2, "kind", ""))
            self._balance_timer.start()
        except Exception as e:
            log.exception("second camera start failed")
            self.analyzer2 = None
            self.error.emit(t("ui.err.camera2_start", detail=str(e)))

    def _technique(self) -> dict[str, Any]:
        """Bike fit and double poling: the skeleton without the runner's angles, and their own values live."""
        return {"labels": False, "technique": self.plan.kind} if self.plan.kind in TECHNIQUE_KINDS else {}

    def latest(self) -> Any:
        return self.analyzer.latest() if self.analyzer is not None else None

    def latest2(self) -> Any:
        """The second view's newest result (None with one camera)."""
        return self.analyzer2.latest() if self.analyzer2 is not None else None

    def camera2_lost(self) -> bool:
        """True when the second camera stopped delivering during the test (the first goes on)."""
        return bool(getattr(self.analyzer2, "source_lost", False)) if self.analyzer2 is not None else False

    def _balance(self) -> None:
        """Two views: the second view's analysis gives way when the main view cannot keep up with its
        camera, and gets its rate back when there is room. Each view's rates are logged."""
        a, b = self.analyzer, self.analyzer2
        if a is None or b is None or getattr(b, "max_fps", None) is None or not callable(getattr(a, "rates", None)):
            return
        try:
            from lopanalys.pose.multiview import balance_second_rate
            cam, ana = a.rates()
            cam2, ana2 = b.rates()
            new = balance_second_rate(cam, ana, float(b.max_fps))
            if new != b.max_fps:
                log.info("two views: the main view analyses %.1f of %.1f fps, the second view's ceiling %.0f -> %.0f fps",
                         ana, cam, b.max_fps, new)
                b.max_fps = new
            self._balance_n += 1
            if self._balance_n % 6 == 1:      # every half minute: each view's rates, for support
                log.info("two views: %s camera %.1f fps, analysis %.1f fps; %s camera %.1f fps, analysis %.1f fps (at most %.0f)",
                         self.plan.view, cam, ana, self.plan.view2, cam2, ana2, b.max_fps)
        except Exception:
            log.exception("balancing the two views failed")

    def resume_from(self, r: ResumeFrom) -> None:
        """Continue a cut-off test instead of starting a new one: same session, protocol and kind;
        the stages and samples saved before the cut are kept and the runner starts at ``r.from_index``."""
        self._resume = r
        self.session = r.session
        kind = (r.session.result or {}).get("plan_kind") or self.plan.kind
        self.plan = dataclasses.replace(self.plan, protocol=Protocol.from_dict(r.session.protocol), kind=kind,
                                        sport=r.session.sport.value)
        for rec in r.warmup + r.records:
            if rec.lactate_mmol is not None:
                self.lactate[rec.index] = rec.lactate_mmol
            if rec.rpe is not None:
                self.rpe[rec.index] = rec.rpe

    def start(self) -> bool:
        """Start the protocol (the treadmill must already be connected in the wizard)."""
        dm = self.ctx.devices
        try:
            self.runner = services.make_runner(self.plan.protocol, dm.treadmill, dm.heart_rate, self.ctx.bus,
                                               self.ctx.time_scale, services.belt_limits(self.ctx.setting))
        except Exception as e:
            log.exception("runner could not be created")
            self.error.emit(t("ui.err.start", detail=str(e)))
            return False
        r = self._resume
        offset = r.t_offset_s if r else 0.0
        self._restart_clock(time.perf_counter() - offset)
        s = self.session
        s.result = dict(s.result or {}, plan_kind=self.plan.kind)
        if r is None:
            s.started = datetime.now(timezone.utc)
        else:
            s.result["resumed"] = list(s.result.get("resumed") or []) + [
                {"from_stage": r.from_index, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "t_s": round(offset, 1)}]
        s.status, s.ended = "running", None
        tech = self.plan.kind in TECHNIQUE_KINDS
        wants_film = self.analyzer is not None and (self.plan.kind in GAIT_KINDS or tech)
        if wants_film and not self.plan.athlete.consents("film"):
            # No consent to film: the live image is analysed and shown, nothing of it is saved.
            wants_film = False
            s.result["no_film_consent"] = True
        if wants_film:
            # The film is named after the test's id, never after the person: the row is written
            # first so that the id exists. A test that then fails to start is removed again below.
            if s.id is None:
                try:
                    self.ctx.store.save_session(s)
                except Exception:
                    log.exception("saving the new test failed")
            name = f"session-{s.id or 'new'}-{datetime.now():%Y%m%d-%H%M%S}.mp4"
            if r is not None and s.video_path:   # the film from before the cut stays with the test
                s.result["earlier_videos"] = list(s.result.get("earlier_videos") or []) + [s.video_path]
            # Bike fit and double poling: every frame the camera gives (the analysis needs 60 a second,
            # the live skeleton may analyse fewer), and only from the first stage after the warm-up.
            self._recorder = _RecorderThread(self.analyzer, paths.recordings_dir() / name, raw=tech, gated=tech,
                                             poll_s=1 / 240 if tech else 1 / 60)
            s.video_path = str(self._recorder.path)   # known from the start: a crash still finds the film
            self._recorder.start()
            s.result["view"] = self.plan.view
            if tech and demo.is_demo_source(self.plan.camera):
                s.result["demo_camera"] = True     # analysed as the demo athlete (services.analyze_technique)
            old2 = s.result.pop("second_video", None)
            if r is not None and old2:               # the second film from before the cut stays too
                s.result["earlier_videos"] = list(s.result.get("earlier_videos") or []) + [old2]
            if self.analyzer2 is not None:
                self._recorder2 = _RecorderThread(self.analyzer2, paths.recordings_dir() / (name[:-4] + "-2.mp4"),
                                                  raw=True, gated=tech, poll_s=1 / 240 if tech else 1 / 60)
                s.result.update(second_video=str(self._recorder2.path), second_view=self.plan.view2)
                self._recorder2.start()
        self._started = True
        self._checkpoint(samples=True)               # the test exists in the database from now on
        try:
            if r is not None:
                self.runner.start(from_index=r.from_index, t_offset_s=offset)
            else:
                self.runner.start()
        except Exception as e:
            log.exception("runner start failed")
            self.error.emit(t("ui.err.start", detail=str(e)))
            self._started = False
            self._discard_unstarted()
            return False
        self._ckpt.start()
        return True

    # ---------------------------------------------------------------- saving while the test runs
    def _merged(self) -> tuple[list[StepRecord], list[StepRecord], list[TimeSample]]:
        """The stages (warm-up apart) and samples so far, with the values typed in the live view,
        joined with what was saved before the cut when the test was continued."""
        rn = self.runner
        recs = [dataclasses.replace(x) for x in (getattr(rn, "records", []) or [])]
        warm = [dataclasses.replace(x) for x in (getattr(rn, "warmup_records", []) or [])]
        for x in recs + warm:
            if x.lactate_mmol is None and x.index in self.lactate:
                x.lactate_mmol = self.lactate[x.index]
            if x.rpe is None and x.index in self.rpe:
                x.rpe = self.rpe[x.index]
            if not x.gait and x.index in self.stage_gait:
                x.gait = self.stage_gait[x.index]
            elif x.gait and x.index not in self.stage_gait:
                # The runner keeps the last summary it saw: with no summary during this stage (the
                # camera stopped delivering) those are an earlier stage's values, not this one's.
                x.gait = {}
            if x.index in self.stage_gait2 and "second" not in (x.gait or {}):
                x.gait = dict(x.gait or {}, second=self.stage_gait2[x.index])   # the second view's summary
        smp = list(getattr(rn, "samples", []) or [])
        r = self._resume
        if r is not None:
            new = {x.index for x in recs + warm}
            # the stages run before the cut: a value typed (or corrected) after the test was continued is theirs
            old = [dataclasses.replace(x, lactate_mmol=self.lactate.get(x.index, x.lactate_mmol),
                                       rpe=self.rpe.get(x.index, x.rpe))
                   for x in r.records + r.warmup if x.index not in new]
            warm_idx = {x.index for x in r.warmup}
            recs = [x for x in old if x.index not in warm_idx] + recs
            warm = [x for x in old if x.index in warm_idx] + warm
            smp = [x for x in r.samples if x.t_s < (smp[0].t_s if smp else 1e18)] + smp
        return recs, warm, smp

    def _store_test(self, recs: list[StepRecord], warm: list[StepRecord], smp: list[TimeSample] | None) -> None:
        st, s = self.ctx.store, self.session
        fn = getattr(getattr(st, "db", None), "save_test", None)
        if callable(fn):
            fn(s, recs, smp, warmup_records=warm)
        else:
            st.save_session(s)
            if s.id is not None:
                st.save_records(s.id, warm + recs)
                if smp is not None:
                    st.save_samples(s.id, smp)

    def _checkpoint(self, samples: bool = False) -> None:
        """Write the running test as it is now. Never raises; the first failure is shown once."""
        if not self._started or self._done or self.runner is None:
            return
        s = self.session
        recs, warm, smp = self._merged()
        done = {x.index for x in recs + warm}
        live = {"lactate": {str(k): v for k, v in self.lactate.items() if k not in done},
                "rpe": {str(k): v for k, v in self.rpe.items() if k not in done}}
        s.result = dict(s.result or {})
        self._note_runner_values()
        if live["lactate"] or live["rpe"]:
            s.result["live"] = live
        else:
            s.result.pop("live", None)
        try:
            self._store_test(recs, warm, smp if samples else None)
        except Exception as e:
            log.exception("saving the running test failed")
            if not self._save_failed:
                self._save_failed = True
                self.error.emit(t("ui.err.save_failed", detail=str(e)))

    def _discard_unstarted(self) -> None:
        """The runner never started: a new test row that was just written is removed again."""
        s = self.session
        if self._resume is None and s.id is not None:
            try:
                self.ctx.store.db.delete_session(s.id, delete_files=False)
                s.id = None
            except Exception:
                log.exception("removing the unstarted test failed")

    # ---------------------------------------------------------------- operator actions
    def pause(self) -> None:
        if self.runner:
            self.runner.pause()
            self.paused = True

    def resume(self) -> None:
        if self.runner:
            self.runner.resume()
            self.paused = False

    def stop(self) -> None:
        """Stop now. In recovery this just ends the recovery (the test is already complete)."""
        if self.runner:
            in_recovery = self.state is not None and self.state.phase == Phase.RECOVERY
            self.runner.stop()
            # the runner publishes FINISHED/ABORTED; this is only a safety net
            QTimer.singleShot(3000, lambda: self._finalize(aborted=not in_recovery))
        else:
            self.abort()

    def exhausted(self) -> None:
        """The athlete cannot go on: the runner walks them through recovery, then finishes."""
        if self.runner:
            self.runner.exhausted()

    def final_sprint(self) -> bool:
        return bool(self.runner.final_sprint()) if self.runner else False

    def in_warmup(self) -> bool:
        return bool(getattr(self.runner, "in_warmup", False)) if self.runner else False

    def start_now(self) -> bool:
        """*Börja testet nu*: end the warm-up and start the first stage."""
        return bool(self.runner.start_now()) if self.runner else False

    def shift_start(self, delta: float) -> float:
        """Move the start speed (and every stage after the warm-up with it) during the warm-up. The
        saved protocol gets the moved stages, so a test that is cut off continues at the right speed."""
        if not self.runner:
            return 0.0
        before = self.runner.start_shift
        shift = self.runner.shift_start(delta)
        if shift != before:
            s, p = self.session, self.plan.protocol
            p.stages[:] = [dataclasses.replace(x) for x in self.runner.stages[:len(p.stages)]]
            s.protocol = p.to_dict()
            s.result = dict(s.result or {}, start_shift=shift)
            self._checkpoint()
        return shift

    def stages(self) -> list[Any]:
        """The runner's stages (with generated open-ended ones), else the protocol's."""
        st = getattr(self.runner, "stages", None) if self.runner else None
        return list(st) if st else list(self.plan.protocol.stages)

    def stage_name(self, i: int) -> str:
        st = self.stages()
        if i < len(st) and st[i].is_warmup:
            return st[i].label or t("ui.live.warmup")
        n = sum(1 for k, s in enumerate(st[: i + 1]) if not s.is_warmup)
        return t("ui.live.stage_n", n=n)

    def operator_ready(self) -> None:
        if self.runner:
            self.runner.operator_ready()

    def lactate_stage(self) -> int:
        """The stage a lactate value typed now belongs to: the oldest one still waiting for a
        value, else the last completed one."""
        st = self.state
        if st is None:
            return 0
        pending = [i for i in getattr(st, "pending_lactate", []) if i not in self.lactate]
        r = self._resume
        if r is not None:       # a continued test: the runner does not know the stages run before the cut
            stages = self.plan.protocol.stages
            before = [x.index for x in r.warmup + r.records if x.index not in self.lactate
                      and x.index < len(stages) and stages[x.index].sample_lactate]
            pending = sorted(before) + pending
        if pending:
            return int(pending[0])
        if st.completed:
            return int(st.completed[-1].get("index", st.stage_index))
        return st.stage_index

    def enter_lactate(self, mmol: float, stage_index: int | None = None) -> None:
        i = self.lactate_stage() if stage_index is None else stage_index
        self.lactate[i] = mmol
        if self.runner:
            self.runner.enter_lactate(i, mmol)
        self.lactate_changed.emit(self.lactate_points())
        self._log_lactate(i, mmol)
        self._checkpoint()

    def _log_lactate(self, i: int, mmol: float) -> None:
        """Every typed value in the lactate log too (corrections and repeat samples are kept)."""
        db = getattr(self.ctx.store, "db", None)
        if self.session.id is None or not self._started or db is None:
            return
        try:
            db.add_lactate_entry(self.session.id, i, mmol)
        except Exception:
            log.exception("lactate log failed")

    def enter_rpe(self, rpe: int, stage_index: int | None = None) -> None:
        i = self.lactate_stage() if stage_index is None else stage_index
        self.rpe[i] = rpe
        if self.runner:
            self.runner.enter_rpe(i, rpe)
        self._checkpoint()

    def lactate_points(self) -> list[tuple[float, float]]:
        speeds = {}
        for r in (self.state.completed if self.state else []):
            speeds[int(r.get("index", 0))] = float(r.get("speed_kmh", 0))
        stages = self.stages()
        out = []
        for i, v in sorted(self.lactate.items()):
            if i < len(stages) and stages[i].is_warmup:
                continue
            sp = speeds.get(i, stages[i].speed_kmh if i < len(stages) else None)
            if sp is not None:
                out.append((sp, v))
        return out

    def abort(self) -> None:
        """Window closed, app quit: a running test is stopped and saved as "stopped" (no results page);
        a test that never started leaves nothing behind."""
        self._ckpt.stop()
        self._closing = True        # the runner's ABORTED may arrive at once: no results page then either
        if self._started and not self._done:
            try:
                if self.runner:
                    self.runner.stop()
            except Exception:
                log.exception("runner stop failed")
            self._finalize(aborted=True, emit=False)
            return
        self._done = True
        try:
            if self.runner:
                self.runner.stop()
        except Exception:
            log.exception("runner stop failed")
        self._teardown()

    # ---------------------------------------------------------------- bus handlers (GUI thread)
    def elapsed(self) -> float:
        return time.perf_counter() - self._t0

    def zone_of(self, bpm: int | None) -> int:
        if not bpm or not self.zones:
            return 0
        for i, z in enumerate(self.zones, 1):
            hi = z.get("hr_high")
            if hi is None or bpm <= hi:
                return i
        return len(self.zones)

    def _restart_clock(self, t0: float) -> None:
        """The test's clock starts at ``t0`` (perf_counter). The heart rate and cadence received before,
        while the camera was framed, keep their place before 0 on the new clock.

        Before 2026-11-15 they kept their times on the old clock, which were later than the new one's
        "now": the strap's cadence tile took them all as the last 12 s, and showed the median of the
        walking before the start ("about 100") for as long as the setup had taken (6 min in a test)."""
        shift = t0 - self._t0
        self._t0 = t0
        self.hr_series = [(tt - shift, h) for tt, h in self.hr_series]
        self.cadence_series = [(tt - shift, c) for tt, c in self.cadence_series]

    def strap_cadence(self, window_s: float = 12.0) -> float | None:
        """The strap's cadence (median of the last ``window_s``), or None when it has none or is stale."""
        if not self.cadence_series:
            return None
        now = self.elapsed()
        if now - self.cadence_series[-1][0] > STRAP_CADENCE_STALE_S:
            return None
        vals = sorted(c for tt, c in self.cadence_series if 0.0 <= now - tt <= window_s)
        return vals[len(vals) // 2] if vals else None

    def log_cadence_tile(self, where: str, value: float | None, source: str | None, title: str) -> None:
        """Once a minute per view (``where``: "tv" | "operator"): what the cadence tile shows, next to
        the cadence the test saves in its 1 Hz samples, so the two can be compared afterwards."""
        now = self.elapsed()
        last = self._cad_logged.get(where)
        if last is not None and 0.0 <= now - last < CADENCE_LOG_S:
            return
        self._cad_logged[where] = now
        smp = list(getattr(self.runner, "samples", []) or [])[-12:] if self.runner else []
        saved = sorted(float(x.cadence_spm) for x in smp if getattr(x, "cadence_spm", None))
        src = {getattr(x, "cadence_source", None) for x in smp if getattr(x, "cadence_spm", None)}
        log.info("cadence tile %s at %.0f s: %s steps/min, source %s, title '%s'; saved samples (last 12 s): "
                 "median %s, source %s", where, now, "–" if value is None else f"{value:.0f}", source or "none",
                 title, f"{saved[len(saved) // 2]:.0f}" if saved else "–", ",".join(sorted(map(str, src))) or "none")

    def distance_m(self) -> float | None:
        """The distance on the belt so far in metres (the 1 Hz belt speed integrated), including the
        part before a cut-off test was resumed; None before any samples."""
        smp = list(getattr(self.runner, "samples", []) or []) if self.runner is not None else []
        if self._resume is not None:
            smp = [x for x in self._resume.samples if x.t_s < (smp[0].t_s if smp else 1e18)] + smp
        return distance_m(smp) if len(smp) > 1 else None

    def _on_hr(self, s: Any) -> None:
        self.hr = int(s.bpm)
        self.hr_series.append((self.elapsed(), self.hr))
        cad = getattr(s, "cadence_spm", None)
        if cad:
            self.cadence_series.append((self.elapsed(), float(cad)))
            del self.cadence_series[:-600]
        self.hr_changed.emit((self.hr, self.zone_of(self.hr)))

    def _on_treadmill(self, s: Any) -> None:
        self.treadmill = s
        self.treadmill_changed.emit(s)

    def _on_gait(self, g: Any) -> None:
        self.gait = g
        update_gait_targets(getattr(g, "speed_kmh", None) or getattr(self.state, "speed_kmh", None),
                            self.plan.athlete.height_cm)
        if self.state and self.state.phase == Phase.RUNNING and g is not None:
            d = g.to_dict() if hasattr(g, "to_dict") else dict(vars(g))
            cad = self.strap_cadence()
            if cad is not None:     # the strap's cadence (RSC) before the camera's
                d["cadence_camera_spm"] = d.get("cadence_spm")
                d["cadence_spm"], d["cadence_source"] = cad, "strap"
            self.stage_gait[self.state.stage_index] = d
        self.gait_changed.emit(g)

    def _on_gait2(self, g: Any) -> None:
        """The second view's rolling summary: kept per stage beside the main view's."""
        if self.analyzer2 is None:
            return
        self.gait2 = g
        if self.state and self.state.phase == Phase.RUNNING and g is not None:
            self.stage_gait2[self.state.stage_index] = g.to_dict() if hasattr(g, "to_dict") else dict(vars(g))
        self.gait2_changed.emit(g)

    def _on_tech(self, v: Any) -> None:
        """Bike fit and double poling: the live values for the TV. Not saved; the film's analysis is the result."""
        if self.analyzer is None or self._done:
            return
        self.tech = v
        self.tech_changed.emit(v)

    def _on_tech2(self, v: Any) -> None:
        if self.analyzer2 is None or self._done:
            return
        self.tech2 = v
        self.tech2_changed.emit(v)

    CUE_SOUNDS = {"countdown": "tick", "go": "go", "pre_announce": "tick", "lactate": "alert",
                  "operator": "alert", "finish": "go", "abort": "alert"}

    def _on_cue(self, c: Any) -> None:
        if not self._done and self.runner is not None:
            snd = self.CUE_SOUNDS.get(getattr(c, "kind", ""))
            if snd:
                self.cue.emit(snd)

    def _on_state(self, st: RunnerState) -> None:
        if self._done:
            return
        self.state = st
        if st.stage_index != self._last_stage and st.phase == Phase.RUNNING:
            self._last_stage = st.stage_index
            self._arm_film(st.stage_index)
            self.stage_changed.emit(st.stage_index)
        self.paused = bool(getattr(st, "paused", self.paused))
        self.state_changed.emit(st)
        if st.phase in (Phase.FINISHED, Phase.ABORTED):
            self._finalize(aborted=st.phase == Phase.ABORTED)
            return
        rn = self.runner
        counts = (len(getattr(rn, "records", []) or []), len(getattr(rn, "warmup_records", []) or []))
        if counts != self._saved_counts:      # a stage was completed: write it now
            self._saved_counts = counts
            self._checkpoint(samples=True)
        elif (getattr(rn, "exhaustion_t_s", None) is not None
              and "exhaustion_t_s" not in (self.session.result or {})):
            self._checkpoint()                # exhausted without a new stage: write that now too

    def _arm_film(self, stage_index: int) -> None:
        """Bike fit and double poling: the film starts with the first stage that is not warm-up."""
        stages = self.plan.protocol.stages
        if stage_index < len(stages) and stages[stage_index].is_warmup:
            return
        armed = [rec for rec in (self._recorder, self._recorder2) if rec is not None and not rec.armed.is_set()]
        for rec in armed:
            rec.arm()
        if armed:
            log.info("film started at stage %d (%d camera(s))", stage_index + 1, len(armed))

    # ---------------------------------------------------------------- finish
    def _teardown(self) -> None:
        self._ckpt.stop()
        self._balance_timer.stop()
        for rec in (self._recorder, self._recorder2):
            if rec:
                rec.stop()
        for an in (self.analyzer, self.analyzer2):
            if an is not None:
                try:
                    an.stop()
                except Exception:
                    log.exception("analyzer stop failed")
        for sig, slot in ((self.ctx.bridge.gait_summary_2, self._on_gait2),
                          (self.ctx.bridge.test_state, self._on_state), (self.ctx.bridge.heart_rate, self._on_hr),
                          (self.ctx.bridge.treadmill, self._on_treadmill), (self.ctx.bridge.gait_summary, self._on_gait),
                          (self.ctx.bridge.test_cue, self._on_cue)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _finalize(self, aborted: bool, emit: bool = True) -> None:
        """The test ended (or the window closed): save it as finished or stopped, whatever it holds.

        Before 2026-11-14 a test without a completed stage was not saved at all, and nothing done on the
        results page afterwards (the film's analysis included) could be saved either."""
        if self._done:
            return
        self._done = True
        self._teardown()
        records, self.warmup_records, samples = self._merged()
        self.exhaustion_t_s = getattr(self.runner, "exhaustion_t_s", None)
        s = self.session
        s.ended = datetime.now(timezone.utc)
        s.status = "stopped" if aborted else "finished"
        s.result = dict(s.result or {})
        s.result.pop("live", None)
        if aborted and t("ui.live.aborted_note") not in (s.notes or ""):
            s.notes = (s.notes + " " + t("ui.live.aborted_note")).strip()
        if self._recorder and not self._recorder.ok:     # the camera gave no frames: no film
            earlier = list(s.result.get("earlier_videos") or [])
            s.video_path = earlier.pop() if earlier else None
            if earlier or "earlier_videos" in s.result:
                s.result["earlier_videos"] = earlier
        r1, r2 = self._recorder, self._recorder2
        if r2 is not None and not r2.ok:                 # the second camera gave no frames: no second film
            s.result.pop("second_video", None)
            s.result.pop("second_view", None)
        elif r2 is not None:
            if r1 is not None and r1.first_t is not None and r2.first_t is not None:
                # Both films are stamped with the same clock: this lines their time axes up.
                s.result["second_offset_s"] = round(r2.first_t - r1.first_t, 4)
            log.info("two views: second film %s, starts %s s after the first", r2.path.name,
                     s.result.get("second_offset_s", "?"))
        self._note_runner_values()
        outcome = TestOutcome(self.plan, s, records, samples,
                              [dict(self.stage_gait.get(k) or {}, **({"second": self.stage_gait2[k]}
                                                                      if k in self.stage_gait2 else {}))
                               for k in sorted(set(self.stage_gait) | set(self.stage_gait2))], s.video_path,
                              self.warmup_records)
        if self._started:
            try:
                self._store_test(records, self.warmup_records, samples)
                self.ctx.sessions_changed.emit()
            except Exception as e:
                log.exception("saving the test failed")
                self.error.emit(t("ui.err.save_failed", detail=str(e)))
        if emit and not self._closing:
            self.finished.emit(outcome)

    def _note_runner_values(self) -> None:
        """What the runner noted beside the stages goes into ``session.result``: the time of exhaustion,
        what the ergometer and the belt measured per stage, the drag factor. Called at every save, so a
        test that is cut off (and one continued afterwards) keeps what was measured before the cut."""
        s = self.session
        exhaustion = getattr(self.runner, "exhaustion_t_s", None)
        if exhaustion is not None:
            s.result["exhaustion_t_s"] = exhaustion
        stats = getattr(self.runner, "stage_stats", None)
        if stats:      # a trainer or a SkiErg: target, measured mean and cadence per stage
            merged = {int(x["index"]): x for x in s.result.get("stage_stats") or [] if x.get("index") is not None}
            merged.update({int(x["index"]): x for x in stats})
            s.result["stage_stats"] = [merged[k] for k in sorted(merged)]
        belt = getattr(self.runner, "belt_stats", None)
        if isinstance(belt, list) and belt:   # stages where the belt did not run as planned (set by hand)
            merged = {int(x["index"]): x for x in s.result.get("belt_stats") or [] if x.get("index") is not None}
            merged.update({int(x["index"]): x for x in belt})
            s.result["belt_stats"] = [merged[k] for k in sorted(merged)]
        drag = getattr(self.runner, "drag_factor", None)
        if isinstance(drag, int) and ((s.result.get("drag_factor") or {}).get("source") != "manual"):
            s.result["drag_factor"] = {"value": drag, "source": "pm5"}      # what the monitor measured


def record_from_dict(d: dict[str, Any]) -> StepRecord:
    """StepRecord from RunnerState.completed entries."""
    names = {f.name for f in dataclasses.fields(StepRecord)}
    return StepRecord(**{k: v for k, v in d.items() if k in names})
