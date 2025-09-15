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
        self.assert_wrapped(module.flows.llm_flows.functions.__call_tool_async)
        self.assert_wrapped(module.flows.llm_flows.functions.__call_tool_live)
        self.assert_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)
        self.assert_wrapped(module.plugins.plugin_manager.PluginManager._run_callbacks)
        self.assert_wrapped(module.memory.InMemoryMemoryService.add_session_to_memory)
        self.assert_wrapped(module.memory.VertexAiMemoryBankService.add_session_to_memory)

    def assert_not_module_patched(self, module):
        self.assert_not_wrapped(module.runners.Runner.run_async)
        self.assert_not_wrapped(module.runners.Runner.run_live)
        self.assert_not_wrapped(module.flows.llm_flows.functions.__call_tool_async)
        self.assert_not_wrapped(module.flows.llm_flows.functions.__call_tool_live)
        self.assert_not_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)
        self.assert_not_wrapped(module.plugins.plugin_manager.PluginManager._run_callbacks)
        self.assert_not_wrapped(module.memory.InMemoryMemoryService.add_session_to_memory)
        self.assert_not_wrapped(module.memory.VertexAiMemoryBankService.add_session_to_memory)

    def assert_not_module_double_patched(self, module):
        self.assert_not_double_wrapped(module.runners.Runner.run_async)
        self.assert_not_double_wrapped(module.runners.Runner.run_live)
        self.assert_not_double_wrapped(module.flows.llm_flows.functions.__call_tool_async)
        self.assert_not_double_wrapped(module.flows.llm_flows.functions.__call_tool_live)
        self.assert_not_double_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.plugins.plugin_manager.PluginManager._run_callbacks)
        self.assert_not_double_wrapped(module.memory.InMemoryMemoryService.add_session_to_memory)
        self.assert_not_double_wrapped(module.memory.VertexAiMemoryBankService.add_session_to_memory)
