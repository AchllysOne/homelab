#!/bin/bash

# Set the script to exit immediately if any command fails
set -e

DATE=$(date +%Y-%m-%d)
BACKUP_DIR=/mnt/backups/vaultwarden  # Corrected path
BACKUP_FILE=backup_vaultwarden-$DATE.tar.gz  # Renamed backup file
CONTAINER=vaultwarden
CONTAINER_DATA_DIR=/mnt/containers/vaultwarden/bitwarden  # Updated path

# Discord Webhook URL (replace with your own)
DISCORD_WEBHOOK_URL="<webhook>"

# Create backups directory if it does not exist
sudo mkdir -p $BACKUP_DIR

# Stop the container
/usr/bin/docker stop $CONTAINER

# Backup the vaultwarden data directory to the backup directory
sudo tar -czf "$BACKUP_DIR/$BACKUP_FILE" -C "$CONTAINER_DATA_DIR" .

# Restart the container
/usr/bin/docker restart $CONTAINER

# Keep only the 3 most recent backups
cd $BACKUP_DIR
sudo ls -t backup_vaultwarden-*.tar.gz | tail -n +4 | xargs sudo rm -f --

# Send a success notification to Discord with a nice embed
curl -X POST -H "Content-Type: application/json" \
  -d '{
    "embeds": [{
      "title": "✅ Vaultwarden Backup Completed",
      "description": "Backup process finished successfully!",
      "color": 5763719,
      "fields": [
        {
          "name": "Backup File",
          "value": "'"$BACKUP_FILE"'",
          "inline": true
        },
        {
          "name": "Date",
          "value": "'"$DATE"'",
          "inline": true
        },
        {
          "name": "Location",
          "value": "'"$BACKUP_DIR"'"
        },
        {
          "name": "Backups Kept",
          "value": "3 most recent backups retained",
          "inline": true
        }
      ],
      "footer": {
        "text": "Automated Backup System"
      },
      "timestamp": "'$(date -u +'%Y-%m-%dT%H:%M:%SZ')'"
    }]
  }' \
  $DISCORD_WEBHOOK_URL