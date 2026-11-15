"""The report marks the values the film's frame rate cannot carry."""
from __future__ import annotations

import re

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.core.models import TestKind
from lopanalys.pose.offline import analyze_core
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import View
from lopanalys.report.builder import ReportData, render_html

from . import sample_data as sd


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


def _analysis(fps: float):
    p = RunnerParams(fps=fps, duration_s=12, noise_px=1.0, view=View.SIDE_LEFT, seed=3)
    poses, truth = generate(p)
    return analyze_core(poses, p.height_cm, p.view, times=truth.times).analysis


def test_report_marks_uncertain_values(tmp_path):
    a = _analysis(25)
    html = render_html(ReportData(sd.athlete(), sd.session(TestKind.GAIT), gait=a.to_dict()))
    row = re.search(r'<tr class="uncertain"><td class="name">Markkontakt', html)
    assert row, "the contact time row is marked"
    assert "Osäkert: filmen har 25 bilder/s" in html
    assert not re.search(r'<tr class="uncertain"><td class="name">Kadens', html)
    # an analysis stored before the frame-rate levels (none) is marked from its frame rate
    d = a.to_dict()
    d.pop("reliability")
    assert 'class="uncertain"' in render_html(ReportData(sd.athlete(), sd.session(TestKind.GAIT), gait=d))
    # at 120 fps nothing is marked
    html = render_html(ReportData(sd.athlete(), sd.session(TestKind.GAIT), gait=_analysis(120).to_dict()))
    assert 'class="uncertain"' not in html
