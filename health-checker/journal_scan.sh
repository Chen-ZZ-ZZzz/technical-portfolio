#!/usr/bin/env bash
set -euo pipefail
[[ "${TRACE:-0}" == "1" ]] && set -x

if [[ "${1-}" =~ ^-*h(elp)?$ ]]; then
    echo "Usage: $0

Grep ABOVE-WARNING-LEVEL logs from systemd journal

"
    exit 0
fi

require_cmd(){
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Error: $1 not found in PATH" >&2
        exit 1
    fi
}

run_dir=""
cleanup() {
    if [[ -n "$run_dir" && -z "${RUNTIME_DIRECTORY:-}" ]]; then
        rm -rf "$run_dir"
    fi
}
trap cleanup EXIT

cd "$(dirname "${BASH_SOURCE[0]}")"
# all paths below are relative to the script's dir

main(){
    if [[ $# -gt 0 ]]; then
        echo "Error: $0 takes no arguments" >&2
        exit 1
    fi

    require_cmd jq
    require_cmd python3

    # systemd provides these; fall back for interactive runs
    local state_dir="${STATE_DIRECTORY:-${XDG_STATE_HOME:-$HOME/.local/state}/checker-journal-scan}"
    if [[ -n "${RUNTIME_DIRECTORY:-}" ]]; then
        run_dir="$RUNTIME_DIRECTORY"
    else
        run_dir="$(mktemp -d)"
    fi
    mkdir -p "$state_dir"

    local cursor_file="$state_dir/cursor"
    local tmp_json="$run_dir/journal.json"
    local tmp_log="$run_dir/journal.log"
    local log_dir='reports'

    if [[ -s "$cursor_file" ]]; then
        journalctl -p warning -o json --after-cursor "$(<"$cursor_file")" > "$tmp_json"
    else
        journalctl -p warning -o json -S today > "$tmp_json"   # first ever run only
    fi

    if [[ -s "$tmp_json" ]]; then
        python3 journal2log.py "$tmp_json" "$tmp_log"
        python3 log_scan.py "$tmp_log" -q -s json
    fi

    new_cursor="$(tail -n 1 "$tmp_json" | jq -r '.__CURSOR // empty')"
    if [[ -n "$new_cursor" ]]; then
        printf '%s\n' "$new_cursor" > "$cursor_file.tmp"
        mv "$cursor_file.tmp" "$cursor_file"                        # atomic commit
    fi

    # delete old report files
    mkdir -p "$log_dir"
    find "$log_dir" -maxdepth 1 -type f -name 'log_scan_*' -mtime '+30' -delete
}

main "$@"
