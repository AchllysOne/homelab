#!/usr/bin/env bash
# ZFS pool health alert → Discord + SMTP2GO
# - Non-root safe
# - Uses /opt/state-time
# - Runs every 15 min (systemd user timer)
# - Alerts once, then suppresses for 6 hours
# - Discord shows full clean zpool status block

set -Eeuo pipefail

################################
# CONFIG
################################

POOL_NAME="achz3"

# Discord
DISCORD_WEBHOOK_URL=""
DISCORD_USER_ID=""

# SMTP2GO
SMTP2GO_API_KEY=""
EMAIL_FROM=""

# ✅ Multiple recipients here
EMAIL_TO_LIST=(
  ""
  ""
)

SMTP2GO_URL="https://api.smtp2go.com/v3/email/send"

# State handling (non-root)
STATE_DIR="/opt/state-time"
LAST_ALERT_FILE="${STATE_DIR}/${POOL_NAME}.last_alert"
SUPPRESS_SECONDS=$((6 * 60 * 60))  # 6 hours

################################
# HELPERS
################################

host() { hostname -s; }
now()  { date +"%Y-%m-%d %H:%M:%S %Z"; }
epoch(){ date +%s; }

################################
# DISCORD
################################

send_discord() {
  local message="$1"
  local ping="$2"

  curl -fsS -X POST \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg content "$message $ping" '{content:$content}')" \
    "$DISCORD_WEBHOOK_URL" >/dev/null
}

################################
# EMAIL (SMTP2GO)
################################

send_email() {
  local subject="$1"
  local body="$2"

  # Convert bash array → JSON array
  local to_json
  to_json="$(printf '%s\n' "${EMAIL_TO_LIST[@]}" | jq -R . | jq -s .)"

  curl -fsS -X POST "$SMTP2GO_URL" \
    -H "Content-Type: application/json" \
    -d "$(jq -n \
      --arg key "$SMTP2GO_API_KEY" \
      --arg from "$EMAIL_FROM" \
      --arg subject "$subject" \
      --arg body "$body" \
      --argjson to "$to_json" \
      '{
        api_key:  $key,
        sender:   $from,
        to:       $to,
        subject:  $subject,
        text_body:$body
      }')" \
    >/dev/null
}

################################
# ZFS PARSING
################################

get_state() {
  awk '/state:/{print $2}'
}

get_faulted_drives() {
  awk '
    /^config:/ {in_cfg=1; next}
    /^errors:/ {exit}
    in_cfg && /^[[:space:]]*NAME/ {next}
    in_cfg && NF < 2 {next}
    in_cfg {
      if ($1 ~ /^(mirror|raidz|spare|logs|cache|special)-/) next
      if ($2 != "ONLINE") printf "• %s — %s\n", $1, $2
    }
  '
}

# Handles leading spaces before "pool:"
get_zfs_status_block() {
  awk '
    /^[[:space:]]*pool:/ {in_block=1}
    in_block {print}
  '
}

################################
# MAIN
################################

mkdir -p "$STATE_DIR"

POOL_STATUS="$(zpool status "$POOL_NAME")"
STATE="$(echo "$POOL_STATUS" | get_state)"

FAULTED_DRIVES="$(echo "$POOL_STATUS" | get_faulted_drives)"
[[ -z "$FAULTED_DRIVES" ]] && FAULTED_DRIVES="• (none reported)"

ZFS_STATUS_BLOCK="$(echo "$POOL_STATUS" | get_zfs_status_block)"

NOW="$(epoch)"
LAST_ALERT=0
[[ -f "$LAST_ALERT_FILE" ]] && LAST_ALERT="$(cat "$LAST_ALERT_FILE")"

# Pool healthy → reset alert state
if [[ "$STATE" == "ONLINE" ]]; then
  rm -f "$LAST_ALERT_FILE"
  exit 0
fi

# Suppression window
if (( NOW - LAST_ALERT < SUPPRESS_SECONDS )); then
  exit 0
fi

################################
# ALERT CONTENT
################################

SUBJECT="ZFS ALERT • $POOL_NAME is $STATE on $(host)"

EMAIL_BODY=$(cat <<EOF
ZFS Storage Alert

Host: $(host)
Pool: $POOL_NAME
Time: $(now)
State: $STATE

Faulted / Offline drives:
$FAULTED_DRIVES

Recommended action:
• Verify cabling and disk health
• Replace or online the affected drive
• Monitor with: zpool status $POOL_NAME

— ZFS Health Monitor
EOF
)

DISCORD_MSG="🚨 **ZFS ALERT — Pool \`$POOL_NAME\` is $STATE** 🚨
\`\`\`
$ZFS_STATUS_BLOCK
\`\`\`"

################################
# SEND
################################

send_discord "$DISCORD_MSG" "<@${DISCORD_USER_ID}>"
send_email "$SUBJECT" "$EMAIL_BODY"

echo "$NOW" > "$LAST_ALERT_FILE"