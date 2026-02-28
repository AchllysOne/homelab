#!/bin/sh
set -eu

START_DELAY=30

WEBHOOK_URL=""
USER_ID=""

DOCKER_BIN="docker"

sleep "$START_DELAY"

# Wait for Docker daemon + networking
i=0
while :; do
  if $DOCKER_BIN info >/dev/null 2>&1 && \
     $DOCKER_BIN network ls >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  [ "$i" -ge 90 ] && break
  sleep 1
done

# Start only stopped/exited containers
STOPPED_IDS="$($DOCKER_BIN ps -aq -f status=exited -f status=created 2>/dev/null || true)"

if [ -n "$STOPPED_IDS" ]; then
  for id in $STOPPED_IDS; do
    $DOCKER_BIN start "$id" >/dev/null 2>&1 || true
  done
fi

# Build running container list
containers="$($DOCKER_BIN ps --format '{{.Names}}' 2>/dev/null | sed 's/^/• /')"
[ -n "$containers" ] || containers="• (none)"

json_escape() {
  printf '%s' "$1" | sed \
    -e 's/\\/\\\\/g' \
    -e 's/"/\\"/g' \
    -e ':a;N;$!ba;s/\n/\\n/g'
}

message="$(printf '%s\n' \
  ':satellite: **Server Status**' \
  '' \
  ':green_circle: **Server is back online**' \
  '' \
  ':card_file_box: **Running Docker Containers:**' \
  '```' \
  "$containers" \
  '```')"

payload="$(printf '{"content":"%s"}' "$(json_escape "<@${USER_ID}> ${message}")")"

curl -fsS -H "Content-Type: application/json" \
  -X POST -d "$payload" "$WEBHOOK_URL" >/dev/null