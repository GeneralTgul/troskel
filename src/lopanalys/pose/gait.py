"""Gait events on a treadmill: initial contact (IC) and toe-off (TO) for each foot.

Side view, two signals together:

* **One contact per stride** from the forward swing: the heel is furthest in front of the pelvis just
  before the foot lands, and the belt then carries it back (Zeni, Richards & Higginson 2008, Gait
  Posture 27:710-714, a standard method on a treadmill without force plates). The stride time comes
  from the autocorrelation of the foot motion, so a noisy frame cannot add or drop a contact.
* **The timing from the height above the belt:** heel and toe each have their own belt line (in a
  moving window), and the foot is on the belt when either is. The landing is the first frame from the
  forward peak on where the foot is down, the toe-off the frame where it leaves. Both are interpolated
  between frames and extrapolated a little towards zero height (at most one frame).

Contacts outside steady running (standing on the belt before the run, a foot hidden behind the
treadmill) are left out, and ``event_quality`` says how far the rest can be trusted.

Rear or front view: the height alone, as before (the foot moves towards the camera, so contact time
is less exact). Accuracy improves with frame rate; 60 fps or more is recommended for contact times.

The method was rebuilt after the first real clip (real hardware 2026-11-14): the lowest point of the
foot against one belt line found heel strikes 2-4 frames late, because MediaPipe's heel landmark sits
above the sole, and standing still before the run moved the belt line for the whole clip.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import find_peaks

from lopanalys.pose.series import PoseSeries, interior_angle, is_side_view
from lopanalys.pose.types import Side, View


@dataclass
class Contact:
    """One ground contact of one foot. Indices are fractional rows of the series."""
    side: Side
    t_ic: float
    t_to: float | None
    i_ic: float
    i_to: float | None
    i_mid: int | None = None
    method: str = "height"          # height above the belt, or position (forward peak) as fallback

    @property
    def gct_ms(self) -> float | None:
        return None if self.t_to is None else (self.t_to - self.t_ic) * 1000.0


@dataclass
class GaitEvents:
    contacts: list[Contact]           # sorted by t_ic
    facing: int                       # +1 athlete faces image right, -1 left (1 for rear/front)
    view: View
    ground_y: dict[Side, float] = field(default_factory=dict)
    threshold_px: dict[Side, float] = field(default_factory=dict)
    stride_s: float | None = None     # stride time from the foot motion (side view)

    def side(self, side: Side) -> list[Contact]:
        return [c for c in self.contacts if c.side == side]

    def step_times(self) -> np.ndarray:
        """Time between consecutive contacts of opposite feet (s), outliers removed."""
        c = self.contacts
        d = np.array([b.t_ic - a.t_ic for a, b in zip(c, c[1:]) if a.side != b.side])
        if d.size == 0:
            # only one foot detected: half the stride time
            strides = []
            for s in (Side.LEFT, Side.RIGHT):
                cs = self.side(s)
                strides += [(b.t_ic - a.t_ic) / 2 for a, b in zip(cs, cs[1:])]
            d = np.array(strides)
        if d.size == 0:
            return d
        med = np.median(d)
        return d[(d > 0.6 * med) & (d < 1.5 * med)]

    def cadence_spm(self) -> float | None:
        d = self.step_times()
        return float(60.0 / np.median(d)) if d.size else None


def detect_facing(s: PoseSeries, view: View | str) -> int:
    """+1 when the athlete faces image right. Uses heel->toe direction (robust), view as fallback."""
    v = View(view)
    if not is_side_view(v):
        return 1
    votes = []
    for side in (Side.LEFT, Side.RIGHT):
        d = s.side_lm(side, "foot")[:, 0] - s.side_lm(side, "heel")[:, 0]
        votes.append(d[~np.isnan(d)])
    nose = s.lm("nose")[:, 0] - s.mid("shoulder")[:, 0]
    allv = np.concatenate(votes)
    if allv.size >= 5 and abs(np.median(allv)) > 2:
        return 1 if np.median(allv) > 0 else -1
    nose = nose[~np.isnan(nose)]
    if nose.size >= 5 and abs(np.median(nose)) > 2:
        return 1 if np.median(nose) > 0 else -1
    return -1 if v == View.SIDE_LEFT else 1


def _belt_line(y: np.ndarray) -> float:
    """Image y where a landmark sits on the belt: the median of its lowest 20 % of positions."""
    v = y[~np.isnan(y)]
    if v.size < 5:
        return float("nan")
    return float(np.median(v[v >= np.percentile(v, 80)]))


def _local_belt_line(t: np.ndarray, y: np.ndarray, window_s: float = 6.0) -> np.ndarray:
    """``_belt_line`` in a moving window (per sample), so that standing still before the run, a
    camera that is moved or a belt that is not level do not shift the line for the whole clip."""
    n = len(y)
    dur = float(t[-1] - t[0]) if n > 1 else 0.0
    if dur <= 1.5 * window_s:
        return np.full(n, _belt_line(y))
    centres = np.arange(t[0] + window_s / 2, t[-1] - window_s / 2 + 1e-9, window_s / 4)
    vals = []
    for c in centres:
        m = (t >= c - window_s / 2) & (t < c + window_s / 2)
        vals.append(_belt_line(y[m]))
    vals = np.array(vals)
    ok = ~np.isnan(vals)
    if not ok.any():
        return np.full(n, np.nan)
    return np.interp(t, centres[ok], vals[ok])


def foot_height(s: PoseSeries, side: Side) -> tuple[np.ndarray, float]:
    """Height (px, up) of a foot above the belt, and the belt line y of its lowest landmark.

    Heel and toe each get their own belt line, and the foot is as low as the lower of the two
    heights. The heel landmark sits on the back of the heel, well above the sole: at a heel strike it
    is on the belt while the lowest point of the foot (the toe, still raised) is not, and a single belt
    line for "the lowest point" found the landing only when the toe came down, 2-4 frames late
    (real hardware 2026-11-14). The belt lines are taken in a moving window of a few seconds:
    standing on the belt before the run had moved them for the whole clip.
    """
    heel_y = s.side_lm(side, "heel")[:, 1]
    toe_y = s.side_lm(side, "foot")[:, 1]
    g_heel, g_toe = _local_belt_line(s.t, heel_y), _local_belt_line(s.t, toe_y)
    if np.all(np.isnan(g_heel)) or np.all(np.isnan(g_toe)):
        return np.full(len(heel_y), np.nan), float("nan")
    h = np.fmin(g_heel - heel_y, g_toe - toe_y)
    return h, float(np.nanmedian(np.fmax(g_heel, g_toe)))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.r_[0, mask.astype(int), 0]))
    return list(zip(edges[::2], edges[1::2]))


def _clean_runs(mask: np.ndarray, min_len: int, max_gap: int) -> list[tuple[int, int]]:
    runs = _runs(mask)
    merged: list[list[int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= max_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= min_len]


def _cross(t: np.ndarray, h: np.ndarray, i: int, level: float) -> float:
    """Time where h crosses ``level`` between samples i-1 and i (linear)."""
    a, b = h[i - 1], h[i]
    if np.isnan(a) or np.isnan(b) or a == b:
        return float(t[i])
    f = (level - a) / (b - a)
    return float(t[i - 1] + np.clip(f, 0.0, 1.0) * (t[i] - t[i - 1]))


def _extrapolate(t: np.ndarray, h: np.ndarray, i_edge: int, thr: float, direction: int) -> float:
    """Event time at zero height from the crossings of ``thr`` and 2.5 x ``thr``.

    ``i_edge`` is the first sample inside (IC, direction=-1: look back) or outside (TO,
    direction=+1: look forward) of contact. Falls back to the plain crossing.

    The extrapolation is used only where the height falls (IC) or rises (TO) steadily between the two
    crossings, and moves the event by at most one frame interval: a foot that skims in over the belt,
    or a bump in the landmarks, made the slope small and moved landings 3 frames late at 25 fps
    (real hardware 2026-11-14).
    """
    hi = 2.5 * thr
    n = len(h)
    if direction < 0:      # IC: crossing of thr between i_edge-1 and i_edge, higher crossing earlier
        t_a = _cross(t, h, i_edge, thr)
        j = i_edge - 1
        while j > 0 and not np.isnan(h[j]) and h[j] < hi and i_edge - j < 6:
            j -= 1
        if j <= 0 or np.isnan(h[j]) or h[j] < hi or np.any(np.diff(h[j:i_edge + 1]) >= 0):
            return t_a
        t_b = _cross(t, h, j + 1, hi)
        dt = t_a - t_b
        if dt <= 0:
            return t_a
        step = float(t[i_edge] - t[i_edge - 1])
        return t_a + min(dt * thr / (hi - thr), step)     # still descending: zero height comes after
    t_a = _cross(t, h, i_edge, thr)
    j = i_edge
    while j < n - 1 and not np.isnan(h[j]) and h[j] < hi and j - i_edge < 6:
        j += 1
    if j >= n - 1 or np.isnan(h[j]) or h[j] < hi or np.any(np.diff(h[i_edge - 1:j + 1]) <= 0):
        return t_a
    t_b = _cross(t, h, j, hi)
    dt = t_b - t_a
    if dt <= 0:
        return t_a
    step = float(t[i_edge] - t[i_edge - 1])
    return t_a - min(dt * thr / (hi - thr), step)         # rising: it left the belt before the crossing


def _frac_index(t: np.ndarray, at: float) -> float:
    return float(np.interp(at, t, np.arange(len(t))))


def detect_contacts_side(s: PoseSeries, side: Side) -> tuple[list[Contact], float, float]:
    """Contacts of one foot. Returns (contacts, belt y, threshold px)."""
    h, ground = foot_height(s, side)
    ok = ~np.isnan(h)
    if ok.sum() < max(10, 0.5 * len(h)):
        return [], ground, float("nan")
    peak = float(np.percentile(h[ok], 95))
    if peak < 0.02 * s.height:
        return [], ground, float("nan")      # the foot hardly leaves the belt: not running
    dh = np.diff(h[ok])
    sigma = 1.4826 * float(np.median(np.abs(dh - np.median(dh)))) / np.sqrt(2)
    thr = max(0.07 * peak, 3.0 * sigma, 0.004 * s.height)
    fps = s.fps
    mask = ok & (h < thr)
    runs = _clean_runs(mask, min_len=max(2, int(round(0.06 * fps))), max_gap=max(1, int(round(0.025 * fps))))
    t = s.t
    out: list[Contact] = []
    for a, b in runs:
        if a == 0:
            continue                         # contact already under way when the clip starts
        t_ic = _extrapolate(t, h, a, thr, -1)
        t_to = None if b >= len(h) else _extrapolate(t, h, b, thr, +1)
        out.append(Contact(side, t_ic, t_to, _frac_index(t, t_ic),
                           None if t_to is None else _frac_index(t, t_to)))
    return out, ground, thr


def _filled(y: np.ndarray, max_gap: int) -> np.ndarray:
    """``y`` with NaN gaps of up to ``max_gap`` samples bridged linearly (longer gaps stay NaN)."""
    ok = ~np.isnan(y)
    if ok.sum() < 2:
        return y.copy()
    idx = np.arange(len(y))
    out = np.interp(idx, idx[ok], y[ok])
    last = np.maximum.accumulate(np.where(ok, idx, -10 ** 9))
    nxt = np.minimum.accumulate(np.where(ok, idx, 10 ** 9)[::-1])[::-1]
    return np.where(ok | ((nxt - last) <= max_gap + 1), out, np.nan)


def forward_position(s: PoseSeries, side: Side, part: str, facing: int) -> np.ndarray:
    """How far a foot landmark is in front of the pelvis (px, + = in the running direction)."""
    return facing * (s.side_lm(side, part)[:, 0] - s.mid("hip")[:, 0])


def stride_period(s: PoseSeries, facing: int) -> float | None:
    """Stride time (s) from the autocorrelation of both feet's forward position (side view)."""
    fps = s.fps
    lo, hi = int(0.45 * fps), int(1.8 * fps)
    acs = []
    for side in (Side.LEFT, Side.RIGHT):
        x = _filled(forward_position(s, side, "ankle", facing), int(0.1 * fps) + 1)
        x = x - np.nanmean(x)
        x = np.nan_to_num(x)
        if len(x) <= hi + 2 or not x.any():
            continue
        ac = np.correlate(x, x, "full")[len(x) - 1:]
        acs.append(ac / ac[0])
    if not acs:
        return None
    ac = np.mean(acs, axis=0)[:hi + 1]
    seg = ac[lo:hi + 1]
    pk, _ = find_peaks(seg)
    if not pk.size or seg[pk].max() < 0.2:
        return None
    best = seg[pk].max()
    k = int(pk[np.argmax(seg[pk] >= 0.8 * best)])          # the first strong peak, not a multiple
    # parabolic refinement of the peak lag
    j = lo + k
    if 0 < j < len(ac) - 1:
        a, b, c = ac[j - 1], ac[j], ac[j + 1]
        den = a - 2 * b + c
        j = j + (0.5 * (a - c) / den if den else 0.0)
    return float(j / fps)


def _height_threshold(h: np.ndarray, s: PoseSeries) -> float:
    ok = ~np.isnan(h)
    peak = float(np.percentile(h[ok], 95))
    dh = np.diff(h[ok])
    sigma = 1.4826 * float(np.median(np.abs(dh - np.median(dh)))) / np.sqrt(2)
    return max(0.07 * peak, 3.0 * sigma, 0.004 * s.height)


def detect_contacts_sagittal(s: PoseSeries, side: Side, facing: int,
                             period: float) -> tuple[list[Contact], float, float]:
    """Contacts of one foot from the side, one per stride. Returns (contacts, belt y, threshold px).

    Each stride is found from the forward swing of the heel: on a treadmill the foot reaches its
    furthest point in front of the pelvis just before it lands, and the belt then carries it back
    (Zeni, Richards & Higginson 2008). That gives exactly one contact per stride, even when the
    landmarks are noisy. Within the stride the timing comes from the height above the belt: the
    landing is the first frame from the forward peak on where heel or toe reaches its belt line, the
    toe-off the frame where the foot leaves it again, both extrapolated to zero height between frames.
    Where the height does not show a clear contact, the forward peak (landing) and the toe's rearmost
    point (toe-off) are used and the contact is marked ``method="position"``.
    """
    h, ground = foot_height(s, side)
    ok = ~np.isnan(h)
    if ok.sum() < max(10, 0.5 * len(h)):
        return [], ground, float("nan")
    if float(np.percentile(h[ok], 95)) < 0.02 * s.height:
        return [], ground, float("nan")      # the foot hardly leaves the belt: not running
    thr = _height_threshold(h, s)
    fps, t, n = s.fps, s.t, len(s)
    heel_f = _filled(forward_position(s, side, "heel", facing), int(0.08 * fps) + 1)
    toe_f = _filled(forward_position(s, side, "foot", facing), int(0.08 * fps) + 1)
    good = ~np.isnan(heel_f)
    if good.sum() < 10:
        return [], ground, thr
    amp = float(np.percentile(heel_f[good], 90) - np.percentile(heel_f[good], 10))
    sig = np.where(good, heel_f, np.nanmin(heel_f) - amp)
    peaks, _ = find_peaks(sig, distance=max(2, int(0.6 * period * fps)), prominence=0.35 * amp)
    half = 0.5 * period * fps
    out: list[Contact] = []
    prev_end = 0
    for k, p in enumerate(peaks):
        if p < 1 or not good[p]:
            continue
        nxt = peaks[k + 1] if k + 1 < len(peaks) else n
        lim = int(min(nxt - 1, p + 0.8 * period * fps, n - 1))
        # landing: the first frame from the forward peak on with the foot on the belt
        # (the heel can be furthest forward a frame or two after it touched down, so look a little
        # before the peak too, for a low stretch that lasts to the peak)
        j0 = max(prev_end, p - max(1, int(round(0.12 * period * fps))))
        low = ok & (h < 1.5 * thr)
        i_ic = next((i for i in range(j0, int(min(lim, p + 0.6 * half)) + 1)
                     if ok[i] and h[i] < thr and (i >= p or low[i:p + 1].all())), None)
        method = "height"
        if i_ic is not None:
            # already low where the search starts: follow the low stretch back to where it began
            stop = max(prev_end, p - int(round(0.25 * period * fps)), 1)
            while i_ic > stop and ok[i_ic - 1] and h[i_ic - 1] < thr:
                i_ic -= 1
            if ok[i_ic - 1] and h[i_ic - 1] >= thr:
                t_ic = _extrapolate(t, h, i_ic, thr, -1)
            else:
                t_ic = float(t[i_ic])
        else:
            i_ic, t_ic, method = p, float(t[p]), "position"
        # toe-off: the end of the low stretch, before the toe is furthest back (plus a little)
        seg = toe_f[i_ic:lim + 1]
        q = i_ic + int(np.nanargmin(seg)) if np.any(~np.isnan(seg)) else None
        t_to: float | None = None
        if method == "height":
            b, miss = i_ic, 0
            while b + 1 <= lim and miss <= 1:
                b += 1
                miss = 0 if (ok[b] and h[b] < 1.5 * thr) else miss + 1
            b = b - miss + 1                 # first sample above the threshold
            if q is not None and b > q + 2:
                b = q + 2
            if b < n and b - i_ic >= 1 and ok[b] and ok[b - 1]:
                t_to = _extrapolate(t, h, b, thr, +1) if h[b] >= thr else float(t[b])
        if t_to is None and q is not None and q > i_ic:
            t_to = float(t[q])
            method = "position"
        if t_to is not None and t_to <= t_ic + 0.5 / fps:
            t_to = float(t[q]) if q is not None and t[q] > t_ic else None
            method = "position"
        if t_to is not None and q is None and nxt >= n:
            t_to = None
        out.append(Contact(side, t_ic, t_to, _frac_index(t, t_ic),
                           None if t_to is None else _frac_index(t, t_to), method=method))
        prev_end = int(np.ceil(_frac_index(t, t_to))) if t_to is not None else i_ic + 1
    return out, ground, thr


def _midstance(s: PoseSeries, c: Contact) -> int | None:
    """Row of maximal knee flexion during the contact (midstance)."""
    if c.i_to is None:
        return None
    i0, i1 = int(np.ceil(c.i_ic)), int(np.floor(c.i_to))
    if i1 < i0:
        return int(round((c.i_ic + c.i_to) / 2))
    hip = s.side_lm(c.side, "hip")[i0:i1 + 1]
    knee = s.side_lm(c.side, "knee")[i0:i1 + 1]
    ankle = s.side_lm(c.side, "ankle")[i0:i1 + 1]
    flex = 180 - interior_angle(hip, knee, ankle)
    if np.all(np.isnan(flex)):
        return int(round((c.i_ic + c.i_to) / 2))
    return i0 + int(np.nanargmax(flex))


def _midstance_frontal(s: PoseSeries, c: Contact) -> int | None:
    """Rear/front view: lowest pelvis during the contact."""
    if c.i_to is None:
        return None
    i0, i1 = int(np.ceil(c.i_ic)), int(np.floor(c.i_to))
    if i1 < i0:
        return int(round((c.i_ic + c.i_to) / 2))
    y = s.mid("hip")[i0:i1 + 1, 1]
    if np.all(np.isnan(y)):
        return int(round((c.i_ic + c.i_to) / 2))
    return i0 + int(np.nanargmax(y))


def _dedupe(contacts: list[Contact]) -> list[Contact]:
    """Merge contacts of the same foot that are far closer than a stride (split by noise)."""
    out: list[Contact] = []
    for side in (Side.LEFT, Side.RIGHT):
        cs = sorted([c for c in contacts if c.side == side], key=lambda c: c.t_ic)
        if len(cs) >= 3:
            d = np.diff([c.t_ic for c in cs])
            med = float(np.median(d))
            keep: list[Contact] = []
            for c in cs:
                if keep and c.t_ic - keep[-1].t_ic < 0.45 * med:
                    prev = keep[-1]
                    prev.t_to, prev.i_to = c.t_to, c.i_to
                else:
                    keep.append(c)
            cs = keep
        out += cs
    return sorted(out, key=lambda c: c.t_ic)


def _steady(contacts: list[Contact], period: float) -> list[Contact]:
    """Only the contacts in steady running: the same foot lands a stride before or a stride after
    (within -25/+33 %), with the other foot landing in between. Drops standing on the belt before the
    run (real hardware 2026-11-14: the first 40 s), and landings misplaced where the foot was hidden."""
    cs = sorted(contacts, key=lambda c: c.t_ic)
    by_side = {sd: [c for c in cs if c.side == sd] for sd in (Side.LEFT, Side.RIGHT)}
    lo, hi = 0.75 * period, 1.33 * period

    def other_between(c: Contact, a: float, b: float) -> bool:
        return any(a < o.t_ic < b for o in by_side[Side.RIGHT if c.side == Side.LEFT else Side.LEFT])

    def regular(a: Contact, b: Contact) -> bool:
        return lo <= b.t_ic - a.t_ic <= hi and other_between(a, a.t_ic, b.t_ic)

    keep = []
    for lst in by_side.values():
        for k, c in enumerate(lst):
            if (k > 0 and regular(lst[k - 1], c)) or (k + 1 < len(lst) and regular(c, lst[k + 1])):
                keep.append(c)
    return sorted(keep, key=lambda c: c.t_ic)


def event_quality(ev: GaitEvents) -> dict[str, float]:
    """How far the landings can be trusted (side view), for the coach's warnings.

    coverage: share of the expected landings (from the stride time, between the first and the last
    landing) that were measured; position_share: share timed from the foot position because the
    height did not show the contact; gct_ms, gct_spread: median contact time and its interquartile
    range relative to the median.
    """
    out: dict[str, float] = {}
    cs = ev.contacts
    if len(cs) < 4:
        return out
    if ev.stride_s:
        span = cs[-1].t_ic - cs[0].t_ic
        out["coverage"] = min(1.0, len(cs) / (span / (ev.stride_s / 2) + 1))
    out["position_share"] = sum(c.method != "height" for c in cs) / len(cs)
    g = np.array([c.gct_ms for c in cs if c.gct_ms is not None])
    if g.size >= 4:
        q1, med, q3 = np.percentile(g, [25, 50, 75])
        out["gct_ms"] = float(med)
        out["gct_spread"] = float((q3 - q1) / med) if med > 0 else float("inf")
    return out


def detect_events(s: PoseSeries, view: View | str, facing: int | None = None) -> GaitEvents:
    """All ground contacts of both feet in a prepared (filtered) series."""
    v = View(view)
    facing = facing if facing is not None else detect_facing(s, v)
    contacts: list[Contact] = []
    ground, thr = {}, {}
    period = stride_period(s, facing) if is_side_view(v) else None
    for side in (Side.LEFT, Side.RIGHT):
        if period is not None:
            cs, g, th = detect_contacts_sagittal(s, side, facing, period)
        else:
            cs, g, th = detect_contacts_side(s, side)
        contacts += cs
        ground[side], thr[side] = g, th
    contacts = _steady(contacts, period) if period is not None else _dedupe(contacts)
    for c in contacts:
        c.i_mid = _midstance(s, c) if is_side_view(v) else _midstance_frontal(s, c)
    return GaitEvents(contacts, facing, v, ground, thr, stride_s=period)
