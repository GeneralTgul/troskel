"""*Till appen* on the plan page: the QR code, the link and the ``.troskel`` file for the athlete's app.

The QR code is read back with OpenCV's reader, so what the dialog shows is the link, and the link is read back with
``plan.link`` to the plan that was saved. Nothing is handed out before the health declaration, and after *Förnya*
the button gives the renewed plan."""
from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox

from lopanalys.plan import link
from lopanalys.share import qr
from lopanalys.ui import services
from lopanalys.ui.views import plan_app

cv2 = pytest.importorskip("cv2")

_spec = importlib.util.spec_from_file_location("_plan_ui_app", Path(__file__).parent / "test_plan_view.py")
plan_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan_ui)


@pytest.fixture
def erik(demo_window):
    return next(a for a in demo_window.ctx.store.athletes() if a.first_name == "Erik")


@pytest.fixture
def shown(monkeypatch):
    """The dialogs *Till appen* opens, without waiting for the coach; message boxes answer by themselves."""
    out: list[plan_app.AppDialog] = []
    monkeypatch.setattr(plan_app.AppDialog, "exec", lambda self: out.append(self) or 1)
    for kind in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    return out


def _read_qr(img: QImage) -> str:
    g = img.convertToFormat(QImage.Format.Format_Grayscale8)
    a = np.frombuffer(g.constBits(), np.uint8).reshape(g.height(), g.bytesPerLine())[:, :g.width()].copy()
    text, *_ = cv2.QRCodeDetectorAruco().detectAndDecode(a)
    return text


def _link_of(length: int) -> str:
    return link.APP_URL + "#p=" + ("aZ-_9" * 1000)[:length - len(link.APP_URL) - 3]


def test_the_qr_code_holds_the_whole_link_up_to_the_limit():
    for n in (300, 2900, qr.QR_MAX):
        s = _link_of(n)
        assert len(s) == n and qr.fits(s)
        img = plan_app.qr_image(s)
        assert img is not None and img.width() >= 500 and _read_qr(img) == s
    assert qr.matrix(_link_of(qr.QR_MAX + 1)) is None and not qr.fits(_link_of(qr.QR_MAX + 1))
    m = qr.matrix("x")
    assert all(not any(row) for row in m[:qr.BORDER]) and len(m) == 21 + 2 * qr.BORDER   # the quiet zone


def test_to_the_app_after_the_health_declaration(demo_window, erik, tmp_path, shown, monkeypatch):
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    plan_ui._fill(v, goal=False)
    assert v.app_btn.text() == "Till appen"
    assert not v.to_app() and shown == [] and "hälsodeklarationen" in v.error.text()
    assert win.ctx.store.db.get_plan(erik.id) is None                     # nothing saved, nothing shown
    v.health["asked"].setChecked(True)
    v.health["cleared"].setChecked(True)
    assert not v.to_app() and shown == []                                 # two of three is not enough
    v.health["advice"].setChecked(True)
    assert v.to_app()
    (dlg,) = shown
    stored = services.load_plan(win.ctx.store, erik)
    assert stored is not None and stored.plan == v.plan and not v.dirty()   # saved first
    # the QR code is the link, and the link is the saved plan with the athlete's name
    assert dlg.qr.pixmap() is not None and not dlg.too_long.isVisibleTo(dlg)
    assert _read_qr(dlg.image) == dlg.link and dlg.link_text.toPlainText() == dlg.link
    payload = link.read_link(dlg.link)
    assert payload == link.stored_plan(stored.plan) and payload["athlete"] == erik.name
    assert "Skanna med telefonens kamera" in " ".join(w.text() for w in dlg.findChildren(type(dlg.too_long)))
    # Kopiera and Spara som fil
    dlg.copy_btn.click()
    assert QGuiApplication.clipboard().text() == dlg.link and dlg.copy_btn.text() == "Kopierad"
    target = tmp_path / "schema.troskel"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    assert dlg.save_file()
    assert target.read_bytes() == link.plan_file(payload)
    assert link.plan_file(payload).decode("utf-8").startswith('{\n "kind": "troskel.plan"')
    assert str(target) in {p["path"] for p in win.ctx.store.db.saved_files(erik.id)}   # erasing the person finds it
    assert dlg.target.suffix == ".troskel"


def test_a_link_too_long_for_a_qr_code_points_to_the_mail_and_the_file(qtbot):
    s = _link_of(qr.QR_MAX + 400)
    dlg = plan_app.AppDialog(s, b"{}", Path("schema.troskel"))
    qtbot.addWidget(dlg)
    assert dlg.image is None and not dlg.qr.isVisibleTo(dlg) and dlg.too_long.isVisibleTo(dlg)
    assert "för långt för en QR-kod" in dlg.too_long.text() and "e-post" in dlg.too_long.text()
    assert str(len(s)) in dlg.too_long.text() and dlg.link_text.toPlainText() == s


def test_after_renewing_the_button_gives_the_renewed_plan(demo_window, erik, shown):
    from datetime import date
    win = demo_window
    win.open_plan(erik)
    v = win.plan
    plan_ui._fill(v, start=date.today())
    for cb in v.health.values():
        cb.setChecked(True)
    assert v.to_app()
    first = link.read_link(shown[-1].link)
    assert first["plan"]["horizon"] == 4
    assert v.renew(today=v.stored.plan.start + timedelta(weeks=3)) and v.stored.plan.horizon == 7
    assert v.to_app()
    renewed = link.read_link(shown[-1].link)
    # the same plan id and start, so the app takes it in place of the old one, now with three more weeks of workouts
    assert renewed["plan"]["horizon"] == 7 and renewed["plan"]["id"] == first["plan"]["id"]
    assert renewed["request"]["start"] == first["request"]["start"]
    assert renewed == link.stored_plan(services.load_plan(win.ctx.store, erik).plan)
