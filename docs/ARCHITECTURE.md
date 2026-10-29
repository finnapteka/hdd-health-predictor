# Architecture

See the full scenario in the project description. Key points:

## Lifecycle of one run

1. `systemd` timer fires `hdd-health-predictor.service` (Type=oneshot, hourly).
2. `hdd_parser.py`:
   - iterates configured disks, skips removable/loop/dm
   - calls `smartctl -n standby -A -d {sat|scsi|auto} /dev/sdX`
   - if disk is in standby (rc=2), skips — **never wakes it**
   - parses attr 1/7/11 raw values (Seagate 48-bit → lower 32 bits)
   - counts kernel ATA errors in `journalctl -k` for the last hour
   - `INSERT OR REPLACE` into `metrics(ts, dev, attr1, attr7, attr11, link_errors)`
   - invokes `trend_analyzer.py --dev X` for each recorded device
3. `trend_analyzer.py`:
   - cold start guard: if samples < `min_points`, return `collecting`
   - Holt-Winters (double exponential smoothing) + linear regression
   - second derivative (acceleration) over a sliding window
   - classify: stable / linear_wear / exponential / critical_jump
   - Health Index (0–100 %) from level + slope + acceleration penalties
   - Time-to-Failure: extrapolate to empirical threshold using slope
   - if HI < `hi_threshold` or exponential/jump → `notifier.send_alert`
4. `notifier.py`:
   - anti-spam: same device+level suppressed for `alert_cooldown_hours`
   - `notify-send` as desktop user, `logger` to syslog, JSON line to `alerts.log`
   - state saved to `state.json`

## Data model

```sql
CREATE TABLE metrics (
    ts          INTEGER NOT NULL,
    dev         TEXT    NOT NULL,
    attr1       INTEGER,
    attr7       INTEGER,
    attr11      INTEGER,
    link_errors INTEGER DEFAULT 0,
    PRIMARY KEY (ts, dev)
);

## Edge cases

Situation	Behavior

Disk in standby	smartctl rc=2 → skip, no false zero

smartctl missing	parser logs, exit 0

Attribute absent (NVMe/SSD)	null, series skipped, HI from others

< min_points samples	collecting, no alert

Sudden jump after reboot	is_jump via 3σ, alert

Double run	INSERT OR REPLACE dedup by (ts, dev)

notify-send fails	fallback to logger + alerts.log

## I/O impact

    smartctl -A -n standby: 0.04–0.4 s of register reads, once per hour

    SQLite write: ~200 bytes per hour per disk

    No wake-ups of standby disks


---

## 22. `docs/CLI.md`

```markdown
# CLI reference

## Install

```bash
sudo ./install.sh

## Check status

systemctl status hdd-health-predictor.timer
systemctl list-timers | grep hdd

## Run manually

# collect + analyze
sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/src/hdd_parser.py --verbose

# analyze one disk
sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/src/trend_analyzer.py --dev sdc --verbose

## Inspect DB

sqlite3 /var/log/hdd_pre_fail/metrics.db \
  "SELECT datetime(ts,'unixepoch'), dev, attr1, attr7, attr11, link_errors \
   FROM metrics ORDER BY ts DESC LIMIT 20;"

## Simulate anomaly (test the trigger)

sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/tools/simulate_anomaly.py --dev sdc --days 30

sudo /opt/hdd-health-predictor/venv/bin/python \
     /opt/hdd-health-predictor/src/trend_analyzer.py --dev sdc

     
## View alerts

tail -f /var/log/hdd_pre_fail/alerts.log
journalctl -t hdd-health-predictor -n 50

## Reset anti-spam

sudo rm /var/log/hdd_pre_fail/state.json

## Uninstall

sudo ./uninstall.sh
