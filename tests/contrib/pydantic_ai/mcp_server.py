from mcp.server.fastmcp import FastMCP


mcp = FastMCP("test-mcp")
mcp._mcp_server.version = "1.0.0"


@mcp.tool()
def calculate_square_tool(x: int) -> int:
    """Calculates the square of a number"""
    return x * x


if __name__ == "__main__":
    mcp.run()
