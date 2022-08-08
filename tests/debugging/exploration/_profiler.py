import os
import typing as t

from debugger import COLS
from debugger import ExplorationDebugger
from debugger import ModuleCollector
from debugger import status

from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._probe.model import FunctionProbe
from ddtrace.internal.utils.formats import asbool


# Track all instrumented functions and their call count.
_tracked_funcs = {}  # type: t.Dict[str, int]


ENABLED = asbool(os.getenv("DD_DEBUGGER_EXPL_PROFILER_ENABLED", True))


class FunctionCollector(ModuleCollector):
    def on_collect(self, discovery):
        # type: (FunctionDiscovery) -> None
        module = discovery._module
        status("[profiler] Collecting functions from %s" % module.__name__)
        for fname, f in discovery._fullname_index.items():
            _tracked_funcs[fname] = 0
            DeterministicProfiler.add_probe(
                FunctionProbe(
                    probe_id=str(hash(f)),
                    module=module.__name__,
                    func_qname=fname.replace(module.__name__, "").lstrip("."),
                    rate=float("inf"),
                )
            )


class DeterministicProfiler(ExplorationDebugger):
    __watchdog__ = FunctionCollector

    @classmethod
    def report_func_calls(cls):
        # type: () -> None
        for probe in (_ for _ in cls.get_triggered_probes() if isinstance(_, FunctionProbe)):
            _tracked_funcs[".".join([probe.module, probe.func_qname])] += 1
        print(("{:=^%ds}" % COLS).format(" Function coverage "))
        print("")
        calls = sorted([(v, k) for k, v in _tracked_funcs.items()], reverse=True)
        if not calls:
            print("No functions called")
            return
        w = max(len(f) for _, f in calls)
        called = sum(v > 0 for v in _tracked_funcs.values())
        print("Functions called: %d/%d" % (called, len(_tracked_funcs)))
        print("")
        print(("{:<%d} {:>5}" % w).format("Function", "Calls"))
        print("=" * (w + 6))
        for calls, func in calls:
            print(("{:<%d} {:>5}" % w).format(func, calls))
        print("")

    @classmethod
    def on_disable(cls):
        # type: () -> None
        cls.report_func_calls()


if ENABLED:
    DeterministicProfiler.enable()
