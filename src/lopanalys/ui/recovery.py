"""Tests cut off by a crash or a power cut: found at program start, opened, and continued.

A running test is in the database with status "running" (``ui.live_controller`` writes it all the
time). Only one program runs at a time, so a test still "running" when the program starts was cut
off: ``collect_interrupted()`` marks it "interrupted" and returns it, and the main window asks
"Ett test avbröts 14:32. Öppna det?".

Continuing (``resume_info()``) is offered only when it gives a usable test:
  - a step protocol (lactate steps, the combined test, the technique test), not a ramp to
    exhaustion: the max HR test (also with technique) is run again from the start, because a break
    before the end makes the peak and the time to exhaustion worthless;
  - at least one stage after the warm-up was completed and a later stage of the protocol is left;
  - the cut was at most ``RESUME_WITHIN_S`` ago: the athlete is still warm, and the lactate of the
    first stage after the break is noted as measured after a pause.
The test then goes on from the next stage in the same session; the time series continues from
where it stopped (the break itself is not in it) and ``result["resumed"]`` says where and when.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lopanalys.assessments.protocol import Protocol
from lopanalys.core.models import Session, TestKind
from lopanalys.ui.context import ResumeFrom

log = logging.getLogger(__name__)

RESUME_WITHIN_S = 20 * 60
RESUMABLE_KINDS = ("lactate", "combined", "gait")


def _plan_kind(s: Session) -> str:
    k = (s.result or {}).get("plan_kind")
    if k:
        return str(k)
    return {TestKind.GAIT: "gait", TestKind.MAX_HR: "max_hr",
            TestKind.HR_ZONES: "hr_zones"}.get(TestKind(s.kind), "lactate")


def video_readable(path: str | None) -> bool | None:
    """True when the film opens and gives a frame, False when not, None when there is no film.

    An MP4 that was not closed (the program died) has no index and cannot be read."""
    if not path:
        return None
    if not Path(path).is_file():
        return False
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        try:
            ok = cap.isOpened() and cap.read()[0]
        finally:
            cap.release()
        return bool(ok)
    except Exception:
        log.exception("checking the film failed")
        return False


def collect_interrupted(store: Any) -> list[Session]:
    """Mark every session still "running" as interrupted and return them, newest first."""
    db = store.db
    out: list[Session] = []
    for s in db.running_sessions():
        assert s.id is not None
        smp = db.time_samples(s.id)
        ended = s.started + timedelta(seconds=smp[-1].t_s) if smp and s.started else None
        db.mark_interrupted(s.id, ended)
        s.status, s.ended = "interrupted", s.ended or ended
        res = dict(s.result or {})
        if video_readable(s.video_path) is False:
            res["video_unreadable"] = True
        res.pop("live", None)
        if res != (s.result or {}):
            s.result = res
            db.set_session_result(s.id, res)
        log.warning("test %s was cut off (started %s)", s.id, s.started)
        out.append(s)
    return sorted(out, key=lambda x: x.started or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def resume_info(store: Any, s: Session, now: datetime | None = None) -> ResumeFrom | None:
    """What continuing ``s`` from its next stage needs, or None when continuing is not reasonable."""
    if s.status != "interrupted" or s.id is None or _plan_kind(s) not in RESUMABLE_KINDS:
        return None
    try:
        p = Protocol.from_dict(s.protocol)
    except Exception:
        return None
    if p.kind in (TestKind.MAX_HR.value, TestKind.HR_ZONES.value):
        return None
    now = now or datetime.now(timezone.utc)
    ended = s.ended or s.started
    if ended is None or (now - ended).total_seconds() > RESUME_WITHIN_S:
        return None
    recs = store.records(s.id)
    warm_idx = {i for i, st in enumerate(p.stages) if st.is_warmup}
    work = [r for r in recs if r.index not in warm_idx]
    if not work:
        return None
    nxt = max(r.index for r in recs) + 1
    if nxt >= len(p.stages):          # nothing left, or the open-ended ramp had begun
        return None
    smp = store.samples(s.id)
    offset = (smp[-1].t_s + 1.0) if smp else sum(r.duration_s for r in recs)
    return ResumeFrom(s, work, [r for r in recs if r.index in warm_idx], smp, nxt, float(offset))
