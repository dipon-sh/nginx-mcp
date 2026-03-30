#!/usr/bin/env python3
"""
nginx MCP Server — full implementation
Tools: list_nginx_configs, read_nginx_config, write_nginx_config,
       validate_nginx, reload_nginx, backup_config, tail_logs,
       list_blocked_ips, block_ip, nginx_status
"""

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

# ── MCP SDK ────────────────────────────────────────────────────────────────────
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ── Config ─────────────────────────────────────────────────────────────────────
NGINX_CONF_DIR    = Path(os.getenv("NGINX_CONF_DIR",    "/etc/nginx/conf.d"))
NGINX_LOG_DIR     = Path(os.getenv("NGINX_LOG_DIR",     "/var/log/nginx"))
NGINX_CONTAINER   = os.getenv("NGINX_CONTAINER",        "nginx")
BACKUP_DIR        = Path(os.getenv("NGINX_BACKUP_DIR",  "/etc/nginx/backups"))

BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _nginx_cmd(args: list[str]) -> subprocess.CompletedProcess:
    """Run nginx binary (installed in this container for config validation)."""
    return subprocess.run(["nginx"] + args, capture_output=True, text=True, timeout=10)


def ok(data: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"success": True, **data}, indent=2))]

def err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"success": False, "error": msg}, indent=2))]

def safe_path(filename: str) -> Path:
    """Resolve filename relative to NGINX_CONF_DIR and reject path traversal."""
    resolved = (NGINX_CONF_DIR / filename).resolve()
    if not str(resolved).startswith(str(NGINX_CONF_DIR.resolve())):
        raise ValueError(f"Path traversal rejected: {filename}")
    return resolved


# ── Tool implementations ───────────────────────────────────────────────────────

def list_nginx_configs() -> list[TextContent]:
    """List all .conf files (and static files) inside NGINX_CONF_DIR."""
    files = sorted(
        str(p.relative_to(NGINX_CONF_DIR))
        for p in NGINX_CONF_DIR.rglob("*")
        if p.is_file()
    )
    return ok({"files": files, "directory": str(NGINX_CONF_DIR)})


def read_nginx_config(filename: str) -> list[TextContent]:
    """Read a single config file."""
    try:
        path = safe_path(filename)
        if not path.exists():
            return err(f"File not found: {filename}")
        return ok({"filename": filename, "content": path.read_text()})
    except ValueError as e:
        return err(str(e))


def backup_config(filename: str) -> list[TextContent]:
    """
    Copy the current file to BACKUP_DIR with a timestamp suffix.
    Returns the backup path so callers can reference or restore it.
    """
    try:
        path = safe_path(filename)
        if not path.exists():
            return err(f"Nothing to back up — file not found: {filename}")

        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        safe_name = filename.replace("/", "_")
        dst = BACKUP_DIR / f"{safe_name}.{stamp}.bak"
        shutil.copy2(path, dst)
        return ok({"filename": filename, "backup": str(dst), "timestamp": stamp})
    except ValueError as e:
        return err(str(e))


def validate_nginx() -> list[TextContent]:
    """Run `nginx -t` to check config syntax."""
    try:
        result = _nginx_cmd(["-t"])
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return ok({"valid": True, "output": output})
        else:
            return ok({"valid": False, "output": output})
    except FileNotFoundError:
        return err("nginx binary and docker not found — cannot validate")
    except subprocess.TimeoutExpired:
        return err("nginx -t timed out after 10 seconds")


def reload_nginx() -> list[TextContent]:
    """Inform the user how to reload nginx — cannot signal another container without docker sock."""
    return ok({
        "note": "Run this on the host to reload nginx:",
        "command": "docker exec nginx nginx -s reload"
    })



def write_nginx_config(filename: str, content: str, auto_backup: bool = True) -> list[TextContent]:
    """
    Safe write pipeline:
      1. backup_config   (unless auto_backup=False)
      2. write new content to a temp file
      3. validate_nginx  (nginx -t)
      4. move temp → real file
      5. reload_nginx
    On validation failure the temp file is removed and the original is untouched.
    """
    try:
        path = safe_path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Step 1: backup
        backup_path = None
        if auto_backup and path.exists():
            b = backup_config(filename)
            b_data = json.loads(b[0].text)
            if b_data.get("success"):
                backup_path = b_data["backup"]

        # Step 2: write to temp
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(content)

        # Step 3: validate (nginx -t reads already-installed configs, so we
        #         temporarily move the temp into place for the test)
        original_content = path.read_text() if path.exists() else None
        path.write_text(content)   # put new content in place for nginx -t

        v = validate_nginx()
        v_data = json.loads(v[0].text)

        if not v_data.get("valid", False):
            # Restore original
            if original_content is not None:
                path.write_text(original_content)
            else:
                path.unlink(missing_ok=True)
            tmp_path.unlink(missing_ok=True)
            return ok({
                "written": False,
                "reason": "nginx -t failed — original config preserved",
                "validate_output": v_data.get("output"),
                "backup": backup_path,
            })

        # Step 4: temp already moved; clean up
        tmp_path.unlink(missing_ok=True)

        return ok({
            "written": True,
            "filename": filename,
            "backup": backup_path,
            "next_step": "Run 'docker exec nginx nginx -s reload' to apply changes",
        })

    except ValueError as e:
        return err(str(e))


def tail_logs(log: str = "access", lines: int = 50) -> list[TextContent]:
    """
    Return the last N lines of an nginx log file.
    log: "access" | "error" | absolute path
    """
    if log.startswith("/"):
        log_path = Path(log)
    else:
        log_path = NGINX_LOG_DIR / f"{log}.log"

    if not log_path.exists():
        return err(f"Log file not found: {log_path}")

    try:
        result = subprocess.run(
            ["tail", f"-{lines}", str(log_path)],
            capture_output=True, text=True, timeout=10
        )
        raw_lines = result.stdout.splitlines()
        return ok({
            "log": str(log_path),
            "lines_returned": len(raw_lines),
            "lines": raw_lines,
        })
    except subprocess.TimeoutExpired:
        return err("tail timed out")


def list_blocked_ips() -> list[TextContent]:
    """
    Scan all .conf files for `deny <ip>;` directives.
    Returns a deduplicated list with the file each deny was found in.
    """
    results = []
    deny_re = re.compile(r'\bdeny\s+([\d./a-fA-F:]+);')

    for conf_file in sorted(NGINX_CONF_DIR.rglob("*.conf")):
        try:
            content = conf_file.read_text()
            for match in deny_re.finditer(content):
                ip = match.group(1)
                if ip != "all":
                    results.append({
                        "ip": ip,
                        "file": str(conf_file.relative_to(NGINX_CONF_DIR)),
                    })
        except OSError:
            continue

    seen = set()
    unique = []
    for entry in results:
        if entry["ip"] not in seen:
            seen.add(entry["ip"])
            unique.append(entry)

    return ok({"blocked_ips": unique, "count": len(unique)})


def block_ip(ip: str, filename: str = "default.conf") -> list[TextContent]:
    """
    Add `deny <ip>;` inside every server{} block in the target file,
    then run the safe write pipeline (backup → validate → write → reload).
    """
    # Basic IP/CIDR validation
    if not re.match(r'^[\d./a-fA-F:]+$', ip):
        return err(f"Invalid IP address: {ip}")

    try:
        path = safe_path(filename)
        if not path.exists():
            return err(f"File not found: {filename}")

        content = path.read_text()

        # Check already blocked
        if f"deny {ip};" in content:
            return ok({"blocked": False, "reason": f"{ip} is already denied in {filename}"})

        # Insert deny after the opening `server {` line
        new_content = re.sub(
            r'(server\s*\{)',
            rf'\1\n    deny {ip};',
            content
        )

        if new_content == content:
            return err("Could not find a server{} block to insert deny rule into")

        result = write_nginx_config(filename, new_content)
        result_data = json.loads(result[0].text)
        return ok({
            "blocked": result_data.get("written", False),
            "ip": ip,
            "file": filename,
            "reloaded": result_data.get("reloaded"),
            "backup": result_data.get("backup"),
            "error": result_data.get("reason") if not result_data.get("written") else None,
        })
    except ValueError as e:
        return err(str(e))


def nginx_status() -> list[TextContent]:
    """
    Read nginx stub_status endpoint if available, otherwise return process info.
    Falls back to `ps` if stub_status isn't configured.
    """
    # Try stub_status (requires stub_status module + location block)
    try:
        result = subprocess.run(
            ["curl", "-sf", "http://127.0.0.1/nginx_status"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout:
            lines = result.stdout.strip().splitlines()
            # Parse standard stub_status output
            parsed = {"raw": result.stdout.strip()}
            for line in lines:
                if "Active connections:" in line:
                    parsed["active_connections"] = int(line.split()[-1])
                elif "server accepts handled requests" in line:
                    pass
                elif re.match(r'\s*\d+\s+\d+\s+\d+', line):
                    nums = line.split()
                    parsed["accepts"] = int(nums[0])
                    parsed["handled"] = int(nums[1])
                    parsed["requests"] = int(nums[2])
                elif "Reading:" in line:
                    m = re.findall(r'(\w+):\s*(\d+)', line)
                    for k, v in m:
                        parsed[k.lower()] = int(v)
            return ok({"source": "stub_status", **parsed})
    except Exception:
        pass

    # Fallback: ps
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5
        )
        nginx_procs = [l for l in result.stdout.splitlines() if "nginx" in l]
        return ok({
            "source": "ps",
            "processes": nginx_procs,
            "process_count": len(nginx_procs),
            "note": "Add 'location /nginx_status { stub_status; allow 127.0.0.1; deny all; }' for richer stats",
        })
    except Exception as e:
        return err(f"Could not retrieve nginx status: {e}")


# ── MCP Server wiring ──────────────────────────────────────────────────────────

server = Server("nginx-manager")

TOOLS = [
    Tool(
        name="list_nginx_configs",
        description="List all nginx config files in the conf directory.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="read_nginx_config",
        description="Read a nginx config file by filename.",
        inputSchema={
            "type": "object",
            "properties": {"filename": {"type": "string", "description": "e.g. default.conf or sites-enabled/mysite.conf"}},
            "required": ["filename"],
        },
    ),
    Tool(
        name="write_nginx_config",
        description=(
            "Safe write pipeline: backup → validate (nginx -t) → write → reload. "
            "Rolls back automatically if nginx -t fails."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "filename":    {"type": "string"},
                "content":     {"type": "string"},
                "auto_backup": {"type": "boolean", "default": True},
            },
            "required": ["filename", "content"],
        },
    ),
    Tool(
        name="validate_nginx",
        description="Run `nginx -t` to check config syntax without reloading.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="reload_nginx",
        description="Send `nginx -s reload` to apply config changes. Always validate first.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="backup_config",
        description="Back up a config file with a UTC timestamp before modifying it.",
        inputSchema={
            "type": "object",
            "properties": {"filename": {"type": "string"}},
            "required": ["filename"],
        },
    ),
    Tool(
        name="tail_logs",
        description="Return the last N lines of an nginx log file (access or error).",
        inputSchema={
            "type": "object",
            "properties": {
                "log":   {"type": "string", "enum": ["access", "error"], "default": "access"},
                "lines": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
            },
            "required": [],
        },
    ),
    Tool(
        name="list_blocked_ips",
        description="Scan all nginx configs for `deny <ip>;` rules and return blocked IPs.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="block_ip",
        description=(
            "Add a deny rule for an IP/CIDR in a config file. "
            "Uses the safe write pipeline: backup → validate → write → reload."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "ip":       {"type": "string", "description": "IPv4, IPv6, or CIDR e.g. 1.2.3.4 or 10.0.0.0/8"},
                "filename": {"type": "string", "default": "default.conf"},
            },
            "required": ["ip"],
        },
    ),
    Tool(
        name="nginx_status",
        description="Get nginx runtime status: active connections, request counts (stub_status or ps fallback).",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
]


@server.list_tools()
async def handle_list_tools():
    return TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    match name:
        case "list_nginx_configs":
            return list_nginx_configs()
        case "read_nginx_config":
            return read_nginx_config(arguments["filename"])
        case "write_nginx_config":
            return write_nginx_config(
                arguments["filename"],
                arguments["content"],
                arguments.get("auto_backup", True),
            )
        case "validate_nginx":
            return validate_nginx()
        case "reload_nginx":
            return reload_nginx()
        case "backup_config":
            return backup_config(arguments["filename"])
        case "tail_logs":
            return tail_logs(
                arguments.get("log", "access"),
                arguments.get("lines", 50),
            )
        case "list_blocked_ips":
            return list_blocked_ips()
        case "block_ip":
            return block_ip(
                arguments["ip"],
                arguments.get("filename", "default.conf"),
            )
        case "nginx_status":
            return nginx_status()
        case _:
            return err(f"Unknown tool: {name}")


async def main():
    async with stdio_server() as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())