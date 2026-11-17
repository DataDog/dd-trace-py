"""Minimal stdio MCP server with an echo tool, spawned by the AI Guard Anthropic MCP tests."""

try:
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp<2
    from mcp.server.fastmcp import FastMCP as Server


server = Server("echo")


@server.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return text


if __name__ == "__main__":
    server.run()
