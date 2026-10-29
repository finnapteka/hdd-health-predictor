# hdd-health-predictor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![systemd](https://img.shields.io/badge/systemd-ready-green.svg)](https://systemd.io/)

**Predictive HDD failure monitor for Linux.** Detects spindle/actuator degradation
weeks before S.M.A.R.T. reports reallocated sectors — by tracking micro-trends in
`Seek Error Rate`, `Calibration Retry Count` and kernel ATA link errors.

> Русская версия: [README.ru.md](README.ru.md)

---

## Why

Standard S.M.A.R.T. alerts you **too late** — when sectors already start failing
(`Reallocated_Sector_Ct` > 0). This daemon watches the *hidden* early signals:

| Attribute | ID | What it means |
|---|---|---|
| Raw Read Error Rate | 1 | Read retries at the platter level |
| Seek Error Rate | 7 | Actuator positioning errors |
| Calibration Retry Count | 11 | Spindle/head recalibration attempts |
| Kernel ATA errors | — | `COMRESET failed`, `soft resetting link`, `DRDY ERR` |

It computes **Holt-Winters smoothing**, **linear regression slope**, and the
**second derivative (acceleration)** of error growth. Linear growth = normal wear.
Exponential growth = mechanical failure imminent.

Output: **Health Index (0–100 %)** and **Time-to-Failure (days)**.

---

## Features

- ✅ Runs as a `systemd` timer, **once per hour** (low I/O impact)
- ✅ **Never wakes a standby disk** (`smartctl -n standby`)
- ✅ Seagate 48-bit raw value decoding
- ✅ Auto-detects device type (`-d sat` → `-d scsi` → `-d auto`)
- ✅ Pure-Python math core (works without numpy/scipy; uses them if present)
- ✅ Desktop notification (`notify-send`) + syslog (`logger`) + JSON alerts log
- ✅ Anti-spam: same alert suppressed for 6 h
- ✅ SQLite storage in `/var/log/hdd_pre_fail/` (not on the monitored disk)

---

## Requirements

- Linux with `systemd` ≥ 240
- Python 3.9+
- `smartmontools` (`smartctl`)
- `libnotify-bin` (`notify-send`) — optional, falls back to syslog
- Root privileges (SMART requires it)

```bash
sudo apt install smartmontools libnotify-bin python3-venv

---

## Install

bash

git clone https://github.com/finnapteka/hdd-health-predictor.git
cd hdd-health-predictor
sudo ./install.sh

The installer will:

    Copy files to /opt/hdd-health-predictor/

    Create a Python venv and install requirements.txt

    Install hdd-health-predictor.service and .timer into /etc/systemd/system/

    Enable and start the timer

Edit /opt/hdd-health-predictor/config.yaml to set which disks to monitor.


## Manual run
bash

# collect one sample now and analyze
sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/src/hdd_parser.py --config /opt/hdd-health-predictor/config.yaml

# analyze only (no collection)
sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/src/trend_analyzer.py \
     --config /opt/hdd-health-predictor/config.yaml --dev sdc

## Simulate a failure (for testing)

bash

sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/tools/simulate_anomaly.py --dev sdc --days 30

This injects an exponential trend of Seek Error Rate and Calibration Retry Count
into the SQLite DB, then runs the analyzer — you should get a desktop notification
and a line in /var/log/hdd_pre_fail/alerts.log.

## Supported hardware

| Type | Mechanical prediction | Notes |
|---|---|---|
| **SATA HDD (Seagate, WD, Hitachi)** | Yes | Uses attr 1/7/11 + Seagate-specific 187/188/193/197/198/240 |
| **SAS HDD** | Partial | Via `-d scsi`, depends on drive firmware |
| **SSD / NVMe** | No | No mechanical attributes; correctly skipped (`ssd_skip`) |
| **USB flash / external** | No | Excluded via `/sys/block/*/removable` |
| **loop / dm / md** | No | Virtual devices, excluded |

Verified on:
- Seagate ST2000DL003-9VT1 (Barracuda Green 2 TB) - attr 1/7/187/188/193/197/198/240
- Seagate ST1000VX005-2EZ1 (SkyHawk 1 TB) - attr 1/7/187/188/193/197/198

## How it works

text

systemd timer (hourly)
        │
        ▼
hdd_parser.py ──► smartctl -A -n standby ──► SQLite
        │         journalctl -k (1h)      /var/log/hdd_pre_fail/metrics.db
        ▼
trend_analyzer.py
        │  Holt-Winters + linregress + 2nd derivative
        │  → Health Index, Time-to-Failure
        ▼
notifier.py ──► notify-send + logger + alerts.log

See docs/ARCHITECTURE.md for the full design.
