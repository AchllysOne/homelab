#!/bin/bash

# Get start time for duration calculation
START_TIME=$(date +%s)

# Configuration
IMMICH_DIR="/mnt/containers/immich"  # Where Immich is installed (contains docker-compose.yml)
BACKUP_DIR="/mnt/backups/immich"
UPLOAD_DIR="$IMMICH_DIR/upload"   # Upload directory relative to Immich directory
MAX_BACKUPS=3
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M")  # e.g. "2025-06-11_14-30"
WEBHOOK_URL="<webhook>"    # Your Discord webhook URL
USER_ID_TO_PING="<User ID>"  # Replace with the Discord user ID you want to ping when there are errors

# Initialize status variables
STATUS="✅ SUCCESS"
ERROR_MESSAGES=()

# Change to Immich directory for docker commands
cd "$IMMICH_DIR" || {
    ERROR_MESSAGES+=("Failed to access Immich directory: $IMMICH_DIR")
    STATUS="❌ FAILED"
    echo "${ERROR_MESSAGES[-1]}"
    exit 1
}

# 1. Backup the database
DB_BACKUP_FILE="$BACKUP_DIR/immich_db_$TIMESTAMP.sql.gz"
if ! docker exec -t immich_postgres pg_dumpall --clean --if-exists --username=achllys | gzip > "$DB_BACKUP_FILE"; then
    ERROR_MESSAGES+=("Database backup failed")
    STATUS="⚠️ COMPLETED WITH WARNINGS"
    DB_BACKUP_STATUS=1
else
    DB_BACKUP_STATUS=0
fi

# 2. Shutdown Immich
if ! sudo docker compose down; then
    ERROR_MESSAGES+=("Failed to shutdown containers")
    STATUS="⚠️ COMPLETED WITH WARNINGS"
    DOWN_STATUS=1
else
    DOWN_STATUS=0
fi

# 3. Backup upload directory
UPLOAD_BACKUP_FILE="$BACKUP_DIR/immich_uploads_$TIMESTAMP.tar.gz"
if ! tar -czf "$UPLOAD_BACKUP_FILE" -C "$UPLOAD_DIR" .; then
    ERROR_MESSAGES+=("Upload directory backup failed")
    STATUS="⚠️ COMPLETED WITH WARNINGS"
    UPLOAD_BACKUP_STATUS=1
else
    UPLOAD_BACKUP_STATUS=0
fi

# 4. Start Immich back up
if ! sudo docker compose up -d; then
    ERROR_MESSAGES+=("Failed to start containers")
    STATUS="⚠️ COMPLETED WITH WARNINGS"
    UP_STATUS=1
else
    UP_STATUS=0
fi

# Cleanup old backups - keep only MAX_BACKUPS
# Database backups cleanup
find "$BACKUP_DIR" -name "immich_db_*.sql.gz" -type f | sort -r | tail -n +$(($MAX_BACKUPS + 1)) | xargs rm -f 2>/dev/null

# Upload backups cleanup
find "$BACKUP_DIR" -name "immich_uploads_*.tar.gz" -type f | sort -r | tail -n +$(($MAX_BACKUPS + 1)) | xargs rm -f 2>/dev/null

# Calculate duration
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
DURATION_MIN=$((DURATION / 60))
DURATION_SEC=$((DURATION % 60))

# Get backup size
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" | awk '{print $1}')

# Current date for version - formatted as "Friday 13 June 2025 07:25 PM"
CURRENT_DATE=$(date +'%A %d %B %Y %I:%M %p')

# Prepare error message if any
ERROR_FIELD=""
if [ ${#ERROR_MESSAGES[@]} -gt 0 ]; then
    ERROR_FIELD=$(printf '\\n- %s' "${ERROR_MESSAGES[@]}")
    PING_USER="<@$USER_ID_TO_PING> "  # Add user ping only if there are errors
else
    PING_USER=""
fi

# Send notification to Discord
curl -H "Content-Type: application/json" -d '{
    "content": "'"$PING_USER"'",
    "embeds": [{
        "title": "Immich Backup Completed",
        "fields": [
            {
                "name": "Backup Status",
                "value": "'"$STATUS$ERROR_FIELD"'",
                "inline": true
            },
            {
                "name": "Duration",
                "value": "'"$DURATION_MIN"'m '"$DURATION_SEC"'s",
                "inline": true
            },
            {
                "name": "Size",
                "value": "'"$BACKUP_SIZE"'",
                "inline": true
            },
            {
                "name": "Backup Version",
                "value": "'"$CURRENT_DATE"'",
                "inline": false
            }
        ],
        "color": 16763904
    }]
}' "$WEBHOOK_URL"