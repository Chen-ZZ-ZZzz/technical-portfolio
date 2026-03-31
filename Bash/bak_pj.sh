#!/usr/bin/env bash

set -euo pipefail
if [[ "${TRACE-0}" == "1" ]]; then
    set -o xtrace
fi

if [[ "${1-}" =~ ^-*h(elp)?$ ]]; then
    echo "Usage: $0 /path/to/folder

Back up a folder into a timestamped archive, in a sibling *-backups directory.

"
    exit 0
fi

cd "$(dirname "$0")"

main() {
    if [[ -z "${1-}" ]]; then
        echo "Usage: $0 /path/to/folder" >&2
        exit 1
    fi

    SOURCE_FOLDER=$1

    if [[ ! -d "$SOURCE_FOLDER" ]]; then
        echo "Error: Folder '$SOURCE_FOLDER' does not exist." >&2
        exit 1
    fi

    FOLDER_NAME=$(basename "$SOURCE_FOLDER")
    BACKUP_FOLDER=$(dirname "$SOURCE_FOLDER")/${FOLDER_NAME}-backups
    TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
    OUTPUT_FILE=${BACKUP_FOLDER}/${FOLDER_NAME}_backup_${TIMESTAMP}.tar.gz

    mkdir -p "$BACKUP_FOLDER"
    tar -cf "$OUTPUT_FILE" -C "$(dirname "$SOURCE_FOLDER")" "$FOLDER_NAME"

    echo "Backup complete: $OUTPUT_FILE"
}

main "$@"
