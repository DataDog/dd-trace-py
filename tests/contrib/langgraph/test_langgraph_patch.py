import os
import sys
from tempfile import NamedTemporaryFile

from ddtrace.contrib.internal.langgraph.patch import LANGGRAPH_VERSION
from ddtrace.contrib.internal.langgraph.patch import get_version
from ddtrace.contrib.internal.langgraph.patch import patch
from ddtrace.contrib.internal.langgraph.patch import unpatch
from tests.contrib.patch import PatchTestCase
from tests.utils import call_program


class TestLangGraphPatch(PatchTestCase.Base):
    __integration_name__ = "langgraph"
    __module_name__ = "langgraph"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, langgraph):
        from langgraph.pregel import Pregel

        if LANGGRAPH_VERSION < (0, 6, 0):
            from langgraph.pregel.loop import PregelLoop
            from langgraph.utils.runnable import RunnableSeq
        else:
            from langgraph._internal._runnable import RunnableSeq
            from langgraph.pregel._loop import PregelLoop

        self.assert_wrapped(RunnableSeq.invoke)
        self.assert_wrapped(RunnableSeq.ainvoke)
        self.assert_wrapped(RunnableSeq.astream)
        self.assert_wrapped(Pregel.stream)
        self.assert_wrapped(Pregel.astream)
        self.assert_wrapped(PregelLoop.tick)

        if LANGGRAPH_VERSION >= (0, 3, 29):
            if LANGGRAPH_VERSION < (0, 6, 0):
                self.assert_wrapped(langgraph.utils.runnable._consume_aiter)
            else:
                self.assert_wrapped(langgraph._internal._runnable._consume_aiter)

    def assert_not_module_patched(self, langgraph):
        from langgraph.pregel import Pregel

        if LANGGRAPH_VERSION < (0, 6, 0):
            from langgraph.pregel.loop import PregelLoop
            from langgraph.utils.runnable import RunnableSeq
        else:
            from langgraph._internal._runnable import RunnableSeq
            from langgraph.pregel._loop import PregelLoop

        self.assert_not_wrapped(RunnableSeq.invoke)
        self.assert_not_wrapped(RunnableSeq.ainvoke)
        self.assert_not_wrapped(RunnableSeq.astream)
        self.assert_not_wrapped(Pregel.stream)
        self.assert_not_wrapped(Pregel.astream)
        self.assert_not_wrapped(PregelLoop.tick)
        if LANGGRAPH_VERSION >= (0, 3, 29):
            if LANGGRAPH_VERSION < (0, 6, 0):
                self.assert_not_wrapped(langgraph.utils.runnable._consume_aiter)
            else:
                self.assert_not_wrapped(langgraph._internal._runnable._consume_aiter)

    def assert_not_module_double_patched(self, langgraph):
        from langgraph.pregel import Pregel

        if LANGGRAPH_VERSION < (0, 6, 0):
            from langgraph.pregel.loop import PregelLoop
            from langgraph.utils.runnable import RunnableSeq
        else:
            from langgraph._internal._runnable import RunnableSeq
            from langgraph.pregel._loop import PregelLoop

        self.assert_not_double_wrapped(RunnableSeq.invoke)
        self.assert_not_double_wrapped(RunnableSeq.ainvoke)
        self.assert_not_double_wrapped(RunnableSeq.astream)
        self.assert_not_double_wrapped(Pregel.stream)
        self.assert_not_double_wrapped(Pregel.astream)
        self.assert_not_double_wrapped(PregelLoop.tick)
        if LANGGRAPH_VERSION >= (0, 3, 29):
            if LANGGRAPH_VERSION < (0, 6, 0):
                self.assert_not_double_wrapped(langgraph.utils.runnable._consume_aiter)
            else:
                self.assert_not_double_wrapped(langgraph._internal._runnable._consume_aiter)

    def test_ddtrace_run_patch_on_import(self):
        # Overriding the base test case due to langgraph's code structure not allowing
        # langgraph to be patched by a direct import.
        with NamedTemporaryFile(mode="w", suffix=".py") as f:
            f.write(
                """
import sys

from ddtrace.internal.module import ModuleWatchdog

from wrapt import wrap_function_wrapper as wrap

patched = False

def patch_hook(module):
    def patch_wrapper(wrapped, _, args, kwrags):
        global patched

        result = wrapped(*args, **kwrags)
        sys.stdout.write("K")
        patched = True
        return result

    wrap(module.__name__, module.patch.__name__, patch_wrapper)

ModuleWatchdog.register_module_hook("ddtrace.contrib..patch", patch_hook)

sys.stdout.write("O")

import langgraph as mod
from langgraph import graph

# If the module was already loaded during the sitecustomize
# we check that the module was marked as patched.
if not patched and (
    getattr(mod, "__datadog_patch", False) or getattr(mod, "_datadog_patch", False)
):
    sys.stdout.write("K")
"""
            )
            f.flush()

            env = os.environ.copy()
            env["DD_TRACE_%s_ENABLED" % self.__integration_name__.upper()] = "1"

            out, err, _, _ = call_program("ddtrace-run", sys.executable, f.name, env=env)

            self.assertEqual(out, b"OK", "stderr:\n%s" % err.decode())

    def test_supported_versions_function_allows_valid_imports(self):
        # Overriding the base test case due to langgraph's code structure not allowing
        # langgraph to be patched by a direct import.
        with NamedTemporaryFile(mode="w", suffix=".py") as f:
            f.write(
                """
import sys
from ddtrace.internal.module import ModuleWatchdog
from wrapt import wrap_function_wrapper as wrap

supported_versions_called = False

def patch_hook(module):
    def supported_versions_wrapper(wrapped, _, args, kwrags):
        global supported_versions_called
        result = wrapped(*args, **kwrags)
        sys.stdout.write("K")
        supported_versions_called = True
        return result

    def patch_wrapper(wrapped, _, args, kwrags):
        global patched

        result = wrapped(*args, **kwrags)
        sys.stdout.write("K")
        patched = True
        return result

    patch_module = module
    if 'patch' not in module.__name__:
        patch_module = module.patch

    wrap(
        patch_module.__name__,
        patch_module._supported_versions.__name__,
        supported_versions_wrapper,
    )
    wrap(module.__name__, module.patch.__name__, patch_wrapper)

ModuleWatchdog.register_module_hook("ddtrace.contrib.internal.%s.patch", patch_hook)

sys.stdout.write("O")

import langgraph.graph as mod

# If the module was already loaded during the sitecustomize
# we check that the module was marked as patched.
if not supported_versions_called and (
    getattr(mod, "__datadog_patch", False) or getattr(mod, "_datadog_patch", False)
):
    sys.stdout.write("K")
                    """
                % (self.__integration_name__)
            )
            f.flush()

            env = os.environ.copy()
            env["DD_TRACE_SAFE_INSTRUMENTATION_ENABLED"] = "1"
            env["DD_TRACE_%s_ENABLED" % self.__integration_name__.upper()] = "1"

            out, err, _, _ = call_program("ddtrace-run", sys.executable, f.name, env=env)
            assert "OKK" in out.decode(), "stderr:\n%s" % err.decode()
