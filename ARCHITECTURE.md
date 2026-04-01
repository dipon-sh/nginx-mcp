# Architecture

## Overview

```
┌─────────────────────────────────────────────────────┐
│                    Your Machine                      │
│                                                      │
│  ┌─────────────┐      ┌──────────────────────────┐  │
│  │ Copilot CLI │      │     Claude Desktop        │  │
│  │             │      │  (+ n8n-mcp also here)    │  │
│  └──────┬──────┘      └────────────┬─────────────┘  │
│         │                          │                 │
│         └──────────┬───────────────┘                 │
│                    │ HTTP Streamable (MCP 2025-11-25) │
│                    ▼                                 │
│  ┌─────────────────────────────────────────────┐    │
│  │              Docker Compose                  │    │
│  │                                              │    │
│  │  ┌─────────────────┐  ┌──────────────────┐  │    │
│  │  │   nginx:alpine  │  │   nginx-mcp      │  │    │
│  │  │   :8088 → :80   │  │   python:3.12    │  │    │
│  │  │                 │  │   :8000/mcp/     │  │    │
│  │  │  serves HTTP    │  │   server.py      │  │    │
│  │  │  rate limiting  │  │   8 MCP tools    │  │    │
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
| `:8000/mcp/` | mcp-server | AI clients connect here (HTTP Streamable) |

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
│   ├── server.py          ← 8 MCP tools, HTTP Streamable transport
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
