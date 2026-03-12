#!/usr/bin/env bash
set -euo pipefail

DIR="${1:?Usage: $0 /path/to/logs}"

if [ ! -d "$DIR" ]; then
    echo "Error: '$DIR' is not a directory." >&2
    exit 1
fi

total=0
while IFS= read -r -d '' file; do
    count=$(grep -c 'ERROR' "$file" || true)
    echo "$count  $file"
    (( total += count ))
done < <(find "$DIR" -type f -name '*.log' -print0)

echo "---"
echo "Total: $total"
