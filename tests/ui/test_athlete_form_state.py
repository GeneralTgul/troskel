"""The athlete form follows what was saved elsewhere, and knows all its fields."""
from __future__ import annotations

from lopanalys.core.models import Athlete
from lopanalys.ui import services
from lopanalys.ui.context import AppContext
from lopanalys.ui.views.athletes import AthletesView


def _view(qtbot, tmp_path) -> tuple[AppContext, AthletesView, Athlete]:
    ctx = AppContext(store=services.open_store(tmp_path / "db.sqlite"))
    a = ctx.store.save_athlete(Athlete(None, "Olle", "Löpare", max_hr=185))
    v = AthletesView(ctx)
    qtbot.addWidget(v)
    v.select(a.id)
    assert v.form.athlete is not None and v.form.athlete.id == a.id
    return ctx, v, a


def test_a_max_hr_saved_by_a_test_is_not_written_over_from_the_open_form(qtbot, tmp_path):
    """Before 2026-11-22 the form kept the athlete as loaded: a max test raised the max HR (or the wizard
    saved the weight), and the next *Spara* in the form, for an e-mail address, wrote the old values back."""
    ctx, v, a = _view(qtbot, tmp_path)
    fresh = ctx.store.athlete(a.id)
    fresh.max_hr, fresh.max_hr_source, fresh.weight_kg = 196, "test", 71.5      # what the results page and the wizard do
    ctx.store.save_athlete(fresh)
    ctx.athletes_changed.emit()
    assert v.form.max_hr.value() == 196 and v.form.weight.value() == 71.5
    assert not v.form.dirty()
    v.form.email.setText("olle@example.com")
    v.form._save()
    saved = ctx.store.athlete(a.id)
    assert (saved.max_hr, saved.max_hr_source, saved.weight_kg, saved.email) == (196, "test", 71.5, "olle@example.com")
    ctx.shutdown()


def test_unsaved_fields_are_kept_when_the_list_is_refreshed(qtbot, tmp_path):
    ctx, v, a = _view(qtbot, tmp_path)
    v.form.email.setText("olle@example.com")            # not saved yet
    ctx.athletes_changed.emit()
    assert v.form.email.text() == "olle@example.com" and v.form.dirty()
    ctx.shutdown()


def test_the_max_hr_of_the_other_sports_counts_as_a_change(qtbot, tmp_path):
    """Before 2026-11-22 a max HR typed for cycling or the SkiErg was not seen as a change: *Avbryt* stayed
    grey and another athlete could be chosen without the question about unsaved changes."""
    ctx, v, a = _view(qtbot, tmp_path)
    assert not v.form.dirty()
    v.form.max_hr_bike.setText("181")
    assert v.form.dirty() and v.form.cancel_btn.isEnabled()
    v.form.revert()
    v.form.max_hr_skierg.setText("175")
    assert v.form.dirty()
    ctx.shutdown()
