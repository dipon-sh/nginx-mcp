# nginx-mcp

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that lets AI assistants (Claude, Cursor, etc.) **read, write, and list nginx configuration files** — all from inside an isolated Docker environment.

## Architecture

```
AI Client (Claude Desktop, Cursor, etc.)
        │  stdio / MCP protocol
        ▼
  mcp-server (Python)       ← spawned on-demand by MCP client
        │  shared volume
        ▼
  nginx-configs/            ← .conf files live here
        │
        ▼
  nginx container           ← serves on :8088
```

## Features

- 🤖 **3 MCP tools**: `list_nginx_configs`, `read_nginx_config`, `write_nginx_config`
- 🔒 **Path traversal protection** — AI can't escape the config directory
- 📈 **Rate limiting** — per-IP with configurable burst
- 📋 **Persistent structured logs** — with `PASSED`/`REJECTED` rate limit status
- 🐳 **Fully Dockerized** — nginx runs persistently; MCP server spawns on-demand

## Quick Start

### 1. Clone & start nginx

```bash
git clone https://github.com/YOUR_USERNAME/nginx-mcp.git
cd nginx-mcp
docker compose up -d
```

Verify:
```bash
curl http://localhost:8088/        # → nginx is up and running!
curl http://localhost:8088/health  # → ok
```

Or from inside Docker:
```bash
docker run --rm --network nginx_mcp_default curlimages/curl curl http://nginx/health
```

### 2. Build the MCP server image

```bash
docker build -t nginx-mcp-server ./mcp-server/
```

### 3. Connect to Claude Desktop

Add to `~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nginx-manager": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/absolute/path/to/nginx-mcp/nginx-configs:/etc/nginx/conf.d",
        "-e", "NGINX_CONFIG_DIR=/etc/nginx/conf.d",
        "nginx-mcp-server"
      ]
    }
  }
}
```

Restart Claude Desktop — you'll see 3 new tools available.

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_nginx_configs` | List all `.conf` files in the config directory |
| `read_nginx_config` | Read the contents of a specific config file |
| `write_nginx_config` | Write/update a config file |

## Logs

Logs are persisted to `./logs/` and survive container restarts.

```bash
tail -f logs/access.log
```

Log format includes:
- IP, timestamp, method, path
- HTTP status code
- Request time (`rt=`)
- Rate limit decision (`limit=PASSED` / `limit=REJECTED`)

## Rate Limiting

Configured in `nginx-configs/default.conf`:

| Setting | Value |
|---------|-------|
| Rate | 10 req/s per IP |
| `/health` burst | 5 |
| `/` burst | 20 |
| Exceeded response | `429 Too Many Requests` |

## Project Structure

```
nginx-mcp/
├── docker-compose.yml          # nginx service + log volume
├── nginx-configs/
│   ├── default.conf            # nginx config with rate limiting
│   └── static/                 # static response files
│       ├── health
│       └── index
├── mcp-server/
│   ├── server.py               # MCP server (3 tools)
│   ├── Dockerfile
│   └── requirements.txt
└── logs/                       # persisted nginx logs (gitignored)
```

## Reload nginx after config changes

```bash
docker exec nginx nginx -s reload
```

## License

MIT
