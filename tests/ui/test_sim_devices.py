"""Simulated treadmill and heart rate outside demo mode: last, marked "Övning", never preselected,
and a test run with them is marked on the operator page, the results and the report.

On real hardware (2026-11-14) "Simulerad puls" was tried in a real test; such a test must never be taken
for a real one.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lopanalys.core import simulated
from lopanalys.core.i18n import t


@pytest.fixture
def real_window(qapp, monkeypatch):
    from lopanalys.ui import services
    monkeypatch.setattr(services, "discover_sources", lambda *a, **k: [])
    from lopanalys.ui.app import build
    _, win = build([], qapp)
    win.resize(1440, 900)
    win.show()
    yield win
    win.close()


def _items(lst):
    return [(lst.item(i).text(), lst.item(i).data(0x0100)) for i in range(lst.count())]


def test_simulators_are_last_marked_and_not_preselected(real_window):
    nt = real_window.new_test
    assert nt.modes[-1][0] == "sim" and nt.mode != "sim"
    assert nt.mode_group.buttons()[-1].title == t("ui.mode.sim") == "Övning: simulerat band"
    assert not nt.mode_group.buttons()[-1].isChecked()
    items = _items(nt.hr_list)
    assert items[-1] == (t("ui.dev.sim_hr"), "sim") and t("ui.dev.sim_hr") == "Övning: simulerad puls"
    assert nt.hr_list.currentItem().data(0x0100) is None          # "Ingen puls" is preselected
    assert real_window.settings.mode.findData("sim") == -1        # never the saved default


def test_saved_sim_mode_is_not_the_default_in_a_real_test(qapp, monkeypatch):
    from lopanalys.ui import services
    from lopanalys.ui.context import AppContext
    from lopanalys.ui.views.new_test import NewTestView
    monkeypatch.setattr(services, "discover_sources", lambda *a, **k: [])
    ctx = AppContext(demo=False)
    ctx.set_setting("devices.treadmill_mode", "sim")
    assert NewTestView(ctx).mode == "ftms"


def test_demo_mode_is_as_before(demo_window):
    nt = demo_window.new_test
    assert nt.mode == "sim"
    assert demo_window.settings.mode.findData("sim") >= 0


def test_marking_of_a_practice_run():
    assert simulated.from_plan("ftms", None) == []
    assert simulated.from_plan("sim", "sim") == ["treadmill", "heart_rate"]

    class S:
        result = {"simulated": ["heart_rate"]}
    assert simulated.label(simulated.of_session(S())) == "Övning: simulerad puls"
    S.result = {}
    assert simulated.label(simulated.of_session(S())) == ""


def test_report_says_practice_run(tmp_path):
    from lopanalys.core.models import Athlete, Session, TestKind
    from lopanalys.report import builder
    a = Athlete(1, "Test Person", "")
    s = Session(1, 1, TestKind.LACTATE_STEP, datetime(2026, 11, 14, 13, 31, tzinfo=timezone.utc),
                protocol_name="Laktattrappa", treadmill_mode="ftms")
    assert "Övning" not in builder.render_html(builder.ReportData(athlete=a, session=s))
    s.result = {"simulated": ["heart_rate"]}
    assert "Övning: simulerad puls" in builder.render_html(builder.ReportData(athlete=a, session=s))


def test_operator_and_results_show_the_mark(real_window, qtbot):
    from lopanalys.ui.views.operator import OperatorView
    nt = real_window.new_test
    w = real_window
    w.go("new_test")
    from lopanalys.core.models import Athlete
    nt.athlete = Athlete(None, "Test Person", "", height_cm=180.0)   # a real install has no demo athletes
    nt.go(2)                                      # builds the protocol
    nt.go(4)                                      # Enheter
    nt._mode_chosen(len(nt.modes) - 1)            # Övning: simulerat band
    nt._connect_tm()
    nt.hr_list.setCurrentRow(nt.hr_list.count() - 1)
    nt._connect_hr()
    qtbot.waitUntil(lambda: nt.tm_connected and nt.hr_connected, timeout=8000)
    nt.go(5)                                      # Kontroll: the controller and its session
    assert simulated.of_session(nt.ctl.session) == ["treadmill", "heart_rate"]
    op = OperatorView(w.ctx, nt.ctl)
    assert op.sim_pill is not None and op.sim_pill._text == "Övning: simulerat band · Övning: simulerad puls"
    nt._teardown_ctl()
