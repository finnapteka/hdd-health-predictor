"""Tests for trend analysis — synthetic stable/linear/exponential series."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trend_analyzer import analyze_series  # noqa: E402


def _ts(n, step=3600):
    return [1_700_000_000 + i * step for i in range(n)]


def test_stable():
    ts = _ts(48)
    vals = [100.0] * 48
    r = analyze_series(ts, vals, threshold=1e6)
    assert r["status"] == "ok"
    assert r["is_exponential"] is False
    assert r["hi_component"] > 50


def test_linear_wear():
    ts = _ts(48)
    vals = [100.0 + i for i in range(48)]
    r = analyze_series(ts, vals, threshold=1e6)
    assert r["status"] == "ok"
    assert r["slope_per_hour"] > 0


def test_exponential():
    ts = _ts(72)
    vals = [100.0 * (1.15 ** i) for i in range(72)]
    r = analyze_series(ts, vals, threshold=1e6)
    assert r["status"] == "ok"
    assert r["is_exponential"] is True
    assert r["hi_component"] < 50
