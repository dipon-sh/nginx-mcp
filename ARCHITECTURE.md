# Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                          Your Machine                            │
│                                                                  │
│  ┌─────────────┐      ┌──────────────────────────┐              │
│  │ Copilot CLI │      │     Claude Desktop        │              │
│  │             │      │  (+ n8n-mcp also here)    │              │
│  └──────┬──────┘      └────────────┬─────────────┘              │
│         │                          │                             │
│         └──────────┬───────────────┘                             │
│                    │ HTTP Streamable (MCP 2025-11-25)             │
│                    ▼                                             │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    Docker Compose                        │    │
│  │                                                          │    │
│  │  ┌──────────────────┐  ┌──────────────────┐             │    │
│  │  │  nginx-vts:latest│  │   nginx-mcp      │             │    │
│  │  │  :8088 → :80     │  │   python:3.12    │             │    │
│  │  │  (internal :8080)│  │   :8000/mcp/     │             │    │
│  │  │                  │  │   server.py      │             │    │
│  │  │  serves HTTP     │  │   9 MCP tools    │             │    │
│  │  │  rate limiting   │  │                  │             │    │
│  │  │  VTS metrics     │◄─┤  nginx_status ─► │             │    │
│  │  └────────┬─────────┘  └────────┬─────────┘             │    │
│  │           │                     │                        │    │
│  │           └──────────┬──────────┘                        │    │
│  │                shared volumes                            │    │
│  │           ┌──────────┴──────────┐                        │    │
│  │           │   ./nginx-configs/  │ ←── AI writes here     │    │
│  │           │   ./logs/           │ ←── persists on disk   │    │
│  │           └─────────────────────┘                        │    │
│  │                                                          │    │
│  │  ┌──────────────────┐  ┌──────────────────┐             │    │
│  │  │   prometheus     │  │    grafana        │             │    │
│  │  │   :9090          │  │    :3000          │             │    │
│  │  │   scrapes        │  │    dashboards     │             │    │
│  │  │   nginx:8080     │◄─┘    from prom      │             │    │
│  │  └──────────────────┘  └──────────────────┘             │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Entry Points

| Port | Service | Purpose |
|------|---------|---------|
| `:8088` | nginx | Serves real HTTP traffic |
| `:8000/mcp/` | mcp-server | AI clients connect here (HTTP Streamable) |
| `:9090` | prometheus | Metrics storage & query UI |
| `:3000` | grafana | Dashboard visualization (admin/admin) |

> nginx port 8080 is **internal only** (not exposed to host) — only prometheus and mcp-server reach it.

---

## VTS Metrics Flow

```
nginx (port 80) handles requests
        ↓
ngx_http_vts_module tracks per-zone counters in shared memory
        ↓
nginx (port 8080) exposes /status endpoints:
  /status              → HTML dashboard
  /status/format/json  → JSON (used by nginx_status MCP tool)
  /status/format/prometheus → Prometheus format (scraped every 15s)
        ↓
Prometheus stores time-series data
        ↓
Grafana visualizes dashboards
```

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_nginx_configs` | List all config files |
| `read_nginx_config` | Read a config file |
| `write_nginx_config` | backup → validate (nginx -t) → write, auto-rollback on failure |
| `validate_nginx` | Run `nginx -t` syntax check |
| `backup_config` | Timestamped backup of a config file |
| `tail_logs` | Read last N lines of access or error log |
| `list_blocked_ips` | Scan configs for `deny` rules |
| `block_ip` | Add `deny <ip>` via safe write pipeline |
| `nginx_status` | Live traffic stats from VTS (requests, bytes, connections, response codes per zone) |

---

## Data Flow — when AI writes a config

```
You ask Claude / Copilot
        ↓
AI calls write_nginx_config tool
        ↓
mcp-server: backup → nginx -t validate → write file
        ↓
nginx-configs/ folder updated (shared volume)
        ↓
You run: docker exec nginx nginx -s reload
        ↓
nginx picks up new config live
```

---

## Project Structure

```
nginx_mcp/
├── docker-compose.yml     ← nginx, mcp-server, prometheus, grafana
├── nginx-build/
│   └── Dockerfile         ← multi-stage: compiles VTS .so, copies into nginx:alpine
├── nginx-configs/
│   ├── default.conf       ← rate limiting, log format, VTS zones, :8080 status server
│   └── static/            ← static response files
│       ├── health         ← returns "ok"
│       └── index          ← returns "nginx is up and running!"
├── monitoring/
│   └── prometheus.yml     ← scrape config: nginx:8080/status/format/prometheus
├── mcp-server/
│   ├── server.py          ← 9 MCP tools, HTTP Streamable transport
│   ├── Dockerfile         ← python:3.12-slim + nginx + procps
│   └── requirements.txt   ← mcp, uvicorn, starlette
└── logs/                  ← persisted access.log + error.log (gitignored)
```

---

## Client Configuration

### Copilot CLI — `~/.copilot/mcp-config.json`
```json
{
  "mcpServers": {
    "nginx-manager": {
      "type": "http",
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```

### Claude Desktop — `~/.config/Claude/claude_desktop_config.json`
```json
{
  "mcpServers": {
    "nginx-manager": {
      "type": "http",
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```
