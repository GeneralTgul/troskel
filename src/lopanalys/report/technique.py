"""The report sections of a bike fit and of a SkiErg technique test (pose.technique).

They follow the running technique sections (key frames, measured values, observations) and add what
these tests need: how each range is founded and how large the measurement error is, the suggested
changes to the bike, the manual checklist, and the comparison with the previous test.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from lopanalys.core.i18n import t

_SEV = {"fix": 0, "watch": 1, "ok": 2}


def _unit(u: str) -> str:
    return u if u in ("°", "") else " " + u


def _view_ctx(b: Any, kind: str, v: Mapping[str, Any]) -> dict[str, Any]:
    findings = []
    for f in sorted(v.get("findings") or [], key=lambda f: _SEV.get(str(f.get("severity")), 3)):
        d = dict(f)
        dec = 0 if d.get("unit") in ("°", "ms") else 1
        d["value_str"] = b.fmt_num(d["value"], dec) + _unit(d.get("unit") or "") if d.get("value") is not None else ""
        d["severity_label"] = t(f"report.severity.{d['severity']}")
        d.setdefault("drill", "")
        findings.append(d)
    keyframes = [{"label": k.get("label", ""), "frame_index": k.get("frame_index"), "t": k.get("t"),
                  "image_path": k.get("image_path"), "findings": list(k.get("findings") or []),
                  "caption": k.get("caption", "") or "", "kind": k.get("kind", "") or ""}
                 for k in v.get("keyframes") or []]
    frames = b._frames_ctx(b._GaitView(dict(v.get("summary") or {}), findings, keyframes))
    metrics, sources = [], []
    for key, r in (v.get("ranges") or {}).items():
        if r.get("value") is None:
            continue
        dec = int(r.get("decimals") or 0)
        unc = r.get("uncertainty")
        metrics.append({
            "key": key, "name": t(f"technique.metric.{key}"), "hint": t(f"technique.hint.{key}"),
            "value": b.fmt_num(r["value"], dec), "unit": r.get("unit") or "", "tight": r.get("unit") == "°",
            "error": "±" + b.fmt_num(unc, dec if unc and unc < 3 else 0) + _unit(r.get("unit") or "") if unc else "",
            "target": r.get("text") or "", "basis": r.get("basis_label") or t(f"technique.basis.{r.get('basis', 'none')}"),
            "status": r.get("severity") or "none", "within_error": bool(r.get("within_error")),
            "judged": r.get("severity") is not None,
        })
        if r.get("source") and r.get("severity") is not None and r["source"] not in sources:
            sources.append(r["source"])
    q = v.get("quality") or {}
    s = v.get("summary") or {}
    n = s.get("revolutions") if kind == "bikefit" else s.get("strokes")
    view = str(v.get("view") or "")
    compare = []
    for c in v.get("comparison") or []:
        dec = int(c.get("decimals") or 0)
        d = round(c["delta"], dec)
        compare.append({"name": t(f"technique.metric.{c['metric']}"), "unit": c.get("unit") or "",
                        "before": b.fmt_num(c["before"], dec), "now": b.fmt_num(c["now"], dec),
                        "delta": ("+" if d > 0 else "−" if d < 0 else "±") + b.fmt_num(abs(d), dec),
                        "trend": c.get("trend") or "none", "trend_label": t(f"technique.report.trend.{c.get('trend') or 'none'}")})
    return {"view": view, "view_label": t(f"technique.view.{view}") if view else "", "frames": frames,
            "metrics": metrics, "findings": findings if q.get("level") != "poor" else [],
            "warnings": [str(w) for w in v.get("warnings") or []], "sources": ", ".join(sources),
            "quality": {"level": q.get("level") or "good", "label": q.get("label") or "",
                        "reasons": list(q.get("reasons") or [])},
            "metrics_lead": t(f"technique.report.metrics_lead.{kind}", n=n or 0), "advice": list(v.get("advice") or []),
            "compare": compare}


def technique_ctx(tech: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The template context of ``ReportData.technique`` (``{"kind", "views", "checklist", "previous_date"}``)."""
    if not tech or not tech.get("views"):
        return None
    from lopanalys.report import builder as b

    kind = str(tech["kind"])
    views = [_view_ctx(b, kind, v) for v in tech["views"]]
    # one list of suggestions for the bike: saddle, cleats (the film from the front says more than
    # "set by hand" when there is one), stem
    advice: list[dict[str, Any]] = []
    for part in ("saddle_height", "cleat", "stem"):
        got = [a for v in views for a in v["advice"] if a["part"] == part]
        real = [a for a in got if a["action"] not in ("manual", "unknown")]
        manual = [a for a in got if a["action"] == "manual"]
        for a in (real or [x for x in got if x["action"] == "unknown"][:1]) + (manual[:1] if part == "cleat" else []):
            advice.append(dict(a, badge=t("technique.report.badge.on_site" if a["on_site"] else "technique.report.badge.recommendation"),
                               uncertain=not a["certain"] and a["action"] not in ("manual", "keep")))
    out: dict[str, Any] = {"kind": kind, "name": t(f"technique.kind.{kind}"), "views": views, "advice": advice,
                           "frames_lead": t(f"technique.report.frames_lead.{kind}")}
    if kind == "bikefit":
        from lopanalys.pose.bike import checklist
        values = tech.get("checklist") or {}
        done, total = checklist.progress(values)
        out["checklist"] = {"sections": checklist.rows(values), "done": done, "total": total,
                            "notes": checklist.notes(values, {k: x for v in tech["views"] for k, x in (v.get("summary") or {}).items()})}
    prev = tech.get("previous_date")
    if prev:
        try:
            prev = b.date_long(date.fromisoformat(str(prev)[:10]))
        except ValueError:
            pass
    out["compare"] = {"date": prev, "views": [v for v in views if v["compare"]]} if prev else None
    out["first_test"] = kind == "ski" and not prev
    return out


def summary(ctx: Mapping[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(tiles, focus) for the cover: the key values and the most important things to work on."""
    tiles: list[dict[str, str]] = []
    main = ctx["views"][0]
    by = {m["key"]: m for v in ctx["views"] for m in v["metrics"]}

    def tile(key: str) -> None:
        m = by.get(key)
        if m and len(tiles) < 4:
            tiles.append({"label": m["name"], "value": m["value"], "unit": m["unit"],
                          "sub": t("technique.report.tile.target", range=f"{m['target']} ({m['basis']})") if m["target"] else m["basis"]})

    if ctx["kind"] == "bikefit":
        tile("knee_flex_bottom_deg")
        saddle = next((a for a in ctx["advice"] if a["part"] == "saddle_height"), None)
        if saddle and saddle["action"] != "unknown":
            act = saddle["action"]
            word = "keep" if act == "keep" else "check" if not saddle["certain"] else "up" if saddle.get("direction") == "up" else "down"
            mm = f"{saddle['mm_lo']}–{saddle['mm_hi']}" if saddle.get("mm_lo") and saddle["certain"] else ""
            tiles.append({"label": t("technique.report.tile.saddle"), "value": mm or t(f"technique.report.tile.saddle_{word}"),
                          "unit": "mm" if mm else "", "cls": "" if mm else "range",
                          "sub": t(f"technique.report.tile.saddle_{word}") if mm else ""})
        tile("trunk_angle_deg")
        tile("knee_lateral_cm")
    else:
        for key in ("stroke_rate_spm", "recovery_ratio", "trunk_lean_end_deg"):
            tile(key)
    judged = [m for v in ctx["views"] for m in v["metrics"] if m["judged"]]
    if judged and len(tiles) < 4:
        tiles.append({"label": t("technique.report.tile.in_range"), "value": str(sum(m["status"] == "ok" for m in judged)),
                      "unit": t("technique.report.tile.of_n", n=len(judged)), "sub": t("technique.report.tile.in_range_sub")})
    focus = []
    for a in ctx["advice"]:
        if a["action"] not in ("keep", "manual", "unknown") and len(focus) < 3:
            focus.append({"title": a["title"], "text": a["text"]})
    for f in [f for v in ctx["views"] for f in v["findings"]]:
        if f["severity"] in ("fix", "watch") and len(focus) < 3 and ctx["kind"] != "bikefit":
            focus.append({"title": f["title"], "text": f["cue"] or f["explanation"]})
    if main["quality"]["level"] == "poor":
        focus = [{"title": main["quality"]["label"], "text": t("technique.report.poor_note")}]
    return tiles[:4], focus
