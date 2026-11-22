"""The athlete step of the wizard: the chosen athlete is the one marked in the list."""
from __future__ import annotations


def test_a_search_that_hides_the_chosen_athlete_unchooses_it(demo_window):
    nt = demo_window.new_test
    demo_window.go("new_test")
    nt.reset()
    nt.a_search.setText("")
    nt.a_list.setCurrentRow(0)
    chosen = nt.athlete
    assert chosen is not None and nt.next.isEnabled()
    nt.a_search.setText("zzzz-nobody")
    assert nt.a_list.count() == 0
    assert nt.athlete is None                      # nobody is marked, so nobody is chosen
    assert not nt.next.isEnabled()
    nt.a_search.setText("")
    assert nt.a_list.currentItem() is None and nt.athlete is None


def test_a_search_that_keeps_the_chosen_athlete_keeps_it_marked(demo_window):
    nt = demo_window.new_test
    demo_window.go("new_test")
    nt.reset()
    nt.a_search.setText("")
    nt.a_list.setCurrentRow(0)
    chosen = nt.athlete
    nt.a_search.setText(chosen.first_name)
    assert nt.athlete is not None and nt.athlete.id == chosen.id
    assert nt.a_list.currentItem() is not None and nt.next.isEnabled()


def test_preselect_is_not_undone_by_an_old_search(demo_window):
    nt = demo_window.new_test
    demo_window.go("new_test")
    nt.reset()
    nt.a_search.setText("zzzz-nobody")
    a = demo_window.ctx.store.athletes()[0]
    nt.preselect(a)
    assert nt.athlete is not None and nt.athlete.id == a.id
    assert nt.stack.currentIndex() == 1
