#!/usr/bin/env bash
set -euo pipefail
# run_checks.sh -- run net_check and log_scan together
# =====================================================
# Usage:
#     ./run_checks.sh hosts.txt /var/log
#     ./run_checks.sh hosts.txt /var/log -s
#     sudo ./run_checks.sh hosts.txt /var/log --save

HOSTS="${1:?Usage: $0 <hosts_file> <log_dir> [-s|--save]}"
LOGDIR="${2:?Usage: $0 <hosts_file> <log_dir> [-s|--save]}"
SAVE=""
[[ "${3:-}" == "--save" || "${3:-}" == "-s" ]] && SAVE="--save"

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Network Check ==="
python3 "$DIR/net_check.py" "$HOSTS" $SAVE || true
echo

echo "=== Log Scan ==="
python3 "$DIR/log_scan.py" "$LOGDIR" $SAVE || true
