#!/usr/bin/env bash
# Remove hdd-health-predictor from the system.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./uninstall.sh" >&2
    exit 1
fi

systemctl disable --now hdd-health-predictor.timer 2>/dev/null || true
rm -f /etc/systemd/system/hdd-health-predictor.service
rm -f /etc/systemd/system/hdd-health-predictor.timer
systemctl daemon-reload

rm -rf /opt/hdd-health-predictor
# Keep /var/log/hdd_pre_fail/ — user may want the data. Remove manually if desired.
echo "[+] Removed. Data in /var/log/hdd_pre_fail/ was kept."
