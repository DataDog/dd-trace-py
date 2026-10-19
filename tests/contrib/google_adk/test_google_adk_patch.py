import importlib
import sys

import pytest

from ddtrace.contrib.internal.google_adk.patch import GOOGLE_ADK_VERSION
from ddtrace.contrib.internal.google_adk.patch import _live_tool_dispatch_target
from ddtrace.contrib.internal.google_adk.patch import _tool_dispatch_target
from ddtrace.contrib.internal.google_adk.patch import get_version
from ddtrace.contrib.internal.google_adk.patch import patch
from ddtrace.contrib.internal.google_adk.patch import unpatch
from ddtrace.internal.utils.wrappers import iswrapped
from tests.contrib.patch import PatchTestCase


def _tool_dispatcher(module):
    """Return the central tool dispatcher of the installed google-adk, looked up with getattr so
    the double-underscore name is not mangled from inside a class body. The module is imported
    rather than reached by attribute because google-adk 2.12.0 loads flows.llm_flows lazily.
    """
    dispatch_module, dispatch_name = _tool_dispatch_target(GOOGLE_ADK_VERSION)
    return getattr(importlib.import_module(f"{module.__name__}.{dispatch_module}"), dispatch_name)


def _live_tool_dispatchers(module):
    """Return the live-mode copy of the dispatcher on google-adk versions that have one."""
    live_target = _live_tool_dispatch_target(GOOGLE_ADK_VERSION)
    if live_target is None:
        return []
    return [getattr(importlib.import_module(f"{module.__name__}.{live_target[0]}"), live_target[1])]


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
        for live_dispatcher in _live_tool_dispatchers(module):
            self.assert_wrapped(live_dispatcher)
        if GOOGLE_ADK_VERSION < (2, 7, 0):
            self.assert_wrapped(getattr(module.flows.llm_flows.functions, "__call_tool_live"))
        self.assert_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_patched(self, module):
        self.assert_not_wrapped(module.runners.Runner.run_async)
        self.assert_not_wrapped(module.runners.Runner.run_live)
        self.assert_not_wrapped(_tool_dispatcher(module))
        for live_dispatcher in _live_tool_dispatchers(module):
            self.assert_not_wrapped(live_dispatcher)
        if GOOGLE_ADK_VERSION < (2, 7, 0):
            self.assert_not_wrapped(getattr(module.flows.llm_flows.functions, "__call_tool_live"))
        self.assert_not_wrapped(module.code_executors.BuiltInCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.VertexAiCodeExecutor.execute_code)
        self.assert_not_wrapped(module.code_executors.UnsafeLocalCodeExecutor.execute_code)

    def assert_not_module_double_patched(self, module):
        self.assert_not_double_wrapped(module.runners.Runner.run_async)
        self.assert_not_double_wrapped(module.runners.Runner.run_live)
        self.assert_not_double_wrapped(_tool_dispatcher(module))
        for live_dispatcher in _live_tool_dispatchers(module):
            self.assert_not_double_wrapped(live_dispatcher)
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
        ((2, 10, 0), ("flows.llm_flows.tools._caller", "_call_tool_async")),
        ((2, 11, 0), ("flows.llm_flows.tools._caller", "_call_tool_async")),
        ((2, 12, 0), ("flows.llm_flows.tools._caller", "_call_tool_async")),
    ],
)
def test_tool_dispatch_target_follows_the_google_adk_moves(version, expected):
    """The wrap target is chosen by version, so this holds whatever google-adk the venv installed."""
    assert _tool_dispatch_target(version) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ((2, 10, 0), None),
        ((2, 11, 0), None),
        ((2, 12, 0), ("flows.llm_flows.tools._live_caller", "_call_tool_async")),
    ],
)
def test_live_tool_dispatch_target(version, expected):
    assert _live_tool_dispatch_target(version) == expected


def test_patch_skips_a_missing_tool_dispatcher(monkeypatch):
    """A dispatcher google-adk no longer defines is skipped instead of failing patch(), so a future
    move costs tool spans but not application startup. Runs on any installed google-adk version.
    """
    import google.adk as adk

    unpatch()  # patch() is a no-op if an earlier test left the integration patched
    dispatch_module, dispatch_name = _tool_dispatch_target(GOOGLE_ADK_VERSION)
    module = importlib.import_module(f"google.adk.{dispatch_module}")
    monkeypatch.delattr(module, dispatch_name)
    try:
        patch()
        assert iswrapped(adk.runners.Runner, "run_async")
        assert not hasattr(module, dispatch_name)
    finally:
        unpatch()
    assert not iswrapped(adk.runners.Runner, "run_async")


@pytest.mark.skipif(_live_tool_dispatch_target(GOOGLE_ADK_VERSION) is None, reason="no live-mode dispatcher copy")
def test_live_dispatcher_imported_after_patch_is_not_double_wrapped(monkeypatch):
    """If _live_caller is first imported after _caller was wrapped, its `from ._caller import` copies the
    wrapper. Wrapping that copy again would trace every run_live tool call twice.
    """
    import google.adk.flows.llm_flows.tools as tools_package

    unpatch()  # patch() is a no-op if an earlier test left the integration patched
    live_module_path, live_name = _live_tool_dispatch_target(GOOGLE_ADK_VERSION)
    live_module_name = f"google.adk.{live_module_path}"
    monkeypatch.delitem(sys.modules, live_module_name)
    monkeypatch.delattr(tools_package, live_module_name.rsplit(".", 1)[1])
    try:
        patch()
        live_dispatcher = getattr(sys.modules[live_module_name], live_name)
        assert iswrapped(live_dispatcher)
        assert not iswrapped(live_dispatcher.__wrapped__)
    finally:
        unpatch()
    assert not iswrapped(getattr(sys.modules[live_module_name], live_name))
