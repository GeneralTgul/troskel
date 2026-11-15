"""Demo data: Erik's two lactate tests show progress. The example report and the screenshots are made
from them, so the later test must come out better than the earlier one (2026-11-15: LT1 looked worse)."""
from __future__ import annotations

from lopanalys.assessments.lactate import analyze
from lopanalys.ui.demo import seed_demo_data


class _Store:
    """Just enough of the store for seed_demo_data."""

    def __init__(self) -> None:
        self.people, self.sessions, self.records = [], [], {}

    def athletes(self):
        return self.people

    def save_athlete(self, a):
        a.id = len(self.people) + 1
        self.people.append(a)
        return a

    def save_session(self, s):
        s.id = len(self.sessions) + 1
        self.sessions.append(s)
        return s

    def save_records(self, sid, recs):
        self.records[sid] = recs

    def save_samples(self, sid, samples):
        pass


def test_erik_gets_better_between_his_two_lactate_tests():
    store = _Store()
    seed_demo_data(store)
    erik = next(a for a in store.people if a.last_name == "Sandberg")
    older, newer = sorted(store.sessions, key=lambda s: s.started)
    assert older.athlete_id == newer.athlete_id == erik.id
    before = analyze(store.records[older.id], erik.max_hr)
    after = analyze(store.records[newer.id], erik.max_hr)
    for r in (before, after):
        assert r.lt1 and r.lt2 and not r.warnings, [w.code for w in r.warnings]
    # both thresholds clearly forward, and a lower heart rate at the same speed
    assert after.lt1.speed_kmh >= before.lt1.speed_kmh + 0.3
    assert after.lt2.speed_kmh >= before.lt2.speed_kmh + 0.3
    assert all(n.hr_end < o.hr_end for o, n in zip(store.records[older.id], store.records[newer.id]))
    # reasonable numbers for a trained amateur: LT1 below LT2, both under his max heart rate
    assert 10.0 < after.lt1.speed_kmh < after.lt2.speed_kmh < 14.0
    # far enough apart for zones 3 and 4 to be a few beats wide each, not two or three
    assert after.lt2.speed_kmh - after.lt1.speed_kmh >= 1.0 and after.lt2.hr - after.lt1.hr >= 8
    assert after.lt2.hr < erik.max_hr
