#!/bin/bash
set -e

LOG="/var/log/powerfail-shutdown.log"
exec >> "$LOG" 2>&1

# ================= CONFIG =================
WEBHOOK_URL=""
USER_ID=""

SMTP2GO_API_KEY='"
EMAIL_FROM=""

# TWO RECIPIENTS:
EMAIL_TO_1="
EMAIL_TO_2=""

HOSTNAME="$(hostname -f)"
# =========================================

send_discord() {
    curl -fsS -H "Content-Type: application/json" -X POST \
        -d "{\"content\":\"$1\"}" \
        "$WEBHOOK_URL" >/dev/null || true
}

# JSON-safe escaping (for email body/subject)
json_escape() {
  printf '%s' "$1" | sed \
    -e 's/\\/\\\\/g' \
    -e 's/"/\\"/g' \
    -e ':a;N;$!ba;s/\n/\\n/g'
}

send_email() {
    SUBJECT="$1"
    BODY="$2"

    SUBJECT_ESC="$(json_escape "$SUBJECT")"
    BODY_ESC="$(json_escape "$BODY")"

    curl -fsS https://api.smtp2go.com/v3/email/send \
      -H "Content-Type: application/json" \
      -d "{
        \"api_key\": \"${SMTP2GO_API_KEY}\",
        \"to\": [\"${EMAIL_TO_1}\", \"${EMAIL_TO_2}\"],
        \"sender\": \"${EMAIL_FROM}\",
        \"subject\": \"${SUBJECT_ESC}\",
        \"text_body\": \"${BODY_ESC}\"
      }" >/dev/null || true
}

echo "[$(date)] Power failure shutdown sequence started."

send_discord "<@${USER_ID}> :red_circle: **Server shutting down due to power loss (offline > 1 minute).**"

send_email \
  "[${HOSTNAME}] Power failure shutdown" \
  "The server ${HOSTNAME} is shutting down due to UPS power loss exceeding 60 seconds."

echo "[$(date)] Stopping Docker containers..."
/opt/scripts/docker-safe-shutdown.sh

# Build container list (if available)
if [ -f /tmp/docker_stopped.list ]; then
    CONTAINERS="$(sed 's/^/• /' /tmp/docker_stopped.list)"
    rm -f /tmp/docker_stopped.list
else
    CONTAINERS="• No running containers detected"
fi

send_discord "<@${USER_ID}> :green_circle: **Docker shutdown successful**\n\n📦 **Containers stopped:**\n$(printf '%s\n' "$CONTAINERS")\n\n⚡ System is now powering off safely."

send_email \
  "[${HOSTNAME}] Docker shutdown complete" \
  "Docker containers were stopped successfully on ${HOSTNAME}:\n\n${CONTAINERS}\n\nThe system is now powering off safely."

echo "[$(date)] Initiating system shutdown..."
/sbin/shutdown -h now "UPS power loss > 60 seconds"