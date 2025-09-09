#!/bin/bash

# 1. Run zpool scrub
echo "Running zpool scrub on achz3..."
sudo zpool scrub achz3

# Check if the scrub command was successful
if [ $? -eq 0 ]; then
    # 2. Create a webhook with embed message (using curl as an example)
    # Replace WEBHOOK_URL with your actual webhook URL
    WEBHOOK_URL=""

    curl -H "Content-Type: application/json" -X POST -d '{
        "embeds": [{
            "title": "ZPool Scrub Notification",
            "description": "Weekly Scrub has commited",
            "color": 5814783,
            "timestamp": "'$(date -u +'%Y-%m-%dT%H:%M:%SZ')'"
        }]
    }' $WEBHOOK_URL

    echo -e "\nWebhook sent successfully!"
else
    echo "Scrub command failed. Webhook not sent."
    exit 1
fi