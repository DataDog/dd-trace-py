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

    def assert_module_patched(self, module):
        from mcp.client.session import ClientSession
        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder

        self.assert_wrapped(BaseSession.send_request)
        self.assert_wrapped(ClientSession.call_tool)
        self.assert_wrapped(ClientSession.__aenter__)
        self.assert_wrapped(ClientSession.__aexit__)
        self.assert_wrapped(ClientSession.list_tools)
        self.assert_wrapped(ClientSession.initialize)
        self.assert_wrapped(RequestResponder.__enter__)
        self.assert_wrapped(RequestResponder.__exit__)
        self.assert_wrapped(RequestResponder.respond)

    def assert_not_module_patched(self, module):
        from mcp.client.session import ClientSession
        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder

        self.assert_not_wrapped(BaseSession.send_request)
        self.assert_not_wrapped(ClientSession.call_tool)
        self.assert_not_wrapped(ClientSession.__aenter__)
        self.assert_not_wrapped(ClientSession.__aexit__)
        self.assert_not_wrapped(ClientSession.list_tools)
        self.assert_not_wrapped(ClientSession.initialize)
        self.assert_not_wrapped(RequestResponder.__enter__)
        self.assert_not_wrapped(RequestResponder.__exit__)
        self.assert_not_wrapped(RequestResponder.respond)

    def assert_not_module_double_patched(self, module):
        from mcp.client.session import ClientSession
        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder

        self.assert_not_double_wrapped(BaseSession.send_request)
        self.assert_not_double_wrapped(ClientSession.call_tool)
        self.assert_not_double_wrapped(ClientSession.__aenter__)
        self.assert_not_double_wrapped(ClientSession.__aexit__)
        self.assert_not_double_wrapped(ClientSession.list_tools)
        self.assert_not_double_wrapped(ClientSession.initialize)
        self.assert_not_double_wrapped(RequestResponder.__enter__)
        self.assert_not_double_wrapped(RequestResponder.__exit__)
        self.assert_not_double_wrapped(RequestResponder.respond)
