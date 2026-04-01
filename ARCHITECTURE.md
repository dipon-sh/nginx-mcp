# Architecture

## Overview

```
┌─────────────────────────────────────────────────────┐
│                    Machine                          │
│                                                     │
│  ┌─────────────┐      ┌──────────────────────────┐  │
│  │ Copilot CLI │      │     Claude Desktop       │  │
│  │             │      │  (+ n8n-mcp also here)   │  │
│  └──────┬──────┘      └────────────┬─────────────┘  │
│         │                          │                │
│         └──────────┬───────────────┘                │
│                    │ HTTP/SSE                       │
│                    ▼                                │
│  ┌─────────────────────────────────────────────┐    │
│  │         Docker Compose                      │    │
│  │                                             │    │
│  │  ┌─────────────────┐  ┌──────────────────┐  │    │
│  │  │   nginx:alpine  │  │  nginx-mcp       │  │    │
│  │  │   :8088 → :80   │  │  python:3.12     │  │    │
│  │  │                 │  │  :8000 (SSE)     │  │    │
│  │  │  serves HTTP    │  │  server.py       │  │    │
│  │  │  rate limiting  │  │  10 MCP tools    │  │    │
│  │  └────────┬────────┘  └────────┬─────────┘  │    │
│  │           │                    │             │    │
│  │           └────────┬───────────┘             │    │
│  │               shared volumes                 │    │
│  │                    │                         │    │
│  │     ┌──────────────┴──────────┐              │    │
│  │     │     ./nginx-configs/    │              │    │
│  │     │       default.conf      │ ←── AI writes│    │
│  │     │       static/health     │     configs  │    │
│  │     │       static/index      │     here     │    │
│  │     └─────────────────────────┘              │    │
│  │     ┌──────────────────────────┐             │    │
│  │     │        ./logs/           │             │    │
│  │     │       access.log         │ ←── persists│    │
│  │     │       error.log          │     on disk │    │
│  │     └──────────────────────────┘             │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

---

## Entry Points

| Port | Service | Purpose |
|------|---------|---------|
| `:8088` | nginx | Serves real HTTP traffic |
| `:8000` | mcp-server | AI clients connect here (SSE) |

---

## MCP Tools

| Tool | Status | Description |
|------|--------|-------------|
| `list_nginx_configs` | ✅ Full | List all config files |
| `read_nginx_config` | ✅ Full | Read a config file |
| `write_nginx_config` | ✅ Full | backup → validate → write pipeline |
| `validate_nginx` | ✅ Full | Run `nginx -t` syntax check |
| `backup_config` | ✅ Full | Timestamped backup before changes |
| `tail_logs` | ✅ Full | Read last N lines of access/error log |
| `list_blocked_ips` | ✅ Full | Scan configs for `deny` rules |
| `block_ip` | ✅ Full | Add `deny <ip>` via safe write pipeline |
| `reload_nginx` | ⚠️ Partial | Returns host command — needs docker.sock to auto-reload |
| `nginx_status` | ⚠️ Partial | ps fallback — needs stub_status location block for full stats |

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
├── docker-compose.yml     ← orchestrates nginx + mcp-server
├── nginx-configs/
│   ├── default.conf       ← rate limiting, log format
│   └── static/            ← static response files
│       ├── health         ← returns "ok"
│       └── index          ← returns "nginx is up and running!"
├── mcp-server/
│   ├── server.py          ← 10 MCP tools, HTTP/SSE transport
│   ├── Dockerfile         ← python:3.12-slim + nginx + procps
│   └── requirements.txt   ← mcp, uvicorn, starlette
└── logs/                  ← persisted access.log + error.log
```

---

## Client Configuration

### Copilot CLI — `~/.copilot/mcp-config.json`
```json
{
  "mcpServers": {
    "nginx-manager": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### Claude Desktop — `~/.config/Claude/claude_desktop_config.json`
```json
{
  "mcpServers": {
    "nginx-manager": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```
