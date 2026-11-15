"""The report shows the strap's cadence (RSC) before the camera's, and says where it comes from."""
from __future__ import annotations

import re

import pytest

from lopanalys.core.i18n import set_language
from lopanalys.core.models import TestKind, TimeSample
from lopanalys.pose.offline import analyze_poses
from lopanalys.pose.synthetic import RunnerParams, generate
from lopanalys.pose.types import View
from lopanalys.report.builder import ReportData, render_html

from . import sample_data as sd


@pytest.fixture(autouse=True)
def _swedish():
    set_language("sv")
    yield
    set_language("sv")


@pytest.fixture(scope="module")
def gait():
    p = RunnerParams(fps=60, width=640, height=360, duration_s=6.0, cadence_spm=150, noise_px=0.5)
    poses, _ = generate(p)
    return analyze_poses(poses, p.height_cm, View.SIDE_LEFT, speed_kmh=12.0).to_dict()


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_strap_cadence_in_the_report(gait):
    samples = [TimeSample(float(i), 150, 12.0, 1.0, 178.0, "strap") for i in range(60)]
    text = _text(render_html(ReportData(sd.athlete(), sd.session(TestKind.GAIT), gait=gait, samples=samples)))
    assert "Kadens (pulsbandet)" in text and "178" in text


def test_camera_cadence_without_strap(gait):
    text = _text(render_html(ReportData(sd.athlete(), sd.session(TestKind.GAIT), gait=gait)))
    assert "Kadens" in text and "pulsbandet" not in text
