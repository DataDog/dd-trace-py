from ddtrace.contrib.internal.mcp.patch import get_version
from ddtrace.contrib.internal.mcp.patch import patch
from ddtrace.contrib.internal.mcp.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestMCPPatch(PatchTestCase.Base):
    __integration_name__ = "mcp"
    __module_name__ = "mcp"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, mcp):
        from mcp.client.session import ClientSession
        from mcp.server.fastmcp.tools.tool_manager import ToolManager

        self.assert_wrapped(ClientSession.call_tool)
        self.assert_wrapped(ToolManager.call_tool)

    def assert_not_module_patched(self, mcp):
        from mcp.client.session import ClientSession
        from mcp.server.fastmcp.tools.tool_manager import ToolManager

        self.assert_not_wrapped(ClientSession.call_tool)
        self.assert_not_wrapped(ToolManager.call_tool)

    def assert_not_module_double_patched(self, mcp):
        from mcp.client.session import ClientSession
        from mcp.server.fastmcp.tools.tool_manager import ToolManager

        self.assert_not_double_wrapped(ClientSession.call_tool)
        self.assert_not_double_wrapped(ToolManager.call_tool)
