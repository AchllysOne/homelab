#!/bin/bash
set -e

LOG="/var/log/powerfail-shutdown.log"
exec >> "$LOG" 2>&1

WEBHOOK_URL=""
USER_ID=""

send_discord() {
    curl -s -H "Content-Type: application/json" -X POST \
        -d "{\"content\":\"$1\"}" \
        "$WEBHOOK_URL" >/dev/null
}

echo "[$(date)] Power failure shutdown sequence started."

send_discord "<@${USER_ID}> :red_circle: **Server shutting down due to power loss (offline > 1 minute).**"

echo "[$(date)] Stopping Docker containers..."
/opt/scripts/docker-safe-shutdown.sh

# Build container list (if available)
if [ -f /tmp/docker_stopped.list ]; then
    CONTAINERS=$(sed 's/^/• /' /tmp/docker_stopped.list | tr '\n' '\\n')
    rm -f /tmp/docker_stopped.list
else
    CONTAINERS="• No running containers detected"
fi

send_discord "<@${USER_ID}> :green_circle: **Docker shutdown successful**\n\n📦 **Containers stopped:**\n${CONTAINERS}\n\n⚡ System is now powering off safely."

echo "[$(date)] Initiating system shutdown..."
/sbin/shutdown -h now "UPS power loss > 60 seconds"