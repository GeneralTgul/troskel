"""Smoke tests: the window builds, the wizard walks through, a demo test runs, the TV view renders."""
import time

from lopanalys.assessments.protocol import Phase


def _pump(qtbot, sec):
    qtbot.wait(int(sec * 1000))


def test_window_builds_and_navigates(demo_window, qtbot):
    w = demo_window
    for key in ("athletes", "new_test", "history", "settings"):
        w.go(key)
        assert w.stack.currentWidget() is not None
    assert w.athletes.list.count() >= 3          # demo athletes


def test_wizard_demo_test_and_tv(demo_window, qtbot):
    w = demo_window
    nt = w.new_test
    w.go("new_test")
    nt.go(0)
    nt.a_list.setCurrentRow(0)
    assert nt.athlete is not None
    nt._next()
    nt.kind_group.button(3).click()               # combined: lactate steps, filmed
    nt._next()
    assert nt.protocol is not None and nt.protocol.stages
    nt.p_warm.setValue(0)                          # no warm-up: get to the stages quickly
    nt._next()
    qtbot.waitUntil(lambda: nt.cam_list.count() >= 2, timeout=5000)
    nt._next()
    qtbot.waitUntil(lambda: nt.tm_connected and nt.hr_connected, timeout=8000)
    nt._next()
    assert nt.stack.currentIndex() == 5
    qtbot.waitUntil(lambda: nt.check_view.frame_count > 0, timeout=5000)
    nt._next()                                     # launch
    op = w.operator
    assert op is not None and op.tv is not None
    op.start()
    qtbot.waitUntil(lambda: op.ctl.state is not None and op.ctl.state.phase == Phase.RUNNING, timeout=15000)
    _pump(qtbot, 1.5)
    assert op.ctl.hr is not None
    assert op.tv.board.video.frame_count > 0
    op.tv.resize(1920, 1080)
    img = op.tv.grab()
    assert img.width() == 1920
    op.pad.press("2")
    op.pad.press(",")
    op.pad.press("1")
    op.pad.submit()
    assert 2.1 in op.ctl.lactate.values()
    op.stop()                                      # QMessageBox.question is stubbed to Yes
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.results, timeout=10000)
    assert w.results.session is not None
