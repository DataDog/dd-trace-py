import os
import sys
from tempfile import NamedTemporaryFile

from ddtrace.contrib.internal.langgraph.patch import get_version
from ddtrace.contrib.internal.langgraph.patch import patch
from ddtrace.contrib.internal.langgraph.patch import unpatch
from tests.contrib.patch import PatchTestCase
from tests.utils import call_program


os.environ["_DD_TRACE_LANGGRAPH_ENABLED"] = "true"


class TestLangGraphPatch(PatchTestCase.Base):
    __integration_name__ = "langgraph"
    __module_name__ = "langgraph"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, langgraph):
        from langgraph.pregel import Pregel
        from langgraph.pregel.loop import PregelLoop
        from langgraph.utils.runnable import RunnableSeq

        self.assert_wrapped(RunnableSeq.invoke)
        self.assert_wrapped(RunnableSeq.ainvoke)
        self.assert_wrapped(Pregel.stream)
        self.assert_wrapped(Pregel.astream)
        self.assert_wrapped(PregelLoop.tick)

    def assert_not_module_patched(self, langgraph):
        from langgraph.pregel import Pregel
        from langgraph.pregel.loop import PregelLoop
        from langgraph.utils.runnable import RunnableSeq

        self.assert_not_wrapped(RunnableSeq.invoke)
        self.assert_not_wrapped(RunnableSeq.ainvoke)
        self.assert_not_wrapped(Pregel.stream)
        self.assert_not_wrapped(Pregel.astream)
        self.assert_not_wrapped(PregelLoop.tick)

    def assert_not_module_double_patched(self, langgraph):
        from langgraph.pregel import Pregel
        from langgraph.pregel.loop import PregelLoop
        from langgraph.utils.runnable import RunnableSeq

        self.assert_not_double_wrapped(RunnableSeq.invoke)
        self.assert_not_double_wrapped(RunnableSeq.ainvoke)
        self.assert_not_double_wrapped(Pregel.stream)
        self.assert_not_double_wrapped(Pregel.astream)
        self.assert_not_double_wrapped(PregelLoop.tick)

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
