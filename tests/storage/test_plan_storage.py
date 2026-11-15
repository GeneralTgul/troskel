"""The training plan in the database: one per athlete, with its log, in the extract and gone on erasure."""
from __future__ import annotations

from datetime import date

import pytest

from lopanalys.assessments.zone_systems import Anchors
from lopanalys.core.models import Athlete
from lopanalys.plan import RUNNING, Capacity, PlanRequest, Slot, build_plan, codec
from lopanalys.storage.db import Database

TESTED = Capacity(RUNNING, Anchors(192, 46, 165, 174, 13.2, 14.4), date(2026, 11, 13), "Laktattrappa")


def _plan(weeks: int = 12):
    return build_plan(PlanRequest("Exempel Löpare", date(2026, 11, 16), (Slot(1, 60), Slot(3, 60), Slot(6, 90)),
                                  (TESTED,), duration_weeks=weeks, recent_weekly_minutes=150))


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "data" / "l.sqlite")
    yield d
    d.close()


def _store(db, aid, plan, **kw):
    return db.save_plan(aid, request=codec.request_to_dict(plan.request), plan=codec.plan_to_dict(plan),
                        engine_version=plan.engine_version, **kw)


def test_save_read_and_replace(db):
    aid = db.add_athlete(Athlete(None, "Exempel", "Löpare"))
    assert db.get_plan(aid) is None
    plan = _plan()
    pid = _store(db, aid, plan, health={"confirmed": ["asked"], "date": "2026-11-14"})
    got = db.get_plan(aid)
    back = codec.plan_from_dict(got["plan"], codec.request_from_dict(got["request"], plan.request.athlete_name))
    assert back == plan and got["health"]["confirmed"] == ["asked"] and got["engine_version"] == plan.engine_version
    # a new plan replaces the old one, keeps the health declaration when none is given, and logs the event
    other = _plan(16)
    assert _store(db, aid, other, event="changed") == pid
    got = db.get_plan(aid)
    assert len(got["plan"]["weeks"]) == 16 and got["health"]["date"] == "2026-11-14"
    assert [e["kind"] for e in db.plan_events(pid)] == ["saved", "changed"]
    assert db._q("SELECT COUNT(*) FROM plans")[0][0] == 1


def test_plan_is_in_the_extract_and_gone_with_the_athlete(db):
    aid = db.add_athlete(Athlete(None, "Exempel", "Löpare"))
    other = db.add_athlete(Athlete(None, "Annan", "Löpare"))
    _store(db, aid, _plan())
    _store(db, other, _plan())
    assert db.export_athlete(aid)["training_plan"]["events"][0]["kind"] == "saved"
    db.erase_athlete(aid)
    assert db._q("SELECT athlete_id FROM plans")[0][0] == other and len(db._q("SELECT * FROM plan_events")) == 1


def test_delete_plan(db):
    aid = db.add_athlete(Athlete(None, "Exempel", "Löpare"))
    assert db.delete_plan(aid) is False
    _store(db, aid, _plan(8))
    assert db.delete_plan(aid) is True and db.get_plan(aid) is None
    assert db._q("SELECT COUNT(*) FROM plan_events")[0][0] == 0
