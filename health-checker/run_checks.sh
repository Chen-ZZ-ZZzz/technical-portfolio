#!/usr/bin/env bash
set -euo pipefail
# run_checks.sh -- run net_check and log_scan together
# =====================================================
# Usage:
#     ./run_checks.sh hosts.txt /var/log
#     ./run_checks.sh hosts.txt /var/log -s
#     ./run_checks.sh hosts.txt /var/log --save --db reports/health.db
#     sudo ./run_checks.sh hosts.txt /var/log -s -d reports/health.db

HOSTS="${1:?Usage: $0 <hosts_file> <log_dir> [-s|--save] [-d|--db PATH]}"
LOGDIR="${2:?Usage: $0 <hosts_file> <log_dir> [-s|--save] [-d|--db PATH]}"
shift 2

SAVE=""
DB_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--save) SAVE="--save"; shift ;;
        -d|--db)   DB_ARG="--db $2"; shift 2 ;;
        *) shift ;;
    esac
done

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Network Check ==="
python3 "$DIR/net_check.py" "$HOSTS" "$SAVE" "$DB_ARG" || true
echo

echo "=== Log Scan ==="
python3 "$DIR/log_scan.py" "$LOGDIR" "$SAVE" "$DB_ARG" || true
