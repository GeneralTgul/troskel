"""The result of a bike fit or a SkiErg technique test on the result page (pose.technique).

Shows, per film: how far the measurement can be trusted, the observations, the key frames and every
measured value with its range, where the range comes from and the measurement error. A bike fit also
gets the suggested changes to the bike and the manual checklist, which the coach fills in here and
which is saved with the test and printed in the report.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from lopanalys.core.i18n import t
from lopanalys.pose.bike import checklist
from lopanalys.report.technique import technique_ctx
from lopanalys.ui import theme
from lopanalys.ui.widgets.common import Card, StatusPill, button, clear_layout, label

_PILL = {"ok": "ok", "watch": "watch", "fix": "fix", "none": "idle", "good": "ok", "uncertain": "watch", "poor": "fix"}
_COLOUR = {"ok": "good", "watch": "watch", "fix": "fix"}


class TechniquePanel(QWidget):
    """``show_record(record, on_save)`` fills it; ``record`` is services.technique_record()."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 12, 0, 0)
        self.lay.setSpacing(14)
        self._inputs: dict[str, QWidget] = {}
        self._on_save: Callable[[dict[str, Any]], None] | None = None
        self.saved_note: QLabel | None = None

    # ------------------------------------------------------------------ filling
    def show_record(self, record: dict[str, Any] | None, on_save: Callable[[dict[str, Any]], None] | None = None) -> None:
        clear_layout(self.lay)
        self._inputs, self._on_save, self.saved_note = {}, on_save, None
        ctx = technique_ctx(record)
        if ctx is None:
            return
        many = len(ctx["views"]) > 1
        for v in ctx["views"]:
            head = QHBoxLayout()
            head.addWidget(label(v["view_label"] if many or v["view_label"] else ctx["name"], "SectionTitle"))
            head.addWidget(StatusPill(v["quality"]["label"], _PILL[v["quality"]["level"]]))
            head.addStretch(1)
            self.lay.addLayout(head)
            for w in v["warnings"]:
                self.lay.addWidget(label(w, "Hint", wrap=True))
            if v["findings"]:
                grid = QGridLayout()
                grid.setSpacing(14)
                for i, f in enumerate(v["findings"]):
                    grid.addWidget(self._finding_card(f), i // 2, i % 2)
                self.lay.addLayout(grid)
            raw = next(x for x in record["views"] if x.get("view") == v["view"])       # type: ignore[index]
            if raw.get("keyframes"):
                self.lay.addWidget(self._keyframes_card(raw["keyframes"]))
            elif v["quality"]["level"] == "poor":
                self.lay.addWidget(label(t("technique.ui.no_keyframes"), "Hint", wrap=True))
            if v["metrics"]:
                self.lay.addWidget(self._metrics_card(v))
        if ctx["advice"]:
            self.lay.addWidget(self._advice_card(ctx["advice"]))
        if ctx.get("compare") and ctx["compare"]["views"]:
            self.lay.addWidget(self._compare_card(ctx["compare"]))
        elif ctx.get("first_test"):
            self.lay.addWidget(label(t("technique.report.compare_first"), "Hint", wrap=True))
        if ctx["kind"] == "bikefit":
            self.lay.addWidget(self._checklist_card((record or {}).get("checklist") or {}, ctx["checklist"]["notes"]))
        self.lay.addStretch(1)

    def _finding_card(self, f: dict[str, Any]) -> Card:
        c = Card()
        top = QHBoxLayout()
        top.addWidget(label(f["title"], "SectionTitle"), 1)
        top.addWidget(StatusPill(t(f"ui.status.{f['severity']}"), _PILL.get(f["severity"], "idle")))
        c.body.addLayout(top)
        if f["value_str"]:
            val = label(f["value_str"])
            val.setStyleSheet(f"font-size: 30px; font-weight: 700; color: {theme.TOKENS[_COLOUR.get(f['severity'], 'text')]};")
            c.body.addWidget(val)
        if f.get("reference"):
            c.body.addWidget(label(t("ui.res.target", ref=f["reference"]), "Hint"))
        c.body.addWidget(label(f["explanation"], wrap=True))
        for key in ("cue", "drill"):
            if f.get(key):
                lb = label(f"{t(f'report.gait.{key}')}: {f[key]}", wrap=True)
                if key == "cue":
                    lb.setStyleSheet(f"color: {theme.TOKENS['accent']}; font-weight: 600;")
                c.body.addWidget(lb)
        return c

    def _keyframes_card(self, keyframes: list[dict[str, Any]]) -> Card:
        kc = Card(t("technique.report.frames_title"))
        grid = QGridLayout()
        grid.setSpacing(12)
        singles = [k for k in keyframes if k.get("kind") != "sequence"]
        for i, kf in enumerate(singles[:4]):
            self._picture(grid, kf, i // 4, i % 4, QSize(215, 290))
        for kf in [k for k in keyframes if k.get("kind") == "sequence"][:1]:
            self._picture(grid, kf, 1, 0, QSize(920, 330), span=4)
        kc.body.addLayout(grid)
        return kc

    @staticmethod
    def _picture(grid: QGridLayout, kf: dict[str, Any], row: int, col: int, size: QSize, span: int = 1) -> None:
        box = QVBoxLayout()
        img = QLabel()
        pm = QPixmap(str(kf.get("image_path") or ""))
        if not pm.isNull():
            img.setPixmap(pm.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        box.addWidget(img)
        box.addWidget(label(kf.get("caption") or kf.get("label") or "", "Hint", wrap=True))
        holder = QWidget()
        holder.setLayout(box)
        grid.addWidget(holder, row, col, 1, span)

    def _metrics_card(self, v: dict[str, Any]) -> Card:
        c = Card(t("technique.report.metrics_title"), v["metrics_lead"])
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(6)
        for col, key in enumerate(("metric", "value", "error", "range", "basis")):
            grid.addWidget(label(t(f"technique.report.col.{key}"), "Hint"), 0, col)
        for r, m in enumerate(v["metrics"], start=1):
            name = label(m["name"])
            name.setToolTip(m["hint"])
            grid.addWidget(name, r, 0)
            val = label(f"{m['value']}{'' if m['tight'] else ' '}{m['unit']}")
            val.setStyleSheet(f"font-weight: 700; color: {theme.TOKENS[_COLOUR.get(m['status'], 'text')]};")
            grid.addWidget(val, r, 1)
            grid.addWidget(label(m["error"], "Hint"), r, 2)
            grid.addWidget(label(m["target"] or "–"), r, 3)
            basis = m["basis"] + (f" · {t('technique.report.within_error')}" if m["within_error"] else "")
            grid.addWidget(label(basis, "Hint"), r, 4)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(4, 2)
        c.body.addLayout(grid)
        c.body.addWidget(label(t("technique.report.disclaimer"), "Hint", wrap=True))
        return c

    def _advice_card(self, advice: list[dict[str, Any]]) -> Card:
        c = Card(t("technique.report.advice_title"), t("technique.report.advice_lead"))
        for a in advice:
            head = QHBoxLayout()
            head.addWidget(label(a["title"], "SectionTitle"))
            head.addWidget(StatusPill(a["badge"], "ok" if a["on_site"] else "idle"))
            if a["uncertain"]:
                head.addWidget(StatusPill(t("technique.report.badge.uncertain"), "watch"))
            head.addStretch(1)
            c.body.addLayout(head)
            c.body.addWidget(label(a["text"], wrap=True))
        return c

    def _compare_card(self, compare: dict[str, Any]) -> Card:
        c = Card(t("technique.report.compare_title"), t("technique.report.compare_lead", date=compare["date"]))
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        for col, key in enumerate(("metric", "before", "now", "change", "trend")):
            grid.addWidget(label(t(f"technique.report.col.{key}"), "Hint"), 0, col)
        r = 1
        for v in compare["views"]:
            for m in v["compare"]:
                grid.addWidget(label(m["name"]), r, 0)
                grid.addWidget(label(m["before"], "Hint"), r, 1)
                grid.addWidget(label(m["now"] + ("°" if m["unit"] == "°" else f" {m['unit']}".rstrip())), r, 2)
                grid.addWidget(label(m["delta"]), r, 3)
                trend = label(m["trend_label"])
                colour = {"better": "good", "worse": "fix"}.get(m["trend"], "muted")
                trend.setStyleSheet(f"color: {theme.TOKENS[colour]}; font-weight: 600;")
                grid.addWidget(trend, r, 4)
                r += 1
        grid.setColumnStretch(0, 3)
        c.body.addLayout(grid)
        return c

    # ------------------------------------------------------------------ the manual checklist
    def _checklist_card(self, values: dict[str, Any], notes: list[str]) -> Card:
        c = Card(t("technique.report.checklist_title"), t("technique.report.checklist_lead"))
        cols = QHBoxLayout()
        cols.setSpacing(28)
        halves = [QVBoxLayout(), QVBoxLayout()]
        for k, (section, items) in enumerate(checklist.SECTIONS):
            box = halves[0 if k < 4 else 1]
            box.addWidget(label(t(f"{checklist.PREFIX}.section.{section}"), "SectionTitle"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(6)
            for r, item in enumerate(items):
                grid.addWidget(label(t(f"{checklist.PREFIX}.item.{item.key}"), wrap=True), r, 0)
                for col, f in enumerate(item.fields(), start=1):
                    w = self._input(item, f, values.get(f))
                    self._inputs[f] = w
                    grid.addWidget(w, r, col, 1, 2 if item.kind == "text" else 1)
            grid.setColumnStretch(0, 3)
            box.addLayout(grid)
        for h in halves:
            h.addStretch(1)
            cols.addLayout(h, 1)
        c.body.addLayout(cols)
        for n in notes:
            c.body.addWidget(label(n, "Hint", wrap=True))
        row = QHBoxLayout()
        save = button(t("technique.ui.save_checklist"), kind="primary")
        save.clicked.connect(self._save)
        self.save_button = save
        self.saved_note = label("", "Hint")
        row.addWidget(save)
        row.addWidget(self.saved_note, 1)
        c.body.addLayout(row)
        return c

    @staticmethod
    def _input(item: checklist.Item, field: str, value: Any) -> QWidget:
        part = field.split(".")[1] if "." in field else ""
        hint = t(f"technique.ui.{part}") if part else ""
        if item.kind == "choice":
            cb = QComboBox()
            cb.addItem(f"{hint} {t('technique.ui.choose')}".strip(), None)
            for o in item.options:
                cb.addItem(f"{hint}: {t(f'{checklist.PREFIX}.opt.{o}')}" if hint else t(f"{checklist.PREFIX}.opt.{o}"), o)
            cb.setCurrentIndex(max(0, cb.findData(value)))
            return cb
        if item.kind == "check":
            box = QCheckBox(hint or t(f"{checklist.PREFIX}.opt.yes"))
            box.setChecked(bool(value))
            return box
        le = QLineEdit("" if value is None else (f"{value:g}" if isinstance(value, float) else str(value)))
        le.setPlaceholderText(" ".join(x for x in (hint, item.unit) if x))
        if item.kind == "number":
            le.setMaximumWidth(120)
        return le

    def values(self) -> dict[str, Any]:
        """What the coach has filled in (cleaned by pose.bike.checklist)."""
        out: dict[str, Any] = {}
        for f, w in self._inputs.items():
            if isinstance(w, QComboBox):
                out[f] = w.currentData()
            elif isinstance(w, QCheckBox):
                out[f] = w.isChecked()
            elif isinstance(w, QLineEdit):
                out[f] = w.text().strip()
        return checklist.clean(out)

    def _save(self) -> None:
        if self._on_save is not None:
            self._on_save(self.values())
        if self.saved_note is not None:
            self.saved_note.setText(t("technique.ui.checklist_saved"))
