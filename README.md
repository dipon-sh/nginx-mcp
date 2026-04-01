# nginx-mcp

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that lets AI assistants **manage nginx configurations, read logs, and monitor live traffic** — all via HTTP from inside Docker.

## What's Running

```
AI Client (Copilot CLI, Claude, etc.)
        │  HTTP Streamable MCP
        ▼
mcp-server :8000/mcp/     ← 7 MCP tools
        │
        ├── nginx-configs/ (shared volume)
        │       ▼
        │   nginx-vts :8088     ← serves public HTTP traffic
        │   nginx-vts :8080     ← internal VTS metrics (Docker-only)
        │       ▼
        │   Prometheus :9090    ← scrapes metrics every 15s
        │       ▼
        │   Grafana :3000       ← dashboards
        │
        └── logs/ (shared volume)
                access.log / error.log
```

## Quick Start

### 1. Build the custom nginx image (with VTS module)

```bash
docker build -t nginx-vts:latest ./nginx-build/
```

### 2. Start the full stack

```bash
docker compose up -d
```

Verify:
```bash
curl http://localhost:8088/        # → nginx is up and running!
curl http://localhost:8088/health  # → ok
```

### 3. Connect to Copilot CLI

Add to `~/.copilot/mcp-config.json`:

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

## MCP Tools (7)

| Tool | What it does |
|------|-------------|
| `list_nginx_configs` | List all config files |
| `read_nginx_config` | Read a config file |
| `write_nginx_config` | backup → `nginx -t` validate → write (auto-rollback on failure) |
| `validate_nginx` | Run `nginx -t` syntax check |
| `backup_config` | Timestamped backup of a config file |
| `tail_logs` | Last N lines of access or error log |
| `nginx_status` | Live traffic stats from VTS (requests, bytes, connections, response codes) |

## Ports

| Port | Service | Access |
|------|---------|--------|
| `:8088` | nginx public traffic | host + network |
| `:8000` | MCP server | localhost only |
| `:8080` | VTS HTML dashboard | localhost only |
| `:9090` | Prometheus | localhost only |
| `:3000` | Grafana (admin/admin) | localhost only |

## VTS Dashboard

Open **http://localhost:8080/status** for a live HTML traffic dashboard.

Prometheus format available at `http://localhost:8080/status/format/prometheus` (scraped automatically).

## Rate Limiting

| Setting | Value |
|---------|-------|
| Rate | 10 req/s per IP |
| `/health` burst | 5 |
| `/` burst | 20 |
| Exceeded response | `429 Too Many Requests` |

## Logs

Persisted to `./logs/` — survives container restarts.

```bash
tail -f logs/access.log
```

Custom log format includes request time (`rt=`) and rate limit decision (`limit=PASSED` / `limit=REJECTED`).

## Reload nginx after config changes

```bash
docker exec nginx nginx -s reload
```

## Project Structure

```
nginx-mcp/
├── docker-compose.yml          # nginx, mcp-server, prometheus, grafana
├── nginx-build/
│   └── Dockerfile              # multi-stage: compiles VTS .so for nginx 1.29.0
├── nginx-configs/
│   ├── default.conf            # rate limiting, VTS zones, metrics server on :8080
│   └── static/                 # static response files (health, index)
├── monitoring/
│   └── prometheus.yml          # scrape config → nginx:8080/status/format/prometheus
├── mcp-server/
│   ├── server.py               # 7 MCP tools, HTTP Streamable transport
│   ├── Dockerfile
│   └── requirements.txt
└── logs/                       # persisted nginx logs (gitignored)
```

## License

MIT

