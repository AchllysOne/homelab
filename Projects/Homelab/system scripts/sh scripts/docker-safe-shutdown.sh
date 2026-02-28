#!/usr/bin/env bash
set -Eeuo pipefail

LOG="/var/log/docker-safe-shutdown.log"
LOCKFILE="/run/docker-safe-shutdown.lock"
STOP_TIMEOUT=60
FORCE_WAIT=15

exec >>"$LOG" 2>&1

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

cleanup() {
  rm -f "$LOCKFILE"
}
trap cleanup EXIT

# Prevent double execution
exec 9>"$LOCKFILE" || exit 1
flock -n 9 || {
  log "Another shutdown already in progress, exiting."
  exit 0
}

log "Starting Docker safe shutdown..."

# Get container list once
mapfile -t CONTAINERS < <(docker ps --format '{{.Names}}')

if [[ ${#CONTAINERS[@]} -eq 0 ]]; then
  log "No running containers."
  exit 0
fi

# Graceful stop
for name in "${CONTAINERS[@]}"; do
  log "Stopping container: $name"
  docker stop -t "$STOP_TIMEOUT" "$name" || true
done

log "Waiting ${FORCE_WAIT}s for graceful shutdown..."
sleep "$FORCE_WAIT"

# Force kill anything still running
REMAINING=$(docker ps -q || true)
if [[ -n "$REMAINING" ]]; then
  log "Force stopping remaining containers:"
  docker ps --format ' - {{.Names}}'
  docker kill $REMAINING || true
fi

log "Docker shutdown complete."