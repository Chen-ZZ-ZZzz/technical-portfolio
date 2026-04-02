#!/usr/bin/env bash

set -euo pipefail
if [[ "${TRACE-0}" == "1" ]]; then
    set -o xtrace
fi

if [[ "${1-}" =~ ^-*h(elp)?$ ]]; then
    echo "Usage: $0 /path/to/logs

Counts occurrences of ERROR in all .log files under a directory.

"
    exit 0
fi

main (){
    if [[ -z "${1-}" ]]; then
        echo "Usage: $0 /path/to/logs" >&2
        exit 1
    fi

    DIR=$1

    if [[ ! -d "$DIR" ]]; then
        echo "Error: '$DIR' is not a directory." >&2
        exit 1
    fi

    total=0
    while IFS= read -r -d '' file; do
        count=$(grep -c 'ERROR' "$file" || true)
        echo "$count  $file"
        total=$(( total + count ))
    done < <(find "$DIR" -type f -name '*.log' -print0)

    echo "---"
    echo "Total: $total"
}

main "$@"
