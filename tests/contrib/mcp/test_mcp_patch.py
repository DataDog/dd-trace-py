from ddtrace.contrib.internal.mcp.patch import MCP_VERSION
from ddtrace.contrib.internal.mcp.patch import get_version
from ddtrace.contrib.internal.mcp.patch import patch
from ddtrace.contrib.internal.mcp.patch import unpatch
from tests.contrib.patch import PatchTestCase


def _patched_functions():
    from mcp.client.session import ClientSession

    functions = [
        ClientSession.call_tool,
        ClientSession.__aenter__,
        ClientSession.__aexit__,
        ClientSession.list_tools,
        ClientSession.initialize,
    ]
    if MCP_VERSION >= (2, 0, 0):
        from mcp.server.runner import ServerRunner

        return functions + [ClientSession.send_request, ClientSession.adopt, ServerRunner.__init__]

    from mcp.shared.session import BaseSession
    from mcp.shared.session import RequestResponder

    return functions + [
        BaseSession.send_request,
        RequestResponder.__enter__,
        RequestResponder.__exit__,
        RequestResponder.respond,
    ]


class TestMCPPatch(PatchTestCase.Base):
    __integration_name__ = "mcp"
    __module_name__ = "mcp"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, module):
        for function in _patched_functions():
            self.assert_wrapped(function)

    def assert_not_module_patched(self, module):
        for function in _patched_functions():
            self.assert_not_wrapped(function)

    def assert_not_module_double_patched(self, module):
        for function in _patched_functions():
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


def test_unrelated_mcp_module_without_anyio(run_python_code_in_subprocess):
    code = """
import sys
import types

class BlockAnyio:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "anyio":
            raise ModuleNotFoundError("No module named 'anyio'", name="anyio")

sys.meta_path.insert(0, BlockAnyio())
sys.modules.pop("anyio", None)
mcp = types.ModuleType("mcp")
sys.modules["mcp"] = mcp

from ddtrace import patch
patch(mcp=True)
assert mcp.__datadog_patch is False
"""
    _, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr.decode()
