"""Alert delivery: notify-send + logger + JSON alerts log, with anti-spam."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

from src.config import Config

log = logging.getLogger("notifier")


def _load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), mode=0o750, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _cooldown_active(state: dict, dev: str, level: str, cooldown_hours: int) -> bool:
    entry = state.get(dev) or {}
    last_ts = entry.get("last_alert_ts", 0)
    last_level = entry.get("last_alert_level")
    # CRITICAL always breaks through a WARNING cooldown
    if level == "CRITICAL" and last_level == "CRITICAL":
        return (time.time() - last_ts) < cooldown_hours * 3600
    return (time.time() - last_ts) < cooldown_hours * 3600


def _desktop_notify(cfg: Config, title: str, body: str) -> None:
    """Send notify-send as the configured desktop user (if any)."""
    if not cfg.notify_user:
        return
    env = os.environ.copy()
    env["DISPLAY"] = cfg.notify_display or ":0"
    env["XAUTHORITY"] = f"/home/{cfg.notify_user}/.Xauthority"
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/1000/bus"
    try:
        subprocess.run(
            ["sudo", "-u", cfg.notify_user, "-E", "notify-send",
             "-u", "critical", "-a", "ИИ-Дефектоскоп", title, body],
            env=env, timeout=10, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("notify-send failed: %s", exc)


def _syslog(msg: str) -> None:
    try:
        subprocess.run(
            ["logger", "-t", "hdd-health-predictor", "-p", "daemon.warning", msg],
            timeout=5, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("logger unavailable")


def send_alert(cfg: Config, report: dict) -> None:
    """Send an alert for a device report (WARNING or CRITICAL)."""
    dev = report["dev"]
    level = report["level"]
    state = _load_state(cfg.state_path)

    if _cooldown_active(state, dev, level, cfg.alert_cooldown_hours):
        log.debug("%s: alert suppressed by cooldown", dev)
        return

    ttf = report.get("ttf_days")
    if ttf is None:
        ttf_str = "не прогнозируется"
    elif ttf < 1:
        ttf_str = "менее 1 дня"
    elif ttf < 30:
        ttf_str = f"{ttf:.0f} дн."
    else:
        ttf_str = f"{ttf:.0f} дн. (~{ttf/30:.1f} мес.)"

    body = (
        f"ВНИМАНИЕ! Обнаружена критическая деградация механики диска /dev/{dev}. "
        f"Прогноз отказа: через {ttf_str}. Срочно сделайте бэкап данных! "
        f"(Health Index: {report['health_index']}%)"
    )
    title = f"[ИИ-Дефектоскоп] /dev/{dev}: {level}"

    _desktop_notify(cfg, title, body)
    _syslog(body)

    # Machine-readable alerts log
    try:
        os.makedirs(os.path.dirname(cfg.alerts_log), mode=0o750, exist_ok=True)
        with open(cfg.alerts_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "dev": dev,
                "level": level,
                "hi": report["health_index"],
                "ttf_days": ttf,
                "exponential": report["exponential"],
                "jump": report["jump"],
            }, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("cannot write alerts log: %s", exc)

    state[dev] = {"last_alert_ts": int(time.time()), "last_alert_level": level}
    _save_state(cfg.state_path, state)
    log.info("%s: alert sent (%s)", dev, level)
