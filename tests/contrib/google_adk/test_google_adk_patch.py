from operator import attrgetter

import pytest

from ddtrace.contrib.internal.google_adk.patch import GOOGLE_ADK_VERSION
from ddtrace.contrib.internal.google_adk.patch import _tool_dispatch_target
from ddtrace.contrib.internal.google_adk.patch import get_version
from ddtrace.contrib.internal.google_adk.patch import patch
from ddtrace.contrib.internal.google_adk.patch import unpatch
from tests.contrib.patch import PatchTestCase


def _tool_dispatcher(module):
    """Return the central tool dispatcher of the installed google-adk, looked up with getattr so
    the double-underscore name is not mangled from inside a class body.
    """
    dispatch_module, dispatch_name = _tool_dispatch_target(GOOGLE_ADK_VERSION)
    return getattr(attrgetter(dispatch_module)(module), dispatch_name)


class TestGoogleADKPatch(PatchTestCase.Base):
    __integration_name__ = "google_adk"
    __module_name__ = "google.adk"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, module):
        self.assert_wrapped(module.runners.Runner.run_async)
        self.assert_wrapped(module.runners.Runner.run_live)
        self.assert_wrapped(_tool_dispatcher(module))
        if GOOGLE_ADK_VERSION < (2, 7, 0):
            self.assert_wrapped(getattr(module.flows.llm_flows.functions, "__call_tool_live"))
        self.assert_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_patched(self, module):
        self.assert_not_wrapped(module.runners.Runner.run_async)
        self.assert_not_wrapped(module.runners.Runner.run_live)
        self.assert_not_wrapped(_tool_dispatcher(module))
        if GOOGLE_ADK_VERSION < (2, 7, 0):
            self.assert_not_wrapped(getattr(module.flows.llm_flows.functions, "__call_tool_live"))
        self.assert_not_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_double_patched(self, module):
        self.assert_not_double_wrapped(module.runners.Runner.run_async)
        self.assert_not_double_wrapped(module.runners.Runner.run_live)
        self.assert_not_double_wrapped(_tool_dispatcher(module))
        if GOOGLE_ADK_VERSION < (2, 7, 0):
            self.assert_not_double_wrapped(getattr(module.flows.llm_flows.functions, "__call_tool_live"))
        self.assert_not_double_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_double_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ((1, 0, 0), ("flows.llm_flows.functions", "__call_tool_async")),
        ((2, 8, 0), ("flows.llm_flows.functions", "__call_tool_async")),
        ((2, 9, 0), ("flows.llm_flows._tool_caller", "_call_tool_async")),
        ((2, 9, 1), ("flows.llm_flows._tool_caller", "_call_tool_async")),
    ],
)
def test_tool_dispatch_target_follows_the_google_adk_2_9_move(version, expected):
    """The wrap target is chosen by version, so this holds whatever google-adk the venv installed."""
    assert _tool_dispatch_target(version) == expected
