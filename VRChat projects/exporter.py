#!/usr/bin/env python3
"""
VRChat Prometheus Exporter
Polls VRChat API endpoints and exposes metrics for Prometheus scraping.

Respects VRChat rate limits: no more than 1 request per 60 seconds.
"""

import os
import sys
import time
import logging
import base64
import json
from urllib.parse import quote

import requests
from prometheus_client import (
    start_http_server,
    Gauge,
    Info,
    Counter,
    Enum,
)

# ─── Configuration ──────────────────────────────────────────────────────────────

EXPORTER_PORT = int(os.environ.get("EXPORTER_PORT", 9100))
SCRAPE_INTERVAL = int(os.environ.get("SCRAPE_INTERVAL", 120))  # seconds
VRCHAT_API_BASE = "https://api.vrchat.cloud/api/1"

AUTH_COOKIE = os.environ.get("VRCHAT_AUTH_COOKIE", "")
USERNAME = os.environ.get("VRCHAT_USERNAME", "")
PASSWORD = os.environ.get("VRCHAT_PASSWORD", "")
USER_AGENT = os.environ.get("VRCHAT_USER_AGENT", "VRChatMonitor/1.0 contact@example.com")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("vrchat-exporter")

# ─── Prometheus Metrics ─────────────────────────────────────────────────────────

# Platform-wide
ONLINE_USERS = Gauge(
    "vrchat_online_users_total",
    "Total number of users currently online on VRChat",
)
API_HEALTHY = Gauge(
    "vrchat_api_healthy",
    "Whether the VRChat API health check passes (1=ok, 0=down)",
)

# Current user
USER_INFO = Info(
    "vrchat_current_user",
    "Current authenticated user info",
)
USER_STATUS = Enum(
    "vrchat_user_status",
    "Current user status",
    states=["active", "join me", "ask me", "busy", "offline"],
)

# Friends
FRIENDS_ONLINE = Gauge(
    "vrchat_friends_online",
    "Number of friends currently online",
)
FRIENDS_OFFLINE = Gauge(
    "vrchat_friends_offline",
    "Number of friends currently offline",
)
FRIENDS_ACTIVE = Gauge(
    "vrchat_friends_active",
    "Number of friends currently active (in non-private instance)",
)
FRIENDS_TOTAL = Gauge(
    "vrchat_friends_total",
    "Total number of friends",
)

# Friend status breakdown
FRIEND_STATUS_GAUGE = Gauge(
    "vrchat_friend_by_status",
    "Number of friends by their status",
    ["status"],
)
FRIEND_PLATFORM_GAUGE = Gauge(
    "vrchat_friend_by_platform",
    "Number of online friends by platform",
    ["platform"],
)

# Notifications
NOTIFICATIONS_TOTAL = Gauge(
    "vrchat_notifications_total",
    "Total unread notifications",
)
NOTIFICATIONS_BY_TYPE = Gauge(
    "vrchat_notifications_by_type",
    "Notifications broken down by type",
    ["type"],
)

# Favorites
FAVORITES_TOTAL = Gauge(
    "vrchat_favorites_total",
    "Total favorites",
    ["type"],
)

# Scrape health
SCRAPE_ERRORS = Counter(
    "vrchat_scrape_errors_total",
    "Total number of failed scrape attempts",
    ["endpoint"],
)
SCRAPE_DURATION = Gauge(
    "vrchat_scrape_duration_seconds",
    "Duration of the last full scrape cycle in seconds",
)
LAST_SCRAPE_SUCCESS = Gauge(
    "vrchat_last_scrape_success_timestamp",
    "Unix timestamp of last successful scrape",
)


# ─── VRChat API Client ──────────────────────────────────────────────────────────

class VRChatClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        self.authenticated = False

    def authenticate(self):
        """Authenticate with VRChat API using cookie or credentials."""
        if AUTH_COOKIE:
            log.info("Authenticating with auth cookie...")
            self.session.cookies.set("auth", AUTH_COOKIE, domain="api.vrchat.cloud")
            # Verify the cookie works
            resp = self._get("/auth/user")
            if resp and "id" in resp:
                log.info(f"Authenticated as: {resp.get('displayName', 'unknown')}")
                self.authenticated = True
                return True
            else:
                log.error("Auth cookie is invalid or expired.")
                return False

        elif USERNAME and PASSWORD:
            log.info(f"Authenticating with credentials for: {USERNAME}")
            encoded = base64.b64encode(
                f"{quote(USERNAME)}:{quote(PASSWORD)}".encode()
            ).decode()
            self.session.headers["Authorization"] = f"Basic {encoded}"
            resp = self._get("/auth/user")
            if resp and "id" in resp:
                log.info(f"Authenticated as: {resp.get('displayName', 'unknown')}")
                # Check if 2FA is required
                if resp.get("requiresTwoFactorAuth"):
                    log.error(
                        "2FA is required. Please use an auth cookie instead, "
                        "or disable 2FA temporarily."
                    )
                    return False
                self.authenticated = True
                # Remove basic auth header, rely on cookie from now on
                self.session.headers.pop("Authorization", None)
                return True
            else:
                log.error("Login failed with provided credentials.")
                return False
        else:
            log.error(
                "No authentication configured! Set VRCHAT_AUTH_COOKIE or "
                "VRCHAT_USERNAME + VRCHAT_PASSWORD in .env"
            )
            return False

    def _get(self, endpoint, params=None):
        """Make a GET request to VRChat API with error handling."""
        url = f"{VRCHAT_API_BASE}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                log.warning(f"401 Unauthorized on {endpoint} — session may have expired")
                self.authenticated = False
                return None
            elif resp.status_code == 429:
                log.warning(f"429 Rate limited on {endpoint} — backing off")
                return None
            else:
                log.warning(f"HTTP {resp.status_code} on {endpoint}: {resp.text[:200]}")
                return None
        except requests.RequestException as e:
            log.error(f"Request error on {endpoint}: {e}")
            return None


# ─── Metric Collection ──────────────────────────────────────────────────────────

def collect_api_health(client: VRChatClient):
    """Check VRChat API health endpoint."""
    try:
        data = client._get("/health")
        if data and data.get("ok"):
            API_HEALTHY.set(1)
        else:
            API_HEALTHY.set(0)
    except Exception:
        SCRAPE_ERRORS.labels(endpoint="health").inc()
        API_HEALTHY.set(0)


def collect_online_users(client: VRChatClient):
    """Get total online user count."""
    try:
        data = client._get("/visits")
        if data is not None:
            ONLINE_USERS.set(data)
    except Exception:
        SCRAPE_ERRORS.labels(endpoint="visits").inc()


def collect_current_user(client: VRChatClient):
    """Collect current user info and friend counts."""
    try:
        data = client._get("/auth/user")
        if not data or "id" not in data:
            SCRAPE_ERRORS.labels(endpoint="auth_user").inc()
            return

        # User info
        USER_INFO.info({
            "display_name": data.get("displayName", ""),
            "user_id": data.get("id", ""),
            "status_description": data.get("statusDescription", ""),
            "last_platform": data.get("last_platform", ""),
            "developer_type": data.get("developerType", "none"),
        })

        # Status
        status = data.get("status", "offline")
        if status in ["active", "join me", "ask me", "busy", "offline"]:
            USER_STATUS.state(status)

        # Friend counts from the user object
        online_friends = data.get("onlineFriends", [])
        offline_friends = data.get("offlineFriends", [])
        active_friends = data.get("activeFriends", [])
        all_friends = data.get("friends", [])

        FRIENDS_ONLINE.set(len(online_friends))
        FRIENDS_OFFLINE.set(len(offline_friends))
        FRIENDS_ACTIVE.set(len(active_friends))
        FRIENDS_TOTAL.set(len(all_friends))

    except Exception as e:
        log.error(f"Error collecting current user: {e}")
        SCRAPE_ERRORS.labels(endpoint="auth_user").inc()


def collect_friends_detail(client: VRChatClient):
    """Collect detailed friend info (status and platform breakdown).
    Paginated: fetches up to 500 friends in batches of 100.
    """
    try:
        status_counts = {}
        platform_counts = {}
        offset = 0
        n = 100

        while True:
            # Respect rate limit — small delay between pages
            if offset > 0:
                time.sleep(2)

            data = client._get("/auth/user/friends", params={
                "offset": offset,
                "n": n,
                "offline": "false",
            })

            if not data or not isinstance(data, list):
                break

            for friend in data:
                status = friend.get("status", "offline")
                status_counts[status] = status_counts.get(status, 0) + 1

                platform = friend.get("last_platform", "unknown")
                platform_counts[platform] = platform_counts.get(platform, 0) + 1

            if len(data) < n:
                break
            offset += n

        # Set gauges
        for status, count in status_counts.items():
            FRIEND_STATUS_GAUGE.labels(status=status).set(count)
        for platform, count in platform_counts.items():
            FRIEND_PLATFORM_GAUGE.labels(platform=platform).set(count)

    except Exception as e:
        log.error(f"Error collecting friends detail: {e}")
        SCRAPE_ERRORS.labels(endpoint="friends").inc()


def collect_notifications(client: VRChatClient):
    """Collect notification counts."""
    try:
        data = client._get("/auth/user/notifications", params={
            "type": "all",
            "hidden": "false",
        })
        if not data or not isinstance(data, list):
            NOTIFICATIONS_TOTAL.set(0)
            return

        NOTIFICATIONS_TOTAL.set(len(data))

        type_counts = {}
        for notif in data:
            ntype = notif.get("type", "unknown")
            type_counts[ntype] = type_counts.get(ntype, 0) + 1

        for ntype, count in type_counts.items():
            NOTIFICATIONS_BY_TYPE.labels(type=ntype).set(count)

    except Exception as e:
        log.error(f"Error collecting notifications: {e}")
        SCRAPE_ERRORS.labels(endpoint="notifications").inc()


# ─── Main Loop ──────────────────────────────────────────────────────────────────

def scrape_all(client: VRChatClient):
    """Run one full scrape cycle, spacing requests to respect rate limits."""
    start = time.time()

    log.info("Starting scrape cycle...")

    # 1) Health check (no auth needed)
    collect_api_health(client)
    time.sleep(2)

    # 2) Online users (no auth needed)
    collect_online_users(client)
    time.sleep(2)

    # 3) Current user + friend counts
    collect_current_user(client)
    time.sleep(2)

    # 4) Friends detail
    collect_friends_detail(client)
    time.sleep(2)

    # 5) Notifications
    collect_notifications(client)

    duration = time.time() - start
    SCRAPE_DURATION.set(duration)
    LAST_SCRAPE_SUCCESS.set(time.time())
    log.info(f"Scrape cycle completed in {duration:.1f}s")


def main():
    log.info(f"═══ VRChat Prometheus Exporter ═══")
    log.info(f"Exporter port: {EXPORTER_PORT}")
    log.info(f"Scrape interval: {SCRAPE_INTERVAL}s")

    # Start Prometheus HTTP server
    start_http_server(EXPORTER_PORT)
    log.info(f"Prometheus metrics server started on :{EXPORTER_PORT}/metrics")

    # Authenticate
    client = VRChatClient()
    if not client.authenticate():
        log.error("Authentication failed. Exiting.")
        sys.exit(1)

    # Main loop
    while True:
        try:
            if not client.authenticated:
                log.warning("Re-authenticating...")
                if not client.authenticate():
                    log.error("Re-authentication failed. Retrying in 60s...")
                    time.sleep(60)
                    continue

            scrape_all(client)
        except Exception as e:
            log.error(f"Unhandled error in scrape loop: {e}")
            SCRAPE_ERRORS.labels(endpoint="main_loop").inc()

        log.info(f"Sleeping {SCRAPE_INTERVAL}s until next scrape...")
        time.sleep(SCRAPE_INTERVAL)


if __name__ == "__main__":
    main()
