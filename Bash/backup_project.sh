#!/bin/bash

# -----------------------------
# Usage: ./backup_project.sh /path/to/folder
# -----------------------------

# Folder to backup (first argument)
SOURCE_FOLDER="$1"

# Check if folder is provided
if [ -z "$SOURCE_FOLDER" ]; then
    echo "Usage: $0 /path/to/folder"
    exit 1
fi

# Check if folder exists
if [ ! -d "$SOURCE_FOLDER" ]; then
    echo "Error: Folder '$SOURCE_FOLDER' does not exist."
    exit 1
fi

# Get folder name without path
FOLDER_NAME=$(basename "$SOURCE_FOLDER")

# Create backup folder next to original folder
BACKUP_FOLDER="$(dirname "$SOURCE_FOLDER")/${FOLDER_NAME}-backups"
mkdir -p "$BACKUP_FOLDER"

# Get current timestamp
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# Output filename
OUTPUT_FILE="${BACKUP_FOLDER}/${FOLDER_NAME}_backup_${TIMESTAMP}.tar.gz"

# Compress the folder into the backup folder
tar -czf "$OUTPUT_FILE" -C "$(dirname "$SOURCE_FOLDER")" "$FOLDER_NAME"

echo "Backup complete: $OUTPUT_FILE"
