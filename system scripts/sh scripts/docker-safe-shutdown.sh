#!/bin/bash
set -e

LOG="/var/log/docker-safe-shutdown.log"
exec >> "$LOG" 2>&1

echo "[$(date)] Starting Docker safe shutdown..."

STOPPED_CONTAINERS=""

docker ps --format "{{.Names}}" | while read -r name; do
    echo "Stopping container $name"
    docker stop -t 60 "$name" && echo "$name" >> /tmp/docker_stopped.list || true
done

sleep 15

REMAINING=$(docker ps -q || true)
if [ -n "$REMAINING" ]; then
    echo "Force stopping remaining containers..."
    docker kill $REMAINING || true
fi

echo "[$(date)] Docker shutdown complete."
exit 0