"""Takes the screenshots in the documentation from the program in demo mode, without a screen.

tools/docs_shots.json says which view goes to which page: ``{"view", "page", "file"}``. The image is
saved in the page's own ``images/`` folder; the images of the README pages in the root go to ``docs/images/readme/``.
A row may carry ``"lang": "en"`` to take the view in English (the default is Swedish, ``"sv"``): the program is
started once per language that the run needs. The program runs offscreen with simulated devices and the
demo athletes only (never real people), in a fixed window size and with fixed dates on the demo tests.
A file is only rewritten when the new image differs from the old one by more than noise, so a run
that changes nothing leaves the repository clean.

Views where something moves (the camera image, a running test) carry ``"live": true``. They differ
on every run, so they are only taken when the file is missing, when named, or with ``--all``.

    python tools/docs_shots.py              the still views, and live views whose file is missing
    python tools/docs_shots.py --all        every view
    python tools/docs_shots.py tv operator  only these views
    python tools/docs_shots.py --lang en    only the rows in this language (with or without view names)
    python tools/docs_shots.py --list       the views the tool knows
    python tools/docs_shots.py --check      start nothing: exit 1 on a bad row or a missing image

Needs the program's own environment (on Linux without a screen: ``source scripts/headless_env.sh``).
It is heavy, so give it a memory limit of its own where the machine is shared:
``systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 python tools/docs_shots.py``.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "tools/docs_shots.json"
TOLERANCE = 0.0001        # share of the pixels that may differ before the file is rewritten
PIXEL_NOISE = 12          # a channel difference up to this is not a difference
FAST = 20                 # the demo clock runs this many times faster, so a stage takes seconds
DEMO_TEST_DATES = (datetime(2026, 4, 14, 8, 30, tzinfo=timezone.utc),
                   datetime(2026, 8, 18, 8, 30, tzinfo=timezone.utc))
VIEWS = ("idrottare", "installningar", "installningar-las", "installningar-epost", "testerna", "testerna-cykel",
         "historik", "simtest",
         "jamforelse", "resultat-laktat", "resultat-zoner", "resultat-klocka", "resultat-vo2max", "skicka",
         "traningsschema", "traningsschema-idrotter", "traningsschema-mal", "till-appen",
         "nytt-test-idrottare", "nytt-test-testtyp", "protokoll-laktat", "protokoll-maxpuls",
         "protokoll-lopteknik", "nytt-test-kamera", "nytt-test-band-och-puls", "nytt-test-kontroll",
         "nytt-test-kontroll-utan-kamera",
         "protokoll-pulsfart", "operator", "tv", "operator-laktatprov", "tv-laktatprov",
         "operator-med-laktat", "tv-med-laktat", "resultat-maxpuls",
         "resultat-pulsfart", "resultat-lopteknik",
         "nytt-test-tva-kameror", "lagg-till-telefon", "tv-tva-vinklar",
         # cycling and SkiErg: the wizard, the result of a simulated test, and the TV last
         "testtyp-cykel", "protokoll-cykel", "protokoll-ftp", "trainer-och-puls", "resultat-cykel-laktat",
         "resultat-cykel-ftp", "protokoll-skierg", "skierg-och-puls", "resultat-skierg", "tv-cykel", "tv-skierg",
         # bike fit and double poling: the guide, the TV, and the result of a saved test
         "protokoll-bikefit", "kamera-bikefit", "kontroll-bikefit", "protokoll-stakteknik", "kamera-stakteknik",
         "resultat-bikefit", "resultat-bikefit-forslag", "resultat-bikefit-checklista", "resultat-stakteknik",
         "resultat-stakteknik-jamforelse", "tv-bikefit", "tv-stakteknik")
LANGS = ("sv", "en")
ROOT_PAGES = ("README.md", "README.sv.md")   # the front pages: their images lie in README_IMAGES
README_IMAGES = "docs/images/readme"
README_WIDTH = 1600       # the front page's images are scaled down to this width and compressed
# the made-up goal race of the training plan, in the language of the shot
PLAN_GOAL = {"sv": ("Vårloppet halvmaraton", "Premiärmilen"), "en": ("Spring half marathon", "Season opener 10K")}
DEMO_LACTATE = ("1,1", "1,2", "1,5")   # typed in after the first stages of the running test
POWER_TEST_DATE = datetime(2026, 9, 22, 8, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------- the list (no Qt needed)
def load(root: Path) -> dict[str, Any]:
    with open(root / CONFIG, encoding="utf-8") as f:
        return json.load(f)


def target(root: Path, shot: dict[str, Any]) -> Path:
    """Where the shot is saved: the images/ folder next to its page, or README_IMAGES for a README in the root."""
    if shot["page"] in ROOT_PAGES:
        return root / README_IMAGES / shot["file"]
    return (root / shot["page"]).parent / "images" / shot["file"]


def lang(shot: dict[str, Any]) -> str:
    return str(shot.get("lang", "sv"))


def problems(root: Path) -> list[str]:
    """Rows in the list that cannot work: unknown view or language, page that does not exist, bad file name."""
    out, seen = [], set()
    for shot in load(root).get("shots", []):
        view, page, file = shot.get("view", ""), shot.get("page", ""), shot.get("file", "")
        if view not in VIEWS:
            out.append(f"{CONFIG}: unknown view '{view}'")
        if lang(shot) not in LANGS:
            out.append(f"{CONFIG}: unknown language '{lang(shot)}' for '{view}'")
        if not (page.startswith("docs/") or page in ROOT_PAGES) or not (root / page).is_file():
            out.append(f"{CONFIG}: the page {page} for '{view}' does not exist")
        elif not file.endswith(".png") or "/" in file:
            out.append(f"{CONFIG}: the file for '{view}' is a plain name that ends with .png")
        elif target(root, shot) in seen:
            out.append(f"{CONFIG}: two rows write {target(root, shot).relative_to(root).as_posix()}")
        else:
            seen.add(target(root, shot))
    return out


def missing(root: Path) -> list[str]:
    return [target(root, s).relative_to(root).as_posix() for s in load(root).get("shots", [])
            if (root / s.get("page", "")).is_file() and not target(root, s).is_file()]


def differs(old: Path, new: Path, tolerance: float = TOLERANCE) -> bool:
    """True when the two images differ by more than noise (or in size)."""
    import numpy as np
    from PIL import Image
    with Image.open(old) as a, Image.open(new) as b:
        if a.size != b.size:
            return True
        d = np.abs(np.asarray(a.convert("RGB"), dtype=np.int16) - np.asarray(b.convert("RGB"), dtype=np.int16))
    return float((d.max(axis=2) > PIXEL_NOISE).mean()) > tolerance


def shrink(path: Path, width: int = README_WIDTH) -> None:
    """Scale the image down to ``width`` (if wider) and save it compressed, without metadata."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS)
        im.save(path, "PNG", optimize=True)


def save_if_changed(new: Path, dest: Path, tolerance: float = TOLERANCE) -> bool:
    """Move the fresh image to its place unless the one already there shows the same thing."""
    if dest.is_file() and not differs(dest, new, tolerance):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(new), str(dest))
    return True


# ---------------------------------------------------------------------- the program, offscreen
class Studio:
    """Drives the demo program to each view and hands back the widget to photograph."""

    def __init__(self, home: Path, size: tuple[int, int], tv_size: tuple[int, int], lang: str = "sv") -> None:
        os.environ["LOPANALYS_HOME"] = str(home)
        os.environ["LOPANALYS_NO_DOWNLOAD"] = "1"   # never reach the internet
        os.environ["LOPANALYS_KEY_DIR"] = str(home / "keys")   # the key for the demo password goes with the folder
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, str(ROOT / "src"))
        from PySide6.QtWidgets import QMessageBox

        from lopanalys.ui.app import build
        # Nothing may block: every question is answered yes, every notice is dismissed.
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)  # type: ignore[method-assign]
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)  # type: ignore[method-assign]
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)  # type: ignore[method-assign]
        # Nothing is fetched from the internet: without the pose model the gait page shows the live values.
        from lopanalys.ui import services
        services.analyze_video = lambda *a, **k: None
        services.model_ready = lambda: True
        self.app, self.win = build(["--demo", "--fast", str(FAST), "--lang", lang])
        self.tv_size = tv_size
        self.lang = lang
        self.kind: str | None = None        # the test that is running now
        self.power_tests: dict[str, int] = {}   # simulated cycling and SkiErg tests saved for the result views
        self.extra: list[Any] = []          # dialogs to close at the end
        ctx = self.win.ctx
        ctx.set_setting("tv.sound", False)
        # A made-up mail account, so the pages show how it looks when mail is set up. Nothing is sent.
        ctx.set_setting("mail.user", "coach@example.com")
        services.set_mail_password(ctx.store, "demo")
        self.win.settings.mail_user.setText("coach@example.com")
        self.win.settings._refresh_mail()
        self.erik = next(a for a in ctx.store.athletes() if a.first_name == "Erik")
        tests = sorted(ctx.store.history(self.erik.id), key=lambda s: s.started)
        for s, day in zip(tests, DEMO_TEST_DATES):   # the demo data dates its tests from today
            s.started, s.ended = day, day + (s.ended - s.started)
            ctx.store.save_session(s)
        self.tests = sorted(ctx.store.history(self.erik.id), key=lambda s: s.started)
        ctx.sessions_changed.emit()
        self.win.resize(*size)
        self.win.show()
        self.wait(300)

    # -- helpers
    def wait(self, ms: int) -> None:
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def wait_until(self, cond: Callable[[], bool], timeout_s: float, what: str) -> None:
        for _ in range(int(timeout_s * 50)):
            if cond():
                return
            self.wait(20)
        raise TimeoutError(f"waited {timeout_s:.0f} s for: {what}")

    def close(self) -> None:
        for w in self.extra:
            w.close()
        if self.win.operator is not None and self.win.operator.started:
            self.win.operator.stop()
            self.wait(500)
        self.wait_until(lambda: self.win.results._analysis is None, 120, "the video analysis to end")
        self.win.plan.revert()      # an unsaved plan form would ask before the window closes
        self.win.close()

    def _select(self, lst: Any) -> None:
        from PySide6.QtCore import Qt
        for i in range(lst.count()):
            if lst.item(i).data(Qt.ItemDataRole.UserRole).id == self.erik.id:
                lst.setCurrentRow(i)

    def _wizard(self, kind: str, step: int, quick: bool = False, two: bool = False, sport: str = "run") -> Any:
        """Walk Nytt test to a step (0-5, the page's index) for the demo athlete. ``quick`` drops the
        warm-up, ``two`` adds the demo runner from behind as the second camera. A test that is not
        filmed has no camera step: asking for step 3 then stops on step 4."""
        self._end_test()
        self.win.go("new_test")
        nt = self.win.new_test
        nt.reset()
        self._select(nt.a_list)
        stuck = 0
        while nt.stack.currentIndex() < step:
            before = nt.stack.currentIndex()
            nt._next()
            at = nt.stack.currentIndex()
            if at == before:            # Next is still grey (the camera is being picked): let the program work
                stuck += 1
                if stuck > 200:
                    raise TimeoutError(f"the wizard stays on step {at}: {nt.hint.text()}")
                self.wait(50)
                continue
            if at == 1:
                nt.set_sport(sport)        # the sport first: it decides which tests there are
                nt.kind_group.button([k for k, _, _ in nt._kinds].index(kind)).click()
            elif at == 2 and quick:
                nt.p_warm.setValue(0)
            elif at == 3:
                # the search is asynchronous, and the list may still hold the cameras of an earlier visit
                self.wait_until(lambda: nt.cam_list.count() >= 2 and nt.camera is not None, 10,
                                "the camera list with the demo runner picked")
                nt.cam2_box.setCurrentIndex(nt.cam2_box.count() - 1 if two else 0)
            elif at == 4:
                self.wait_until(lambda: nt.tm_connected and nt.hr_connected, 15,
                                "the simulated ergometer and heart rate")
            elif at == 5 and nt.films():
                self.wait_until(lambda: nt.check_view.frame_count > 0, 30, "the camera image in the check step")
        self.wait(400)
        return nt

    def _test(self, kind: str, two: bool = False, sport: str = "run") -> Any:
        """The operator page of a running test of this kind (started when it is not running yet)."""
        key = kind + ("+2" if two else "") + ("" if sport == "run" else f"@{sport}")
        if self.kind != key:
            nt = self._wizard(kind, 5, quick=True, two=two, sport=sport)
            nt._next()   # Starta livevyn
            op = self.win.operator
            op.tv.resize(*self.tv_size)
            board = op.tv.board
            if board.tech_tiles:
                # Bike fit and double poling: the angles need a few revolutions or strokes, and the fast
                # demo test is over in seconds. The camera runs already before the start (the framing).
                tiles = list(board.tech_tiles.values()) + list(board.tech2_tiles.values())
                self.wait_until(lambda: all(tile.value != "–" for tile, _ in tiles), 40, "the live angles on the TV")
            op.start()
            self.kind = key
        return self.win.operator

    def _phase(self, name: str, timeout_s: float = 60) -> Any:
        from lopanalys.assessments.protocol import Phase
        op = self.win.operator
        self.wait_until(lambda: op.ctl.state is not None and op.ctl.state.phase == Phase(name), timeout_s,
                        f"the test to reach {name}")
        return op

    def _end_test(self) -> None:
        if self.kind is None:
            return
        op = self.win.operator
        if op is not None and self.win.ctx.live is not None:
            op.stop()
            self.wait_until(lambda: self.win.ctx.live is None, 20, "the test to stop")
        self.kind = None

    def _results(self) -> Any:
        self._end_test()
        self.win.open_session(self.tests[-1].id)
        self.win.results.tabs.setCurrentIndex(0)
        self.wait(300)
        return self.win.results

    # -- the views, in the order they are taken
    def idrottare(self) -> Any:
        self._end_test()
        self.win.go("athletes")
        self._select(self.win.athletes.list)
        return self.win

    def installningar(self) -> Any:
        self._end_test()
        self.win.go("settings")
        return self.win

    def installningar_las(self) -> Any:
        """The lower half of the settings page: the card Lås och kundläge."""
        self._end_test()
        self.win.go("settings")
        self.wait(200)
        self.win.settings.scroll.ensureWidgetVisible(self.win.settings.lock_card, 0, 40)
        self.wait(200)
        return self.win

    def installningar_epost(self) -> Any:
        """The lower half of the settings page: the card E-post."""
        self._end_test()
        self.win.go("settings")
        self.wait(200)
        bar = self.win.settings.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.wait(200)
        return self.win

    def skicka(self) -> Any:
        """The question before a mail goes to the athlete. Nothing is sent."""
        from lopanalys.share import fit
        from lopanalys.ui import services
        from lopanalys.ui.views.send_mail import SendDialog
        s = self.tests[-1]
        erik = self.win.ctx.store.athlete(self.erik.id)
        day = s.started.astimezone().strftime("%Y-%m-%d")
        subject, body = services.mail_greeting(erik, day, {"coach": "Coach Demo", "business": "Demo Löpanalys"}, True)
        dlg = SendDialog(erik.email, [("Rapporten", f"testrapport-{day}.pdf"), ("FIT-fil", fit.file_name(s))],
                         subject, body, self.win)
        dlg.show()
        self.extra.append(dlg)
        return dlg

    def testerna(self) -> Any:
        self._end_test()
        self.win.go("tests")
        self.win.tests_info.set_sport("run")
        return self.win

    def testerna_cykel(self) -> Any:
        self._end_test()
        self.win.go("tests")
        self.win.tests_info.set_sport("bike")
        return self.win

    def historik(self) -> Any:
        self._end_test()
        self.win.go("history")
        self.win.history.select_athlete(self.erik.id)
        return self.win

    def simtest(self) -> Any:
        """The form for the swimming test with two times written in."""
        from lopanalys.ui.views.css_test import CssTestDialog
        dlg = CssTestDialog(self.win.ctx, self.erik, None, self.win)
        dlg.t400.setText("6:40")
        dlg.t200.setText("3:10")
        dlg.show()
        self.extra.append(dlg)
        return dlg

    def jamforelse(self) -> Any:
        from lopanalys.ui.views.history import CompareDialog
        dlg = CompareDialog(self.win.ctx, list(self.tests), self.win)
        dlg.show()
        self.extra.append(dlg)
        return dlg

    def resultat_laktat(self) -> Any:
        self._results()
        return self.win

    def resultat_zoner(self) -> Any:
        return self._results().zones_table.parentWidget()

    def resultat_klocka(self) -> Any:
        return self._results().watch_card

    def resultat_vo2max(self) -> Any:
        return self._results().vo2_card

    def nytt_test_idrottare(self) -> Any:
        self._wizard("lactate", 0)
        return self.win

    def nytt_test_testtyp(self) -> Any:
        self._wizard("lactate", 1)
        return self.win

    def protokoll_laktat(self) -> Any:
        self._wizard("lactate", 2)
        return self.win

    def protokoll_maxpuls(self) -> Any:
        self._wizard("max_hr", 2)
        return self.win

    def protokoll_lopteknik(self) -> Any:
        self._wizard("gait", 2)
        return self.win

    def protokoll_pulsfart(self) -> Any:
        self._wizard("hr_zones", 2)
        return self.win

    def nytt_test_kamera(self) -> Any:
        nt = self._wizard("combined", 3)       # a test that films: the others have no camera step
        self.wait_until(lambda: nt.cam_view.frame_count > 0, 10, "the camera preview")
        return self.win

    def nytt_test_tva_kameror(self) -> Any:
        nt = self._wizard("gait", 3, two=True)
        self.wait_until(lambda: nt.cam_view.frame_count > 0 and nt.cam2_view.frame_count > 0, 10,
                        "both camera previews")
        return self.win

    def lagg_till_telefon(self) -> Any:
        from lopanalys.ui.views.add_stream import AddStreamDialog
        dlg = AddStreamDialog(self.win)
        dlg.show()
        dlg.found.setFocus()      # no blinking text cursor in the picture
        self.extra.append(dlg)
        return dlg

    def tv_tva_vinklar(self) -> Any:
        self._test("gait", two=True)
        op = self._phase("running")
        self.wait_until(lambda: op.ctl.gait is not None and op.ctl.gait2 is not None, 20, "both views' values")
        self.wait(1500)
        return self.win.operator.tv

    def nytt_test_band_och_puls(self) -> Any:
        self._wizard("lactate", 4)
        return self.win

    def nytt_test_kontroll(self) -> Any:
        self._wizard("combined", 5)
        self.wait(1200)   # let the framing check see the runner
        return self.win

    def nytt_test_kontroll_utan_kamera(self) -> Any:
        self._wizard("max_hr", 5)
        return self.win

    def operator(self) -> Any:
        self._test("combined")
        self._phase("running")
        self.wait(3000)
        return self.win

    def tv(self) -> Any:
        self._test("combined")
        self._phase("running")
        self.wait(1500)
        return self.win.operator.tv

    def operator_laktatprov(self) -> Any:
        self._test("combined")
        op = self._phase("lactate_pause")
        for key in ("2", ",", "1"):
            op.pad.press(key)
        self.wait(100)
        return self.win

    def tv_laktatprov(self) -> Any:
        self._test("combined")
        return self._phase("lactate_pause").tv

    def _with_lactate(self) -> Any:
        """The running test a few stages in, with the lactate of the first stages typed in."""
        from lopanalys.assessments.protocol import Phase
        op = self._test("combined")
        while len(op.ctl.lactate) < len(DEMO_LACTATE):
            self._phase("lactate_pause", 90)
            for key in DEMO_LACTATE[len(op.ctl.lactate)]:
                op.pad.press(key)
            op.pad.submit()
            self.wait_until(lambda: op.ctl.state.phase != Phase.LACTATE_PAUSE, 60, "the next stage")
        self._phase("running")
        self.wait(2500)
        return op

    def operator_med_laktat(self) -> Any:
        self._with_lactate()
        return self.win

    def tv_med_laktat(self) -> Any:
        return self._with_lactate().tv

    def _plan(self) -> Any:
        """Träningsschema for the demo athlete with the form filled in and the plan previewed (fixed dates)."""
        self._end_test()
        self.win.go("athletes")
        self.win.open_plan(self.erik)
        v = self.win.plan
        v.revert()
        v.level.setCurrentIndex(v.level.findData("recreational"))
        v.recent.setText("200")
        for day, minutes, clock in ((0, "45", "18:00"), (1, "75", "18:00"), (3, "75", "18:00"), (5, "60", "09:00"),
                                    (6, "120", "09:00")):
            v.minutes[day].setText(minutes)
            v.clocks[day].setText(clock)
        goal, milestone = PLAN_GOAL[self.lang]
        v.goal_name.setText(goal)
        v.goal_date.edit.setText("2027-05-22")
        v.goal_km.setText("21,1")
        v.goal_target.setText("under 1:45")
        row = v.add_milestone()
        row.name.setText(milestone)
        row.date.edit.setText("2027-03-27")
        row.km.setText("10")
        v.start.edit.setText("2026-11-16")
        assert v.preview(), v.error.text()
        self.wait(300)
        return v

    def traningsschema(self) -> Any:
        self._plan()
        return self.win

    def traningsschema_idrotter(self) -> Any:
        """The form with running and cycling: the sports' rows, the button per weekday and a second time on Thursday
        (the bike in the morning), the plan previewed."""
        v = self._plan()
        v.sport_rows["bike"].check.setChecked(True)
        v.day_sports[1].actions_["bike"].setChecked(True)
        v.second_btns[3].click()
        v.minutes2[3].setText("45")
        v.clocks2[3].setText("07:00")
        v.day_sports2[3].actions_["bike"].setChecked(True)
        assert v.preview(), v.error.text()
        self.wait(100)
        bar = v.form_scroll.verticalScrollBar()
        bar.setValue(v.sport_rows["bike"].y() - 30)      # the sports and the days down to the second time on Thursday
        self.wait(300)
        return self.win

    def traningsschema_mal(self) -> Any:
        v = self._plan()
        bar = v.form_scroll.verticalScrollBar()
        self.wait(100)
        bar.setValue(bar.maximum())
        return self.win

    def till_appen(self) -> Any:
        """*Till appen*: the QR code, the link and *Spara som fil* for the previewed plan. Nothing is saved."""
        from lopanalys.ui import services
        from lopanalys.ui.views.plan_app import AppDialog
        v = self._plan()
        link, data = services.plan_app(v.plan)
        dlg = AppDialog(link, data, Path("traningsschema.troskel"), self.win)
        dlg.show()
        self.extra.append(dlg)
        return dlg

    def resultat_maxpuls(self) -> Any:
        from lopanalys.assessments.protocol import Phase
        op = self._test("max_hr")
        self._phase("running")
        self.wait_until(lambda: op.ctl.state.stage_index >= 5, 90, "the sixth stage of the max test")
        op.exhausted()
        self._phase("recovery", 20)
        self.wait_until(lambda: self.win.stack.currentWidget() is self.win.results, 30, "the result page")
        assert op.ctl.state.phase in (Phase.FINISHED, Phase.RECOVERY)
        self.kind = None
        self.wait(300)
        return self.win

    def resultat_pulsfart(self) -> Any:
        """Puls- och fartzoner run to the ramp, then ended the way a lone runner does it: the belt is
        stopped on the treadmill and nobody presses anything in the program."""
        op = self._test("hr_zones")
        self._phase("running")
        n = len(op.ctl.plan.protocol.stages)
        self.wait_until(lambda: op.ctl.state.stage_index >= n + 6, 240, "the eighth stage of the ramp")
        self.win.ctx.devices.treadmill.stop()
        self.wait_until(lambda: self.win.stack.currentWidget() is self.win.results, 60, "the result page")
        self.kind = None
        self.win.results.tabs.setCurrentIndex(0)
        self.wait(300)
        return self.win

    def resultat_lopteknik(self) -> Any:
        self._test("gait")
        self.wait_until(lambda: self.win.stack.currentWidget() is self.win.results, 60, "the result page")
        self.kind = None
        self.wait_until(lambda: self.win.results._analysis is None, 120, "the video analysis to end")
        self.win.results.tabs.setCurrentIndex(1)
        self.wait(300)
        return self.win


    # -- cycling and SkiErg
    def _power_result(self, name: str) -> Any:
        """The result page of a whole simulated test in watt, saved for the demo athlete: "bike-lactate",
        "bike-ftp" or "skierg-lactate". The test is computed at once with the simulators, not run live."""
        from datetime import timedelta

        from lopanalys.assessments import library_power
        from lopanalys.assessments.runner import TestRunner
        from lopanalys.core.bus import Bus
        from lopanalys.core.models import Session, TestKind
        from lopanalys.devices import sim
        self._end_test()
        if name not in self.power_tests:
            sport = name.split("-")[0]
            if name == "bike-ftp":
                protocol, kind, lactate = library_power.bike_ftp_20(250), TestKind.FTP, None
                erg_kw: dict[str, Any] = {"rider_power_w": 262}
            elif sport == "bike":
                protocol, kind = library_power.bike_lactate_step_for_level("recreational"), TestKind.LACTATE_STEP
                lactate, erg_kw = [1.1, 1.0, 1.2, 1.6, 2.3, 3.6, 5.9], {}
            else:
                protocol, kind = library_power.skierg_lactate_step_for_level("recreational"), TestKind.LACTATE_STEP
                lactate, erg_kw = [1.2, 1.1, 1.3, 1.8, 2.6, 3.9, 6.1], {"follow": 0.97}
            bus, mc = Bus(), sim.ManualClock(0.0)
            erg = (sim.SimBike if sport == "bike" else sim.SimSkiErg)(bus, clock=mc, autorun=False, seed=1, **erg_kw)
            hr = sim.SimHeartRate(bus, treadmill=erg, clock=mc, autorun=False, seed=1)
            erg.connect()
            hr.connect()

            class Clock:
                now = staticmethod(mc)
                sleep = staticmethod(mc.advance)
            r = TestRunner(protocol, erg, hr, bus, clock=Clock(), threaded=False)
            r.start()
            while not r.done:
                mc.advance(0.25)
                erg.tick()
                hr.tick()
                r.tick()
            if lactate:
                r.enter_lactate(r.warmup_records[0].index, 1.2)
                for rec, la in zip(r.records, lactate):
                    r.enter_lactate(rec.index, la)
            store = self.win.ctx.store
            day = POWER_TEST_DATE + timedelta(days=("bike-lactate", "bike-ftp", "skierg-lactate").index(name))
            s = store.save_session(Session(None, self.erik.id, kind, day, day + timedelta(seconds=r.samples[-1].t_s),
                                           protocol.name, protocol.to_dict(), "ftms", sport=sport,
                                           result={"weight_kg": self.erik.weight_kg, "stage_stats": r.stage_stats,
                                                   **({"drag_factor": {"value": r.drag_factor, "source": "pm5"}}
                                                      if r.drag_factor else {})}))
            store.save_records(s.id, r.warmup_records + r.records)
            store.save_samples(s.id, r.samples)
            self.power_tests[name] = s.id
        self.win.open_session(self.power_tests[name])
        self.win.results.tabs.setCurrentIndex(0)
        self.wait(300)
        return self.win

    def testtyp_cykel(self) -> Any:
        self._wizard("lactate", 1, sport="bike")
        return self.win

    def protokoll_cykel(self) -> Any:
        self._wizard("lactate", 2, sport="bike")
        return self.win

    def protokoll_ftp(self) -> Any:
        self._wizard("ftp", 2, sport="bike")
        return self.win

    def trainer_och_puls(self) -> Any:
        self._wizard("lactate", 4, sport="bike")
        return self.win

    def resultat_cykel_laktat(self) -> Any:
        return self._power_result("bike-lactate")

    def resultat_cykel_ftp(self) -> Any:
        return self._power_result("bike-ftp")

    def protokoll_skierg(self) -> Any:
        self._wizard("lactate", 2, sport="skierg")
        return self.win

    def skierg_och_puls(self) -> Any:
        self._wizard("lactate", 4, sport="skierg")
        return self.win

    def resultat_skierg(self) -> Any:
        return self._power_result("skierg-lactate")

    def _tv_power(self, sport: str) -> Any:
        op = self._test("lactate", sport=sport)
        self._phase("running")
        board = op.tv.board
        self.wait_until(lambda: board.t_speed.value.isdigit() and int(board.t_speed.value) > 60
                        and board.t_cadence.value.isdigit(), 30, "watt and cadence on the TV")
        self.wait(2500)
        return op.tv

    def tv_cykel(self) -> Any:
        return self._tv_power("bike")

    def tv_skierg(self) -> Any:
        return self._tv_power("skierg")
# -- bike fit and SkiErg technique: saved tests of the demo athlete, from films of the synthetic athletes
def _technique_views(cls: type) -> None:
    def _make(self: Any, kind: str) -> int:
        made = self.__dict__.setdefault("_tech", {})
        if kind in made:
            return made[kind]
        from datetime import timedelta

        from lopanalys.core.models import Session, TestKind
        from lopanalys.pose import technique_synth as ts
        from lopanalys.pose.types import View
        store = self.win.ctx.store
        h = self.erik.height_cm or 180.0
        home = Path(os.environ["LOPANALYS_HOME"])
        day = DEMO_TEST_DATES[1] + timedelta(days=14)

        def session(when: Any, name: str) -> Any:
            tk, sport = (TestKind.BIKEFIT, "bike") if kind == "bikefit" else (TestKind.SKI_TECHNIQUE, "skierg")
            return store.save_session(Session(None, self.erik.id, tk, when, when + timedelta(minutes=30),
                                              protocol_name=name, sport=sport))

        def film(mod: Any, analysis: Any, p: Any, s: Any, name: str, previous: Any = None) -> None:
            video = mod.write_video(str(home / f"{name}.mp4"), p)
            a = analysis.analyze_video(video, h, p.view, keyframes_dir=store.db.keyframes_root / name,
                                       estimator=ts.replay(mod.generate(p)[0]), previous=previous)
            a.video_path = None           # the demo film is not kept
            Path(video).unlink(missing_ok=True)
            store.save_technique(s.id, a.to_dict())

        if kind == "bikefit":
            from lopanalys.pose.bike import analysis as ba
            from lopanalys.pose.bike import synthetic as bs
            from lopanalys.ui import services
            s = session(day, "Bikefit landsväg")
            side = bs.with_knee(bs.CyclistParams(height_cm=h, duration_s=12, width=960, height=540, elbow_flex_deg=4.0,
                                                 shoulder_angle_deg=101.0), 50.0)
            film(bs, ba, side, s, "demo-bikefit-side")
            film(bs, ba, bs.CyclistParams(height_cm=h, view=View.FRONT, duration_s=12, width=540, height=960,
                                          knee_lateral_cm=5.5), s, "demo-bikefit-front")
            services.save_checklist(store, s, {
                "goal": "Vätternrundan, 300 km", "riding": "6 h/vecka, landsväg", "hamstring.left": "medium",
                "hamstring.right": "limited", "forward_bend": "medium", "arch.left": "normal", "arch.right": "low",
                "leg_length_diff_mm": 6, "longer_leg": "left", "sit_bones_mm": 118, "ball_over_axle.left": True,
                "ball_over_axle.right": True, "rotation_checked.left": True, "saddle_height_mm.before": 728,
                "saddle_height_mm.after": 736, "stem_length_mm.before": 110, "crank_length_mm": 170,
                "done": "Sadeln höjd 8 mm i två steg. Höger kloss vriden 2° utåt.",
                "recommended": "Styrstam 100 mm."})
        else:
            from lopanalys.pose.ski import analysis as sa
            from lopanalys.pose.ski import synthetic as ss
            size = dict(height_cm=h, duration_s=20, width=540, height=960)
            first = session(day - timedelta(days=42), "Stakteknik")
            p1 = ss.SkierParams(trunk_lean_end_deg=24.0, trunk_lead_ms=-120.0, stroke_rate_spm=46.0, drive_ms=640.0, **size)
            film(ss, sa, p1, first, "demo-ski-1")
            s = session(day, "Stakteknik")
            prev = store.previous_technique(s, "ski", "side_left")
            film(ss, sa, ss.SkierParams(trunk_lean_end_deg=38.0, trunk_lead_ms=30.0, knee_flex_end_deg=56.0, **size), s,
                 "demo-ski-2", previous=prev[0] if prev else None)
        made[kind] = s.id
        self.win.ctx.sessions_changed.emit()
        return s.id

    def _open(self: Any, kind: str, scroll: float = 0.0) -> Any:
        self._end_test()
        self.win.open_session(_make(self, kind))
        res = self.win.results
        res.tabs.setCurrentIndex(res.tabs.indexOf(res.tech_tab))
        self.wait(400)
        bar = res.tech_tab.verticalScrollBar()
        bar.setValue(int(bar.maximum() * scroll))
        self.wait(300)
        return self.win

    def _camera(self: Any, kind: str, sport: str, step: int, two: bool) -> Any:
        nt = self._wizard(kind, step, two=two, sport=sport)
        if step == 3:      # the demo athlete takes a moment to set up: wait for its first picture
            self.wait_until(lambda: nt.preview is not None and nt.preview.frame is not None, 20,
                            "the demo athlete in the preview")
        view = nt.cam_view if step == 3 else nt.check_view
        self.wait_until(lambda: view.frame_count > 0, 15, "the demo athlete in the picture")
        self.wait(1200)
        return self.win

    def _tv(self: Any, kind: str, sport: str, two: bool) -> Any:
        op = self._test(kind, two=two, sport=sport)
        self._phase("running")
        board = op.tv.board
        self.wait_until(lambda: board.video.frame_count > 0 and board.t_speed.value.isdigit(), 30,
                        "the picture and the watt on the TV")
        self.wait(2500)
        return op.tv

    cls.protokoll_bikefit = lambda self: (self._wizard("bikefit", 2, sport="bike"), self.win)[1]
    cls.kamera_bikefit = lambda self: _camera(self, "bikefit", "bike", 3, True)
    cls.kontroll_bikefit = lambda self: _camera(self, "bikefit", "bike", 5, True)
    cls.protokoll_stakteknik = lambda self: (self._wizard("ski", 2, sport="skierg"), self.win)[1]
    cls.kamera_stakteknik = lambda self: _camera(self, "ski", "skierg", 3, False)
    cls.tv_bikefit = lambda self: _tv(self, "bikefit", "bike", True)
    cls.tv_stakteknik = lambda self: _tv(self, "ski", "skierg", False)
    cls.resultat_bikefit = lambda self: _open(self, "bikefit")
    cls.resultat_bikefit_forslag = lambda self: _open(self, "bikefit", 0.62)
    cls.resultat_bikefit_checklista = lambda self: _open(self, "bikefit", 1.0)
    cls.resultat_stakteknik = lambda self: _open(self, "ski")
    cls.resultat_stakteknik_jamforelse = lambda self: _open(self, "ski", 1.0)


_technique_views(Studio)


def run(root: Path, names: list[str], everything: bool, only: str | None = None) -> int:
    cfg = load(root)
    shots = [s for s in cfg["shots"] if (s["view"] in names if names else
                                         everything or not s.get("live") or not target(root, s).is_file())
             and (only is None or lang(s) == only)]
    unknown = [n for n in names if n not in {s["view"] for s in cfg["shots"]}]
    if unknown:
        print(f"not in {CONFIG}: {', '.join(unknown)}")
        return 1
    failed = 0
    for language in LANGS:     # one start of the program per language, with fresh demo data
        mine = [s for s in shots if lang(s) == language]
        if mine:
            failed += _run(root, cfg, mine, language)
    return 1 if failed else 0


def _run(root: Path, cfg: dict[str, Any], shots: list[dict[str, Any]], language: str) -> int:
    home = Path(tempfile.gettempdir()) / "lopanalys-docs-shots"   # a fixed path: it shows in Inställningar
    shutil.rmtree(home, ignore_errors=True)
    tmp = Path(tempfile.mkdtemp(prefix="docs-shots-"))
    failed = 0
    studio = Studio(home, tuple(cfg.get("size", (1440, 900))), tuple(cfg.get("tv_size", (1920, 1080))), language)
    try:
        for shot in sorted(shots, key=lambda s: VIEWS.index(s["view"])):
            dest = target(root, shot)
            try:
                widget = getattr(studio, shot["view"].replace("-", "_"))()
                studio.wait(150)
                fresh = tmp / shot["file"]
                if not widget.grab().save(str(fresh), "PNG"):
                    raise OSError("the image could not be saved")
                if shot["page"] in ROOT_PAGES:
                    shrink(fresh)
                changed = save_if_changed(fresh, dest, float(shot.get("tolerance", TOLERANCE)))
                print(("updated:   " if changed else "unchanged: ") + dest.relative_to(root).as_posix())
            except Exception as e:  # noqa: BLE001 - one failed view must not lose the others
                failed += 1
                print(f"FAILED:    {shot['view']} ({language}): {e}")
    finally:
        try:
            studio.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)
    return failed


def main(argv: list[str]) -> int:
    if "--list" in argv:
        print("\n".join(VIEWS))
        return 0
    if "--check" in argv:
        found = problems(ROOT) + [f"{m}: the image is missing, run python tools/docs_shots.py" for m in missing(ROOT)]
        print("\n".join(found), end="\n" if found else "")
        return 1 if found else 0
    bad = problems(ROOT)
    if bad:
        print("\n".join(bad))
        return 1
    only = argv[argv.index("--lang") + 1] if "--lang" in argv[:-1] else None
    if only is not None and only not in LANGS:
        print(f"unknown language '{only}': {', '.join(LANGS)}")
        return 1
    names = [a for i, a in enumerate(argv) if not a.startswith("--") and not (i and argv[i - 1] == "--lang")]
    return run(ROOT, names, "--all" in argv, only)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
