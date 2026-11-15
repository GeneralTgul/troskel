"""Entry point: ``lopanalys`` (or ``python -m lopanalys.ui.app``).

Options:
  --demo          simulated treadmill and heart rate, synthetic runner video, demo athletes
  --lang sv|en    override the language setting
  --fast N        demo only: run the protocol clock N times faster (for showing a whole test)
  --reset-pin     remove a forgotten PIN (the lock protects the screen, not the files) and start
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
import traceback
from types import TracebackType
from typing import Any


def _setup_logging() -> str:
    """One log file per day, kept for 30 days. The log names tests by id, never people or paths."""
    from lopanalys.core import paths
    from lopanalys.storage import privacy
    d = paths.sub("logs")
    path = d / "lopanalys.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.BaseRotatingHandler) for h in root.handlers):
        fh = logging.handlers.TimedRotatingFileHandler(path, when="midnight", backupCount=privacy.LOG_DAYS,
                                                       encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
        root.addHandler(fh)
    try:
        privacy.purge_old_logs(d)     # also the files of the earlier rotation by size
    except Exception:  # noqa: BLE001 - logging must never stop the program
        root.exception("removing old logs failed")
    _enable_crash_log(d / "crash.log")
    return str(path)


_CRASH_FILE: Any = None


def _enable_crash_log(path: Any) -> None:
    """Native crashes (real hardware 2026-11-14, a camera driver corrupting the heap) end the process without
    a Python traceback. faulthandler writes the Python stack of every thread to ``logs/crash.log`` then."""
    global _CRASH_FILE
    import faulthandler
    try:
        if _CRASH_FILE is None:
            _CRASH_FILE = open(path, "a", encoding="utf-8")  # noqa: SIM115 - kept open for the process
        faulthandler.enable(file=_CRASH_FILE, all_threads=True)
    except Exception:  # noqa: BLE001 - logging must never stop the program
        logging.getLogger(__name__).exception("faulthandler not enabled")


def install_exception_hook(log_path: str) -> None:
    """Log every uncaught exception (GUI and worker threads) and show a friendly dialog."""
    from PySide6.QtCore import QObject, Signal

    class _Relay(QObject):
        show = Signal(str)

    relay = _Relay()

    def show(detail: str) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMessageBox

        from lopanalys.core.i18n import t
        if QApplication.instance() is None:
            return
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("ui.app.name"))
        box.setText(t("ui.err.unexpected"))
        box.setInformativeText(t("ui.err.unexpected_sub", path=log_path))
        box.setDetailedText(detail)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.setModal(True)
        box.show()  # not exec(): never block a running test or a headless run

    relay.show.connect(show)
    install_exception_hook.relay = relay  # type: ignore[attr-defined]  # keep alive
    _showing = threading.Lock()

    def hook(et: type[BaseException], ev: BaseException, tb: TracebackType | None) -> None:
        if issubclass(et, KeyboardInterrupt):
            sys.__excepthook__(et, ev, tb)
            return
        text = "".join(traceback.format_exception(et, ev, tb))
        logging.getLogger("lopanalys").error("uncaught exception\n%s", text)
        if _showing.acquire(blocking=False):
            try:
                relay.show.emit(text)
            finally:
                _showing.release()

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value or Exception(), a.exc_traceback)  # type: ignore[arg-type]


def build(argv: list[str] | None = None, app: Any = None) -> tuple[Any, Any]:
    """Create (or reuse) the QApplication, the context and the main window. Used by main() and tests."""
    p = argparse.ArgumentParser(prog="lopanalys")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--lang", choices=["sv", "en"])
    p.add_argument("--fast", type=float, default=1.0)
    p.add_argument("--reset-pin", action="store_true")
    args, _ = p.parse_known_args(argv)

    from PySide6.QtCore import QSettings
    moved_from = ""
    if not os.environ.get("LOPANALYS_HOME"):
        qs = QSettings("Lopanalys", "Lopanalys")
        override = qs.value("data_dir", "")
        moved_from = str(qs.value("data_dir_moved_from", "") or "")
        if override:
            os.environ["LOPANALYS_HOME"] = str(override)
    from lopanalys.core import paths
    paths.set_demo(args.demo)       # demo people and demo tests live in a folder of their own
    log_path = _setup_logging()
    if moved_from:      # "Flytta mapp" last time: the new folder is in use now, the old one is emptied
        try:
            from lopanalys.storage import privacy
            privacy.finish_move(moved_from, paths.real_data_dir())
            QSettings("Lopanalys", "Lopanalys").remove("data_dir_moved_from")
        except Exception:  # noqa: BLE001 - never stops the start
            logging.getLogger(__name__).exception("clearing the old data folder failed")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    if app is None:
        app = QApplication.instance()
    if app is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(sys.argv[:1])
    app.setApplicationName("Lopanalys")
    app.setOrganizationName("Lopanalys")
    install_exception_hook(log_path)

    from lopanalys.core import i18n
    from lopanalys.ui import demo, theme
    from lopanalys.ui.context import AppContext
    ctx = AppContext(demo=args.demo, time_scale=max(1.0, args.fast) if args.demo else 1.0)
    i18n.set_language(args.lang or str(ctx.setting("app.language") or "sv"))
    if args.reset_pin:
        ctx.set_setting("lock.pin", None)
        logging.getLogger(__name__).warning("the PIN was removed with --reset-pin")
    theme.set_accent(str(ctx.setting("branding.accent") or ""))
    from PySide6.QtCore import QLocale
    QLocale.setDefault(QLocale(QLocale.Language.Swedish, QLocale.Country.Sweden) if i18n.language() == "sv"
                       else QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom))
    app.setFont(theme.font(14))
    app.setStyleSheet(theme.qss())
    if args.demo:
        try:
            demo.seed_demo_data(ctx.store)
        except Exception:
            logging.getLogger(__name__).exception("demo seeding failed")
    ctx.housekeeping()
    logging.getLogger(__name__).info("start demo=%s", args.demo)

    from lopanalys.ui.main_window import MainWindow
    win = MainWindow(ctx)
    return app, win


SELFCHECK_MODULES = (
    "lopanalys.storage.db", "lopanalys.assessments.library", "lopanalys.assessments.runner",
    "lopanalys.assessments.lactate", "lopanalys.assessments.zones", "lopanalys.assessments.max_hr",
    "lopanalys.assessments.zone_systems", "lopanalys.assessments.vo2max", "lopanalys.assessments.catalog",
    "lopanalys.devices.manager", "lopanalys.devices.ftms", "lopanalys.devices.heart_rate", "lopanalys.devices.qz",
    "lopanalys.camera.discovery", "lopanalys.camera.gopro", "lopanalys.camera.recorder",
    "lopanalys.pose.live", "lopanalys.pose.offline", "lopanalys.pose.estimator", "lopanalys.pose.findings",
    "lopanalys.report.builder", "lopanalys.report.pdf", "lopanalys.report.charts",
    "mediapipe", "cv2", "bleak", "websockets", "scipy.optimize", "scipy.signal", "matplotlib.backends.backend_svg",
    "jinja2", "PIL.Image", "cryptography.hazmat.primitives.ciphers.aead", "cryptography.hazmat.primitives.kdf.scrypt",
    "lopanalys.storage.privacy", "PySide6.QtWebEngineWidgets", "PySide6.QtPdf", "PySide6.QtPrintSupport",
    "lopanalys.share.fit", "lopanalys.share.mail", "lopanalys.share.secret", "smtplib", "ssl", "email.message",
)


def selfcheck(out_path: str | None = None) -> int:
    """Import every module the app needs and render a tiny report. 0 = complete installation.

    The Windows build runs this on the packaged exe: a module that PyInstaller left out must fail the
    build, not surface as a silently degraded program (2026-11-13: the first install ran on stubs).
    """
    import importlib
    import traceback
    lines, bad = [], 0
    for name in SELFCHECK_MODULES:
        try:
            importlib.import_module(name)
            lines.append(f"ok    {name}")
        except Exception:  # noqa: BLE001 - report everything
            bad += 1
            lines.append(f"FAIL  {name}: {traceback.format_exc(limit=1).strip().splitlines()[-1]}")
    try:
        from lopanalys.pose.models import model_path
        lines.append(f"ok    pose model: {model_path('full')}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"note  pose model not bundled: {e}")
    try:
        from lopanalys.core.i18n import t
        from lopanalys.report import builder
        assert t("ui.nav.athletes") != "ui.nav.athletes", "ui translations missing"
        assert (builder.Path(builder.__file__).parent / "templates" / "report.html.j2").exists(), "report template missing"
        assert (builder.Path(builder.__file__).parent / "templates" / "tests_info.html.j2").exists(), "tests sheet template missing"
        lines.append("ok    data files (i18n, report templates)")
    except Exception as e:  # noqa: BLE001
        bad += 1
        lines.append(f"FAIL  data files: {e}")
    text = "\n".join(lines + [f"selfcheck: {'FAILED ' + str(bad) if bad else 'ok'}"])
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 1 if bad else 0


def _splash(title: str, text: str) -> Any:
    """A plain start screen, shown before the heavy imports so a click visibly does something."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import QSplashScreen
    pm = QPixmap(520, 260)
    pm.fill(QColor("#141A21"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillRect(QRect(0, 0, 520, 6), QColor("#22A699"))
    f = QFont("Segoe UI", 26)
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    p.setPen(QColor("#E9EEF3"))
    p.drawText(QRect(40, 70, 440, 60), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
    p.setFont(QFont("Segoe UI", 12))
    p.setPen(QColor("#8B98A6"))
    p.drawText(QRect(40, 140, 440, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
    p.end()
    return QSplashScreen(pm, Qt.WindowType.WindowStaysOnTopHint)


def main(argv: list[str] | None = None) -> int:
    """Run the app."""
    argv = argv if argv is not None else sys.argv[1:]
    if "--selfcheck" in argv:
        i = argv.index("--selfcheck")
        return selfcheck(argv[i + 1] if len(argv) > i + 1 else None)
    from PySide6.QtCore import QLockFile, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance()
    if app is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication(sys.argv[:1])
    from lopanalys.core.i18n import t
    # One instance only: the first start takes a while, and impatient double clicks opened three copies
    # that shared one database (first install, 2026-11-13).
    import tempfile
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "lopanalys.lock"))
    lock.setStaleLockTime(0)   # a lock left by a crash is released when its process is gone
    if not lock.tryLock(100):
        QMessageBox.information(None, t("ui.app.name"), t("ui.app.already_running"))
        return 0
    splash = _splash(t("ui.app.name"), t("ui.app.starting"))
    splash.show()
    app.processEvents()
    app, win = build(argv, app)
    splash.finish(win)
    app._instance_lock = lock  # keep the lock for the lifetime of the app
    win.show()
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
