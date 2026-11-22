"""The manual part of a bike fit: what the coach examines and measures by hand, and what was changed.

The camera measures the rider on the bike. A complete fit (the level of a Body Geometry Fit) also
needs an interview, an assessment of flexibility, feet and leg length off the bike, the
cleats, and the bike's measurements before and after. The coach fills this in on the result page, it
is stored with the test and printed in the report.

Values are a flat dict: ``{"<item>": value}``, with ``"<item>.left"`` / ``"<item>.right"`` for items
per side and ``"<item>.before"`` / ``"<item>.after"`` for the bike's measurements. Texts are in
``i18n/technique.*.json`` under ``technique.bikefit.check.*``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lopanalys.core.i18n import t
from lopanalys.pose.findings import num

PREFIX = "technique.bikefit.check"


@dataclass(frozen=True)
class Item:
    key: str
    kind: str = "choice"                # "choice" | "number" | "text" | "check"
    options: tuple[str, ...] = ()       # choice: option keys, texts under <PREFIX>.opt.<option>
    unit: str = ""
    sides: bool = False                 # asked for left and right
    before_after: bool = False          # the bike: before and after the fit

    def fields(self) -> list[str]:
        if self.sides:
            return [f"{self.key}.left", f"{self.key}.right"]
        if self.before_after:
            return [f"{self.key}.before", f"{self.key}.after"]
        return [self.key]


_LEVEL = ("good", "medium", "limited")
_ALIGN = ("varus", "neutral", "valgus")

SECTIONS: list[tuple[str, list[Item]]] = [
    ("interview", [Item("goal", "text"), Item("complaints", "text"), Item("riding", "text")]),
    ("mobility", [Item("hamstring", options=_LEVEL, sides=True), Item("hip_flexor", options=_LEVEL, sides=True),
                  Item("hip_rotation", options=_LEVEL, sides=True), Item("forward_bend", options=_LEVEL),
                  Item("shoulder_neck", options=_LEVEL)]),
    ("feet", [Item("arch", options=("high", "normal", "low"), sides=True),
              Item("forefoot", options=_ALIGN, sides=True), Item("insoles", "check"),
              Item("shoe_size", "number", unit="EU")]),
    ("legs", [Item("leg_length_diff_mm", "number", unit="mm"), Item("longer_leg", options=("left", "right", "equal")),
              Item("knee_alignment", options=_ALIGN, sides=True),
              Item("single_leg_squat", options=("straight", "inwards", "outwards"), sides=True),
              Item("sit_bones_mm", "number", unit="mm")]),
    ("cleats", [Item("ball_over_axle", "check", sides=True), Item("rotation_checked", "check", sides=True),
                Item("stance_width", options=("unchanged", "wider", "narrower")),
                Item("cleat_wear", options=("ok", "worn")), Item("cleat_notes", "text")]),
    ("bike", [Item("saddle_height_mm", "number", unit="mm", before_after=True),
              Item("saddle_setback_mm", "number", unit="mm", before_after=True),
              Item("saddle_tilt_deg", "number", unit="°", before_after=True),
              Item("stem_length_mm", "number", unit="mm", before_after=True),
              Item("bar_drop_mm", "number", unit="mm", before_after=True),
              Item("crank_length_mm", "number", unit="mm")]),
    ("actions", [Item("done", "text"), Item("recommended", "text")]),
]

ITEMS: dict[str, Item] = {i.key: i for _, items in SECTIONS for i in items}
FIELDS: list[str] = [f for i in ITEMS.values() for f in i.fields()]


def empty() -> dict[str, Any]:
    return {}


def clean(values: dict[str, Any] | None) -> dict[str, Any]:
    """Only known fields with a real value; numbers as floats, unknown choices dropped."""
    out: dict[str, Any] = {}
    for f, v in (values or {}).items():
        item = ITEMS.get(f.split(".")[0])
        if item is None or f not in item.fields() or v is None or v is False or v == "":
            continue                    # not ``v in (None, "", False)``: 0 == False, and 0 is a measurement
        if item.kind == "number":
            try:
                out[f] = float(str(v).replace(",", "."))
            except ValueError:
                continue
        elif item.kind == "choice":
            if v in item.options:
                out[f] = v
        elif item.kind == "check":
            out[f] = True
        else:
            out[f] = str(v).strip()
    return {k: v for k, v in out.items() if v != ""}


def progress(values: dict[str, Any] | None) -> tuple[int, int]:
    """(items with something filled in, items in all)."""
    v = clean(values)
    return sum(any(f in v for f in i.fields()) for i in ITEMS.values()), len(ITEMS)


def _show(item: Item, value: Any) -> str:
    if value is None:
        return "–"
    if item.kind == "number":
        return f"{num(value, 0 if float(value).is_integer() else 1)} {item.unit}".strip()
    if item.kind == "choice":
        return t(f"{PREFIX}.opt.{value}")
    if item.kind == "check":
        return t(f"{PREFIX}.opt.yes")
    return str(value)


def rows(values: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The filled-in items, section by section, as texts for the report and the result page."""
    v = clean(values)
    out = []
    for section, items in SECTIONS:
        lines = []
        for item in items:
            got = {f: v[f] for f in item.fields() if f in v}
            if not got:
                continue
            if item.sides:
                text = " · ".join(f"{t(f'pose.side.{s}')} {_show(item, got[f'{item.key}.{s}'])}"
                                  for s in ("left", "right") if f"{item.key}.{s}" in got)
            elif item.before_after:
                b, a = got.get(f"{item.key}.before"), got.get(f"{item.key}.after")
                text = t(f"{PREFIX}.before_after", before=_show(item, b), after=_show(item, a))
                if b is not None and a is not None and a != b:
                    d = a - b
                    text += f" ({'+' if d > 0 else '−'}{num(abs(d), 0 if float(d).is_integer() else 1)} {item.unit})"
            else:
                text = _show(item, got[item.key])
            lines.append({"key": item.key, "label": t(f"{PREFIX}.item.{item.key}"), "value": text})
        if lines:
            out.append({"key": section, "title": t(f"{PREFIX}.section.{section}"), "rows": lines})
    return out


def notes(values: dict[str, Any] | None, summary: dict[str, Any] | None = None) -> list[str]:
    """What the manual findings mean for the fit, read together with the film. Rules of thumb from
    fitting practice, worded as things to consider."""
    v = clean(values)
    s = summary or {}
    out = []
    diff = v.get("leg_length_diff_mm")
    if diff and diff >= 5:
        out.append(t(f"{PREFIX}.note.leg_length", mm=num(diff)))
    tight = any(v.get(f"hamstring.{sd}") == "limited" for sd in ("left", "right")) or v.get("forward_bend") == "limited"
    if tight:
        trunk = s.get("trunk_angle_deg")
        out.append(t(f"{PREFIX}.note.hamstring_low" if trunk is not None and trunk < 40 else f"{PREFIX}.note.hamstring"))
    varus = [sd for sd in ("left", "right") if v.get(f"forefoot.{sd}") == "varus" or v.get(f"arch.{sd}") == "low"]
    if varus and (s.get("knee_direction") == "in" or any(v.get(f"single_leg_squat.{sd}") == "inwards" for sd in varus)):
        out.append(t(f"{PREFIX}.note.forefoot"))
    b, a = v.get("saddle_height_mm.before"), v.get("saddle_height_mm.after")
    if b is not None and a is not None and abs(a - b) > 10:
        out.append(t(f"{PREFIX}.note.big_change", mm=num(abs(a - b))))
    return out
