import asyncio
from unittest import mock

import pytest

from ddtrace.contrib.internal.mcp.patch import get_version
from ddtrace.contrib.internal.mcp.patch import patch
from ddtrace.contrib.internal.mcp.patch import traced_server_runner_on_request
from ddtrace.contrib.internal.mcp.patch import unpatch
from ddtrace.contrib.trace_utils import iswrapped
from tests.contrib.mcp.utils import MCP_V2
from tests.contrib.patch import PatchTestCase


class TestMCPPatch(PatchTestCase.Base):
    __integration_name__ = "mcp"
    __module_name__ = "mcp"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def _wrapped_functions(self):
        from mcp.client.session import ClientSession

        functions = [
            ClientSession.__aenter__,
            ClientSession.__aexit__,
            ClientSession.call_tool,
            ClientSession.list_tools,
            ClientSession.initialize,
        ]
        if MCP_V2:
            from mcp.server.runner import ServerRunner

            return functions + [ClientSession.send_request, ClientSession.adopt, ServerRunner._on_request]

        from mcp.shared.session import BaseSession
        from mcp.shared.session import RequestResponder

        return functions + [
            BaseSession.send_request,
            RequestResponder.__enter__,
            RequestResponder.__exit__,
            RequestResponder.respond,
        ]

    def assert_module_patched(self, module):
        for function in self._wrapped_functions():
            self.assert_wrapped(function)

    def assert_not_module_patched(self, module):
        for function in self._wrapped_functions():
            self.assert_not_wrapped(function)

    def assert_not_module_double_patched(self, module):
        for function in self._wrapped_functions():
            self.assert_not_double_wrapped(function)


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


@pytest.mark.skipif(not MCP_V2, reason="ServerRunner only exists on mcp 2.x")
def test_patch_skips_server_when_on_request_is_missing():
    """A 2.x release renaming the private ServerRunner._on_request must only lose server tracing."""
    from mcp.client.session import ClientSession
    from mcp.server.runner import ServerRunner

    # Start unpatched, since the autouse mcp_setup fixture has already patched.
    unpatch()
    original = ServerRunner._on_request
    del ServerRunner._on_request
    try:
        patch()
        assert iswrapped(ClientSession, "call_tool")
        assert not hasattr(ServerRunner, "_on_request")
    finally:
        unpatch()
        ServerRunner._on_request = original
    assert not iswrapped(ClientSession, "call_tool")
    assert not iswrapped(ServerRunner, "_on_request")


@pytest.mark.skipif(not MCP_V2, reason="ServerRunner only exists on mcp 2.x")
def test_server_runner_wrapper_passes_through_unexpected_arguments():
    """A changed private signature must pass requests through untraced instead of failing them."""
    expected = {"content": [], "isError": False}

    async def on_request(*args, **kwargs):
        return expected

    async def run():
        # method arrives as a dict, as it would if the SDK reordered the arguments
        return await traced_server_runner_on_request(on_request, mock.MagicMock(), ({"a": 1}, {}, "tools/call"), {})

    assert asyncio.run(run()) is expected
