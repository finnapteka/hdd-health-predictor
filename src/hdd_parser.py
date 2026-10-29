#!/usr/bin/env python3
"""Low-level collector: smartctl + journalctl -> SQLite.

Run as root (systemd service). Never wakes a standby disk.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# Allow running as a script from /opt/.../src/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config, load_config  # noqa: E402

log = logging.getLogger("hdd_parser")

# Kernel ATA error patterns (hidden hardware warnings)
ATA_ERROR_PATTERNS = [
    re.compile(r"COMRESET failed", re.I),
    re.compile(r"soft resetting link", re.I),
    re.compile(r"status:\s*\{\s*DRDY ERR\s*\}", re.I),
    re.compile(r"failed command:\s*(READ|WRITE)\s+DMA", re.I),
    re.compile(r"I/O error,\s*dev\s+sd[a-z]+", re.I),
    re.compile(r"SMART error logging", re.I),
    re.compile(r"SMART self-test log", re.I),
]

# smartctl attribute IDs we care about
ATTR_IDS = {
    1:   "attr1",    # Raw_Read_Error_Rate
    7:   "attr7",    # Seek_Error_Rate
    11:  "attr11",   # Calibration_Retry_Count (WD/Hitachi; absent on most Seagate)
    187: "attr187",  # Reported_Uncorrectable
    188: "attr188",  # Command_Timeout
    193: "attr193",  # Load_Cycle_Count
    197: "attr197",  # Current_Pending_Sector
    198: "attr198",  # Offline_Uncorrectable
    240: "attr240",  # Head_Flying_Hours
}


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, p.stdout, p.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 127, "", str(exc)


def _is_removable(dev: str) -> bool:
    """Check /sys/block/<dev>/removable — exclude USB sticks."""
    name = os.path.basename(dev)
    path = f"/sys/block/{name}/removable"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def _is_physical_disk(dev: str) -> bool:
    """Exclude loop/ram/dm/md and removable devices."""
    name = os.path.basename(dev)
    if re.match(r"^(loop|ram|dm-|md|sr|fd|nbd|zram)", name):
        return False
    return not _is_removable(dev)


SSD_INDICATOR_ATTRS = {160, 161, 163, 164, 165, 166, 167, 168, 169,
                        175, 176, 177, 178, 181, 182, 232, 245}


def _looks_like_ssd(attr_table_text: str) -> bool:
    """True if smartctl -A output has SSD-only attributes and no Seek_Error_Rate."""
    has_seek = bool(re.search(r"^\s*7\s+Seek_Error_Rate", attr_table_text, re.M))
    if has_seek:
        return False
    for aid in SSD_INDICATOR_ATTRS:
        if re.search(rf"^\s*{aid}\s+", attr_table_text, re.M):
            return True
    return False


def _smartctl_attributes(dev: str) -> Optional[dict]:
    """Try -d sat, -d scsi, -d auto. Returns dict attr1/attr7/attr11 or None if asleep/unavailable.

    Uses `-n standby` so we never wake a sleeping disk.
    """
    for dev_type in ("sat", "scsi", "auto"):
        cmd = ["smartctl", "-n", "standby", "-A", "-d", dev_type, dev]
        rc, out, err = _run(cmd)

        # rc 2 == disk in standby, do not wake. Skip, do not record a point.
        if rc == 2 or "Device is in STANDBY mode" in out:
            log.debug("%s: standby, skipping", dev)
            return None

        if rc != 0 and "Unknown USB bridge" in (out + err):
            continue  # try next device type

        if rc not in (0, 4):  # 4 = some SMART command failed but attributes present
            continue

        if _looks_like_ssd(out):
            log.debug("%s (%s): SSD/NVMe detected, mechanical attrs unavailable",
                      dev, dev_type)
            return {"attr1": None, "attr7": None, "attr11": None,
                    "attr187": None, "attr188": None, "attr193": None,
                    "attr197": None, "attr198": None, "attr240": None}

        attrs = _parse_smart_attributes(out)
        if attrs is not None:
            if log.isEnabledFor(logging.DEBUG):
                log.debug("%s (%s): parsed attrs=%s", dev, dev_type, attrs)
                in_table = False
                for line in out.splitlines():
                    if "ATTRIBUTE_NAME" in line:
                        in_table = True
                    if in_table:
                        log.debug("  | %s", line)
            return attrs
    log.warning("%s: smartctl failed for all device types", dev)
    return None


def _parse_smart_attributes(text: str) -> Optional[dict]:
    """Parse `smartctl -A` output. Returns dict of attr ID -> raw int (or None).

    Handles:
      - plain integers:            "... - 12345"
      - Seagate 48-bit:            "... - 34385099575"
      - values with suffixes:      "... - 23211 (103 48 0)"
      - time strings:              "... - 15678h+00m+00.000s"   (skipped)
    """
    result: dict = {
        "attr1": None, "attr7": None, "attr11": None,
        "attr187": None, "attr188": None, "attr193": None,
        "attr197": None, "attr198": None, "attr240": None,
    }
    found_any = False

    # Match ID, name, flags, val, worst, thresh, type, updated, when_failed, raw...
    # We only need ID (group 1) and the raw field (everything after when_failed).
    row_re = re.compile(
        r"^\s*(\d{1,3})\s+\S+\s+0x[0-9a-fA-F]+"   # ID, name, flag
        r"\s+\d+\s+\d+\s+\d+"                     # value worst thresh
        r"\s+\S+\s+\S+\s+\S+"                     # type updated when_failed
        r"\s+(.+?)\s*$"                              # raw (rest of line)
    )

    for line in text.splitlines():
        m = row_re.match(line)
        if not m:
            continue
        attr_id = int(m.group(1))
        if attr_id not in ATTR_IDS:
            continue
        raw_field = m.group(2).strip()

        # If the field looks like a time string ("15678h+00m+..."), skip.
        if re.search(r"[hms]\+", raw_field):
            continue

        # First token is the raw value (may be followed by "(...)" suffixes).
        first = raw_field.split()[0].rstrip(",")
        try:
            raw = int(first)
        except ValueError:
            continue

        # Seagate encodes 48-bit counters.  We keep the full integer;
        # baseline normalization happens in trend_analyzer, not here.
        result[ATTR_IDS[attr_id]] = raw
        found_any = True

    return result if found_any else None

def _count_kernel_ata_errors(hours: int = 1) -> int:
    """Count hidden ATA/link errors in journalctl for the last N hours."""
    since = f"{hours} hour ago"
    # journalctl -k = kernel only; -o cat = message only
    rc, out, _ = _run(["journalctl", "-k", "--since", since, "--no-pager", "-o", "cat"])
    if rc != 0 or not out:
        # fallback to dmesg (last N minutes not filtered, simple count)
        rc, out, _ = _run(["dmesg", "-T"])
        if rc != 0:
            return 0

    count = 0
    for line in out.splitlines():
        if any(p.search(line) for p in ATA_ERROR_PATTERNS):
            count += 1
    return count


def _init_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), mode=0o750, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            ts          INTEGER NOT NULL,
            dev         TEXT    NOT NULL,
            attr1       INTEGER,
            attr7       INTEGER,
            attr11      INTEGER,
            attr187     INTEGER,
            attr188     INTEGER,
            attr193     INTEGER,
            attr197     INTEGER,
            attr198     INTEGER,
            attr240     INTEGER,
            link_errors INTEGER DEFAULT 0,
            PRIMARY KEY (ts, dev)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dev_ts ON metrics(dev, ts)")
    # Migration: add new columns if upgrading from v1 schema
    existing = {row[1] for row in conn.execute("PRAGMA table_info(metrics)")}
    for col in ("attr187", "attr188", "attr193", "attr197", "attr198", "attr240"):
        if col not in existing:
            conn.execute(f"ALTER TABLE metrics ADD COLUMN {col} INTEGER")
            print(f"  migrated: added column {col}")
    conn.commit()
    return conn


def collect_one(dev: str, conn: sqlite3.Connection, now_ts: int) -> bool:
    """Collect a single point. Returns True if recorded."""
    if not _is_physical_disk(dev):
        log.debug("%s: not a physical disk, skipping", dev)
        return False

    attrs = _smartctl_attributes(dev)
    if attrs is None:
        return False  # standby or unavailable — do not create a false zero

    link_errors = _count_kernel_ata_errors(hours=1)
    conn.execute(
        "INSERT OR REPLACE INTO metrics "
        "(ts, dev, attr1, attr7, attr11, attr187, attr188, attr193, "
        " attr197, attr198, attr240, link_errors) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now_ts, os.path.basename(dev),
         attrs["attr1"], attrs["attr7"], attrs["attr11"],
         attrs["attr187"], attrs["attr188"], attrs["attr193"],
         attrs["attr197"], attrs["attr198"], attrs["attr240"],
         link_errors),
    )
    conn.commit()
    log.info("%s: attr1=%s attr7=%s attr11=%s attr187=%s attr188=%s "
             "attr193=%s attr197=%s attr198=%s attr240=%s link_errors=%d",
             dev, attrs["attr1"], attrs["attr7"], attrs["attr11"],
             attrs["attr187"], attrs["attr188"], attrs["attr193"],
             attrs["attr197"], attrs["attr198"], attrs["attr240"],
             link_errors)
    return True


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect SMART metrics into SQLite.")
    parser.add_argument("--config", default="/opt/hdd-health-predictor/config.yaml")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg: Config = load_config(args.config)
    conn = _init_db(cfg.db_path)
    now_ts = int(time.time())

    recorded = 0
    for dev in cfg.disks:
        try:
            if collect_one(dev, conn, now_ts):
                recorded += 1
        except Exception as exc:  # noqa: BLE001 — never let one disk kill the run
            log.exception("%s: collection failed: %s", dev, exc)

    conn.close()

    # Invoke analyzer for each recorded device (best-effort, same process tree)
    if recorded:
        for dev in cfg.disks:
            name = os.path.basename(dev)
            _run([
                sys.executable,
                os.path.join(os.path.dirname(__file__), "trend_analyzer.py"),
                "--config", args.config, "--dev", name,
            ], timeout=60)

    log.info("collected %d/%d devices", recorded, len(cfg.disks))
    return 0  # always exit 0 — alerts are normal, not failures


if __name__ == "__main__":
    sys.exit(main())
