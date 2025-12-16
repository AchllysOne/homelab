#!/bin/sh

# Configuration
WEBHOOK_URL=""
ALERT_THRESHOLD=60
ALERT_USER="<>"

# Create temp file
JSON_FILE=$(mktemp)
trap 'rm -f "$JSON_FILE"' EXIT

# Initialize variables
ALERT_NEEDED=false
CPU_TEMPS=""
HDD_TEMPS=""
NVME_TEMPS=""

# Function to compare temperatures
temp_compare() {
    echo "$1 $2" | awk '{exit ($1 >= $2) ? 0 : 1}'
}

# Get CPU temperatures
get_cpu_temps() {
    for sensor in Tctl Tccd3 Tccd5; do
        temp=$(sensors 2>/dev/null | awk -v s="$sensor" '$0 ~ s {gsub(/[^0-9.]/, "", $2); print $2}')
        [ -n "$temp" ] || continue

        if temp_compare "$temp" "$ALERT_THRESHOLD"; then
            ALERT_NEEDED=true
        fi
        CPU_TEMPS="$CPU_TEMPS• $sensor: $temp°C\n"
    done
}

# Get HDD temperatures
get_hdd_temps() {
    DISKS=$(lsblk -d -n -o NAME 2>/dev/null | grep -E '^sd[a-z]')

    for disk in $DISKS; do
        temp=$(sudo smartctl -A "/dev/$disk" 2>/dev/null | \
              awk '/Temperature_Celsius/ {print $10}')

        [ -z "$temp" ] && temp=$(sudo smartctl -A "/dev/$disk" 2>/dev/null | \
                                awk '/Current Drive Temperature/ {print $4}')

        if [ -n "$temp" ]; then
            if temp_compare "$temp" "$ALERT_THRESHOLD"; then
                ALERT_NEEDED=true
            fi
            HDD_TEMPS="$HDD_TEMPS• $disk: $temp°C\n"
        else
            HDD_TEMPS="$HDD_TEMPS• $disk: No data\n"
        fi
    done

    [ -z "$HDD_TEMPS" ] && HDD_TEMPS="• No HDDs detected\n"
}

# Get NVMe temperatures
get_nvme_temps() {
    for nvme in /dev/nvme[0-9]n[0-9]; do
        [ -e "$nvme" ] || continue
        temp=$(sudo smartctl -A "$nvme" 2>/dev/null | awk '/Temperature:/ {print $2}')
        [ -n "$temp" ] || continue

        if temp_compare "$temp" "$ALERT_THRESHOLD"; then
            ALERT_NEEDED=true
        fi
        NVME_TEMPS="$NVME_TEMPS• ${nvme##*/}: $temp°C\n"
    done
}

# Main execution
get_cpu_temps
get_hdd_temps
get_nvme_temps

# Determine embed color
HIGHEST_TEMP=$(printf "%s\n%s\n%s" "$CPU_TEMPS" "$HDD_TEMPS" "$NVME_TEMPS" |
               grep -oE '[0-9]+(\.[0-9]+)?°C' | sed 's/°C//' |
               awk '{if($1>max)max=$1} END{print max}')

COLOR=65280  # Green
if [ -n "$HIGHEST_TEMP" ]; then
    if temp_compare "$HIGHEST_TEMP" "$ALERT_THRESHOLD"; then
        COLOR=16711680  # Red
    elif temp_compare "$HIGHEST_TEMP" "$(($ALERT_THRESHOLD - 10))"; then
        COLOR=16761024  # Yellow
    fi
fi

# Create Discord payload
cat > "$JSON_FILE" <<EOF
{
  "content": "$([ "$ALERT_NEEDED" = true ] && echo "$ALERT_USER - Temperature threshold exceeded!")",
  "embeds": [
    {
      "title": "System Temperature Report",
      "color": $COLOR,
      "fields": [
        {
          "name": "CPU",
          "value": "${CPU_TEMPS:-• No CPU data}",
          "inline": false
        },
        {
          "name": "HDD",
          "value": "${HDD_TEMPS}",
          "inline": false
        },
        {
          "name": "NVMe",
          "value": "${NVME_TEMPS:-• No NVMe data}",
          "inline": false
        }
      ],
      "footer": {
        "text": "Generated on $(date +'%Y-%m-%d %H:%M:%S %Z')"
      }
    }
  ]
}
EOF

# Send to Discord
curl -sS -H "Content-Type: application/json" -X POST -d @"$JSON_FILE" "$WEBHOOK_URL" >/dev/null