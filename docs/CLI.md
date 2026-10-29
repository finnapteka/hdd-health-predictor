# CLI reference

## Install

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
      "SELECT datetime(ts,'unixepoch'), dev, attr1, attr7, attr187, attr188, attr193 \
       FROM metrics ORDER BY ts DESC LIMIT 20;"

## Simulate anomaly (test the trigger)

    sudo /opt/hdd-health-predictor/venv/bin/python \
         /opt/hdd-health-predictor/tools/simulate_anomaly.py --dev sdc --days 30

    sudo /opt/hdd-health-predictor/venv/bin/python \
         /opt/hdd-health-predictor/src/trend_analyzer.py --dev sdc

Expected: desktop notification + line in `/var/log/hdd_pre_fail/alerts.log`.

## View alerts

    tail -f /var/log/hdd_pre_fail/alerts.log
    journalctl -t hdd-health-predictor -n 50

## Reset anti-spam

    sudo rm /var/log/hdd_pre_fail/state.json

## Uninstall

    sudo ./uninstall.sh
