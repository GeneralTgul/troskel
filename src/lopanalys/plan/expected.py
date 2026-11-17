"""What the plan counts on, and how much of it was done (#778). No Qt, no clock: the days come in as arguments.

A new test is also a check on the plan: did the thresholds move as fast as the plan counted on, and how much of
the plan was done between the tests? Two pure functions answer that, and the report's part *Mot schemat* shows them
beside the comparison with the previous test (report.builder).

**The expected development** (``expected``) is an estimate, never a promise, and the report says so. The engine has
no model of the athlete's development, only progressions in the workouts, so the rule is a plain one: the threshold
load (speed at LT1 and LT2 when running, watt on the bike and the SkiErg) rises by a share a week that depends on the
athlete's level, counted only for the plan's weeks between the tests and weighted by the week's kind and the sport's
role in the plan. After 12 such weeks the gain halves: the curve flattens with time. The heart rate at the
thresholds is expected to stay about the same.

The shares are Tröskel's own estimate, chosen from what the studies report, not numbers taken from one study:

- Londeree, B. R. (1997). Effect of training on lactate/ventilatory thresholds: a meta-analysis. *Medicine &
  Science in Sports & Exercise*, 29(6), 837-843. The thresholds of untrained people rise most; trained athletes gain
  far less from the same training.
- Jones, A. M. & Carter, H. (2000). The effect of endurance training on parameters of aerobic fitness. *Sports
  Medicine*, 29(6), 373-386. The lactate threshold moves with training in weeks, also when VO2max stands still; the
  heart rate at the threshold changes little.
- Midgley, A. W., McNaughton, L. R. & Jones, A. M. (2007). Training to enhance the physiological determinants of
  long-distance running performance. *Sports Medicine*, 37(10), 857-880. Well-trained runners improve the threshold
  by a few per cent over a season.
- Mujika, I. & Padilla, S. (2003). Scientific bases for precompetition tapering strategies. *Medicine & Science in
  Sports & Exercise*, 35(7), 1182-1187. A taper keeps the adaptations rather than adding new ones.

With these shares 12 loaded weeks give about +12 % for a beginner, +7 % for a recreational athlete, +4 % for a
trained one and +2.4 % at high volume. A test-retest difference of 1-2 % is within the measurement error
(``TOLERANCE_PCT``), so the report calls a test within it of the expectation *as planned*.

**How much was done** (``done``) comes from the workouts' status (what Tröskel saved, the coach's ticks and what was
read in from the phone, plan.phone.to_plan) and from the logs the athlete sent from the phone (the ``phone`` events,
plan.phone.PhonePlan.log): the last log that marks a workout wins. A partly done workout counts its logged minutes
(half the planned ones when the log has none). A workout after the last log and without a status is *not logged*, not
missed: the report counts the share only over what is known.

**Own values are not a test** (#777): the athlete may type thresholds into the app without a test
(``Capacity.own_values``). The follow-up never reads the plan's capacity: the caller passes the days of two tests in
Tröskel (services.plan_followup takes the previous test from the database), so a new test is compared with the last
measured one only. A plan built on own values counts its weeks from the previous test like any other, and with no
previous test there is no follow-up.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .models import BORROWS, LEVELS, STRENGTH, Plan, Workout

# The rise of the threshold load in per cent a week, per level (PlanRequest.level), for a loaded week of the main sport.
RATE_PCT = {"beginner": 1.0, "recreational": 0.6, "trained": 0.35, "high": 0.2}
# What a week counts for, by kind (models.WEEK_KINDS): an easy or test week rests and absorbs, a taper keeps.
WEEK_FACTOR = {"load": 1.0, "easy": 0.5, "test": 0.5, "taper": 0.25, "race": 0.0}
# What the sport's role gives (models.ROLES): a supporting sport gets less of the time, a maintained one only keeps.
ROLE_FACTOR = {"main": 1.0, "support": 0.5, "maintenance": 0.0}
FULL_WEEKS = 12                 # after this many counted weeks the gain halves
TOLERANCE_PCT = 1.5             # the measurement error of a retest: closer than this to the expectation is "as planned"
PARTIAL_SHARE = 0.5             # a partly done workout without logged minutes


@dataclass(frozen=True)
class Expected:
    """What the plan counted on in one sport between two tests: ``weeks`` counted weeks give ``gain_pct``."""
    sport: str
    role: str
    level: str
    start: date
    end: date
    weeks: float                 # weeks of the plan between the tests, weighted by kind and role
    gain_pct: float              # the expected rise of the threshold load, per cent

    def value(self, before: float) -> float:
        """The threshold load the plan counted on, from the value at the previous test."""
        return before * (1 + self.gain_pct / 100)

    def verdict(self, before: float, measured: float) -> str:
        """``ahead`` | ``on`` | ``behind``: the measured load against the expected, with the measurement error."""
        diff = (measured - self.value(before)) / before * 100 if before else 0.0
        return "on" if abs(diff) <= TOLERANCE_PCT else ("ahead" if diff > 0 else "behind")


@dataclass(frozen=True)
class SportDone:
    """The workouts of one sport between the tests: minutes and quality sessions, planned and done."""
    sport: str
    planned_min: int = 0
    done_min: int = 0
    unknown_min: int = 0         # planned minutes after the last log and without a status
    quality_planned: int = 0
    quality_done: int = 0

    @property
    def known_min(self) -> int:
        return self.planned_min - self.unknown_min


@dataclass(frozen=True)
class Done:
    """How much of the plan was done between the tests, per sport, and the sessions outside the plan."""
    start: date
    end: date
    sports: tuple[SportDone, ...] = ()
    extras: int = 0              # sessions outside the plan the athlete logged
    extras_min: int = 0
    logged_until: date | None = None   # the day of the last log from the phone

    @property
    def planned_min(self) -> int:
        return sum(s.planned_min for s in self.sports)

    @property
    def done_min(self) -> int:
        return sum(s.done_min for s in self.sports)

    @property
    def known_min(self) -> int:
        return sum(s.known_min for s in self.sports)

    @property
    def share(self) -> float | None:
        """Done minutes over the planned minutes that are known; None when nothing is known."""
        return self.done_min / self.known_min if self.known_min else None

    @property
    def quality_planned(self) -> int:
        return sum(s.quality_planned for s in self.sports)

    @property
    def quality_done(self) -> int:
        return sum(s.quality_done for s in self.sports)


@dataclass(frozen=True)
class Followup:
    """The report's *Mot schemat*: the expectation in the test's sport and what was done in the whole plan."""
    expected: Expected | None
    done: Done
    plan_start: date

    def to_dict(self) -> dict[str, Any]:
        d = self.done
        return {
            "plan_start": self.plan_start.isoformat(),
            "expected": None if self.expected is None else
            {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(self.expected).items()},
            "done": {"start": d.start.isoformat(), "end": d.end.isoformat(), "share": d.share,
                     "planned_min": d.planned_min, "done_min": d.done_min, "known_min": d.known_min,
                     "quality_planned": d.quality_planned, "quality_done": d.quality_done,
                     "extras": d.extras, "extras_min": d.extras_min,
                     "logged_until": d.logged_until.isoformat() if d.logged_until else None,
                     "sports": [{**asdict(s), "known_min": s.known_min} for s in d.sports]},
        }


def role_of(plan: Plan, sport: str) -> str | None:
    """The role in the plan of the test's sport: its own, else that of a sport that borrows its zones (skis from the
    SkiErg); None when the plan does not have it."""
    roles = {r.sport: r.role for r in plan.request.plan_roles()}
    if sport in roles:
        return roles[sport]
    borrowers = [roles[s] for s, own in BORROWS.items() if own == sport and s in roles]
    return min(borrowers, key=lambda r: list(ROLE_FACTOR).index(r)) if borrowers else None


def _days(start: date, end: date) -> Iterable[date]:
    """The days after ``start`` and before ``end``: the time between two tests."""
    d = start + timedelta(days=1)
    while d < end:
        yield d
        d += timedelta(days=1)


def expected(plan: Plan, sport: str, start: date, end: date) -> Expected | None:
    """What the plan counted on in ``sport`` between a test on ``start`` and one on ``end``. None when the plan has
    no such sport. Days outside the plan's weeks count for nothing."""
    role = role_of(plan, sport)
    if role is None:
        return None
    level = plan.request.level if plan.request.level in LEVELS else "recreational"
    kind = {wk.monday + timedelta(days=i): wk.kind for wk in plan.weeks for i in range(7)}
    weeks = sum(WEEK_FACTOR.get(kind[d], 0.0) for d in _days(start, end) if d in kind) / 7 * ROLE_FACTOR[role]
    rate = RATE_PCT[level]
    gain = rate * min(weeks, FULL_WEEKS) + rate / 2 * max(0.0, weeks - FULL_WEEKS)
    return Expected(sport, role, level, start, end, round(weeks, 2), round(gain, 2))


def _marks(logs: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Mapping[str, Any]], date | None]:
    """The marks of the logs (oldest first), the later one winning, and the day of the last log."""
    marks: dict[str, Mapping[str, Any]] = {}
    last: date | None = None
    for log in logs:
        marks.update(log.get("marks") or {})
        try:
            day = date.fromisoformat(str(log.get("exported")))
        except ValueError:
            continue
        last = day if last is None or day > last else last
    return marks, last


def _done_minutes(w: Workout, mark: Mapping[str, Any] | None, logged_until: date | None) -> int | None:
    """The minutes done of the workout; None when nothing is known about it."""
    if mark is not None:
        st = mark.get("status")
        if st == "missed":
            return 0
        m = mark.get("minutes")
        if isinstance(m, (int, float)) and m >= 0:
            return round(m)
        return w.minutes if st == "done" else round(w.minutes * PARTIAL_SHARE)
    if w.status == "done":
        return w.minutes
    if w.status in ("missed", "skipped"):
        return 0
    return 0 if logged_until is not None and w.date <= logged_until else None


def done(plan: Plan, start: date, end: date, logs: Iterable[Mapping[str, Any]] = ()) -> Done:
    """How much of the plan was done after a test on ``start`` and before one on ``end``, per sport (strength too),
    from the workouts' status and the phone's logs (oldest first). Races are left out; the sketched weeks have no
    workouts yet, so only what was handed out counts."""
    logs = list(logs)
    marks, last = _marks(logs)
    per: dict[str, dict[str, int]] = {}
    for w in plan.workouts:
        if w.goal is not None or not (start < w.date < end):
            continue
        s = per.setdefault(w.sport, {"planned_min": 0, "done_min": 0, "unknown_min": 0, "quality_planned": 0,
                                     "quality_done": 0})
        got = _done_minutes(w, marks.get(w.key), last)
        s["planned_min"] += w.minutes
        if got is None:
            s["unknown_min"] += w.minutes
        else:
            s["done_min"] += got
        if w.quality:
            s["quality_planned"] += 1
            s["quality_done"] += 1 if got else 0
    order = [*plan.request.plan_sports, STRENGTH]
    sports = tuple(SportDone(sp, **per[sp]) for sp in sorted(per, key=lambda x: order.index(x) if x in order else 99))
    extras = [x for log in logs[-1:] for x in log.get("extras") or []
              if isinstance(x, Mapping) and start.isoformat() < str(x.get("date")) < end.isoformat()]
    return Done(start, end, sports, len(extras), sum(round(x.get("minutes") or 0) for x in extras), last)


def followup(plan: Plan, sport: str, start: date, end: date, logs: Iterable[Mapping[str, Any]] = ()) -> Followup | None:
    """*Mot schemat* for a test in ``sport`` on ``end`` after one on ``start``: None when the plan has no days between
    them (it began after the new test, or ended before the previous one)."""
    if end <= start or plan.start >= end or plan.end <= start:
        return None
    return Followup(expected(plan, sport, start, end), done(plan, start, end, logs), plan.start)


__all__ = ["Done", "Expected", "Followup", "SportDone", "done", "expected", "followup", "role_of"]
