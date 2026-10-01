"""
The two-tier disk check in scripts/ops_heartbeat.py: a quiet early warning
at DISK_WARN_PERCENT and a more urgent alert at DISK_ALERT_PERCENT, with
the warning suppressed once usage has crossed into critical territory (one
full-disk event should produce one alert, not two).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import ops_heartbeat  # noqa: E402


def _usage(percent_used: float, total: int = 100_000):
    """A round total so `used/total*100` reproduces `percent_used` exactly —
    boundary tests need that, not just "close enough"."""
    used = round(total * percent_used / 100)
    return SimpleNamespace(total=total, used=used, free=total - used)


def test_below_warning_threshold_is_silent(monkeypatch):
    monkeypatch.setattr(ops_heartbeat.shutil, "disk_usage", lambda _: _usage(50))
    assert ops_heartbeat.check_disk_usage_warning() is None
    assert ops_heartbeat.check_disk_usage() is None


def test_warning_band_fires_only_the_warning(monkeypatch):
    monkeypatch.setattr(ops_heartbeat.shutil, "disk_usage", lambda _: _usage(75))
    warning = ops_heartbeat.check_disk_usage_warning()
    assert warning is not None
    assert "75%" in warning
    assert ops_heartbeat.check_disk_usage() is None


def test_critical_band_fires_only_the_critical_alert(monkeypatch):
    monkeypatch.setattr(ops_heartbeat.shutil, "disk_usage", lambda _: _usage(90))
    assert ops_heartbeat.check_disk_usage_warning() is None
    critical = ops_heartbeat.check_disk_usage()
    assert critical is not None
    assert "90%" in critical


def test_thresholds_are_boundary_inclusive(monkeypatch):
    monkeypatch.setattr(
        ops_heartbeat.shutil, "disk_usage", lambda _: _usage(ops_heartbeat.DISK_WARN_PERCENT),
    )
    assert ops_heartbeat.check_disk_usage_warning() is not None

    monkeypatch.setattr(
        ops_heartbeat.shutil, "disk_usage", lambda _: _usage(ops_heartbeat.DISK_ALERT_PERCENT),
    )
    assert ops_heartbeat.check_disk_usage_warning() is None
    assert ops_heartbeat.check_disk_usage() is not None
