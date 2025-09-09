#!/bin/bash

# Set PATH to ensure commands are found
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Log file path
LOGFILE="/var/log/pwrstatd-powerfail.log"

# Redirect all output to the log file
exec > >(tee -a $LOGFILE) 2>&1

# Capture the start time of the script
start_time=$(date +%s)

# Discord Webhook URL and User ID for notifications
WEBHOOK_URL=""
USER_ID=""

# Function to send shutdown warning
send_shutdown_warning() {
    echo "Sending Discord notification: Warning: The system is shutting down."
    local payload="{\"content\": \"<@${USER_ID}> :red_circle: **Warning: The system is shutting down soon.**\"}"
    curl -H "Content-Type: application/json" -X POST -d "$payload" "$WEBHOOK_URL"
}

# Function to send general shutdown info
send_shutdown_info() {
    local message="$1"
    echo "Sending Discord notification: $message"
    message=$(echo "$message" | sed 's/"/\\"/g' | sed 's/\n/\\n/g' | sed 's/\r//g')
    local payload="{\"content\": \"$message\"}"
    curl -H "Content-Type: application/json" -X POST -d "$payload" "$WEBHOOK_URL"
}

# Function to send shutdown info with container list
send_shutdown_info_C() {
    local message="$1"
    local running_containers="$2"
    running_containers=${running_containers//$'\n'/\\n}
    message=$(echo "$message" | sed 's/"/\\"/g' | sed 's/\n/\\n/g' | sed 's/\r//g')
    message+="\n\`\`\`\n${running_containers}\n\`\`\`"
    echo "Sending Discord notification: $message"
    local payload="{\"content\": \"$message\"}"
    curl -H "Content-Type: application/json" -X POST -d "$payload" "$WEBHOOK_URL"
}

# Function to stop Docker containers
stop_docker_containers() {
    # Get running containers
    local running_containers=$(sudo docker ps --format "{{.Names}}" | sort)

    if [ -n "$running_containers" ]; then
        echo "Stopping the following Docker containers:"
        echo "$running_containers"

        # Send notification with container list
        send_shutdown_info_C ":card_box: **Stopping the following Docker containers:**" "$running_containers"

        # Stop all containers
        sudo docker stop $(sudo docker ps -q)

        # Check for containers still running
        local new_running_containers=$(sudo docker ps --format "{{.Names}}")
        local stopped_containers=$(comm -23 <(echo "$running_containers") <(echo "$new_running_containers" | sort))

        # Send stop confirmation
        if [ -n "$stopped_containers" ]; then
            send_shutdown_info_C ":satellite_orbital: **Docker containers have been stopped:**" "$stopped_containers"
        else
            send_shutdown_info ":warning: **No containers were successfully stopped.**"
        fi
        echo "Docker containers have been stopped."
    else
        echo "No running Docker containers to stop."
        send_shutdown_info ":rotating_light:***No running Docker containers to stop.***"
    fi
}

# Main script execution
echo "Starting shutdown sequence..."
send_shutdown_warning

# Stop Docker containers
echo "Initiating Docker container shutdown..."
stop_docker_containers

# Capture the end time of the script and calculate execution time
echo "Waiting 20 seconds to allow containers and services to finalize..."
sleep 20

# Calculate and report execution time
end_time=$(date +%s)
execution_time=$((end_time - start_time))
echo "Shutdown sequence completed in $execution_time seconds."
send_shutdown_info ":stopwatch: Shutdown sequence completed in *$execution_time seconds*.