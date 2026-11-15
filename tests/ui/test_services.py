import subprocess
import sys
from datetime import date
from pathlib import Path

from lopanalys.core.models import Athlete, StepRecord, TimeSample
from lopanalys.ui import services


def test_store_roundtrip(tmp_path):
    st = services.open_store(tmp_path / "db.sqlite")
    a = st.save_athlete(Athlete(None, "Anna", "Lindqvist", date(1991, 4, 12), height_cm=168, max_hr=192))
    assert a.id is not None
    assert [x.name for x in st.athletes("lind")] == ["Anna Lindqvist"]
    st.set_setting("branding.business", "Löpstudion")
    assert st.setting("branding.business") == "Löpstudion"
    st.close()


def test_lactate_and_zones():
    recs = [StepRecord(i, 8.0 + i, 1.0, 240, hr_end=120 + 9 * i,
                       lactate_mmol=round(0.9 + 0.03 * max(0.0, 8.0 + i - 9.5) ** 2.6, 2)) for i in range(7)]
    lt1m, lt2m = services.lactate_methods()
    assert lt1m and lt2m
    lv = services.analyze_lactate(recs, None, None, 192)
    assert lv is not None and lv.lt2 is not None and lv.lt1 is not None
    assert lv.lt1.speed_kmh < lv.lt2.speed_kmh
    zs = services.zones(lv, 192, 48)
    assert len(zs) >= 3 and all("name" in z for z in zs)


def test_rebuild_protocol_changes_parameters():
    base = next(p for p in services.default_protocols() if p.kind == "lactate")
    p = services.rebuild_protocol(base, start_kmh=10.0, step_kmh=0.5, stages=5)
    assert p is not None and p.name == base.name
    work = [s for s in p.stages if not s.is_warmup]
    assert len(work) == 5 and work[0].speed_kmh == 10.0 and work[1].speed_kmh == 10.5


def test_every_library_protocol_can_be_rebuilt():
    for base in services.default_protocols():
        p = services.rebuild_protocol(base)
        assert p is not None, base.name
        assert [(s.speed_kmh, s.incline_pct, s.duration_s) for s in p.stages] == \
               [(s.speed_kmh, s.incline_pct, s.duration_s) for s in base.stages]


def test_max_hr_ramp_uses_the_coachs_parameters():
    p = services.max_hr_ramp(11.0, 0.5, 30, 2.0)
    first = next(s for s in p.stages if not s.is_warmup)
    assert (first.speed_kmh, first.incline_pct, first.duration_s) == (11.0, 2.0, 30)
    assert p.open_ended and p.increment_speed_kmh == 0.5 and p.increment_incline_pct == 0.0


def test_zones_from_heart_rate_and_without_a_usable_max():
    zs = services.zones(None, 190, 50)
    assert len(zs) >= 3 and all(z["hr_low"] < z["hr_high"] and z["raw"] is not None for z in zs)
    assert services.zones(None, None, None) == []
    assert services.zones(None, 60, None) == []          # implausible max HR: no zones, no made-up ones


def test_max_hr_view():
    samples = [TimeSample(float(i), 120 + min(i, 60)) for i in range(120)]
    mv = services.analyze_max_hr(samples, [], end_t_s=119.0, age=35)
    assert mv.raw is not None and mv.peak is not None and 175 <= mv.peak <= 180
    assert services.analyze_max_hr([]).peak is None


def test_no_fallbacks_and_no_heavy_imports_at_startup():
    """services.py imports the real modules directly (PyInstaller sees them, a missing one fails at once),
    and the heavy libraries stay out of the start-up path."""
    src = Path(services.__file__).read_text(encoding="utf-8")
    assert "importlib" not in src and "stub" not in src.lower()
    code = ("import sys; import lopanalys.ui.services; "
            "print([m for m in ('mediapipe', 'matplotlib', 'jinja2', 'scipy', 'PySide6.QtWebEngineWidgets', "
            "'PySide6.QtWebEngineCore', 'PySide6.QtPrintSupport') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_a_training_plan_is_built_on_running_tests_only():
    """The plan is for running: a lactate or max HR test in another sport never carries one."""
    from datetime import datetime, timezone

    from lopanalys.core.models import Session, TestKind
    from lopanalys.ui import services as sv
    for sport, kind, ok in (("run", "lactate", True), ("run", "max_hr", True), ("bike", "lactate", False),
                            ("skierg", "max_hr", False), ("run", "gait", False), ("bike", "bikefit", False)):
        s = Session(None, 1, TestKind(kind), datetime.now(timezone.utc), sport=sport)
        assert sv.plan_test_ok(s) is ok, (sport, kind)
