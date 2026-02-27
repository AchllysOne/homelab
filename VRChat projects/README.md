# VRChat Monitoring Stack

**Prometheus + Grafana monitoring for VRChat API data via Docker Compose.**

Polls the VRChat API, exposes metrics as Prometheus gauges/counters, and visualizes everything in a pre-built Grafana dashboard.

> ⚠️ **Disclaimer**: VRChat's API is not officially documented or supported. This project uses the community-documented API. Do not exceed rate limits (1 request per 60 seconds). Abuse may result in account termination. Use responsibly per VRChat's Terms of Service.

---

## Architecture

```
┌──────────────┐  scrape  ┌─────────────┐  query  ┌──────────┐
│  VRChat API  │◄─────────│  Exporter   │◄────────│Prometheus│◄─────┐
│  (REST)      │          │  :9100      │         │  :9090   │      │
└──────────────┘          └─────────────┘         └──────────┘      │
                                                       ▲            │
                                                       │ datasource │
                                                  ┌────┴─────┐     │
                                                  │  Grafana  │─────┘
                                                  │  :3000    │
                                                  └───────────┘
```

## Metrics Collected

| Metric | Type | Description |
|--------|------|-------------|
| `vrchat_api_healthy` | Gauge | API health (1=ok, 0=down) |
| `vrchat_online_users_total` | Gauge | Total VRChat users online |
| `vrchat_friends_online` | Gauge | Friends currently online |
| `vrchat_friends_active` | Gauge | Friends in non-private instances |
| `vrchat_friends_offline` | Gauge | Friends currently offline |
| `vrchat_friends_total` | Gauge | Total friend count |
| `vrchat_friend_by_status{status}` | Gauge | Friends by status (active, join me, ask me, busy) |
| `vrchat_friend_by_platform{platform}` | Gauge | Friends by platform (PC, Quest, etc.) |
| `vrchat_notifications_total` | Gauge | Pending notification count |
| `vrchat_notifications_by_type{type}` | Gauge | Notifications by type |
| `vrchat_user_status` | Enum | Your current status |
| `vrchat_current_user_info` | Info | Display name, user ID, platform |
| `vrchat_scrape_duration_seconds` | Gauge | Scrape cycle timing |
| `vrchat_scrape_errors_total{endpoint}` | Counter | Error tracking per endpoint |

## Quick Start

### 1. Prerequisites

```bash
# Debian 13 / Ubuntu
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # log out/in after this
```

### 2. Clone & Configure

```bash
cd vrchat-monitoring

# Edit .env with your VRChat credentials
cp .env .env.backup
nano .env
```

**Getting your auth cookie (recommended method):**

1. Go to https://vrchat.com and log in
2. Open browser DevTools (F12) → Application → Cookies
3. Copy the value of the `auth` cookie
4. Paste it as `VRCHAT_AUTH_COOKIE` in `.env`

### 3. Launch

```bash
docker compose up -d
```

### 4. Access

| Service | URL | Credentials |
|---------|-----|-------------|
| **Grafana** | http://localhost:3000 | admin / admin |
| **Prometheus** | http://localhost:9090 | — |
| **Exporter** | http://localhost:9100/metrics | — |

The VRChat dashboard is auto-provisioned — find it under **Dashboards → VRChat → VRChat Monitoring**.

## Dashboard Panels

- **Top row**: Stat panels — API status, online users, friends online/active/total, notifications
- **Middle row**: Time series — online users over time, friends over time
- **Bottom row**: Pie charts (friends by status & platform), bar gauge (notifications), scrape health

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VRCHAT_AUTH_COOKIE` | — | Auth cookie (preferred) |
| `VRCHAT_USERNAME` | — | Username (fallback) |
| `VRCHAT_PASSWORD` | — | Password (fallback) |
| `VRCHAT_USER_AGENT` | `VRChatMonitor/1.0` | Required per VRChat ToS |
| `SCRAPE_INTERVAL` | `120` | Seconds between API poll cycles |
| `EXPORTER_PORT` | `9100` | Prometheus exporter port |

### Rate Limits

VRChat enforces a rate limit of **1 request per 60 seconds**. The exporter spaces its API calls with 2-second pauses between endpoints and runs full cycles every 120 seconds by default. Do not lower `SCRAPE_INTERVAL` below 60.

## Operations

```bash
# View logs
docker compose logs -f vrchat-exporter

# Restart after config change
docker compose restart vrchat-exporter

# Stop everything
docker compose down

# Stop and delete all data
docker compose down -v

# Rebuild exporter after code changes
docker compose up -d --build vrchat-exporter
```

## Troubleshooting

- **401 errors in logs** → Auth cookie expired. Get a new one from the browser.
- **429 rate limited** → Increase `SCRAPE_INTERVAL` in docker-compose.yml.
- **No data in Grafana** → Check Prometheus targets at http://localhost:9090/targets — the `vrchat-exporter` target should be UP.
- **Exporter won't start** → Check `.env` file has valid credentials and `VRCHAT_USER_AGENT` is set.

## License

MIT — This project is not affiliated with or endorsed by VRChat Inc.
