from ddtrace.contrib.internal.google_adk.patch import get_version
from ddtrace.contrib.internal.google_adk.patch import patch
from ddtrace.contrib.internal.google_adk.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestGoogleADKPatch(PatchTestCase.Base):
    __integration_name__ = "google_adk"
    __module_name__ = "google.adk"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, module):
        self.assert_wrapped(module.runners.Runner.run_async)
        self.assert_wrapped(module.runners.Runner.run_live)
        functions = module.flows.llm_flows.functions
        self.assert_wrapped(getattr(functions, "__call_tool_async"))
        if hasattr(functions, "__call_tool_live"):
            self.assert_wrapped(getattr(functions, "__call_tool_live"))
        self.assert_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_patched(self, module):
        self.assert_not_wrapped(module.runners.Runner.run_async)
        self.assert_not_wrapped(module.runners.Runner.run_live)
        functions = module.flows.llm_flows.functions
        self.assert_not_wrapped(getattr(functions, "__call_tool_async"))
        if hasattr(functions, "__call_tool_live"):
            self.assert_not_wrapped(getattr(functions, "__call_tool_live"))
        self.assert_not_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_double_patched(self, module):
        self.assert_not_double_wrapped(module.runners.Runner.run_async)
        self.assert_not_double_wrapped(module.runners.Runner.run_live)
        functions = module.flows.llm_flows.functions
        self.assert_not_double_wrapped(getattr(functions, "__call_tool_async"))
        if hasattr(functions, "__call_tool_live"):
            self.assert_not_double_wrapped(getattr(functions, "__call_tool_live"))
        self.assert_not_double_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)


def test_patch_without_call_tool_live(monkeypatch):
    # Not every google-adk release below 2.7.0 has __call_tool_live, e.g. 1.39.1 does not.
    from google.adk.flows.llm_flows import functions

    unpatch()
    monkeypatch.delattr(functions, "__call_tool_live", raising=False)
    patch()
    try:
        assert hasattr(getattr(functions, "__call_tool_async"), "__wrapped__")
    finally:
        unpatch()
    assert not hasattr(getattr(functions, "__call_tool_async"), "__wrapped__")
