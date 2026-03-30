import os
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

NGINX_CONFIG_DIR = os.environ.get("NGINX_CONFIG_DIR", "/etc/nginx/conf.d")

app = Server("nginx-mcp")

@app.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="read_nginx_config",
            description="Read an nginx config file",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Config filename, e.g. nginx.conf or sites-enabled/mysite.conf"
                    }
                },
                "required": ["filename"]
            }
        ),
        types.Tool(
            name="write_nginx_config",
            description="Write/update an nginx config file",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string", "description": "Full file content to write"}
                },
                "required": ["filename", "content"]
            }
        ),
        types.Tool(
            name="list_nginx_configs",
            description="List all nginx config files available",
            inputSchema={"type": "object", "properties": {}}
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "list_nginx_configs":
        files = []
        for root, dirs, filenames in os.walk(NGINX_CONFIG_DIR):
            for f in filenames:
                rel = os.path.relpath(os.path.join(root, f), NGINX_CONFIG_DIR)
                files.append(rel)
        return [types.TextContent(type="text", text="\n".join(files) or "No files found")]

    elif name == "read_nginx_config":
        filepath = os.path.join(NGINX_CONFIG_DIR, arguments["filename"])
        if not os.path.realpath(filepath).startswith(os.path.realpath(NGINX_CONFIG_DIR)):
            return [types.TextContent(type="text", text="Error: path traversal detected")]
        try:
            with open(filepath, "r") as f:
                return [types.TextContent(type="text", text=f.read())]
        except FileNotFoundError:
            return [types.TextContent(type="text", text=f"File not found: {arguments['filename']}")]

    elif name == "write_nginx_config":
        filepath = os.path.join(NGINX_CONFIG_DIR, arguments["filename"])
        safe_base = os.path.realpath(NGINX_CONFIG_DIR)

        # Validate parent directory before creation
        if not os.path.realpath(os.path.dirname(os.path.abspath(filepath))).startswith(safe_base):
            return [types.TextContent(type="text", text="Error: path traversal detected")]

        try:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            # Re-validate after makedirs to defend against symlink race (TOCTOU)
            if not os.path.realpath(os.path.abspath(filepath)).startswith(safe_base):
                return [types.TextContent(type="text", text="Error: path traversal detected")]
            with open(filepath, "w") as f:
                f.write(arguments["content"])
        except OSError as e:
            return [types.TextContent(type="text", text=f"Error writing file: {e}")]

        return [types.TextContent(type="text", text=f"Written: {arguments['filename']}")]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
