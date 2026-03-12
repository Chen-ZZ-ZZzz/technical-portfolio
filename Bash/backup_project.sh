#!/usr/bin/env bash
set -euo pipefail
# -----------------------------
# Usage: ./backup_project.sh /path/to/folder
# -----------------------------

SOURCE_FOLDER="${1:?Usage: $0 /path/to/folder}"

if [ ! -d "$SOURCE_FOLDER" ]; then
    echo "Error: Folder '$SOURCE_FOLDER' does not exist." >&2
    exit 1
fi

FOLDER_NAME=$(basename "$SOURCE_FOLDER")
BACKUP_FOLDER="$(dirname "$SOURCE_FOLDER")/${FOLDER_NAME}-backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTPUT_FILE="${BACKUP_FOLDER}/${FOLDER_NAME}_backup_${TIMESTAMP}.tar.gz"

mkdir -p "$BACKUP_FOLDER"
tar -czf "$OUTPUT_FILE" -C "$(dirname "$SOURCE_FOLDER")" "$FOLDER_NAME"

echo "Backup complete: $OUTPUT_FILE"
