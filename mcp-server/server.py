#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

# ── Config ─────────────────────────────────────────────────────────────────────
NGINX_CONF_DIR = Path(os.getenv("NGINX_CONF_DIR", "/etc/nginx/conf.d"))
NGINX_LOG_DIR  = Path(os.getenv("NGINX_LOG_DIR",  "/var/log/nginx"))
BACKUP_DIR     = Path(os.getenv("NGINX_BACKUP_DIR", "/etc/nginx/backups"))

BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ── Helpers ────────────────────────────────────────────────────────────────────

def ok(data: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"success": True, **data}, indent=2))]

def err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"success": False, "error": msg}, indent=2))]

def safe_path(filename: str) -> Path:
    resolved = (NGINX_CONF_DIR / filename).resolve()
    if not str(resolved).startswith(str(NGINX_CONF_DIR.resolve())):
        raise ValueError(f"Path traversal rejected: {filename}")
    return resolved

# ── Tools ──────────────────────────────────────────────────────────────────────

def list_nginx_configs() -> list[TextContent]:
    files = sorted(
        str(p.relative_to(NGINX_CONF_DIR))
        for p in NGINX_CONF_DIR.rglob("*") if p.is_file()
    )
    return ok({"files": files, "directory": str(NGINX_CONF_DIR)})


def read_nginx_config(filename: str) -> list[TextContent]:
    try:
        path = safe_path(filename)
        if not path.exists():
            return err(f"File not found: {filename}")
        return ok({"filename": filename, "content": path.read_text()})
    except ValueError as e:
        return err(str(e))


def backup_config(filename: str) -> list[TextContent]:
    try:
        path = safe_path(filename)
        if not path.exists():
            return err(f"Nothing to back up — file not found: {filename}")
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        dst = BACKUP_DIR / f"{filename.replace('/', '_')}.{stamp}.bak"
        shutil.copy2(path, dst)
        return ok({"filename": filename, "backup": str(dst), "timestamp": stamp})
    except ValueError as e:
        return err(str(e))


def validate_nginx() -> list[TextContent]:
    try:
        result = subprocess.run(["nginx", "-t"], capture_output=True, text=True, timeout=10)
        output = (result.stdout + result.stderr).strip()
        return ok({"valid": result.returncode == 0, "output": output})
    except FileNotFoundError:
        return err("nginx binary not found")
    except subprocess.TimeoutExpired:
        return err("nginx -t timed out")


def write_nginx_config(filename: str, content: str, auto_backup: bool = True) -> list[TextContent]:
    """backup → validate → write. Rolls back automatically if nginx -t fails."""
    try:
        path = safe_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)

        backup_path = None
        if auto_backup and path.exists():
            b_data = json.loads(backup_config(filename)[0].text)
            if b_data.get("success"):
                backup_path = b_data["backup"]

        original_content = path.read_text() if path.exists() else None
        path.write_text(content)

        v_data = json.loads(validate_nginx()[0].text)
        if not v_data.get("valid", False):
            path.write_text(original_content) if original_content else path.unlink(missing_ok=True)
            return ok({
                "written": False,
                "reason": "nginx -t failed — original config preserved",
                "validate_output": v_data.get("output"),
                "backup": backup_path,
            })

        return ok({
            "written": True,
            "filename": filename,
            "backup": backup_path,
            "next_step": "Run 'docker exec nginx nginx -s reload' to apply changes",
        })
    except ValueError as e:
        return err(str(e))


def tail_logs(log: str = "access", lines: int = 50) -> list[TextContent]:
    log_path = Path(log) if log.startswith("/") else NGINX_LOG_DIR / f"{log}.log"
    if not log_path.exists():
        return err(f"Log file not found: {log_path}")
    try:
        result = subprocess.run(["tail", f"-{lines}", str(log_path)], capture_output=True, text=True, timeout=10)
        raw_lines = result.stdout.splitlines()
        return ok({"log": str(log_path), "lines_returned": len(raw_lines), "lines": raw_lines})
    except subprocess.TimeoutExpired:
        return err("tail timed out")


def list_blocked_ips() -> list[TextContent]:
    deny_re = re.compile(r'\bdeny\s+([\d./a-fA-F:]+);')
    seen, unique = set(), []
    for conf_file in sorted(NGINX_CONF_DIR.rglob("*.conf")):
        try:
            for match in deny_re.finditer(conf_file.read_text()):
                ip = match.group(1)
                if ip != "all" and ip not in seen:
                    seen.add(ip)
                    unique.append({"ip": ip, "file": str(conf_file.relative_to(NGINX_CONF_DIR))})
        except OSError:
            continue
    return ok({"blocked_ips": unique, "count": len(unique)})


def block_ip(ip: str, filename: str = "default.conf") -> list[TextContent]:
    if not re.match(r'^[\d./a-fA-F:]+$', ip):
        return err(f"Invalid IP address: {ip}")
    try:
        path = safe_path(filename)
        if not path.exists():
            return err(f"File not found: {filename}")
        content = path.read_text()
        if f"deny {ip};" in content:
            return ok({"blocked": False, "reason": f"{ip} is already denied in {filename}"})
        new_content = re.sub(r'(server\s*\{)', rf'\1\n    deny {ip};', content)
        if new_content == content:
            return err("Could not find a server{} block to insert deny rule into")
        result_data = json.loads(write_nginx_config(filename, new_content)[0].text)
        return ok({
            "blocked": result_data.get("written", False),
            "ip": ip,
            "file": filename,
            "backup": result_data.get("backup"),
            "error": result_data.get("reason") if not result_data.get("written") else None,
        })
    except ValueError as e:
        return err(str(e))

# ── MCP wiring ─────────────────────────────────────────────────────────────────

server = Server("nginx-manager")

TOOLS = [
    Tool(name="list_nginx_configs", description="List all nginx config files.", inputSchema={"type": "object", "properties": {}, "required": []}),
    Tool(name="read_nginx_config",  description="Read a nginx config file.", inputSchema={"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}),
    Tool(name="write_nginx_config", description="backup → validate (nginx -t) → write. Auto-rollback on failure.", inputSchema={"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}, "auto_backup": {"type": "boolean", "default": True}}, "required": ["filename", "content"]}),
    Tool(name="validate_nginx",     description="Run nginx -t to check config syntax.", inputSchema={"type": "object", "properties": {}, "required": []}),
    Tool(name="backup_config",      description="Backup a config file with a UTC timestamp.", inputSchema={"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}),
    Tool(name="tail_logs",          description="Return last N lines of access or error log.", inputSchema={"type": "object", "properties": {"log": {"type": "string", "enum": ["access", "error"], "default": "access"}, "lines": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500}}, "required": []}),
    Tool(name="list_blocked_ips",   description="Scan nginx configs for deny rules.", inputSchema={"type": "object", "properties": {}, "required": []}),
    Tool(name="block_ip",           description="Add deny rule for an IP/CIDR. Uses write pipeline.", inputSchema={"type": "object", "properties": {"ip": {"type": "string"}, "filename": {"type": "string", "default": "default.conf"}}, "required": ["ip"]}),
]

@server.list_tools()
async def handle_list_tools():
    return TOOLS

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    match name:
        case "list_nginx_configs": return list_nginx_configs()
        case "read_nginx_config":  return read_nginx_config(arguments["filename"])
        case "write_nginx_config": return write_nginx_config(arguments["filename"], arguments["content"], arguments.get("auto_backup", True))
        case "validate_nginx":     return validate_nginx()
        case "backup_config":      return backup_config(arguments["filename"])
        case "tail_logs":          return tail_logs(arguments.get("log", "access"), arguments.get("lines", 50))
        case "list_blocked_ips":   return list_blocked_ips()
        case "block_ip":           return block_ip(arguments["ip"], arguments.get("filename", "default.conf"))
        case _:                    return err(f"Unknown tool: {name}")

# ── HTTP Streamable transport ──────────────────────────────────────────────────

session_manager = StreamableHTTPSessionManager(app=server, json_response=False, stateless=True)

@asynccontextmanager
async def lifespan(app):
    async with session_manager.run():
        yield

async def handle_mcp(request: Request) -> Response:
    return await session_manager.handle_request(request.scope, request.receive, request._send)

app = Starlette(
    lifespan=lifespan,
    routes=[Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"])],
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8000")))
