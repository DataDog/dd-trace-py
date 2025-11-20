"""FastAPI app with MCP server for IAST testing.

This app provides both regular HTTP endpoints and MCP tools to test IAST
with different communication patterns.
"""

import subprocess

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from mcp.server.fastmcp import FastMCP

from ddtrace.appsec._iast._iast_request_context_base import is_iast_request_enabled


def get_app():
    """Create a FastAPI app with MCP server endpoints for IAST testing."""
    app = FastAPI()

    # Create MCP server instance
    mcp = FastMCP(name="IASTTestServer")

    @mcp.tool(description="Execute a command (vulnerable)")
    def execute_command(command: str) -> str:
        """Execute a command - intentionally vulnerable for IAST testing."""
        # This triggers CMDI vulnerability
        subp = subprocess.Popen(args=["ls", "-la", command])
        subp.communicate()
        subp.wait()
        return f"Command executed: {command}"

    @mcp.tool(description="Get weather for a location")
    def get_weather(location: str) -> str:
        """Get weather information for a location."""
        return f"Weather in {location} is 72°F"

    @mcp.tool(description="A simple calculator tool")
    def calculator(operation: str, a: int, b: int) -> dict:
        """Perform arithmetic operations."""
        if operation == "add":
            return {"result": a + b}
        elif operation == "multiply":
            return {"result": a * b}
        return {"result": 0}

    @app.get("/")
    async def index():
        """Health check endpoint."""
        return {"status": "ok", "iast_enabled": is_iast_request_enabled()}

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "ok"}

    @app.get("/shutdown")
    async def shutdown():
        """Shutdown endpoint for test cleanup."""
        from ddtrace import tracer

        tracer.shutdown()
        return {"status": "shutting down"}

    @app.post("/iast-cmdi-form")
    async def iast_cmdi_form(command: str = Form(...)):
        """Regular CMDI endpoint for baseline comparison."""
        subp = subprocess.Popen(args=["ls", "-la", command])
        subp.communicate()
        subp.wait()
        return {"status": "ok", "command": command}

    @app.get("/iast-cmdi-header")
    async def iast_cmdi_header(request: Request):
        """CMDI endpoint using tainted header value.

        This endpoint reads a custom header and uses it in a command execution,
        testing IAST's ability to detect tainted headers leading to CMDI.
        """
        # Get the custom header - this should be tainted by IAST
        command = request.headers.get("X-Command", "default")

        # Use the tainted header value in a subprocess (vulnerable)
        subp = subprocess.Popen(args=["ls", "-la", command])
        subp.communicate()
        subp.wait()

        return {
            "status": "ok",
            "command_from_header": command,
            "user_agent": request.headers.get("User-Agent", ""),
            "referer": request.headers.get("Referer", ""),
        }

    @app.get("/iast-header-echo")
    async def iast_header_echo(request: Request):
        """Echo headers back to test header tainting without vulnerability.

        Returns all relevant headers to verify they are properly tainted.
        """
        return {
            "headers": {
                "user_agent": request.headers.get("User-Agent", ""),
                "referer": request.headers.get("Referer", ""),
                "x_custom": request.headers.get("X-Custom-Header", ""),
                "x_command": request.headers.get("X-Command", ""),
                "accept": request.headers.get("Accept", ""),
                "accept_language": request.headers.get("Accept-Language", ""),
            },
            "iast_enabled": is_iast_request_enabled(),
        }

    # Mount MCP's SSE app for streaming
    # FastMCP provides an sse_app() method that returns a Starlette app
    # Get the SSE app from FastMCP - pass None as mount_path since we're mounting it ourselves
    mcp_sse_starlette_app = mcp.sse_app(mount_path=None)

    # Mount the MCP SSE app to our FastAPI app at /iast/mcp
    app.mount("/iast/mcp", mcp_sse_starlette_app)

    # Store the MCP server on the app for access in tests
    app.state.mcp_server = mcp

    return app


# Create the app instance for uvicorn
app = get_app()
