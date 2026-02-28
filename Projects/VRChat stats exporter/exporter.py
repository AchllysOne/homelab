#!/usr/bin/env python3
"""
VRChat Prometheus Exporter — Enhanced Edition
Collects rich metrics from the VRChat API with smart rate limiting.

Rate limiting strategy:
  - All requests are gated through a single throttle: REQUEST_DELAY seconds apart.
  - Expensive sub-collections (world lookups, groups, avatars) are capped per cycle
    but their caches grow over time so the cap matters less each run.
  - Offline friends are fetched every OFFLINE_SCRAPE_CYCLES cycles (default: 5)
    since they change slowly — no need to hammer that endpoint every 2 minutes.
"""

import os
import sys
import time
import logging
import base64
import collections
from urllib.parse import quote

import requests
from prometheus_client import (
    start_http_server,
    Gauge,
    Info,
    Counter,
    Enum,
    Histogram,
)

# ─── Configuration ────────────────────────────────────────────────────────────────

EXPORTER_PORT        = int(os.environ.get("EXPORTER_PORT", 9101))
SCRAPE_INTERVAL      = int(os.environ.get("SCRAPE_INTERVAL", 120))    # seconds between cycles
REQUEST_DELAY        = float(os.environ.get("REQUEST_DELAY", 1.5))    # seconds between API calls
MAX_WORLD_LOOKUPS    = int(os.environ.get("MAX_WORLD_LOOKUPS", 40))   # new world IDs resolved per cycle
MAX_AVATAR_LOOKUPS   = int(os.environ.get("MAX_AVATAR_LOOKUPS", 20))  # new avatar IDs resolved per cycle
OFFLINE_SCRAPE_CYCLES = int(os.environ.get("OFFLINE_SCRAPE_CYCLES", 5)) # how often to fetch offline friends

VRCHAT_API_BASE = "https://api.vrchat.cloud/api/1"
AUTH_COOKIE     = os.environ.get("VRCHAT_AUTH_COOKIE", "")
USERNAME        = os.environ.get("VRCHAT_USERNAME", "")
PASSWORD        = os.environ.get("VRCHAT_PASSWORD", "")
USER_AGENT      = os.environ.get("VRCHAT_USER_AGENT", "VRChatMonitor/2.0 contact@example.com")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vrchat-exporter")

# ─── Prometheus Metrics ───────────────────────────────────────────────────────────

# ── Platform-wide ──
ONLINE_USERS = Gauge("vrchat_online_users_total", "Total users currently online on VRChat")
API_HEALTHY  = Gauge("vrchat_api_healthy", "VRChat API health (1=ok, 0=down)")

# ── Current user ──
USER_INFO   = Info("vrchat_current_user", "Authenticated user info")
USER_STATUS = Enum("vrchat_user_status", "Current user status",
                   states=["active", "join me", "ask me", "busy", "offline"])
USER_TRUST_RANK = Gauge("vrchat_user_trust_rank", "Current user trust rank (numeric: 0=visitor..5=legend)")

# ── Friends summary ──
FRIENDS_ONLINE  = Gauge("vrchat_friends_online",  "Friends currently online")
FRIENDS_OFFLINE = Gauge("vrchat_friends_offline", "Friends currently offline")
FRIENDS_ACTIVE  = Gauge("vrchat_friends_active",  "Friends active in a non-private instance")
FRIENDS_TOTAL   = Gauge("vrchat_friends_total",   "Total friends")

# ── Friend breakdowns ──
FRIEND_STATUS_GAUGE   = Gauge("vrchat_friend_by_status",   "Online friends by status",   ["status"])
FRIEND_PLATFORM_GAUGE = Gauge("vrchat_friend_by_platform", "Online friends by platform", ["platform"])
FRIEND_WORLD_GAUGE    = Gauge("vrchat_friend_by_world",    "Online friends by world",    ["world_name", "world_id"])

# ── Per-friend detail (online friends only) ──
FRIEND_DETAIL = Gauge(
    "vrchat_friend_detail",
    "Per-friend detail (1 = online)",
    ["display_name", "status", "platform", "world_name", "instance_type", "avatar_name"],
)

# ── World metrics ──
WORLD_VISITS      = Gauge("vrchat_world_visits",       "Visit count for a cached world",    ["world_id", "world_name", "author_name"])
WORLD_FAVORITES   = Gauge("vrchat_world_favorites",    "Favorite count for a cached world", ["world_id", "world_name"])
WORLD_OCCUPANTS   = Gauge("vrchat_world_occupants",    "Current occupants in a cached world (all instances)", ["world_id", "world_name"])
WORLD_HEAT        = Gauge("vrchat_world_heat",         "Popularity heat for a cached world", ["world_id", "world_name"])
WORLDS_CACHED     = Gauge("vrchat_worlds_cached_total","Total worlds in local cache")

# ── Favorites ──
FAVORITES_TOTAL = Gauge("vrchat_favorites_total", "Favorites by tag/type", ["tag"])


# ── Instances (worlds friends are in) ──
INSTANCE_PLAYER_COUNT = Gauge(
    "vrchat_instance_player_count",
    "Player count in a world instance friends are in",
    ["world_id", "world_name", "instance_id", "instance_type", "region"],
)

# ── API request tracking ──
API_REQUEST_DURATION = Histogram(
    "vrchat_api_request_duration_seconds",
    "Duration of individual VRChat API requests",
    ["endpoint"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)
SCRAPE_ERRORS    = Counter("vrchat_scrape_errors_total",          "Failed scrape attempts",          ["endpoint"])
SCRAPE_DURATION  = Gauge("vrchat_scrape_duration_seconds",        "Duration of last full scrape cycle")
LAST_SCRAPE_TS   = Gauge("vrchat_last_scrape_success_timestamp",  "Unix timestamp of last successful scrape")
SCRAPE_CYCLE_NUM = Gauge("vrchat_scrape_cycle_number",            "Total completed scrape cycles")

# ─── Caches ──────────────────────────────────────────────────────────────────────

_world_cache: dict[str, dict]  = {}   # world_id → {name, visits, favorites, ...}
_avatar_cache: dict[str, dict] = {}   # avatar_id → {name, platform}
_instance_cache: dict[str, dict] = {} # "world_id:instance_id" → {playerCount, region, type}

TRUST_RANK_MAP = {
    "system_legend":    5,
    "system_trust_veteran": 4,
    "system_trust_trusted": 3,
    "system_trust_known": 2,
    "system_trust_basic": 1,
    "system_probable_troll": -1,
    "system_troll": -2,
}

INSTANCE_TYPE_MAP = {
    "public":   "public",
    "hidden":   "friends+",
    "friends":  "friends",
    "private":  "private",
    "group":    "group",
}

# ─── VRChat API Client ────────────────────────────────────────────────────────────

class VRChatClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.authenticated = False
        self._last_request_time = 0.0

    def _throttle(self):
        """Ensure at least REQUEST_DELAY seconds between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()

    def authenticate(self) -> bool:
        if AUTH_COOKIE:
            log.info("Authenticating with auth cookie…")
            self.session.cookies.set("auth", AUTH_COOKIE, domain="api.vrchat.cloud")
            resp = self._get("/auth/user")
            if resp and "id" in resp:
                log.info(f"Authenticated as: {resp.get('displayName')}")
                self.authenticated = True
                return True
            log.error("Auth cookie invalid or expired.")
            return False

        if USERNAME and PASSWORD:
            log.info(f"Authenticating with credentials for: {USERNAME}")
            encoded = base64.b64encode(f"{quote(USERNAME)}:{quote(PASSWORD)}".encode()).decode()
            self.session.headers["Authorization"] = f"Basic {encoded}"
            resp = self._get("/auth/user")
            if resp and "id" in resp:
                if resp.get("requiresTwoFactorAuth"):
                    log.error("2FA required — use an auth cookie instead.")
                    return False
                log.info(f"Authenticated as: {resp.get('displayName')}")
                self.authenticated = True
                self.session.headers.pop("Authorization", None)
                return True
            log.error("Login failed.")
            return False

        log.error("No auth configured! Set VRCHAT_AUTH_COOKIE or VRCHAT_USERNAME+VRCHAT_PASSWORD.")
        return False

    def _get(self, endpoint: str, params: dict | None = None) -> dict | list | int | None:
        self._throttle()
        url = f"{VRCHAT_API_BASE}{endpoint}"
        t0 = time.time()
        try:
            resp = self.session.get(url, params=params, timeout=30)
            duration = time.time() - t0
            # Record histogram using a cleaned-up label (strip dynamic IDs)
            label = endpoint.split("/")[1] if "/" in endpoint else endpoint
            API_REQUEST_DURATION.labels(endpoint=label).observe(duration)

            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                log.warning(f"401 on {endpoint} — session expired")
                self.authenticated = False
            elif resp.status_code == 429:
                log.warning(f"429 rate-limited on {endpoint} — sleeping 10s")
                time.sleep(10)
            else:
                log.warning(f"HTTP {resp.status_code} on {endpoint}: {resp.text[:200]}")
        except requests.RequestException as e:
            log.error(f"Request error on {endpoint}: {e}")
        return None


# ─── Helpers ──────────────────────────────────────────────────────────────────────

def _parse_instance_type(location: str) -> str:
    """Extract human-readable instance type from a location string."""
    if not location or location in ("private", "offline", "traveling"):
        return location or "unknown"
    # location format: wrld_xxx:12345~instanceType(...)
    if "~" not in location:
        return "public"
    for key, label in INSTANCE_TYPE_MAP.items():
        if f"~{key}" in location:
            return label
    return "unknown"


def _parse_region(location: str) -> str:
    for region in ("us", "use", "eu", "jp", "aus"):
        if f"~region({region})" in location:
            return region
    return "us"  # default


def _trust_rank_value(tags: list[str]) -> int:
    for tag in reversed(tags):
        if tag in TRUST_RANK_MAP:
            return TRUST_RANK_MAP[tag]
    return 0  # visitor


# ─── Collection Functions ─────────────────────────────────────────────────────────

def collect_platform(client: VRChatClient):
    """Online user count + API health (single endpoint, no auth required)."""
    data = client._get("/visits")
    if data is not None:
        API_HEALTHY.set(1)
        ONLINE_USERS.set(data)
    else:
        API_HEALTHY.set(0)
        SCRAPE_ERRORS.labels(endpoint="visits").inc()


def collect_current_user(client: VRChatClient):
    """Current user info, status, trust rank, and friend ID lists."""
    data = client._get("/auth/user")
    if not data or "id" not in data:
        SCRAPE_ERRORS.labels(endpoint="auth_user").inc()
        return None

    USER_INFO.info({
        "display_name":       str(data.get("displayName", "")),
        "user_id":            str(data.get("id", "")),
        "username":           str(data.get("username", "")),
        "status_description": str(data.get("statusDescription", "")),
        "last_platform":      str(data.get("last_platform", "")),
        "developer_type":     str(data.get("developerType", "none")),
        "home_location":      str(data.get("homeLocation", "")),
    })

    status = data.get("status", "offline")
    if status in ["active", "join me", "ask me", "busy", "offline"]:
        USER_STATUS.state(status)

    tags = data.get("tags", [])
    USER_TRUST_RANK.set(_trust_rank_value(tags))

    online_friends  = data.get("onlineFriends", [])
    offline_friends = data.get("offlineFriends", [])
    active_friends  = data.get("activeFriends", [])
    all_friends     = data.get("friends", [])

    FRIENDS_ONLINE.set(len(online_friends))
    FRIENDS_OFFLINE.set(len(offline_friends))
    FRIENDS_ACTIVE.set(len(active_friends))
    FRIENDS_TOTAL.set(len(all_friends))

    return data


def collect_friends_online(client: VRChatClient):
    """
    Fetch all online friends (paginated), update status/platform/world breakdowns,
    and per-friend detail metrics.
    """
    all_friends = []
    offset, page_size = 0, 100

    while True:
        page = client._get("/auth/user/friends", params={
            "offset": offset, "n": page_size, "offline": "false",
        })
        if not page or not isinstance(page, list):
            break
        all_friends.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    # ── Aggregate counts ──
    status_counts: dict[str, int]   = {}
    platform_counts: dict[str, int] = {}
    world_counts: dict[str, int]    = {}  # world_id → count

    FRIEND_DETAIL._metrics.clear()

    # ── Resolve unknown world IDs ──
    unknown_world_ids = set()
    for f in all_friends:
        loc = f.get("location", "")
        if loc and loc not in ("", "private", "offline", "traveling"):
            wid = loc.split(":")[0]
            if wid not in _world_cache:
                unknown_world_ids.add(wid)

    resolved = 0
    for wid in list(unknown_world_ids):
        if resolved >= MAX_WORLD_LOOKUPS:
            break
        wdata = client._get(f"/worlds/{wid}")
        if wdata and "name" in wdata:
            _world_cache[wid] = {
                "name":        wdata.get("name", wid),
                "authorName":  wdata.get("authorName", ""),
                "visits":      wdata.get("visits", 0),
                "favorites":   wdata.get("favorites", 0),
                "occupants":   wdata.get("occupants", 0),
                "heat":        wdata.get("heat", 0),
            }
        else:
            _world_cache[wid] = {"name": wid, "authorName": "", "visits": 0, "favorites": 0, "occupants": 0, "heat": 0}
        resolved += 1

    # ── Resolve unknown instances ──
    unknown_instances: dict[str, tuple[str, str]] = {}  # key → (world_id, instance_id)
    for f in all_friends:
        loc = f.get("location", "")
        if loc and ":" in loc and loc not in ("private", "offline", "traveling"):
            parts = loc.split(":")
            wid, iid = parts[0], parts[1].split("~")[0]
            key = f"{wid}:{iid}"
            if key not in _instance_cache:
                unknown_instances[key] = (wid, iid)

    for key, (wid, iid) in list(unknown_instances.items())[:20]:
        inst = client._get(f"/instances/{wid}:{iid}")
        if inst:
            _instance_cache[key] = {
                "playerCount":  inst.get("n_users", inst.get("userCount", 0)),
                "region":       inst.get("region", "us"),
                "type":         inst.get("type", "public"),
                "world_name":   _world_cache.get(wid, {}).get("name", wid),
                "world_id":     wid,
                "instance_id":  iid,
            }

    # ── Per-friend metrics ──
    for f in all_friends:
        status   = f.get("status", "offline")
        platform = f.get("last_platform", "unknown")
        name     = f.get("displayName", "unknown")
        avatar   = f.get("currentAvatarThumbnailImageUrl", "")  # just for existence; name below
        avatar_name = f.get("currentAvatarName", "unknown") or "unknown"
        loc      = f.get("location", "")

        status_counts[status]     = status_counts.get(status, 0) + 1
        platform_counts[platform] = platform_counts.get(platform, 0) + 1

        if loc and loc not in ("", "private", "offline", "traveling") and ":" in loc:
            wid = loc.split(":")[0]
            world_counts[wid] = world_counts.get(wid, 0) + 1
            world_name    = _world_cache.get(wid, {}).get("name", wid)
            instance_type = _parse_instance_type(loc)
        else:
            world_name    = loc or "unknown"
            instance_type = loc or "unknown"

        FRIEND_DETAIL.labels(
            display_name=name,
            status=status,
            platform=platform,
            world_name=world_name,
            instance_type=instance_type,
            avatar_name=avatar_name,
        ).set(1)

    for status, count in status_counts.items():
        FRIEND_STATUS_GAUGE.labels(status=status).set(count)
    for platform, count in platform_counts.items():
        FRIEND_PLATFORM_GAUGE.labels(platform=platform).set(count)

    # World occupancy by friends
    FRIEND_WORLD_GAUGE._metrics.clear()
    for wid, count in world_counts.items():
        wname = _world_cache.get(wid, {}).get("name", wid)
        FRIEND_WORLD_GAUGE.labels(world_name=wname, world_id=wid).set(count)

    log.info(f"Online friends: {len(all_friends)}, worlds cached: {len(_world_cache)}, instances cached: {len(_instance_cache)}")


def collect_friends_offline(client: VRChatClient):
    """Fetch offline friends — runs less frequently (controlled by cycle counter)."""
    all_offline = []
    offset, page_size = 0, 100
    while True:
        page = client._get("/auth/user/friends", params={
            "offset": offset, "n": page_size, "offline": "true",
        })
        if not page or not isinstance(page, list):
            break
        all_offline.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    FRIENDS_OFFLINE.set(len(all_offline))
    log.info(f"Offline friends: {len(all_offline)}")


def collect_world_metrics(client: VRChatClient):
    """
    Refresh stats (visits, favorites, occupants, heat) for cached worlds.
    Refreshes up to MAX_WORLD_LOOKUPS worlds per cycle, rotating through the cache.
    """
    if not _world_cache:
        return

    # Rotate: always refresh the stalest entries
    world_ids = list(_world_cache.keys())

    refreshed = 0
    for wid in world_ids:
        if refreshed >= MAX_WORLD_LOOKUPS:
            break
        wdata = client._get(f"/worlds/{wid}")
        if wdata and "name" in wdata:
            _world_cache[wid].update({
                "name":       wdata.get("name", wid),
                "authorName": wdata.get("authorName", ""),
                "visits":     wdata.get("visits", 0),
                "favorites":  wdata.get("favorites", 0),
                "occupants":  wdata.get("occupants", 0),
                "heat":       wdata.get("heat", 0),
            })
        refreshed += 1

    # Push all cached worlds to Prometheus
    for wid, info in _world_cache.items():
        wname  = info.get("name", wid)
        author = info.get("authorName", "")
        WORLD_VISITS.labels(world_id=wid, world_name=wname, author_name=author).set(info.get("visits", 0))
        WORLD_FAVORITES.labels(world_id=wid, world_name=wname).set(info.get("favorites", 0))
        WORLD_OCCUPANTS.labels(world_id=wid, world_name=wname).set(info.get("occupants", 0))
        WORLD_HEAT.labels(world_id=wid, world_name=wname).set(info.get("heat", 0))

    WORLDS_CACHED.set(len(_world_cache))
    log.info(f"World metrics pushed for {len(_world_cache)} cached worlds ({refreshed} refreshed)")


def collect_instance_metrics(client: VRChatClient):
    """Update player counts for cached instances (friends' current worlds)."""
    INSTANCE_PLAYER_COUNT._metrics.clear()

    for key, info in _instance_cache.items():
        wid  = info.get("world_id", "")
        iid  = info.get("instance_id", "")
        # Re-fetch to get live player count
        inst = client._get(f"/instances/{wid}:{iid}")
        if inst:
            info["playerCount"] = inst.get("n_users", inst.get("userCount", info.get("playerCount", 0)))
            info["region"]      = inst.get("region", info.get("region", "us"))
            info["type"]        = inst.get("type", info.get("type", "public"))

        wname = _world_cache.get(wid, {}).get("name", wid)
        INSTANCE_PLAYER_COUNT.labels(
            world_id=wid,
            world_name=wname,
            instance_id=iid,
            instance_type=info.get("type", "unknown"),
            region=info.get("region", "us"),
        ).set(info.get("playerCount", 0))


def collect_favorites(client: VRChatClient):
    """Fetch favorites (worlds, avatars, friends) by tag."""
    # VRChat returns favorites in pages; we just need the tag breakdown
    all_favs: list[dict] = []
    for ftype in ("world", "avatar", "friend"):
        offset, page_size = 0, 100
        while True:
            page = client._get("/favorites", params={
                "type": ftype, "offset": offset, "n": page_size,
            })
            if not page or not isinstance(page, list):
                break
            all_favs.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

    tag_counts: dict[str, int] = {}
    for fav in all_favs:
        tag = fav.get("tags", ["unknown"])[0] if fav.get("tags") else fav.get("type", "unknown")
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    FAVORITES_TOTAL._metrics.clear()
    for tag, count in tag_counts.items():
        FAVORITES_TOTAL.labels(tag=tag).set(count)

    log.info(f"Favorites: {len(all_favs)} total across world/avatar/friend")




# ─── Main Scrape Loop ─────────────────────────────────────────────────────────────

def scrape_all(client: VRChatClient, cycle: int):
    start = time.time()
    log.info(f"── Scrape cycle #{cycle} starting ──")

    # Always collected every cycle
    collect_platform(client)
    collect_current_user(client)
    collect_friends_online(client)

    # World stats for worlds we know about (friends' worlds always fresh)
    collect_world_metrics(client)

    # Instance player counts for currently-active instances
    if _instance_cache:
        collect_instance_metrics(client)

    # Offline friends — slower cadence (they don't change often)
    if cycle % OFFLINE_SCRAPE_CYCLES == 0:
        log.info("Collecting offline friends (cadenced)…")
        collect_friends_offline(client)

    # Favorites — every 3 cycles
    if cycle % 3 == 0:
        collect_favorites(client)

    duration = time.time() - start
    SCRAPE_DURATION.set(duration)
    LAST_SCRAPE_TS.set(time.time())
    SCRAPE_CYCLE_NUM.set(cycle)
    log.info(f"── Cycle #{cycle} done in {duration:.1f}s ──")


def main():
    log.info("═══ VRChat Prometheus Exporter v2 ═══")
    log.info(f"Port: {EXPORTER_PORT}  |  Interval: {SCRAPE_INTERVAL}s  |  Request delay: {REQUEST_DELAY}s")

    start_http_server(EXPORTER_PORT)
    log.info(f"Metrics available at :{EXPORTER_PORT}/metrics")

    client = VRChatClient()
    if not client.authenticate():
        log.error("Authentication failed. Exiting.")
        sys.exit(1)

    cycle = 0
    while True:
        try:
            if not client.authenticated:
                log.warning("Session expired — re-authenticating…")
                if not client.authenticate():
                    log.error("Re-auth failed. Retrying in 60s…")
                    time.sleep(60)
                    continue
            cycle += 1
            scrape_all(client, cycle)
        except Exception as e:
            log.exception(f"Unhandled error in scrape loop: {e}")
            SCRAPE_ERRORS.labels(endpoint="main_loop").inc()

        log.info(f"Sleeping {SCRAPE_INTERVAL}s…")
        time.sleep(SCRAPE_INTERVAL)


if __name__ == "__main__":
    main()
