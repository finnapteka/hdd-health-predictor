#!/usr/bin/env python3
"""Trend analysis: Holt-Winters + linregress + 2nd derivative -> Health Index & TTF.

Pure-Python core; uses numpy/scipy if available.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config, load_config  # noqa: E402
from src.notifier import send_alert  # noqa: E402

log = logging.getLogger("trend_analyzer")

# Empirical thresholds at which the mechanics is considered dead.
# attr7 (Seek Error Rate) for Seagate is a huge 48-bit number; use a soft cap.
# Empirical thresholds. Seagate encodes Seek_Error_Rate as a huge 48-bit
# counter, so absolute values are meaningless — we use baseline-normalized
# growth. The thresholds below are for the *absolute* value at which the
# mechanics is considered dead.
THRESHOLD_ATTR1   = 10_000_000.0   # Raw_Read_Error_Rate (Seagate: 48-bit)
THRESHOLD_ATTR7   = 1.0e10         # Seek_Error_Rate  (Seagate: up to 1e10)
THRESHOLD_ATTR11  = 100.0          # Calibration_Retry_Count (WD/Hitachi)
THRESHOLD_ATTR187 = 100.0          # Reported_Uncorrectable
THRESHOLD_ATTR188 = 100_000.0      # Command_Timeout (Seagate: count x100)
THRESHOLD_ATTR193 = 300_000.0      # Load_Cycle_Count
THRESHOLD_ATTR197 = 50.0           # Current_Pending_Sector
THRESHOLD_ATTR198 = 100.0          # Offline_Uncorrectable
THRESHOLD_ATTR240 = 100_000.0      # Head_Flying_Hours



# --------------------------------------------------------------------------- #
# Math helpers — pure Python, no hard dependencies.
# --------------------------------------------------------------------------- #

def _linregress(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """Return (slope, intercept, r_value). Fallback if scipy missing."""
    try:
        from scipy.stats import linregress  # type: ignore
        res = linregress(x, y)
        return float(res.slope), float(res.intercept), float(res.rvalue)
    except ImportError:
        n = len(x)
        if n < 2:
            return 0.0, (y[0] if y else 0.0), 0.0
        mx = sum(x) / n
        my = sum(y) / n
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        den = sum((xi - mx) ** 2 for xi in x)
        slope = num / den if den else 0.0
        intercept = my - slope * mx
        # r
        ss_tot = sum((yi - my) ** 2 for yi in y)
        ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
        r = math.sqrt(1 - ss_res / ss_tot) if ss_tot else 0.0
        return slope, intercept, r


def _holt_winters(y: list[float], alpha: float = 0.3, beta: float = 0.1) -> tuple[list[float], list[float]]:
    """Double exponential smoothing. Returns (level[], trend[]). Pure Python."""
    if not y:
        return [], []
    level = [y[0]]
    trend = [0.0]
    for i in range(1, len(y)):
        prev_level = level[-1]
        prev_trend = trend[-1]
        new_level = alpha * y[i] + (1 - alpha) * (prev_level + prev_trend)
        new_trend = beta * (new_level - prev_level) + (1 - beta) * prev_trend
        level.append(new_level)
        trend.append(new_trend)
    return level, trend


def _second_derivative(values: list[float], window: int = 5) -> float:
    """Mean of second differences over the last `window` points."""
    if len(values) < window + 2:
        return 0.0
    tail = values[-(window + 2):]
    second = []
    for i in range(2, len(tail)):
        second.append(tail[i] - 2 * tail[i - 1] + tail[i - 2])
    return sum(second) / len(second)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #

def analyze_series(
    ts: list[int], values: list[Optional[float]], threshold: float,
) -> dict:
    """Analyze one attribute series. Missing values are skipped (not zeroed)."""
    # Pair (ts, value) where value is not None
    pairs = [(t, v) for t, v in zip(ts, values) if v is not None]
    if len(pairs) < 3:
        return {"status": "insufficient", "n": len(pairs)}

    xs = [float(p[0]) for p in pairs]
    raw_ys = [float(p[1]) for p in pairs]

    # Baseline normalization: Seagate attr7/attr188 are huge absolute counters.
    # What matters is *growth* relative to the starting value, not the absolute.
    baseline = raw_ys[0]
    ys = [v - baseline for v in raw_ys]

    # Normalize x to hours since first point (stabilizes regression numbers)
    x0 = xs[0]
    xh = [(x - x0) / 3600.0 for x in xs]

    slope, intercept, r = _linregress(xh, ys)
    level, trend = _holt_winters(ys)
    accel = _second_derivative(ys, window=min(5, max(2, len(ys) // 4)))

    # Normalized acceleration: relative to the mean level, to be scale-free.
    mean_level = sum(level) / len(level) if level else 0.0
    accel_norm = accel / max(1.0, mean_level)

    current = ys[-1]           # relative to baseline (growth since first sample)
    current_abs = raw_ys[-1]   # absolute value for logging
    # Exponential growth = positive slope AND meaningful positive acceleration
    is_exponential = slope > 0 and accel_norm > 0.05 and r > 0.5
    # Sudden jump: last point far above smoothed level
    is_jump = False
    if len(level) >= 3:
        stdev = math.sqrt(sum((y - mean_level) ** 2 for y in ys) / len(ys)) or 1.0
        if current > level[-2] + 3 * stdev:
            is_jump = True

    # Time-to-Failure: extrapolate to threshold using slope (hours)
    ttf_hours: Optional[float] = None
    if slope > 0 and current < threshold:
        ttf_hours = (threshold - current) / slope
        ttf_hours = _clamp(ttf_hours, 1.0, 24.0 * 365.0)
    elif current >= threshold:
        ttf_hours = 1.0

    # Health penalties
    penalty_level = min(40.0, math.log1p(max(0.0, current)) * 5.0)
    penalty_slope = min(30.0, max(0.0, slope) * 100.0)
    penalty_accel = min(30.0, max(0.0, accel_norm) * 500.0)
    hi = _clamp(100.0 - penalty_level - penalty_slope - penalty_accel, 0.0, 100.0)

    return {
        "status": "ok",
        "n": len(pairs),
        "slope_per_hour": slope,
        "r": r,
        "accel": accel,
        "accel_norm": accel_norm,
        "current": current,
        "current_abs": current_abs,
        "baseline": baseline,
        "mean_level": mean_level,
        "is_exponential": is_exponential,
        "is_jump": is_jump,
        "ttf_hours": ttf_hours,
        "hi_component": hi,
    }


def analyze_device(dev: str, conn: sqlite3.Connection, cfg: Config) -> dict:
    """Run full analysis for one device. Returns a report dict."""
    rows = conn.execute(
        "SELECT ts, attr1, attr7, attr11, attr187, attr188, attr193, "
        "attr197, attr198, attr240, link_errors FROM metrics "
        "WHERE dev = ? ORDER BY ts ASC",
        (dev,),
    ).fetchall()

    if len(rows) < cfg.min_points:
        return {
            "dev": dev,
            "status": "collecting",
            "samples": len(rows),
            "need": cfg.min_points,
        }

    ts = [r[0] for r in rows]
    series = {
        "attr1":   [r[1]  for r in rows],
        "attr7":   [r[2]  for r in rows],
        "attr11":  [r[3]  for r in rows],
        "attr187": [r[4]  for r in rows],
        "attr188": [r[5]  for r in rows],
        "attr193": [r[6]  for r in rows],
        "attr197": [r[7]  for r in rows],
        "attr198": [r[8]  for r in rows],
        "attr240": [r[9]  for r in rows],
    }
    thresholds = {
        "attr1":   THRESHOLD_ATTR1,
        "attr7":   THRESHOLD_ATTR7,
        "attr11":  THRESHOLD_ATTR11,
        "attr187": THRESHOLD_ATTR187,
        "attr188": THRESHOLD_ATTR188,
        "attr193": THRESHOLD_ATTR193,
        "attr197": THRESHOLD_ATTR197,
        "attr198": THRESHOLD_ATTR198,
        "attr240": THRESHOLD_ATTR240,
    }

    results = {}
    for name, vals in series.items():
        results[name] = analyze_series(ts, vals, thresholds[name])

    # Aggregate Health Index: worst of available attributes
    his = [r["hi_component"] for r in results.values() if r.get("status") == "ok"]
    hi = min(his) if his else 100.0

    # Aggregate TTF: worst (smallest) across attributes
    ttfs = [r["ttf_hours"] for r in results.values()
            if r.get("status") == "ok" and r.get("ttf_hours") is not None]
    ttf_hours = min(ttfs) if ttfs else None

    exponential = any(r.get("is_exponential") for r in results.values())
    jump = any(r.get("is_jump") for r in results.values())

    level = "OK"
    if hi < cfg.hi_threshold or exponential or jump:
        level = "CRITICAL" if (hi < cfg.hi_threshold or jump) else "WARNING"

    return {
        "dev": dev,
        "status": "ok",
        "samples": len(rows),
        "health_index": round(hi, 1),
        "ttf_hours": ttf_hours,
        "ttf_days": round(ttf_hours / 24.0, 1) if ttf_hours else None,
        "exponential": exponential,
        "jump": jump,
        "level": level,
        "series": results,
    }


def _log_analyzer(cfg: Config, msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(cfg.analyzer_log), mode=0o750, exist_ok=True)
        with open(cfg.analyzer_log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")
    except OSError:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze HDD trends and alert.")
    parser.add_argument("--config", default="/opt/hdd-health-predictor/config.yaml")
    parser.add_argument("--dev", default=None, help="Only analyze this device (e.g. sdc)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    if not os.path.exists(cfg.db_path):
        log.error("DB not found: %s", cfg.db_path)
        return 1

    conn = sqlite3.connect(cfg.db_path, timeout=10)
    try:
        if args.dev:
            devices = [args.dev]
        else:
            devices = [os.path.basename(d) for d in cfg.disks]

        reports = []
        for dev in devices:
            report = analyze_device(dev, conn, cfg)
            reports.append(report)
            _log_analyzer(cfg, json.dumps(report, ensure_ascii=False))

            if report["status"] != "ok":
                log.info("%s: %s (samples=%s/%s)", dev, report["status"],
                         report.get("samples"), report.get("need"))
                continue

            log.info(
                "%s: HI=%.1f%% TTF=%s days level=%s exp=%s jump=%s",
                dev, report["health_index"], report["ttf_days"],
                report["level"], report["exponential"], report["jump"],
            )

            if report["level"] in ("WARNING", "CRITICAL"):
                send_alert(cfg, report)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
