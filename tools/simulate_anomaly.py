#!/usr/bin/env python3
"""Inject an exponential anomaly into the SQLite DB to test the alert trigger.

Usage:
    sudo python3 tools/simulate_anomaly.py --dev sdc --days 30
"""
from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import load_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="/opt/hdd-health-predictor/config.yaml")
    p.add_argument("--dev", default="sdc")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--db", default=None)
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    db_path = args.db or cfg.db_path
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    now = int(time.time())
    interval = 3600  # hourly points
    n = args.days * 24

    # Exponential growth: attr7 starts at 100, doubles every ~7 days.
    # attr11 starts at 1, reaches 100 in ~30 days.
    for i in range(n):
        ts = now - (n - i) * interval
        t_days = i / 24.0
        attr1 = int(10 * math.exp(0.02 * t_days))
        attr7 = int(100 * math.exp(0.15 * t_days))
        attr11 = int(1 * math.exp(0.15 * t_days))
        conn.execute(
            "INSERT OR REPLACE INTO metrics (ts, dev, attr1, attr7, attr11, link_errors) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, args.dev, attr1, attr7, attr11, 0),
        )
    conn.commit()
    conn.close()
    print(f"Injected {n} anomalous points for /dev/{args.dev} into {db_path}")
    print("Now run:")
    print(f"  sudo /opt/hdd-health-predictor/venv/bin/python "
          f"/opt/hdd-health-predictor/src/trend_analyzer.py "
          f"--config {args.config} --dev {args.dev}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
