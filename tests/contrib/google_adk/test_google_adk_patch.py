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
        # self.assert_wrapped(module.flows.llm_flows.functions.__call_tool_async)
        # self.assert_wrapped(module.flows.llm_flows.functions.__call_tool_live)
        # TODO: fix this
        # it fails with AttributeError: module 'google.adk.flows.llm_flows.functions' has no
        # attribute '_TestGoogleADKPatch__call_tool_async'. Note the class name prepended with
        # wrapped function name.
        self.assert_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_patched(self, module):
        self.assert_not_wrapped(module.runners.Runner.run_async)
        self.assert_not_wrapped(module.runners.Runner.run_live)
        # self.assert_not_wrapped(module.flows.llm_flows.functions.__call_tool_async)
        # self.assert_not_wrapped(module.flows.llm_flows.functions.__call_tool_live)
        self.assert_not_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_double_patched(self, module):
        self.assert_not_double_wrapped(module.runners.Runner.run_async)
        self.assert_not_double_wrapped(module.runners.Runner.run_live)
        # self.assert_not_double_wrapped(module.flows.llm_flows.functions.__call_tool_async)
        # self.assert_not_double_wrapped(module.flows.llm_flows.functions.__call_tool_live)
        self.assert_not_double_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)
