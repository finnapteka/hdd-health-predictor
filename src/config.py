"""Configuration loader: YAML if PyYAML available, else JSON."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    disks: List[str] = field(default_factory=lambda: ["/dev/sdc", "/dev/sdd", "/dev/sda"])
    interval_hours: int = 1
    min_points: int = 24
    hi_threshold: float = 50.0
    alert_cooldown_hours: int = 6
    notify_user: str = ""
    notify_display: str = ":0"
    db_path: str = "/var/log/hdd_pre_fail/metrics.db"
    state_path: str = "/var/log/hdd_pre_fail/state.json"
    alerts_log: str = "/var/log/hdd_pre_fail/alerts.log"
    analyzer_log: str = "/var/log/hdd_pre_fail/analyzer.log"


def load_config(path: str) -> Config:
    """Load config from YAML (if PyYAML present) or JSON."""
    if not os.path.exists(path):
        # sensible defaults — caller may still override
        return Config()

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    data = None
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except ImportError:
        data = json.loads(text)

    if not isinstance(data, dict):
        return Config()

    cfg = Config()
    if "disks" in data and isinstance(data["disks"], list):
        cfg.disks = [str(d) for d in data["disks"]]
    for key in ("interval_hours", "min_points", "alert_cooldown_hours"):
        if key in data:
            setattr(cfg, key, int(data[key]))
    if "hi_threshold" in data:
        cfg.hi_threshold = float(data["hi_threshold"])
    if "notify_user" in data:
        cfg.notify_user = str(data["notify_user"])
    if "notify_display" in data:
        cfg.notify_display = str(data["notify_display"])
    paths = data.get("paths") or {}
    for key in ("db_path", "state_path", "alerts_log", "analyzer_log"):
        if key in paths:
            setattr(cfg, key, str(paths[key]))
    return cfg
