from importlib.metadata import version

from ddtrace.contrib.internal.mcp.patch import get_version
from ddtrace.contrib.internal.mcp.patch import patch
from ddtrace.contrib.internal.mcp.patch import unpatch
from ddtrace.internal.utils.version import parse_version
from tests.contrib.patch import PatchTestCase


MCP_VERSION = parse_version(version("mcp"))


class TestMCPPatch(PatchTestCase.Base):
    __integration_name__ = "mcp"
    __module_name__ = "mcp"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, module):
        from mcp.client.session import ClientSession

        try:
            from mcp.shared.session import BaseSession
            from mcp.shared.session import RequestResponder
        except ImportError:
            from mcp.server import Server

            self.assert_wrapped(ClientSession.send_request)
            self.assert_wrapped(Server.__init__)
            self.assert_wrapped(ClientSession.send_discover)
        else:
            self.assert_wrapped(BaseSession.send_request)
            self.assert_wrapped(RequestResponder.__enter__)
            self.assert_wrapped(RequestResponder.__exit__)
            self.assert_wrapped(RequestResponder.respond)

        self.assert_wrapped(ClientSession.call_tool)
        self.assert_wrapped(ClientSession.__aenter__)
        self.assert_wrapped(ClientSession.__aexit__)
        self.assert_wrapped(ClientSession.list_tools)
        self.assert_wrapped(ClientSession.initialize)

    def assert_not_module_patched(self, module):
        from mcp.client.session import ClientSession

        try:
            from mcp.shared.session import BaseSession
            from mcp.shared.session import RequestResponder
        except ImportError:
            from mcp.server import Server

            self.assert_not_wrapped(ClientSession.send_request)
            self.assert_not_wrapped(Server.__init__)
            self.assert_not_wrapped(ClientSession.send_discover)
        else:
            self.assert_not_wrapped(BaseSession.send_request)
            self.assert_not_wrapped(RequestResponder.__enter__)
            self.assert_not_wrapped(RequestResponder.__exit__)
            self.assert_not_wrapped(RequestResponder.respond)

        self.assert_not_wrapped(ClientSession.call_tool)
        self.assert_not_wrapped(ClientSession.__aenter__)
        self.assert_not_wrapped(ClientSession.__aexit__)
        self.assert_not_wrapped(ClientSession.list_tools)
        self.assert_not_wrapped(ClientSession.initialize)

    def assert_not_module_double_patched(self, module):
        from mcp.client.session import ClientSession

        try:
            from mcp.shared.session import BaseSession
            from mcp.shared.session import RequestResponder
        except ImportError:
            from mcp.server import Server

            self.assert_not_double_wrapped(ClientSession.send_request)
            self.assert_not_double_wrapped(Server.__init__)
            self.assert_not_double_wrapped(ClientSession.send_discover)
        else:
            self.assert_not_double_wrapped(BaseSession.send_request)
            self.assert_not_double_wrapped(RequestResponder.__enter__)
            self.assert_not_double_wrapped(RequestResponder.__exit__)
            self.assert_not_double_wrapped(RequestResponder.respond)

        self.assert_not_double_wrapped(ClientSession.call_tool)
        self.assert_not_double_wrapped(ClientSession.__aenter__)
        self.assert_not_double_wrapped(ClientSession.__aexit__)
        self.assert_not_double_wrapped(ClientSession.list_tools)
        self.assert_not_double_wrapped(ClientSession.initialize)


def test_mcp_server_middleware_unpatch():
    """Unpatching MCP v2 removes middleware from servers created while patched."""
    from mcp.server import Server

    from ddtrace.contrib.internal.mcp.patch import traced_server_middleware

    server = Server("test")
    if MCP_VERSION < (2, 0, 0):
        assert not hasattr(server, "middleware")
        return
    assert traced_server_middleware in server.middleware

    unpatch()
    assert traced_server_middleware not in server.middleware

    patch()


def test_mcp_auto_patch_during_experiment_import(run_python_code_in_subprocess):
    """MCP auto-patching must not recursively import a partial LLMObs experiment module."""
    code = """
import sys

from ddtrace._monkey import patch

patch(raise_errors=False, mcp=True)


class ImportMCPWhileExperimentInitializes:
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "pydantic_evals":
            return None

        sys.meta_path.remove(self)
        import mcp

        assert getattr(mcp, "__datadog_patch", False) is True
        return None


sys.meta_path.insert(0, ImportMCPWhileExperimentInitializes())
try:
    import ddtrace.llmobs._experiment
except ModuleNotFoundError as error:
    # The MCP suite does not require pydantic-evals. Its import is only used to
    # pause experiment initialization at the point that exposes this cycle.
    if error.name != "pydantic_evals":
        raise
"""

    _, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr.decode()
    assert b"failed to enable ddtrace support for mcp" not in stderr
