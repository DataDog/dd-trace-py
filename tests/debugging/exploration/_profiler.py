from pathlib import Path
import typing as t

from _config import config as expl_config
from debugger import COLS
from debugger import ExplorationDebugger
from debugger import ModuleCollector
from debugger import config
from debugger import status
from debugging.utils import create_snapshot_function_probe
from output import log

from ddtrace.debugging._function.discovery import FunctionDiscovery
from ddtrace.debugging._probe.model import FunctionLocationMixin
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal.module import origin


# Track all instrumented functions and their call count.
_tracked_funcs: t.Dict[str, int] = {}


class FunctionCollector(ModuleCollector):
    def on_collect(self, discovery: FunctionDiscovery) -> None:
        module = discovery._module
        status("[profiler] Collecting functions from %s" % module.__name__)
        for fname, f in discovery._fullname_index.items():
            if origin(module) != Path(f.__code__.co_filename).resolve():
                # Do not wrap functions that do not belong to the module. We
                # will have a chance to wrap them when we discover the module
                # they belong to.
                continue
            _tracked_funcs[fname] = 0
            DeterministicProfiler.add_probe(
                create_snapshot_function_probe(
                    probe_id=str(hash(f)),
                    module=module.__name__,
                    func_qname=fname.replace(module.__name__, "").lstrip("."),
                    rate=float("inf"),
                    limits=expl_config.limits,
                )
            )


class DeterministicProfiler(ExplorationDebugger):
    __watchdog__ = FunctionCollector

    @classmethod
    def report_func_calls(cls) -> None:
        for probe in (_ for _ in cls.get_triggered_probes() if isinstance(_, FunctionLocationMixin)):
            _tracked_funcs[".".join([probe.module, probe.func_qname])] += 1
        log(("{:=^%ds}" % COLS).format(" Function coverage "))
        log("")
        calls = sorted([(v, k) for k, v in _tracked_funcs.items()], reverse=True)
        if not calls:
            log("No functions called")
            return
        w = max(len(f) for _, f in calls)
        called = sum(v > 0 for v in _tracked_funcs.values())
        log("Functions called: %d/%d" % (called, len(_tracked_funcs)))
        log("")
        log(("{:<%d} {:>5}" % w).format("Function", "Calls"))
        log("=" * (w + 6))
        for ncalls, func in calls:
            log(("{:<%d} {:>5}" % w).format(func, ncalls))
        log("")

    @classmethod
    def on_disable(cls) -> None:
        cls.report_func_calls()

    @classmethod
    def on_snapshot(cls, snapshot: Snapshot) -> None:
        if config.profiler.delete_probes:
            # Change the state of the snapshot to avoid setting the emitting
            # state. This would be too late, when the probe is already
            # deleted from the registry.
            snapshot.state = SignalState.NONE
            cls.delete_probe(snapshot.probe)


if config.profiler.enabled:
    DeterministicProfiler.enable()
