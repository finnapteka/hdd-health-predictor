#!/usr/bin/env bash
# Install hdd-health-predictor into /opt and enable systemd timer.
set -euo pipefail

PREFIX="/opt/hdd-health-predictor"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"
LOG_DIR="/var/log/hdd_pre_fail"

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./install.sh" >&2
    exit 1
fi

echo "[*] Installing to ${PREFIX}"
install -d -m 755 "${PREFIX}"
install -d -m 750 "${LOG_DIR}"

# Copy source tree (exclude venv, git, pycache)
rsync -a --delete \
    --exclude '.git' --exclude 'venv' --exclude '__pycache__' \
    --exclude '*.db' --exclude '*.jsonl' \
    "${SRC_DIR}/" "${PREFIX}/"

# Venv
if [[ ! -d "${PREFIX}/venv" ]]; then
    echo "[*] Creating venv"
    python3 -m venv "${PREFIX}/venv"
fi
"${PREFIX}/venv/bin/pip" install --upgrade pip >/dev/null
"${PREFIX}/venv/bin/pip" install -r "${PREFIX}/requirements.txt"

# Default config
if [[ ! -f "${PREFIX}/config.yaml" ]]; then
    cp "${PREFIX}/config.example.yaml" "${PREFIX}/config.yaml"
    chmod 640 "${PREFIX}/config.yaml"
    echo "[*] Wrote default config: ${PREFIX}/config.yaml"
fi

# systemd units
echo "[*] Installing systemd units"
install -m 644 "${PREFIX}/systemd/hdd-health-predictor.service" "${SYSTEMD_DIR}/"
install -m 644 "${PREFIX}/systemd/hdd-health-predictor.timer"   "${SYSTEMD_DIR}/"

systemctl daemon-reload
systemctl enable --now hdd-health-predictor.timer

echo "[+] Done. Check status: systemctl status hdd-health-predictor.timer"
echo "[+] Logs:               journalctl -u hdd-health-predictor.service -n 50"
echo "[+] First run will only collect a baseline (min_points=${MIN_POINTS:-24} samples needed)."
