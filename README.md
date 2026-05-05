# Homelab

My personal homelab: what I use every day for media, photos, passwords, and DNS, and where I do most of my learning around Linux, networking, and monitoring.

Running 20+ containerized services with around 97% uptime.

## Hardware

AMD EPYC, 64 GB ECC, redundant PSUs, Debian 13 on bare metal.

## Stack

- **Containers:** Docker, Docker Compoe
- **Networking:** pfSense, VLANs, AdGuard Home (split DNS), WireGuard, Nginx
- **Monitoring:** Prometheus, Grafana, Loki, Alloy, Alertmanager
- **Backups:** 3-2-1 with Duplicati and Backblaze B2
- **Power:** NUT with custom shutdown scripts on power loss

## Services

Jellyfin, Immich, Vaultwarden, Syncthing, Linkwarden, Airsonic, Homepage, IT-Tools, plus the full monitoring stack.

## Custom stuff

- VRChat Prometheus exporter (Python) with its own Grafana dashboard
- NUT scripts for graceful Docker + host shutdown on UPS events
- Discord scripts for system, ZFS, and power alerts
- Immich backup/restore shell scripts

## Layout

```
Projects/
├── Homelab/
│   ├── docker configuration/
│   ├── grafana dashboards/
│   └── system scripts/
└── Projects/vrchat-prometheus-exporter/
```
