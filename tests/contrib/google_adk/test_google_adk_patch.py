import google.adk

from ddtrace.contrib.internal.google_adk.patch import TOOL_DISPATCH_FUNCTIONS
from ddtrace.contrib.internal.google_adk.patch import get_version
from ddtrace.contrib.internal.google_adk.patch import patch
from ddtrace.contrib.internal.google_adk.patch import unpatch
from tests.contrib.google_adk.conftest import call_tool_async
from tests.contrib.patch import PatchTestCase


def test_patch_without_call_tool_live(monkeypatch):
    """Patching must not raise when the installed google-adk has no `__call_tool_live`.

    google-adk 2.7.0 removed it, which crashed applications on startup because LLM Observability
    patches with `raise_errors=True`.
    """
    from google.adk.flows.llm_flows import functions

    if hasattr(functions, "__call_tool_live"):
        monkeypatch.delattr(functions, "__call_tool_live")

    patch()
    try:
        assert hasattr(call_tool_async(google.adk), "__wrapped__")
    finally:
        unpatch()

    assert not hasattr(call_tool_async(google.adk), "__wrapped__")


def tool_dispatch_functions(module):
    """Return the tool dispatch functions present in the installed google-adk.

    google-adk >= 2.7.0 removed ``__call_tool_live``, so only the ones that exist are checked.
    They are looked up with ``getattr`` because writing ``functions.__call_tool_async`` inside a
    class body mangles the attribute name to ``_TestGoogleADKPatch__call_tool_async``.
    """
    functions = module.flows.llm_flows.functions
    return [getattr(functions, name) for name, _ in TOOL_DISPATCH_FUNCTIONS if hasattr(functions, name)]


class TestGoogleADKPatch(PatchTestCase.Base):
    __integration_name__ = "google_adk"
    __module_name__ = "google.adk"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, module):
        self.assert_wrapped(module.runners.Runner.run_async)
        self.assert_wrapped(module.runners.Runner.run_live)
        for tool_dispatch_func in tool_dispatch_functions(module):
            self.assert_wrapped(tool_dispatch_func)
        self.assert_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_patched(self, module):
        self.assert_not_wrapped(module.runners.Runner.run_async)
        self.assert_not_wrapped(module.runners.Runner.run_live)
        for tool_dispatch_func in tool_dispatch_functions(module):
            self.assert_not_wrapped(tool_dispatch_func)
        self.assert_not_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_double_patched(self, module):
        self.assert_not_double_wrapped(module.runners.Runner.run_async)
        self.assert_not_double_wrapped(module.runners.Runner.run_live)
        for tool_dispatch_func in tool_dispatch_functions(module):
            self.assert_not_double_wrapped(tool_dispatch_func)
        self.assert_not_double_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)
