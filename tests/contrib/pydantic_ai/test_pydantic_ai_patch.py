from ddtrace.contrib.internal.pydantic_ai.patch import get_version
from ddtrace.contrib.internal.pydantic_ai.patch import patch
from ddtrace.contrib.internal.pydantic_ai.patch import unpatch
from ddtrace.internal.utils.version import parse_version
from tests.contrib.patch import PatchTestCase


PYDANTIC_AI_VERSION = parse_version(get_version())


class TestPydanticAIPatch(PatchTestCase.Base):
    __integration_name__ = "pydantic_ai"
    __module_name__ = "pydantic_ai"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, pydantic_ai):
        self.assert_wrapped(pydantic_ai.agent.Agent.iter)
        self.assert_wrapped(pydantic_ai.agent.Agent.run_stream)
        if PYDANTIC_AI_VERSION >= (0, 4, 4):
            self.assert_wrapped(pydantic_ai.agent.ToolManager.handle_call)
        else:
            self.assert_wrapped(pydantic_ai.tools.Tool.run)

    def assert_not_module_patched(self, pydantic_ai):
        self.assert_not_wrapped(pydantic_ai.agent.Agent.iter)
        self.assert_not_wrapped(pydantic_ai.agent.Agent.run_stream)
        if PYDANTIC_AI_VERSION >= (0, 4, 4):
            self.assert_not_wrapped(pydantic_ai.agent.ToolManager.handle_call)
        else:
            self.assert_not_wrapped(pydantic_ai.tools.Tool.run)

    def assert_not_module_double_patched(self, pydantic_ai):
        self.assert_not_double_wrapped(pydantic_ai.agent.Agent.iter)
        self.assert_not_double_wrapped(pydantic_ai.agent.Agent.run_stream)
        if PYDANTIC_AI_VERSION >= (0, 4, 4):
            self.assert_not_double_wrapped(pydantic_ai.agent.ToolManager.handle_call)
        else:
            self.assert_not_double_wrapped(pydantic_ai.tools.Tool.run)
